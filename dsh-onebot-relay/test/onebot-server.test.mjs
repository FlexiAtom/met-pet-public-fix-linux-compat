/**
 * `lib/onebot.js` 的 mode B 端到端单测（真起端口、真连 WS、真发 HTTP）。
 *
 * 与 `scripts/self-test.mjs` 的分工：那边是「给运维看的一屏 PASS/FAIL」，
 * 这里是「给 CI 看的结构化断言」。两者覆盖同一套线上形状，
 * 故意重复——协议回归必须有两道独立的闸。
 *
 * ⚠️ 每个用例都走 `withServer` 收尾。一个失败的断言若跳过了 `server.stop()`，
 * 监听中的 `http.Server` 会把测试进程挂住（表现为「卡死」而不是「失败」）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import WebSocket from 'ws'
import { ROUTES, RETCODE, STATUS, isApiResponse, joinPath, makeResponse } from '../lib/contract.js'
import {
  MAX_BODY_BYTES, OneBotServer, authorizeToken, bearerTokenOf, extractToken,
  timingSafeTokenEqual, urlOf, withAccessToken,
} from '../lib/onebot.js'

const TOKEN = 'unit-test-token-abcdef0123456789'
const PREFIX = '/onebot-relay'

function waitFor(emitter, event, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`等待 ${event} 超时`)), timeoutMs)
    emitter.once(event, (...args) => {
      clearTimeout(timer)
      resolve(args)
    })
  })
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function until(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return true
    await sleep(15)
  }
  return predicate()
}

/** 起一个 mode B 服务 + 一个替身 onCall；用例结束**一定**收尾。 */
async function withServer(t, options, body) {
  const calls = []
  const server = new OneBotServer({
    host: '127.0.0.1',
    port: 0,
    pathPrefix: PREFIX,
    token: TOKEN,
    heartbeatMs: 15_000,
    onCall: async (action, params, meta) => {
      calls.push({ action, params, transport: meta?.transport })
      if (action === 'boom') throw new Error('替身炸了')
      return { status: STATUS.OK, retcode: RETCODE.OK, data: { action, received: params } }
    },
    ...options,
  })
  const address = await server.start()
  t.after(async () => { await server.stop() })
  return await body({ server, address, calls })
}

function connectRaw(url, headers) {
  return new Promise((resolve) => {
    const ws = new WebSocket(url, { headers, handshakeTimeout: 4000 })
    const timer = setTimeout(() => {
      try { ws.terminate() } catch { /* 忽略 */ }
      resolve({ ok: false, ws, error: 'timeout' })
    }, 4000)
    ws.once('open', () => { clearTimeout(timer); resolve({ ok: true, ws, error: '' }) })
    ws.once('error', (error) => { clearTimeout(timer); resolve({ ok: false, ws, error: String(error?.message ?? error) }) })
  })
}

test('鉴权辅助函数：头优先、其次 query、定长比较、空 token 一律拒', () => {
  assert.equal(bearerTokenOf({ authorization: 'Bearer abc' }), 'abc')
  assert.equal(bearerTokenOf({ Authorization: 'bearer  abc ' }), 'abc')
  assert.equal(bearerTokenOf({ authorization: 'abc' }), '')
  assert.equal(bearerTokenOf({}), '')
  assert.equal(bearerTokenOf(null), '')

  const request = { headers: { authorization: 'Bearer head' }, url: '/x?access_token=query' }
  assert.equal(extractToken(request), 'head')
  assert.equal(extractToken({ headers: {}, url: '/x?access_token=query' }), 'query')
  assert.equal(extractToken({ headers: {}, url: '/x' }), '')

  assert.equal(authorizeToken(request, 'head'), true)
  assert.equal(authorizeToken(request, 'query'), false)
  assert.equal(authorizeToken({ headers: {}, url: '/x?access_token=q' }, 'q'), true)
  // token 为空字符串 = 拒绝（不是「不鉴权」——那是实现端主动选择的宽松模式）
  assert.equal(authorizeToken(request, ''), false)
  assert.equal(timingSafeTokenEqual('abc', 'abc'), true)
  assert.equal(timingSafeTokenEqual('abc', 'abd'), false)
  assert.equal(timingSafeTokenEqual('', ''), true)

  assert.equal(urlOf({ url: '/a/b?c=1' }).pathname, '/a/b')
  assert.equal(withAccessToken('ws://h/p', 't'), 'ws://h/p?access_token=t')
  assert.equal(withAccessToken('ws://h/p?access_token=keep', 't'), 'ws://h/p?access_token=keep')
  assert.equal(MAX_BODY_BYTES, 8 * 1024 * 1024)
  assert.equal(joinPath(PREFIX, ROUTES.WS), '/onebot-relay/ws')
})

