/**
 * dsh-onebot-relay · 独立协议自测（不需要 dsh，也不需要 MeaPet）
 *
 * 这个脚本证明的是**线上形状**，不是「插件装上了」：
 *
 *   A. mode B（本插件当 OneBot 服务端，自己起端口）
 *      A1 无 token 的 WS 连接被拒
 *      A2 `Authorization: Bearer` 能连上
 *      A3 `?access_token=` 也能连上
 *      A4 `send_private_msg` 的应答 echo 与请求一致，且形状是合法
 *         `{status, retcode, data, echo}`
 *      A5 事件推送方向：`broadcast()` 的事件以 `post_type: "message"` 原样到达客户端
 *      A6 HTTP API（`POST {action,params,echo}`）走同一套帧与应答
 *      A7 HTTP 未鉴权 → 401 + retcode 104
 *
 *   B. mode A（本插件当 OneBot 客户端，连一个假 MeaPet hub）
 *      B1 拨号时带上 `Authorization: Bearer <token>` 头
 *      B2 收到的 `message` 事件能解析出会话键 `private:<user_id>`
 *      B3 出站帧形状是 `{action, params, echo}`，且 echo 唯一
 *      B4 按 echo 关联响应（乱序也能对上一个）；孤儿响应不会被当成事件
 *
 * 运行：`node scripts/self-test.mjs`。失败时**非零退出**。
 * 只依赖 `ws`（package.json 的 dependencies），没有任何 dsh 依赖。
 */
import assert from 'node:assert/strict'
import WebSocket, { WebSocketServer } from 'ws'
import {
  conversationKeyOf, isApiResponse, makeResponse, normalizeMessage,
  RETCODE, STATUS, segmentsToText, hasAtSelf, chunkText, targetOf,
} from '../lib/contract.js'
import { OneBotClient, OneBotServer } from '../lib/onebot.js'

const TOKEN = 'self-test-token-0123456789abcdef'
const results = []

/** 记一条结果并**立刻**打出来（出问题时能一眼看到卡在哪一步）。 */
function check(title, ok, detail = '') {
  results.push({ title, ok: ok === true })
  const suffix = detail === '' ? '' : `  — ${detail}`
  console.log(`${ok === true ? 'PASS' : 'FAIL'}  ${title}${suffix}`)
}

/** 等一个一次性事件，超时即失败（绝不无限挂住 CI）。 */
function once(emitter, event, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      emitter.off?.(event, onEvent)
      reject(new Error(`等待 ${event} 超时（${timeoutMs}ms）`))
    }, timeoutMs)
    const onEvent = (...args) => {
      clearTimeout(timer)
      resolve(args)
    }
    emitter.once(event, onEvent)
  })
}

/** 等一个真假条件成立（轮询，用于「客户端已登记」这类无事件可等的情况）。 */
async function until(predicate, timeoutMs = 5000, stepMs = 20) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return true
    await new Promise((resolve) => setTimeout(resolve, stepMs))
  }
  return predicate()
}

/** 建一个最小的 ws 客户端，并把握手结果收成 `{ok, ws, error}`。 */
function connect(url, { headers, timeoutMs = 5000 } = {}) {
  return new Promise((resolve) => {
    const ws = new WebSocket(url, { headers, handshakeTimeout: timeoutMs })
    const timer = setTimeout(() => {
      try { ws.terminate() } catch { /* 忽略 */ }
      resolve({ ok: false, ws, error: 'connect timeout' })
    }, timeoutMs)
    ws.once('open', () => {
      clearTimeout(timer)
      resolve({ ok: true, ws, error: '' })
    })
    ws.once('error', (error) => {
      clearTimeout(timer)
      resolve({ ok: false, ws, error: String(error?.message ?? error) })
    })
  })
}

// ══════════════════════════════════════════════════════════════════
// A. mode B：本插件当 OneBot 服务端
// ══════════════════════════════════════════════════════════════════

