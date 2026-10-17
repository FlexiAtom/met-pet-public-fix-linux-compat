/**
 * `lib/onebot.js` 的 mode A 端到端单测：本插件当 OneBot 客户端，
 * 对面是一个**假的 MeaPet hub**（真起 WS 服务、真握手、真按 echo 回帧）。
 *
 * 这一层钉的是三条最贵的事实：
 *   1. echo 是唯一的关联手段（响应乱序也必须对得上）
 *   2. 孤儿响应**绝不能**被当成事件派发出去（否则凭空造出假事件）
 *   3. 断线要退避重连（MeaPet 重启是常态）
 *
 * ⚠️ 每个用例都走 `withHub` / `t.after` 收尾。**不要**在用例里手写
 * `await fake.close()` 而不放 `finally`：一个失败的断言会让监听中的
 * `WebSocketServer` 把整个测试进程挂住（表现为「测试卡死」而不是「测试失败」，
 * 排查成本高得多）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { WebSocketServer } from 'ws'
import { RETCODE, STATUS, conversationKeyOf, makeResponse } from '../lib/contract.js'
import { OneBotClient, withAccessToken } from '../lib/onebot.js'

const TOKEN = 'client-test-token-0123456789'

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function until(predicate, timeoutMs = 6000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return true
    await sleep(15)
  }
  return predicate()
}

/**
 * 假 MeaPet hub。
 *
 * @param {{ autoReply?: boolean }} options
 *   `autoReply: false` 时不自动回，由测试自己按 `received` 里的 echo 手工回
 *   （用于证明「乱序响应也能归位」）。
 */
async function startFakeHub({ autoReply = true } = {}) {
  const received = []
  const handshakes = []
  const hub = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await new Promise((resolve) => hub.once('listening', resolve))
  hub.on('connection', (ws, request) => {
    handshakes.push(request.headers)
    ws.on('message', (raw) => {
      const frame = JSON.parse(raw.toString('utf8'))
      received.push(frame)
      if (!autoReply) return
      if (frame.params?.message === '__no_reply__') return
      ws.send(JSON.stringify(makeResponse({
        status: STATUS.OK, retcode: RETCODE.OK, data: { message_id: 1 }, echo: frame.echo,
      })))
    })
  })
  return {
    hub, received, handshakes,
    port: hub.address().port,
    clients: () => [...hub.clients],
    async close() {
      for (const ws of hub.clients) { try { ws.terminate() } catch { /* 忽略 */ } }
      await new Promise((resolve) => hub.close(resolve))
    },
  }
}

/** 起一个假 hub、建一个客户端，跑完**无论如何**都收尾。 */
async function withHub(t, options, body) {
  const fake = await startFakeHub(options)
  const clients = []
  t.after(async () => {
    for (const client of clients) client.stop()
    await fake.close()
  })
  const open = (overrides = {}) => {
    const client = new OneBotClient({
      url: `ws://127.0.0.1:${fake.port}/onebot/v11/hub`,
      token: TOKEN,
      heartbeatMs: 15_000,
      reconnectInitialMs: 150,
      reconnectMaxMs: 600,
      callTimeoutMs: 3000,
      ...overrides,
    })
    clients.push(client)
    client.start()
    return client
  }
  return await body({ fake, open })
}

test('连接：握手带 Bearer 头；快照反映连接状态', async (t) => {
  await withHub(t, {}, async ({ fake, open }) => {
    const client = open()
    assert.equal(await until(() => client.connected), true)
    assert.equal(fake.handshakes[0]?.authorization, `Bearer ${TOKEN}`)
    assert.equal(client.snapshot().connected, true)
    assert.equal(client.snapshot().attempts, 0)
  })
})