test('mode B：超大请求体一律得到 413（含不带 content-length 的分块请求）', async (t) => {
  await withServer(t, {}, async ({ address }) => {
    const url = `http://127.0.0.1:${address.port}${address.apiPath}`

    // ① 带 content-length
    const declared = await fetch(url, {
      method: 'POST',
      headers: { authorization: `Bearer ${TOKEN}` },
      body: Buffer.alloc(MAX_BODY_BYTES + 4096, 0x61),
    })
    assert.equal(declared.status, 413)
    assert.equal((await declared.json()).retcode, RETCODE.BAD_PARAM)

    // ② 分块（不带 content-length）：这里曾经是 ECONNRESET 而不是 413
    async function* big() {
      for (let index = 0; index < 9; index += 1) yield Buffer.alloc(1024 * 1024, 0x62)
    }
    const chunked = await fetch(url, {
      method: 'POST',
      headers: { authorization: `Bearer ${TOKEN}` },
      body: Readable.from(big()),
      duplex: 'half',
    })
    assert.equal(chunked.status, 413, '分块超大请求也必须拿到 413，而不是连接被掐断')
    assert.equal((await chunked.json()).retcode, RETCODE.BAD_PARAM)
  })
})

test('mode B：正常大小的请求体不受影响（对照）', async (t) => {
  await withServer(t, {}, async ({ address, calls }) => {
    const response = await fetch(`http://127.0.0.1:${address.port}${address.apiPath}`, {
      method: 'POST',
      headers: { authorization: `Bearer ${TOKEN}`, 'content-type': 'application/json' },
      body: JSON.stringify({ action: 'get_status', params: {}, echo: 'ok-1' }),
    })
    assert.equal(response.status, 200)
    const body = await response.json()
    assert.equal(body.echo, 'ok-1')
    assert.equal(calls.length, 1)
  })
})

test('mode B：路径与 start/stop 生命周期', async (t) => {
  await withServer(t, {}, async ({ server, address }) => {
    assert.equal(address.wsPath, '/onebot-relay/ws')
    assert.equal(address.apiPath, '/onebot-relay/api')
    assert.equal(address.statusPath, '/onebot-relay/status')
    assert.equal(address.port > 0, true)
    assert.equal(server.snapshot().listening, true)
    // 重复 start 幂等
    const again = await server.start()
    assert.equal(again.port, address.port)
  })
})

test('mode B：未鉴权 / 错 token / 错路径的 WS 都被拒；头与 query 都能连上', async (t) => {
  await withServer(t, {}, async ({ address }) => {
    const base = `ws://127.0.0.1:${address.port}${address.wsPath}`

    const noToken = await connectRaw(base)
    assert.equal(noToken.ok, false)
    assert.match(noToken.error, /401/)

    const wrong = await connectRaw(base, { Authorization: 'Bearer nope' })
    assert.equal(wrong.ok, false)

    const viaHeader = await connectRaw(base, { Authorization: `Bearer ${TOKEN}` })
    assert.equal(viaHeader.ok, true)

    const viaQuery = await connectRaw(`${base}?access_token=${TOKEN}`)
    assert.equal(viaQuery.ok, true)

    // 未知路径的 upgrade 也要被拒（不能变成「任意路径都能连进来」）
    const wrongPath = await connectRaw(`ws://127.0.0.1:${address.port}/other`, {
      Authorization: `Bearer ${TOKEN}`,
    })
    assert.equal(wrongPath.ok, false)
    assert.match(wrongPath.error, /404/)

    for (const ws of [noToken.ws, wrong.ws, viaHeader.ws, viaQuery.ws, wrongPath.ws]) {
      try { ws.terminate() } catch { /* 忽略 */ }
    }
  })
})