async function partA() {
  console.log('\n── A. mode B（本插件当 OneBot 服务端）────────────────────────')

  /** onCall 的替身：只做帧形状的回执，不碰 dsh。 */
  const calls = []
  const server = new OneBotServer({
    host: '127.0.0.1',
    port: 0,
    pathPrefix: '/onebot-relay',
    token: TOKEN,
    heartbeatMs: 15_000,
    onCall: async (action, params) => {
      calls.push({ action, params })
      if (action === 'send_private_msg') {
        return { status: STATUS.OK, retcode: RETCODE.OK, data: { message_id: 4242 } }
      }
      if (action === 'not_in_whitelist') {
        // 走真实路由器时这里会是 103；替身只负责证明**形状**能带出非零 retcode。
        return { status: STATUS.FAILED, retcode: RETCODE.FORBIDDEN, data: null, error: '动作不在白名单内' }
      }
      throw new Error(`替身 onCall 不认识的动作：${action}`)
    },
  })

  const address = await server.start()
  check('A0  mode B 服务已在 127.0.0.1 上监听（端口由系统分配）',
    address.port > 0 && address.wsPath === '/onebot-relay/ws' && address.apiPath === '/onebot-relay/api',
    `port=${address.port} ws=${address.wsPath} api=${address.apiPath}`)

  const base = `ws://127.0.0.1:${address.port}`

  // ── A1 无 token 必须被拒 ──
  const noToken = await connect(`${base}${address.wsPath}`)
  check('A1  无 token 的 WS 连接被拒绝', noToken.ok === false, `error=${JSON.stringify(noToken.error)}`)
  try { noToken.ws.terminate() } catch { /* 忽略 */ }

  // ── A1b 错误 token 也必须被拒 ──
  const badToken = await connect(`${base}${address.wsPath}`, { headers: { Authorization: 'Bearer wrong-token' } })
  check('A1b 错误 token 的 WS 连接被拒绝', badToken.ok === false, `error=${JSON.stringify(badToken.error)}`)
  try { badToken.ws.terminate() } catch { /* 忽略 */ }

  // ── A2 header 鉴权 ──
  const viaHeader = await connect(`${base}${address.wsPath}`, { headers: { Authorization: `Bearer ${TOKEN}` } })
  check('A2  Authorization: Bearer 头可以连上', viaHeader.ok === true, `error=${JSON.stringify(viaHeader.error)}`)
  assert.equal(viaHeader.ok, true, 'A2 是后续断言的前提')

  // ── A4 在 header 连接上走一次 send_private_msg ──
  const frames = []
  viaHeader.ws.on('message', (raw) => frames.push(JSON.parse(raw.toString('utf8'))))
  viaHeader.ws.send(JSON.stringify({
    action: 'send_private_msg',
    params: { user_id: 10001, message: '你好' },
    echo: 'self-1',
  }))
  const answered = await until(() => frames.length > 0)
  check('A4a send_private_msg 收到了应答', answered === true && frames.length > 0, `frames=${frames.length}`)
  const response = frames[0] ?? {}
  check('A4b 应答的 echo 与请求完全一致', response.echo === 'self-1', `echo=${JSON.stringify(response.echo)}`)
  check('A4c 应答形状是 {status, retcode, data, echo}',
    response.status === 'ok'
    && response.retcode === RETCODE.OK
    && response.data !== null && typeof response.data === 'object'
    && 'echo' in response,
    JSON.stringify(response))
  check('A4d 路由替身确实收到了 action 与 params',
    calls.length === 1 && calls[0].action === 'send_private_msg' && calls[0].params.user_id === 10001,
    JSON.stringify(calls[0] ?? null))

  // ── A4e 非零 retcode 也能带出来（形状不因失败而变） ──
  frames.length = 0
  viaHeader.ws.send(JSON.stringify({ action: 'not_in_whitelist', params: {}, echo: 'self-2' }))
  await until(() => frames.length > 0)
  const failedFrame = frames[0] ?? {}
  check('A4e 失败应答仍是 {status, retcode, data, echo} 形状',
    failedFrame.status === 'failed' && failedFrame.retcode === RETCODE.FORBIDDEN
    && failedFrame.data === null && failedFrame.echo === 'self-2' && failedFrame.error === '动作不在白名单内',
    JSON.stringify(failedFrame))

  // ── A4f 缺 action 的帧 → retcode 100（与 MeaPet 的缺参口径一致） ──
  frames.length = 0
  viaHeader.ws.send(JSON.stringify({ params: {}, echo: 'self-3' }))
  await until(() => frames.length > 0)
  check('A4f 缺 action 的帧回 retcode 100（缺少参数）',
    (frames[0] ?? {}).retcode === RETCODE.MISSING_PARAM && (frames[0] ?? {}).echo === 'self-3',
    JSON.stringify(frames[0] ?? null))

  // ── A5 事件推送方向 ──
  const pushed = []
  viaHeader.ws.on('message', (raw) => {
    const payload = JSON.parse(raw.toString('utf8'))
    if (!isApiResponse(payload)) pushed.push(payload)
  })
  const event = {
    time: 1_700_000_000,
    self_id: 10001,
    post_type: 'message',
    message_type: 'private',
    sub_type: 'friend',
    message_id: 99,
    user_id: 123456789,
    message: 'Hello',
    raw_message: 'Hello',
    font: 0,
    sender: { user_id: 123456789, nickname: '测试用户', sex: 'unknown', age: 0 },
  }
  const sentCount = server.broadcast(event)
  const arrived = await until(() => pushed.length > 0)
  check('A5a broadcast() 至少写给了 1 条连接', sentCount >= 1, `sent=${sentCount}`)
  check('A5b 事件原样到达客户端且 post_type === "message"',
    arrived === true && pushed[0]?.post_type === 'message',
    JSON.stringify(pushed[0] ?? null))
  check('A5c 事件字段未被改写（message_id / user_id / sender 逐字一致）',
    pushed[0]?.message_id === 99 && pushed[0]?.user_id === 123456789
    && pushed[0]?.sender?.nickname === '测试用户',
    JSON.stringify(pushed[0] ?? null))

  // ── A3 query 鉴权 ──
  const viaQuery = await connect(`${base}${address.wsPath}?access_token=${TOKEN}`)
  check('A3  ?access_token= query 可以连上', viaQuery.ok === true, `error=${JSON.stringify(viaQuery.error)}`)
  try { viaQuery.ws.close() } catch { /* 忽略 */ }

  // ── A6/A7 HTTP API ──
  const apiUrl = `http://127.0.0.1:${address.port}${address.apiPath}`
  const httpOk = await fetch(apiUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json', Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ action: 'send_private_msg', params: { user_id: 10001, message: 'hi' }, echo: 'http-1' }),
  })
  const httpBody = await httpOk.json()
  check('A6a HTTP API **带 header 鉴权**时返回 200 + ok 信封',
    httpOk.status === 200 && httpBody.status === 'ok' && httpBody.echo === 'http-1',
    `http=${httpOk.status} body=${JSON.stringify(httpBody)}`)

  const httpQuery = await fetch(`${apiUrl}?access_token=${TOKEN}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ action: 'send_private_msg', params: { user_id: 10001, message: 'hi' }, echo: 'http-2' }),
  })
  const httpQueryBody = await httpQuery.json()
  check('A6b HTTP API 的 ?access_token= 也能鉴权',
    httpQuery.status === 200 && httpQueryBody.status === 'ok' && httpQueryBody.echo === 'http-2',
    `http=${httpQuery.status} body=${JSON.stringify(httpQueryBody)}`)

  const httpNoAuth = await fetch(apiUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ action: 'send_private_msg', params: { user_id: 1, message: 'x' }, echo: 'http-3' }),
  })
  const httpNoAuthBody = await httpNoAuth.json()
  check('A7  HTTP API 未鉴权 → 401 + retcode 104',
    httpNoAuth.status === 401 && httpNoAuthBody.retcode === RETCODE.UNAUTHORIZED,
    `http=${httpNoAuth.status} body=${JSON.stringify(httpNoAuthBody)}`)

  const unknownPath = await fetch(`http://127.0.0.1:${address.port}/nope`, { method: 'POST' })
  check('A8  未知路径 → 404（不是把事件当 API 收下）', unknownPath.status === 404, `http=${unknownPath.status}`)

  const statusRes = await fetch(`http://127.0.0.1:${address.port}${address.statusPath}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  })
  const statusBody = await statusRes.json()
  check('A9  mode B 的 /status 快照可读且带鉴权',
    statusRes.status === 200 && statusBody.ok === true && statusBody.port === address.port,
    `http=${statusRes.status}`)

  try { viaHeader.ws.close() } catch { /* 忽略 */ }
  await server.stop()
  check('A10 服务可以干净停止', server.clientCount === 0 && server.snapshot().listening === false)

  // ── 纯函数层的抽样自检（与协议帧一起跑，省一次启动） ──
  check('A11 conversationKeyOf 对私聊/群聊给出 private:<uid> / group:<gid>',
    conversationKeyOf({ post_type: 'message', message_type: 'private', user_id: 7 }) === 'private:7'
    && conversationKeyOf({ post_type: 'message', message_type: 'group', group_id: 8, user_id: 7 }) === 'group:8'
    && conversationKeyOf({ post_type: 'notice', notice_type: 'friend_add' }) === null)

  const segments = normalizeMessage('[CQ:at,qq=10001] 看一下 [CQ:image,file=a.jpg]')
  check('A12 CQ 码串能解析成消息段，且 @ 判定与占位符都对',
    segments.length === 3
    && hasAtSelf(segments, '10001') === true
    && segmentsToText(segments, { selfId: '10001' }) === '看一下 [图片]',
    JSON.stringify({ segments, text: segmentsToText(segments, { selfId: '10001' }) }))

  check('A13 发送目标按会话键挑 action',
    targetOf('group:8', 'x').action === 'send_group_msg'
    && targetOf('private:7', 'x').action === 'send_private_msg'
    && targetOf('private:7', 'x').params.user_id === 7)

  const chunks = chunkText('第一段。\n第二段。\n第三段。', 6)
  check('A14 切分不劈坏多字节字符且不产生空段',
    chunks.length >= 2 && chunks.every((piece) => piece.length > 0)
    && chunks.join('').replace(/\n/g, '') === '第一段。第二段。第三段。',
    JSON.stringify(chunks))
}

// ══════════════════════════════════════════════════════════════════
// B. mode A：本插件当 OneBot 客户端（假 MeaPet hub）
// ══════════════════════════════════════════════════════════════════

async function partB() {
  console.log('\n── B. mode A（本插件当 OneBot 客户端 / 假 MeaPet hub）────────')

  /** 假 hub：收客户端帧、按 echo 回响应、可主动推事件。 */
  const received = []
  const handshakes = []
  const hub = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await once(hub, 'listening')
  const hubPort = hub.address().port
  hub.on('connection', (ws, request) => {
    handshakes.push(request.headers)
    ws.on('message', (raw) => {
      const frame = JSON.parse(raw.toString('utf8'))
      received.push(frame)
      // 特殊的 message 值 = 「这条故意不回应」，用来证明超时路径。
      if (frame.params?.message === '__no_reply__') return
      // 特殊的 probe 值 = 「这条由测试自己按乱序回应」，用来证明 echo 关联与顺序无关。
      if (frame.params?.probe === '__manual__') return
      ws.send(JSON.stringify(makeResponse({
        status: STATUS.OK, retcode: RETCODE.OK, data: { message_id: 1 }, echo: frame.echo,
      })))
    })
  })

  const client = new OneBotClient({
    url: `ws://127.0.0.1:${hubPort}/onebot/v11/hub`,
    token: TOKEN,
    heartbeatMs: 15_000,
    reconnectInitialMs: 200,
    reconnectMaxMs: 1000,
    callTimeoutMs: 3000,
  })
  const events = []
  client.on('event', (payload) => events.push(payload))
  client.start()

  const connected = await until(() => client.connected, 5000)
  check('B1 客户端连上了假 hub', connected === true, `connected=${client.connected}`)

  // ── B1b 握手带上 Bearer 头 ──
  const header = handshakes[0]?.authorization ?? ''
  check('B1b 握手头带 Authorization: Bearer <token>',
    header === `Bearer ${TOKEN}`, `authorization=${JSON.stringify(header)}`)

  // 连接对端先等一拍，确保 connection 处理器已登记
  await until(() => hub.clients.size > 0, 3000)

  // ── B2 入站事件 → 会话键 ──
  const inbound = {
    time: 1_700_000_001,
    self_id: 10001,
    post_type: 'message',
    message_type: 'private',
    sub_type: 'friend',
    message_id: 555,
    user_id: 10002,
    message: '在吗',
    raw_message: '在吗',
    sender: { user_id: 10002, nickname: '阿米娅' },
  }
  for (const ws of hub.clients) ws.send(JSON.stringify(inbound))
  await until(() => events.length > 0, 3000)
  const parsedConversation = conversationKeyOf(events[0] ?? {})
  check('B2a 入站 message 事件被派发到 event 回调（未被当成响应吞掉）',
    events.length === 1 && events[0]?.post_type === 'message',
    JSON.stringify(events[0] ?? null))
  check('B2b 事件解析出的会话键是 private:<user_id>',
    parsedConversation === 'private:10002', `conversation=${JSON.stringify(parsedConversation)}`)

  const groupInbound = { ...inbound, message_type: 'group', group_id: 10003, message_id: 556 }
  for (const ws of hub.clients) ws.send(JSON.stringify(groupInbound))
  await until(() => events.length > 1, 3000)
  check('B2c 群消息解析出的会话键是 group:<group_id>',
    conversationKeyOf(events[1] ?? {}) === 'group:10003',
    `conversation=${JSON.stringify(conversationKeyOf(events[1] ?? {}))}`)

  // ── B3/B4 出站帧形状与 echo 关联 ──
  received.length = 0
  const first = await client.call('send_private_msg', { user_id: 10002, message: '第一条' })
  const second = await client.call('send_private_msg', { user_id: 10002, message: '第二条' })
  check('B3a 出站帧形状是 {action, params, echo}',
    received.length === 2
    && received[0].action === 'send_private_msg'
    && received[0].params.user_id === 10002
    && typeof received[0].echo === 'string' && received[0].echo !== '',
    JSON.stringify(received[0] ?? null))
  check('B3b 两次调用的 echo 互不相同（唯一性）',
    received[0]?.echo !== received[1]?.echo,
    `${JSON.stringify(received[0]?.echo)} vs ${JSON.stringify(received[1]?.echo)}`)
  check('B4a 按 echo 关联的响应被 resolve 成同一个信封',
    first.echo === received[0].echo && first.status === 'ok' && first.retcode === 0
    && second.echo === received[1].echo,
    `${first.echo} / ${second.echo}`)

  // ── B4b 乱序响应：先回第二条的，再回第一条的 ──
  // 这两条带 `probe: '__manual__'`，假 hub 不会自动应答，由测试自己按逆序回。
  received.length = 0
  const pendingA = client.call('get_status', { probe: '__manual__' })
  const pendingB = client.call('get_status', { probe: '__manual__' })
  const bothArrived = await until(() => received.length >= 2, 3000)
  const [frameA, frameB] = received
  for (const ws of hub.clients) {
    // 逆序回：先 B 后 A
    ws.send(JSON.stringify(makeResponse({
      status: STATUS.OK, retcode: RETCODE.OK, data: { tag: 'B' }, echo: frameB.echo,
    })))
    ws.send(JSON.stringify(makeResponse({
      status: STATUS.OK, retcode: RETCODE.OK, data: { tag: 'A' }, echo: frameA.echo,
    })))
  }
  const [resA, resB] = await Promise.all([pendingA, pendingB])
  check('B4b 响应乱序到达时仍按 echo 正确归位（不依赖顺序）',
    bothArrived === true && resA?.data?.tag === 'A' && resB?.data?.tag === 'B',
    `A=${JSON.stringify(resA?.data)} B=${JSON.stringify(resB?.data)}`)

  // ── B4c 孤儿响应不得被当成事件派发 ──
  const before = events.length
  for (const ws of hub.clients) {
    ws.send(JSON.stringify({ status: 'ok', retcode: 0, data: null, echo: 'never-requested' }))
  }
  await new Promise((resolve) => setTimeout(resolve, 150))
  check('B4c 无法关联的响应帧被丢弃，不会变成假事件',
    events.length === before, `events 从 ${before} 变成 ${events.length}`)

  // ── B4d 对端不回应时按 callTimeoutMs 超时（而不是永久挂住） ──
  let timedOut = false
  try {
    await client.call('send_private_msg', { user_id: 1, message: '__no_reply__' }, { timeoutMs: 400 })
  } catch (error) {
    timedOut = true
  }
  check('B4d 上游不回应时按超时失败（不永久挂住）', timedOut === true)

  // ── B5 断了之后自动重连（MeaPet 重启的模拟） ──
  const beforeReconnect = client.snapshot().opened
  for (const ws of hub.clients) ws.terminate()
  const reconnected = await until(() => client.connected && client.snapshot().opened > beforeReconnect, 6000)
  check('B5 上游断开后自动重连成功（survives MeaPet restart）',
    reconnected === true, `opened=${client.snapshot().opened} connected=${client.connected}`)

  client.stop()
  await until(() => client.connected === false, 3000)
  check('B6 客户端可以干净停止', client.connected === false && client.pendingCount === 0)

  for (const ws of hub.clients) ws.terminate()
  await new Promise((resolve) => hub.close(resolve))
}