test('tokenInQuery：把 token 附到 query 上（URL 里已有则不覆盖）', async (t) => {
  assert.equal(withAccessToken('ws://h/p', 'T'), 'ws://h/p?access_token=T')
  assert.equal(withAccessToken('ws://h/p?access_token=KEEP', 'T'), 'ws://h/p?access_token=KEEP')
  assert.equal(withAccessToken('ws://h/p', ''), 'ws://h/p')

  await withHub(t, {}, async ({ fake, open }) => {
    const client = open({ tokenInQuery: true })
    assert.equal(await until(() => client.connected), true)
    // 头仍然发（两种都发对 MeaPet 无害：它先查头、再查 query）
    assert.equal(fake.handshakes[0]?.authorization, `Bearer ${TOKEN}`)
  })
})

test('入站事件：message 事件解析成会话键，非消息事件原样派发', async (t) => {
  await withHub(t, {}, async ({ fake, open }) => {
    const client = open()
    const events = []
    client.on('event', (payload) => events.push(payload))
    assert.equal(await until(() => client.connected), true)
    await until(() => fake.clients().length === 1)
    const ws = fake.clients()[0]

    ws.send(JSON.stringify({
      time: 1, self_id: 10001, post_type: 'message', message_type: 'private',
      sub_type: 'friend', message_id: 1, user_id: 10002, message: '在吗', sender: { nickname: 'x' },
    }))
    ws.send(JSON.stringify({
      time: 2, self_id: 10001, post_type: 'message', message_type: 'group',
      sub_type: 'normal', message_id: 2, group_id: 90001, user_id: 10002,
      message: [{ type: 'at', data: { qq: '10001' } }, { type: 'text', data: { text: ' hi' } }],
      sender: { nickname: 'x' },
    }))
    ws.send(JSON.stringify({ time: 3, self_id: 10001, post_type: 'meta_event', meta_event_type: 'heartbeat' }))

    assert.equal(await until(() => events.length >= 3), true)
    assert.equal(conversationKeyOf(events[0]), 'private:10002')
    assert.equal(conversationKeyOf(events[1]), 'group:90001')
    // 非消息事件也要派发（mode B 的中继要用），但解析不出会话键
    assert.equal(events[2].post_type, 'meta_event')
    assert.equal(conversationKeyOf(events[2]), null)
  })
})

test('出站：帧形状 {action,params,echo}，echo 唯一，响应按 echo 归位', async (t) => {
  await withHub(t, {}, async ({ fake, open }) => {
    const client = open()
    assert.equal(await until(() => client.connected), true)

    const first = await client.call('send_private_msg', { user_id: 10002, message: '第一条' })
    const second = await client.call('send_group_msg', { group_id: 90001, message: '第二条' })
    assert.equal(fake.received.length, 2)
    assert.deepEqual(Object.keys(fake.received[0]).sort(), ['action', 'echo', 'params'])
    assert.equal(fake.received[0].action, 'send_private_msg')
    assert.equal(fake.received[0].params.message, '第一条')
    assert.match(fake.received[0].echo, /^dsh-/)
    assert.notEqual(fake.received[0].echo, fake.received[1].echo)
    assert.equal(first.echo, fake.received[0].echo)
    assert.equal(second.echo, fake.received[1].echo)
    assert.equal(first.status, 'ok')
    assert.equal(client.pendingCount, 0)
  })
})

test('乱序响应仍按 echo 归位（协议不保证响应顺序）', async (t) => {
  await withHub(t, { autoReply: false }, async ({ fake, open }) => {
    const client = open()
    assert.equal(await until(() => client.connected), true)

    const pendingA = client.call('get_status', {})
    const pendingB = client.call('get_status', {})
    assert.equal(await until(() => fake.received.length >= 2), true)
    const [frameA, frameB] = fake.received
    const ws = fake.clients()[0]
    // 先回 B，再回 A
    ws.send(JSON.stringify(makeResponse({ status: STATUS.OK, retcode: 0, data: { tag: 'B' }, echo: frameB.echo })))
    ws.send(JSON.stringify(makeResponse({ status: STATUS.OK, retcode: 0, data: { tag: 'A' }, echo: frameA.echo })))
    const [resA, resB] = await Promise.all([pendingA, pendingB])
    assert.deepEqual(resA.data, { tag: 'A' })
    assert.deepEqual(resB.data, { tag: 'B' })
  })
})