test('mode B：WS 上的 API 调用按 echo 应答，形状固定', async (t) => {
  await withServer(t, {}, async ({ server, address, calls }) => {
    const ws = new WebSocket(`ws://127.0.0.1:${address.port}${address.wsPath}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    })
    t.after(() => { try { ws.terminate() } catch { /* 忽略 */ } })
    await waitFor(ws, 'open')
    const frames = []
    ws.on('message', (raw) => frames.push(JSON.parse(raw.toString('utf8'))))

    ws.send(JSON.stringify({ action: 'send_private_msg', params: { user_id: 10001, message: 'hi' }, echo: 'e-1' }))
    await until(() => frames.length >= 1)
    assert.deepEqual(Object.keys(frames[0]).sort(), ['data', 'echo', 'retcode', 'status'])
    assert.equal(frames[0].status, 'ok')
    assert.equal(frames[0].retcode, 0)
    assert.equal(frames[0].echo, 'e-1')
    assert.deepEqual(frames[0].data, { action: 'send_private_msg', received: { user_id: 10001, message: 'hi' } })
    assert.equal(calls[0].transport, 'ws')

    // echo 原样回传：数字 / null 都要逐字保留（调用方按它关联）
    frames.length = 0
    ws.send(JSON.stringify({ action: 'get_status', params: {}, echo: 12345 }))
    await until(() => frames.length >= 1)
    assert.equal(frames[0].echo, 12345)

    frames.length = 0
    ws.send(JSON.stringify({ action: 'get_status', params: {} }))
    await until(() => frames.length >= 1)
    assert.equal(frames[0].echo, null)

    // 缺 action → 100
    frames.length = 0
    ws.send(JSON.stringify({ params: {}, echo: 'e-2' }))
    await until(() => frames.length >= 1)
    assert.equal(frames[0].retcode, RETCODE.MISSING_PARAM)

    // 非法 JSON → 102
    frames.length = 0
    ws.send('{ not json')
    await until(() => frames.length >= 1)
    assert.equal(frames[0].retcode, RETCODE.BAD_PARAM)

    // onCall 抛错 → 收成 200（内部错误），连接不断
    frames.length = 0
    ws.send(JSON.stringify({ action: 'boom', params: {}, echo: 'e-3' }))
    await until(() => frames.length >= 1)
    assert.equal(frames[0].retcode, RETCODE.INTERNAL)
    assert.equal(frames[0].echo, 'e-3')
    assert.match(frames[0].error, /替身炸了/)

    assert.equal(server.clientCount, 1)
  })
})

test('mode B：事件推送方向（broadcast 原样到达，无消费者时返回 0）', async (t) => {
  await withServer(t, {}, async ({ server, address }) => {
    // 没有客户端时广播是「丢掉」，不是错误
    assert.equal(server.broadcast({ post_type: 'meta_event' }), 0)

    const ws = new WebSocket(`ws://127.0.0.1:${address.port}${address.wsPath}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    })
    t.after(() => { try { ws.terminate() } catch { /* 忽略 */ } })
    await waitFor(ws, 'open')
    const events = []
    ws.on('message', (raw) => {
      const payload = JSON.parse(raw.toString('utf8'))
      if (!isApiResponse(payload)) events.push(payload)
    })
    await until(() => server.clientCount === 1)

    const event = {
      time: 1_700_000_000,
      self_id: 10001,
      post_type: 'message',
      message_type: 'group',
      sub_type: 'normal',
      message_id: 456,
      group_id: 987654321,
      user_id: 123456789,
      anonymous: null,
      message: '[CQ:at,qq=10001] Hello',
      raw_message: '[CQ:at,qq=10001] Hello',
      font: 0,
      sender: { user_id: 123456789, nickname: '测试用户', card: '', role: 'member', level: '1', title: '' },
    }
    assert.equal(server.broadcast(event), 1)
    await until(() => events.length >= 1)
    assert.deepEqual(events[0], event)
    assert.equal(events[0].post_type, 'message')
  })
})

test('mode B：HTTP API 与 WS 共用同一套帧与鉴权', async (t) => {
  await withServer(t, {}, async ({ address, calls }) => {
    const api = `http://127.0.0.1:${address.port}${address.apiPath}`

    const okRes = await fetch(api, {
      method: 'POST',
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ action: 'send_group_msg', params: { group_id: 5, message: 'hi' }, echo: 'h-1' }),
    })
    const okBody = await okRes.json()
    assert.equal(okRes.status, 200)
    assert.equal(okBody.status, 'ok')
    assert.equal(okBody.echo, 'h-1')
    assert.equal(calls[0].transport, 'http')

    const queryRes = await fetch(`${api}?access_token=${TOKEN}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ action: 'get_status', params: {}, echo: 'h-2' }),
    })
    assert.equal(queryRes.status, 200)

    const denied = await fetch(api, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ action: 'get_status', params: {}, echo: 'h-3' }),
    })
    const deniedBody = await denied.json()
    assert.equal(denied.status, 401)
    assert.equal(deniedBody.retcode, RETCODE.UNAUTHORIZED)
    assert.equal(deniedBody.data, null)

    const wrongMethod = await fetch(api, { headers: { Authorization: `Bearer ${TOKEN}` } })
    assert.equal(wrongMethod.status, 405)

    const notFound = await fetch(`http://127.0.0.1:${address.port}/nope`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    })
    assert.equal(notFound.status, 404)

    const badBody = await fetch(api, {
      method: 'POST',
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${TOKEN}` },
      body: 'not json',
    })
    const badBodyJson = await badBody.json()
    assert.equal(badBody.status, 400)
    assert.equal(badBodyJson.retcode, RETCODE.BAD_PARAM)

    // /status 快照也要鉴权
    const statusDenied = await fetch(`http://127.0.0.1:${address.port}${address.statusPath}`)
    assert.equal(statusDenied.status, 401)
    const statusOk = await fetch(`http://127.0.0.1:${address.port}${address.statusPath}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    })
    const statusBody = await statusOk.json()
    assert.equal(statusOk.status, 200)
    assert.equal(statusBody.ok, true)
    assert.equal(statusBody.authFailures >= 2, true)
  })
})

test('mode B：stop() 会断开所有客户端，且还能再 start', async (t) => {
  await withServer(t, {}, async ({ server, address }) => {
    const ws = new WebSocket(`ws://127.0.0.1:${address.port}${address.wsPath}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    })
    t.after(() => { try { ws.terminate() } catch { /* 忽略 */ } })
    await waitFor(ws, 'open')
    await until(() => server.clientCount === 1)
    await server.stop()
    assert.equal(server.clientCount, 0)
    assert.equal(server.snapshot().listening, false)
    // 配置端口是 0，所以再 start 会拿到一个新的临时端口
    const rebound = await server.start()
    assert.equal(rebound.port > 0, true)
    assert.equal(server.snapshot().listening, true)
  })
})

test('OneBotServer 缺 onCall 时立刻抛错（不是运行期才发现）', () => {
  assert.throws(() => new OneBotServer({ onCall: undefined }), /onCall/)
})

test('发出去的响应形状与 makeResponse 一致（搬运不重算）', () => {
  assert.deepEqual(makeResponse({ status: STATUS.ASYNC, retcode: RETCODE.OK, data: null, echo: 'e' }), {
    status: 'async', retcode: 0, data: null, echo: 'e',
  })
})