// ══════════════════════════════════════════════════════════════════
// C. mode B 的 401 不该被当成「服务没起」（顺手证明 http 服务确实在听）
// ══════════════════════════════════════════════════════════════════

async function partC() {
  console.log('\n── C. 端口与生命周期 ────────────────────────────────────────')
  const server = new OneBotServer({
    host: '127.0.0.1',
    port: 0,
    pathPrefix: '/onebot-relay',
    token: TOKEN,
    onCall: async () => ({ status: STATUS.OK, retcode: RETCODE.OK, data: null }),
  })
  const first = await server.start()
  const second = await server.start()
  check('C1 重复 start() 是幂等的（端口不变）', first.port === second.port, `${first.port} vs ${second.port}`)

  // 显式端口：先绑一个系统分配的端口，记下号，停掉，再用同一个号绑一次。
  // （直接随机挑一个号会被「恰好被占用」变成偶发 FAIL。）
  const probe = new OneBotServer({
    host: '127.0.0.1',
    port: 0,
    pathPrefix: '/onebot-relay',
    token: TOKEN,
    onCall: async () => ({ status: STATUS.OK, retcode: RETCODE.OK, data: null }),
  })
  const probed = await probe.start()
  await probe.stop()
  const fixed = probed.port
  const server2 = new OneBotServer({
    host: '127.0.0.1',
    port: fixed,
    pathPrefix: '/onebot-relay',
    token: TOKEN,
    onCall: async () => ({ status: STATUS.OK, retcode: RETCODE.OK, data: null }),
  })
  try {
    const bound = await server2.start()
    check('C2 显式端口能绑定', bound.port === fixed, `port=${bound.port}（期望 ${fixed}）`)
  } catch (error) {
    check('C2 显式端口能绑定', false, String(error?.message ?? error))
  }
  await server2.stop()
  await server.stop()
  check('C3 stop() 后端口已释放（快照 listening=false）',
    server.snapshot().listening === false && server2.snapshot().listening === false)
}