test('孤儿响应被丢弃，不会变成假事件', async (t) => {
  await withHub(t, {}, async ({ fake, open }) => {
    const client = open()
    const events = []
    client.on('event', (payload) => events.push(payload))
    assert.equal(await until(() => client.connected), true)
    await until(() => fake.clients().length === 1)

    fake.clients()[0].send(JSON.stringify({ status: 'ok', retcode: 0, data: null, echo: 'never-asked' }))
    // 同时来一条带 status 的**真事件**：post_type 守卫必须让它落在事件侧
    fake.clients()[0].send(JSON.stringify({
      post_type: 'message', message_type: 'private', user_id: 5, message_id: 9, status: 'weird',
    }))
    assert.equal(await until(() => events.length >= 1), true)
    await sleep(80)
    assert.equal(events.length, 1)
    assert.equal(events[0].post_type, 'message')
  })
})

test('超时：上游不回应时 reject（不永久挂住），并把待办表清干净', async (t) => {
  await withHub(t, {}, async ({ open }) => {
    const client = open()
    assert.equal(await until(() => client.connected), true)
    await assert.rejects(
      () => client.call('send_private_msg', { user_id: 1, message: '__no_reply__' }, { timeoutMs: 250 }),
      /调用超时/,
    )
    assert.equal(client.pendingCount, 0)
  })
})

test('未连接时调用直接 reject（不是静默丢帧）', async () => {
  const client = new OneBotClient({ url: 'ws://127.0.0.1:1/x' })
  await assert.rejects(() => client.call('send_private_msg', {}), /连接未建立/)
  client.stop()
})

test('whenConnected：连上就 resolve；从未 start 时按超时 reject', async (t) => {
  await withHub(t, {}, async ({ open }) => {
    const client = open()
    await assert.doesNotReject(() => client.whenConnected(5000))
  })

  // 从未 start 的客户端：没有任何事件会来，只能走超时分支。
  const idling = new OneBotClient({ url: 'ws://127.0.0.1:9/x' })
  t.after(() => idling.stop())
  await assert.rejects(() => idling.whenConnected(200), /等待 OneBot 连接超时/)
})

test('连不上时 whenConnected 也会立刻失败（不会白等到超时）', async (t) => {
  const refused = new OneBotClient({
    url: 'ws://127.0.0.1:1/x',
    token: 't',
    reconnectInitialMs: 100_000,
    reconnectMaxMs: 100_000,
    callTimeoutMs: 3000,
  })
  t.after(() => refused.stop())
  refused.start()
  await assert.rejects(
    async () => refused.whenConnected(5000),
    (error) => String(error?.message ?? '').length > 0,
  )
})

test('断线后按退避自动重连（MeaPet 重启是常态）', async (t) => {
  await withHub(t, {}, async ({ fake, open }) => {
    const client = open()
    const closes = []
    client.on('close', (info) => closes.push(info))
    assert.equal(await until(() => client.connected), true)
    const opened = client.snapshot().opened

    for (const ws of fake.clients()) ws.terminate()
    assert.equal(await until(() => closes.length >= 1), true)
    assert.equal(await until(() => client.connected && client.snapshot().opened > opened), true)
  })
})

test('stop()：清定时器、清待办、幂等、不再重连', async (t) => {
  await withHub(t, {}, async ({ open }) => {
    const client = open()
    assert.equal(await until(() => client.connected), true)
    client.stop()
    assert.equal(client.pendingCount, 0)
    assert.equal(client.connected, false)
    client.stop()
    await sleep(400)
    assert.equal(client.connected, false)
  })
})

test('pendingCount 在途可见（诊断快照要能看出卡在哪）', async (t) => {
  await withHub(t, {}, async ({ open }) => {
    const client = open()
    assert.equal(await until(() => client.connected), true)
    const promise = client.call('send_private_msg', { user_id: 1, message: '__no_reply__' }, { timeoutMs: 300 })
    assert.equal(await until(() => client.pendingCount === 1), true)
    assert.equal(client.snapshot().pending, 1)
    await assert.rejects(() => promise)
    assert.equal(client.pendingCount, 0)
  })
})