// ══════════════════════════════════════════════════════════════════

async function main() {
  console.log('dsh-onebot-relay 协议自测（不需要 dsh，也不需要 MeaPet）')
  console.log(`node ${process.version}`)
  try {
    await partA()
  } catch (error) {
    check('A  段落整体执行', false, String(error?.stack ?? error))
  }
  try {
    await partB()
  } catch (error) {
    check('B  段落整体执行', false, String(error?.stack ?? error))
  }
  try {
    await partC()
  } catch (error) {
    check('C  段落整体执行', false, String(error?.stack ?? error))
  }

  const failed = results.filter((item) => !item.ok)
  console.log('\n────────────────────────────────────────────────────────────')
  console.log(`总计 ${results.length} 项：${results.length - failed.length} PASS / ${failed.length} FAIL`)
  if (failed.length > 0) {
    console.log('失败项：')
    for (const item of failed) console.log(`  FAIL  ${item.title}`)
    process.exitCode = 1
  } else {
    console.log('全部通过。')
  }
}

// 让脚本**永远**以明确的退出码结束：一个悬着的句柄不该把 CI 拖到超时。
const watchdog = setTimeout(() => {
  console.log('\nFAIL  自测总时长超过 60s，强制退出（多半有句柄没关）')
  process.exitCode = 1
  process.exit(1)
}, 60_000)
watchdog.unref()

main().catch((error) => {
  console.log(`\nFAIL  自测脚本自身抛错：${String(error?.stack ?? error)}`)
  process.exitCode = 1
}).finally(() => {
  clearTimeout(watchdog)
})
