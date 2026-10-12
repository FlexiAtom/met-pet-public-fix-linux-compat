/**
 * `lib/index.js` 的**端到端编排测试**。
 *
 * ## 为什么这个文件存在
 *
 * `lib/index.js` 需要 `@deepseek-ai/dsh-agent` / `dsh-llm` 才能加载，因此单测一直
 * 「测不动」——只能靠 `node --check` 证明它语法没错。这个文件用
 * `node:module` 的 `registerHooks` 把这两个包**在解析期**指到 `test/stubs/`，
 * 再用一个假 cordis ctx 提供 `agents` / `sessions` / `agentDefaultModel` /
 * `webServer`，于是整条链路可以真跑：
 *
 *   QQ 消息（真 WS，假 MeaPet hub）→ 事件解析 → agents.create/resume
 *   → followup → 宿主事件（session/event、agent/assistant-stream）
 *   → 回复（真的 send_private_msg 帧回到 hub）
 *
 * **它证明什么**：本插件的编排逻辑（会话映射、触发规则、审批、回复切分、
 * 事件过滤）是自洽的，且线上帧确实按契约发出去。
 * **它不证明什么**：真实 dsh 宿主里的服务语义、生命周期与错误形状。
 * 替身是按**已核实的类型定义**写的，不是按运行观察写的——这条差别请记住。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, rmSync } from 'node:fs'
import { registerHooks } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { WebSocketServer } from 'ws'
import { RETCODE, STATUS, makeResponse } from '../lib/contract.js'

const here = dirname(fileURLToPath(import.meta.url))

// ── 把两个 dsh 包在**解析期**指到替身，然后再加载被测模块 ────────────────
const STUBS = new Map([
  ['@deepseek-ai/dsh-agent', new URL('./stubs/dsh-agent.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-llm', new URL('./stubs/dsh-llm.mjs', import.meta.url).href],
])
registerHooks({
  resolve(specifier, context, nextResolve) {
    const stubbed = STUBS.get(specifier)
    if (stubbed !== undefined) return { url: stubbed, shortCircuit: true }
    return nextResolve(specifier, context)
  },
})

const agentStub = await import('./stubs/dsh-agent.mjs')
const plugin = await import('../lib/index.js')
const { Config } = await import('../lib/config.js')

const TOKEN = 'drive-test-token-0123456789abcdef'
const TMP = join(here, '.tmp')
mkdirSync(TMP, { recursive: true })

let counter = 0
function freshStatePath() {
  counter += 1
  return join(TMP, `drive-${process.pid}-${counter}.json`)
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function until(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return true
    await sleep(10)
  }
  return predicate()
}

// ── 真实的假 MeaPet hub ──────────────────────────────────────────────────

async function startHub() {
  const received = []
  const sockets = new Set()
  const hub = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await new Promise((resolve) => hub.once('listening', resolve))
  hub.on('connection', (ws) => {
    sockets.add(ws)
    ws.on('close', () => sockets.delete(ws))
    ws.on('message', (raw) => {
      const frame = JSON.parse(raw.toString('utf8'))
      received.push(frame)
      ws.send(JSON.stringify(makeResponse({
        status: STATUS.OK, retcode: RETCODE.OK, data: { message_id: received.length }, echo: frame.echo,
      })))
    })
  })
  return {
    received,
    port: hub.address().port,
    push: (payload) => { for (const ws of sockets) ws.send(JSON.stringify(payload)) },
    clientCount: () => sockets.size,
    async close() {
      for (const ws of sockets) { try { ws.terminate() } catch { /* 忽略 */ } }
      await new Promise((resolve) => hub.close(resolve))
    },
  }
}

// ── 假 cordis ctx + 假 dsh 服务 ──────────────────────────────────────────

function createLogger(sink) {
  const make = (level) => (line) => { sink.push({ level, line: String(line) }) }
  return { info: make('info'), warn: make('warn'), error: make('error'), debug: make('debug') }
}

function createFakeHost({ logSink }) {
  const listeners = new Map()
  const effects = []
  const routes = []
  const services = new Map()

  const sessions = new Map()
  const agents = new Map()
  const created = []
  const idleResolvers = new Map()

  const agentsService = {
    get: (id) => agents.get(id),
    async create(options) {
      return await build(options, options.sessionId)
    },
    async resume(options) {
      return await build(options, options.resumeSessionId)
    },
  }

  async function build(options, sessionId) {
    // 真实宿主会在 mint 出 agentCtx 之后、公布之前 await setup。
    const agentCtx = { kind: 'agentCtx', sessionId }
    if (typeof options.setup === 'function') await options.setup(agentCtx)
    const session = { id: sessionId, meta: options.meta ?? {} }
    let resolveIdle = null
    const idle = new Promise((resolve) => { resolveIdle = resolve })
    const agent = {
      id: sessionId,
      session,
      ctx: agentCtx,
      options: options.agentOptions,
      followups: [],
      followup(message) { this.followups.push(message) },
      whenIdle: () => idle,
      cancel() { resolutions.push({ sessionId, cancelled: true }) },
    }
    sessions.set(sessionId, session)
    agents.set(sessionId, agent)
    idleResolvers.set(sessionId, resolveIdle)
    created.push({ agent, session, options, agentCtx })
    return {
      agent,
      dispose: async () => { agents.delete(sessionId) },
    }
  }

  const resolutions = []

  const ctx = {
    logger: createLogger(logSink),
    get: (serviceName) => services.get(serviceName),
    inject: (_names, callback) => callback(ctx),
    effect: (fn, effectName) => {
      const dispose = fn()
      effects.push({ name: effectName, dispose })
    },
    on: (event, handler) => {
      if (!listeners.has(event)) listeners.set(event, [])
      listeners.get(event).push(handler)
    },
    // 测试专用
    logSink,
    effects,
    routes,
    created,
    agents,
    sessions,
    resolutions,
    handlers: (event) => listeners.get(event) ?? [],
    emit: (event, ...args) => {
      for (const handler of listeners.get(event) ?? []) handler(...args)
    },
    finishTurn: (sessionId) => { idleResolvers.get(sessionId)?.() },
    disposeAll: () => {
      for (const effect of [...effects].reverse()) {
        try { effect.dispose?.() } catch { /* 忽略 */ }
      }
    },
  }

  services.set('webServer', {
    register: (route) => { routes.push(route); return () => {} },
  })
  services.set('agents', agentsService)
  services.set('sessions', { flush: async () => {} })
  services.set('agentDefaultModel', {
    currentSelection: () => ({ provider: 'stub-provider', model: 'stub-model' }),
  })
  // 会话标题服务：**必须挂上**。缺席时 `applySessionTitle` 会走 service-missing
  // 静默跳过，于是"到底有没有写标题""写得是不是时候"全都没人验。
  // 记录**调用那一刻**的状态，用来钉住时序（attach 之后、followup 之前）。
  const titleRenames = []
  services.set('sessionTitle', {
    rename(session, title) {
      titleRenames.push({
        session,
        title,
        liveAtCall: agents.has(session?.id),
        followupsAtCall: agents.get(session?.id)?.followups.length ?? -1,
      })
      return { title }
    },
  })
  ctx.titleRenames = titleRenames
  // ⚠️ cordis 的服务是**既挂 ctx.get(name)、也挂 ctx.<name>** 的。
  // 只填 Map 会让插件里 `host.agents.create(...)` 这类直接属性访问炸成
  // 「Cannot read properties of undefined」——这正是本文件要照出来的那类错。
  for (const [serviceName, value] of services) ctx[serviceName] = value
  return ctx
}

/** 起一套完整的测试环境：假 hub + 假 ctx + 真 apply。 */
async function boot(t, configPatch = {}) {
  const hub = await startHub()
  const statePath = configPatch.statePath ?? freshStatePath()
  const ctx = createFakeHost({ logSink: [] })
  const config = Config({
    selfId: '10001',
    meapetToken: TOKEN,
    meapetWsUrl: `ws://127.0.0.1:${hub.port}/onebot/v11/hub`,
    cwd: join(TMP, 'workspace'),
    statePath,
    // 测试里的默认值：关掉空闲回收的噪声，保持 final 模式（除非用例覆盖）
    idleTtlMs: 86_400_000,
    ...configPatch,
  })
  agentStub.__resetInstallCalls()

  t.after(async () => {
    ctx.disposeAll()
    // 等一拍照给卸载收尾的异步路径
    await sleep(30)
    await hub.close()
    rmSync(statePath, { force: true })
    rmSync(`${statePath}.tmp-${process.pid}`, { force: true })
  })

  plugin.apply(ctx, config)
  // 等客户端连上假 hub（apply 的 effect 里会 start()）
  await until(() => hub.clientCount() === 1, 5000)
  return { hub, ctx, config, statePath }
}

function privateMessage({ userId = 10002, messageId, text, segments }) {
  return {
    time: Math.floor(Date.now() / 1000),
    self_id: 10001,
    post_type: 'message',
    message_type: 'private',
    sub_type: 'friend',
    message_id: messageId,
    user_id: userId,
    message: segments ?? text,
    raw_message: text ?? '',
    sender: { user_id: userId, nickname: '阿米娅' },
  }
}

function groupMessage({ groupId = 90001, userId = 10002, messageId, segments, text = '' }) {
  return {
    time: Math.floor(Date.now() / 1000),
    self_id: 10001,
    post_type: 'message',
    message_type: 'group',
    sub_type: 'normal',
    message_id: messageId,
    group_id: groupId,
    user_id: userId,
    message: segments ?? text,
    raw_message: text,
    sender: { user_id: userId, nickname: '阿米娅', card: '', role: 'member' },
  }
}

/** 走一轮「助手定稿 + turn 结束」，把权威文本推给插件。 */
function finishAssistantTurn(ctx, sessionId, text, { streamDeltas = [] } = {}) {
  const entry = ctx.created.find((item) => item.session.id === sessionId)
  const agent = entry.agent
  const session = entry.session
  for (const delta of streamDeltas) {
    ctx.emit('agent/assistant-stream', {
      agent,
      frame: { type: 'chunk', attemptId: 'a1', revision: 1, index: 0, time: Date.now(), chunk: { type: 'text-delta', index: 0, text: delta } },
    })
  }
  ctx.emit('session/event', session, {
    type: 'assistant/message',
    data: { turn: 1, step: 1, message: { content: [{ type: 'text', text }] } },
  })
  ctx.emit('session/event', session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
  ctx.finishTurn(sessionId)
}

// ══════════════════════════════════════════════════════════════════════

test('插件契约：name / inject / Config 的形状', () => {
  assert.equal(plugin.name, 'dsh-onebot-relay')
  assert.deepEqual(plugin.inject, ['webServer', 'agents', 'sessions', 'agentDefaultModel'])
  assert.equal(typeof plugin.Config, 'function')
  assert.equal(typeof plugin.apply, 'function')
})

test('私聊消息驱动一轮会话，并把最终文本发回同一个会话', async (t) => {
  const { hub, ctx, statePath } = await boot(t)

  hub.push(privateMessage({ messageId: 1, text: '你好' }))
  assert.equal(await until(() => ctx.created.length === 1), true)

  const { agent, session, options, agentCtx } = ctx.created[0]
  assert.equal(agent.followups.length, 1)
  assert.deepEqual(agent.followups[0].content, [{ type: 'text', text: '你好' }])
  assert.deepEqual(agent.followups[0].source, { kind: 'user' })
  // cwd 落进了 create 的 meta，且模型选择被装过
  assert.equal(options.meta.cwd, join(TMP, 'workspace'))
  assert.equal(options.agentOptions.provider, 'stub-provider')
  assert.equal(agentCtx.modelSelection.model, 'stub-model')
  assert.equal(agentStub.installCalls.length, 1)
  // 会话 id 带 ob- 前缀（在会话列表里认得出来源）
  assert.match(session.id, /^ob-/)

  hub.received.length = 0
  finishAssistantTurn(ctx, session.id, '你好呀，我是喵桥')
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  const reply = hub.received.find((frame) => frame.action === 'send_private_msg')
  assert.equal(reply.params.user_id, 10002)
  assert.equal(reply.params.message, '你好呀，我是喵桥')

  // state.json 落盘：一条映射，指向那个会话 id
  const state = JSON.parse(readFileSync(statePath, 'utf8'))
  assert.deepEqual(Object.keys(state.conversations), ['private:10002'])
  assert.equal(state.conversations['private:10002'].dshSessionId, session.id)
})

test('第二条消息复用同一个会话（agents.get 命中 → 不 resume、不新建）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, text: '第一句' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const sessionId = ctx.created[0].session.id
  finishAssistantTurn(ctx, sessionId, '好')
  assert.equal(await until(() => ctx.created[0].agent.followups.length === 1), true)
  // 让 whenIdle 结算，闸门放开
  await sleep(20)

  hub.push(privateMessage({ messageId: 2, text: '第二句' }))
  assert.equal(await until(() => ctx.created[0].agent.followups.length === 2), true)
  assert.equal(ctx.created.length, 1) // 没有第二次 create
  assert.equal(ctx.created[0].agent.followups[1].content[0].text, '第二句')
})

test('在途期间同一会话的第二条消息被闸门挡住（不会并发跑两轮）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, text: '第一句' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const agent = ctx.created[0].agent
  assert.equal(agent.followups.length, 1)

  // 第一轮尚未结束：既不 emit turn/end，也不让 whenIdle 结算
  hub.received.length = 0
  hub.push(privateMessage({ messageId: 2, text: '第二句' }))
  await sleep(80)

  assert.equal(agent.followups.length, 1, '同一会话在途时不得再起一轮')
  assert.equal(
    hub.received.some((frame) => frame.action === 'send_private_msg'
      && String(frame.params.message).includes('正在处理上一条消息')),
    true,
    '应当明确回一句"正在处理"，而不是静默丢弃',
  )
})

test('闸门由 turn/end 释放：不必等 whenIdle 兜底，下一条立即能投', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, text: '第一句' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const entry = ctx.created[0]

  ctx.emit('session/event', entry.session, {
    type: 'assistant/message',
    data: { turn: 1, step: 1, message: { content: [{ type: 'text', text: '好' }] } },
  })
  ctx.emit('session/event', entry.session, {
    type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } },
  })
  // 刻意**不**调用 ctx.finishTurn（那会结算 whenIdle）：闸门只应由 turn/end 释放
  hub.push(privateMessage({ messageId: 2, text: '紧接着的第二句' }))
  assert.equal(await until(() => entry.agent.followups.length === 2), true)
  assert.equal(entry.agent.followups[1].content[0].text, '紧接着的第二句')
})

test('maxQueuedPerConversation=1：在途期间的第二条被背压丢弃并明确告知', async (t) => {
  const { hub, ctx } = await boot(t, { maxQueuedPerConversation: 1 })
  hub.push(privateMessage({ messageId: 1, text: '第一句' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const agent = ctx.created[0].agent

  hub.received.length = 0
  hub.push(privateMessage({ messageId: 2, text: '第二句' }))
  await sleep(80)

  assert.equal(agent.followups.length, 1, '背压上限到了就不能再投')
  // 注意：日志里写的是"已达上限"，但发给用户的文案是"上一轮还在处理中（已排队 N 条）"
  assert.equal(
    hub.received.some((frame) => frame.action === 'send_private_msg'
      && String(frame.params.message).includes('上一轮还在处理中')
      && String(frame.params.message).includes('已排队 1 条')),
    true,
  )
})

test('会话标题：attach 之后、followup 之前写入，占位符按会话键解析', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, userId: 10002, text: '你好' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  assert.equal(await until(() => ctx.titleRenames.length === 1), true)

  const call = ctx.titleRenames[0]
  assert.equal(call.title, '喵桥 · private/10002')
  assert.equal(call.session.id, ctx.created[0].session.id)
  // 时序：排在 create/resume **之后**（否则 dsh 的 rename 会因为"不 live"直接抛错，
  // 被归类成 rename-failed，症状是"标题永远没写上"）
  assert.equal(call.liveAtCall, true, '写标题时那个会话必须已经 live')
  // 时序：排在 followup **之前**（列表里先认得出来）
  assert.equal(call.followupsAtCall, 0, '写标题必须早于 followup')
})

test('会话标题：群聊渲染成 group/<群号>', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(groupMessage({
    messageId: 1,
    groupId: 90001,
    segments: [{ type: 'at', data: { qq: '10001' } }, { type: 'text', data: { text: ' 在吗' } }],
  }))
  assert.equal(await until(() => ctx.titleRenames.length === 1), true)
  assert.equal(ctx.titleRenames[0].title, '喵桥 · group/90001')
})

test('会话标题：模板可自定义，未知占位符原样保留（看得见比空标题好）', async (t) => {
  const { hub, ctx } = await boot(t, { sessionTitleTemplate: 'QQ {nope} · {conversation}' })
  hub.push(privateMessage({ messageId: 1, userId: 10002, text: '你好' }))
  assert.equal(await until(() => ctx.titleRenames.length === 1), true)
  assert.equal(ctx.titleRenames[0].title, 'QQ {nope} · private:10002')
})

test('群聊：requireAt 时未 @ 机器人直接跳过；@ 了才投递且文本里的 @ 被剥掉', async (t) => {  const { hub, ctx } = await boot(t)

  hub.push(groupMessage({ messageId: 1, text: '大家好' }))
  await sleep(120)
  assert.equal(ctx.created.length, 0, '没 @ 机器人时不该建立任何会话')

  hub.push(groupMessage({
    messageId: 2,
    segments: [
      { type: 'at', data: { qq: '10001' } },
      { type: 'text', data: { text: ' 看下这个' } },
    ],
  }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const followup = ctx.created[0].agent.followups[0]
  assert.deepEqual(followup.content, [{ type: 'text', text: '看下这个' }])

  // 回发目标是群，不是私聊
  hub.received.length = 0
  finishAssistantTurn(ctx, ctx.created[0].session.id, '好的')
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_group_msg')), true)
  const reply = hub.received.find((frame) => frame.action === 'send_group_msg')
  assert.equal(reply.params.group_id, 90001)
})

test('requireAt: false 时群里不 @ 也响应（配置真的生效）', async (t) => {
  const { hub, ctx } = await boot(t, { requireAt: false })
  hub.push(groupMessage({ messageId: 1, text: '大家好' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  assert.equal(ctx.created[0].agent.followups[0].content[0].text, '大家好')
})

test('CQ 码形态的 @ 也能命中触发规则', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(groupMessage({ messageId: 1, text: '[CQ:at,qq=10001] 在吗' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  assert.equal(ctx.created[0].agent.followups[0].content[0].text, '在吗')
})

test('重复的 message_id 被去重（agent 不会被问两遍）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 77, text: '只问一次' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  hub.push(privateMessage({ messageId: 77, text: '只问一次' }))
  await sleep(150)
  assert.equal(ctx.created[0].agent.followups.length, 1)
})

test('纯图片消息：非文本段变成占位符投给模型（与 MeaPet 的占位表对齐）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, segments: [{ type: 'image', data: { file: 'a.jpg' } }] }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  // 「这里原本有一张图」这条语义必须保留：静默变成空提示词会让模型以为用户没说话
  assert.equal(ctx.created[0].agent.followups[0].content[0].text, '[图片]')
})

test('文本真为空的消息不投给 agent（没有占位符可留的情况）', async (t) => {
  const { hub, ctx } = await boot(t)
  // `reply`（引用回复）段在占位表里映射为空串，单独出现时整条消息的文本就是空的
  hub.push(privateMessage({ messageId: 1, segments: [{ type: 'reply', data: { id: '1' } }] }))
  await sleep(150)
  assert.equal(ctx.created.length, 0)
})

test('output: 非消息事件不进任何会话，但 relayInboundToServer 时可转发（默认关）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push({ time: 1, self_id: 10001, post_type: 'meta_event', meta_event_type: 'heartbeat' })
  await sleep(120)
  assert.equal(ctx.created.length, 0)
})

test('审批：approval/request 被接管，聊天里回 /approve <码> 后 resolve 成 allowed-once', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, text: '删掉它' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const agent = ctx.created[0].agent

  const handler = ctx.handlers('approval/request')[0]
  assert.equal(typeof handler, 'function')
  hub.received.length = 0
  const settled = handler(
    {
      agent,
      callId: 'call-1',
      toolName: 'bash',
      reason: '要执行 rm',
      signal: new AbortController().signal,
    },
    () => Promise.resolve('rejected-by-framework'),
  )

  // 提示必须发到同一个会话，且带一次性验证码
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  const prompt = hub.received.find((frame) => frame.action === 'send_private_msg')
  assert.equal(prompt.params.user_id, 10002)
  assert.match(prompt.params.message, /需要审批/)
  assert.match(prompt.params.message, /bash/)
  assert.match(prompt.params.message, /要执行 rm/)
  const code = /\/approve\s+([A-Z0-9]{4})/.exec(prompt.params.message)?.[1]
  assert.equal(typeof code, 'string', `提示里应当有验证码：${prompt.params.message}`)

  // 验证码不对 → 不改决议，并回一句说明
  hub.push(privateMessage({ messageId: 2, text: '/approve ZZZZ' }))
  assert.equal(await until(() => hub.received.some((frame) => String(frame.params?.message ?? '').includes('没有匹配的待审批项'))), true)

  // 正确验证码 → 决议为 allowed-once（不是框架默认值）
  hub.push(privateMessage({ messageId: 3, text: `/approve ${code}` }))
  assert.equal(await settled, 'allowed-once')
  // 审批回执**不该**被当成普通消息投给 agent
  assert.equal(agent.followups.length, 1)
})

test('审批：没人回执时按 approvalTimeoutMs 超时并 fail closed 成 rejected', async (t) => {
  const { hub, ctx } = await boot(t, { approvalTimeoutMs: 1000 })
  hub.push(privateMessage({ messageId: 1, text: '跑一下' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const handler = ctx.handlers('approval/request')[0]
  const settled = handler(
    { agent: ctx.created[0].agent, callId: 'call-2', toolName: 'bash', reason: '', signal: new AbortController().signal },
    () => Promise.resolve('framework'),
  )
  assert.equal(await settled, 'rejected')
})

test('审批：approvalEnabled=false 时不接管，原样交给框架的 next()', async (t) => {
  const { hub, ctx } = await boot(t, { approvalEnabled: false })
  hub.push(privateMessage({ messageId: 1, text: '跑一下' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const handler = ctx.handlers('approval/request')[0]
  const result = await handler(
    { agent: ctx.created[0].agent, callId: 'call-3', toolName: 'bash', signal: new AbortController().signal },
    () => Promise.resolve('framework-default'),
  )
  assert.equal(result, 'framework-default')
})

test('审批：别的会话拿不到也用不了这条验证码（跨会话隔离）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, userId: 10002, text: '删掉它' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const agent = ctx.created[0].agent

  const handler = ctx.handlers('approval/request')[0]
  hub.received.length = 0
  const settled = handler(
    { agent, callId: 'call-iso', toolName: 'bash', reason: '要执行 rm', signal: new AbortController().signal },
    () => Promise.resolve('framework'),
  )
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  const prompt = hub.received.find((frame) => frame.action === 'send_private_msg')
  const code = /\/approve\s+([A-Z0-9]{4})/.exec(prompt.params.message)?.[1]
  assert.equal(typeof code, 'string')

  // 另一个 QQ 用户（= 另一条会话）知道验证码也不行
  hub.received.length = 0
  hub.push(privateMessage({ messageId: 2, userId: 10003, text: `/approve ${code}` }))
  assert.equal(
    await until(() => hub.received.some((frame) => String(frame.params?.message ?? '').includes('没有匹配的待审批项'))),
    true,
  )
  assert.equal(await Promise.race([settled, sleep(40).then(() => 'still-pending')]), 'still-pending',
    '别的会话不该能动这条审批')

  // 控制组：本会话用同一个码仍然能批 —— 证明上面不是"审批坏了"
  hub.push(privateMessage({ messageId: 3, userId: 10002, text: `/approve ${code}` }))
  assert.equal(await settled, 'allowed-once')
})

test('审批：一次性——同一个码用过再回一次只会得到"没有匹配"', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, text: '删掉它' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const handler = ctx.handlers('approval/request')[0]
  hub.received.length = 0
  const settled = handler(
    { agent: ctx.created[0].agent, callId: 'call-replay', toolName: 'bash', signal: new AbortController().signal },
    () => Promise.resolve('framework'),
  )
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  const code = /\/approve\s+([A-Z0-9]{4})/.exec(
    hub.received.find((frame) => frame.action === 'send_private_msg').params.message,
  )?.[1]

  hub.push(privateMessage({ messageId: 2, text: `/approve ${code}` }))
  assert.equal(await settled, 'allowed-once')

  hub.received.length = 0
  hub.push(privateMessage({ messageId: 3, text: `/approve ${code}` }))
  assert.equal(
    await until(() => hub.received.some((frame) => String(frame.params?.message ?? '').includes('没有匹配的待审批项'))),
    true,
    '重放同一个码必须被拒（它已经处理过了）',
  )
})

test('审批：/reject <码> 结算为 rejected（不是 allowed）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, text: '删掉它' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const handler = ctx.handlers('approval/request')[0]
  hub.received.length = 0
  const settled = handler(
    { agent: ctx.created[0].agent, callId: 'call-reject', toolName: 'bash', signal: new AbortController().signal },
    () => Promise.resolve('framework'),
  )
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  const code = /\/reject\s+([A-Z0-9]{4})/.exec(
    hub.received.find((frame) => frame.action === 'send_private_msg').params.message,
  )?.[1]
  assert.equal(typeof code, 'string', '提示里也应当给出 /reject 的用法')

  hub.push(privateMessage({ messageId: 2, text: `/reject ${code}` }))
  assert.equal(await settled, 'rejected')
})

test('审批：超时之后再用那个码回执 → 按拒绝结算并说明已过期', async (t) => {
  const { hub, ctx } = await boot(t, { approvalTimeoutMs: 1000 })
  hub.push(privateMessage({ messageId: 1, text: '删掉它' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const handler = ctx.handlers('approval/request')[0]
  hub.received.length = 0
  const settled = handler(
    { agent: ctx.created[0].agent, callId: 'call-late', toolName: 'bash', signal: new AbortController().signal },
    () => Promise.resolve('framework'),
  )
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  const code = /\/approve\s+([A-Z0-9]{4})/.exec(
    hub.received.find((frame) => frame.action === 'send_private_msg').params.message,
  )?.[1]

  assert.equal(await settled, 'rejected') // 超时先到，fail closed

  // 迟到的回执：表里已经没有这一项了 → 得到"没有匹配"，且不会二次结算
  hub.received.length = 0
  hub.push(privateMessage({ messageId: 2, text: `/approve ${code}` }))
  assert.equal(
    await until(() => hub.received.some((frame) => String(frame.params?.message ?? '').includes('没有匹配的待审批项'))),
    true,
  )
})

test('准入名单：默认空 = 不限制（向后兼容，谁都能驱动）', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, userId: 10003, text: '你好' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  assert.equal(ctx.created[0].agent.followups.length, 1)
})

test('准入名单：allowUsers 之外的人驱动不了 agent（私聊）', async (t) => {
  const { hub, ctx } = await boot(t, { allowUsers: ['10002'] })

  // 不在名单里 → 不建会话、不投递
  hub.push(privateMessage({ messageId: 1, userId: 10003, text: '你好' }))
  await sleep(120)
  assert.equal(ctx.created.length, 0, '名单外的人不该建立会话')
  assert.equal(hub.received.some((frame) => frame.action === 'send_private_msg'), false,
    '也不该回任何消息')

  // 名单内 → 正常
  hub.push(privateMessage({ messageId: 2, userId: 10002, text: '你好' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  assert.equal(ctx.created[0].agent.followups.length, 1)
})

test('准入名单：形式宽容（空格/前缀里的数字照样匹配）', async (t) => {
  const { hub, ctx } = await boot(t, { allowUsers: [' 10002 ', 'qq:10005'] })
  hub.push(privateMessage({ messageId: 1, userId: 10002, text: '你好' }))
  assert.equal(await until(() => ctx.created.length === 1), true)

  hub.push(privateMessage({ messageId: 2, userId: 10005, text: '你好' }))
  assert.equal(await until(() => ctx.created.length === 2), true)
})

test('准入名单：allowGroups 之外群里 @ 也不驱动', async (t) => {
  const { hub, ctx } = await boot(t, { allowGroups: ['90001'] })
  const atSelf = (text) => ([
    { type: 'at', data: { qq: '10001' } },
    { type: 'text', data: { text } },
  ])

  hub.push(groupMessage({ messageId: 1, groupId: 90002, segments: atSelf(' 别的群') }))
  await sleep(120)
  assert.equal(ctx.created.length, 0, '名单外的群不该建立会话')

  hub.push(groupMessage({ messageId: 2, groupId: 90001, segments: atSelf(' 本群') }))
  assert.equal(await until(() => ctx.created.length === 1), true)
})

test('/status 里报告归一化后的名单（诊断"为什么它不理某人"）', async (t) => {
  const { ctx } = await boot(t, { allowUsers: [' 10002 ', 'qq:10005'], allowGroups: ['90001'] })
  const respond = { status: 0, body: null }
  const response = {
    writeHead(code) { respond.status = code },
    end(payload) { respond.body = payload },
  }
  ctx.routes[0].handler(
    { headers: { authorization: `Bearer ${TOKEN}` }, url: '/onebot-relay/status' },
    response,
  )
  const snapshot = JSON.parse(respond.body)
  assert.deepEqual(snapshot.allowUsers, ['10002', '10005'])
  assert.deepEqual(snapshot.allowGroups, ['90001'])
})

test('事件过滤：别的 agent 的事件不会串进来', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.push(privateMessage({ messageId: 1, text: '你好' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const mine = ctx.created[0].agent

  hub.received.length = 0
  // 一个**不属于本插件**的 agent（模拟用户在 Web UI 里的会话）
  const stranger = { id: 'web-ui-session', session: { id: 'web-ui-session', meta: {} } }
  ctx.emit('agent/assistant-stream', {
    agent: stranger,
    frame: { type: 'chunk', attemptId: 'x', revision: 1, index: 0, time: 0, chunk: { type: 'text-delta', index: 0, text: '串台了' } },
  })
  ctx.emit('session/event', stranger.session, {
    type: 'assistant/message',
    data: { turn: 1, message: { content: [{ type: 'text', text: '串台了' }] } },
  })
  ctx.emit('session/event', stranger.session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
  // 自己的 turn 收尾
  finishAssistantTurn(ctx, mine.session.id, '本会话的答复')

  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  const texts = hub.received
    .filter((frame) => frame.action === 'send_private_msg')
    .map((frame) => frame.params.message)
  assert.deepEqual(texts, ['本会话的答复'])
})

test('streamMode=preview：边跑边发增量，结束时只补尾巴（不重复）', async (t) => {
  const { hub, ctx } = await boot(t, {
    streamMode: 'preview',
    previewMinChars: 5,
    previewIntervalMs: 200,
  })
  hub.push(privateMessage({ messageId: 1, text: '写一段' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  const sessionId = ctx.created[0].session.id
  hub.received.length = 0

  finishAssistantTurn(ctx, sessionId, '你好，我是喵桥', { streamDeltas: ['你好，我是'] })
  assert.equal(await until(() => hub.received.filter((frame) => frame.action === 'send_private_msg').length >= 2), true)
  const sent = hub.received
    .filter((frame) => frame.action === 'send_private_msg')
    .map((frame) => frame.params.message)
  // 预览发了公共前缀，尾巴只补余下部分：拼起来正好是最终文本，且没有重复
  assert.equal(sent.join(''), '你好，我是喵桥')
})

test('streamMode=preview：模型重排文本时从头补发（宁可重复，绝不漏）', async (t) => {
  const { hub, ctx } = await boot(t, {
    streamMode: 'preview',
    previewMinChars: 5,
    previewIntervalMs: 200,
  })
  hub.push(privateMessage({ messageId: 1, text: '写一段' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  hub.received.length = 0

  // 预览已经把「你好，我是」发出去了，但权威最终文本完全换了一套（公共前缀 = 0）
  finishAssistantTurn(ctx, ctx.created[0].session.id, '抱歉，我不知道', {
    streamDeltas: ['你好，我是'],
  })
  assert.equal(
    await until(() => hub.received.filter((f) => f.action === 'send_private_msg').length >= 2),
    true,
  )
  await sleep(60)
  const sent = hub.received
    .filter((f) => f.action === 'send_private_msg')
    .map((f) => f.params.message)
  // 这是**有意的不对称**：宁可让用户看到一点重复，也绝不漏字。
  // 谁要是把它"优化"成不重复，这条会红——而那正是会丢文本的方向。
  assert.equal(sent.at(-1), '抱歉，我不知道')
  assert.equal(sent.join('').includes('抱歉，我不知道'), true)
})

test('streamMode=preview：部分重叠时只补重叠之后的部分', async (t) => {
  const { hub, ctx } = await boot(t, {
    streamMode: 'preview',
    previewMinChars: 5,
    previewIntervalMs: 200,
  })
  hub.push(privateMessage({ messageId: 1, text: '写一段' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  hub.received.length = 0

  // 公共前缀是「你好，我是」（码点 5），因此只该补「猫娘」
  finishAssistantTurn(ctx, ctx.created[0].session.id, '你好，我是猫娘', {
    streamDeltas: ['你好，我是喵桥'],
  })
  assert.equal(
    await until(() => hub.received.filter((f) => f.action === 'send_private_msg').length >= 2),
    true,
  )
  await sleep(60)
  const sent = hub.received
    .filter((f) => f.action === 'send_private_msg')
    .map((f) => f.params.message)
  assert.equal(sent.at(-1), '猫娘')
})

test('streamMode=preview：最终文本是已发内容的前缀时不再补发（不刷空消息）', async (t) => {
  const { hub, ctx } = await boot(t, {
    streamMode: 'preview',
    previewMinChars: 5,
    previewIntervalMs: 200,
  })
  hub.push(privateMessage({ messageId: 1, text: '写一段' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  hub.received.length = 0

  finishAssistantTurn(ctx, ctx.created[0].session.id, '你好，我是', {
    streamDeltas: ['你好，我是喵桥'],
  })
  await sleep(150)
  const sent = hub.received
    .filter((f) => f.action === 'send_private_msg')
    .map((f) => f.params.message)
  assert.deepEqual(sent, ['你好，我是喵桥'], '只应有那一条预览，不该再补一条空消息')
})

test('长回复按 replyChunkChars 切段，且每段都单独发一次', async (t) => {
  const { hub, ctx } = await boot(t, { replyChunkChars: 4 })
  hub.push(privateMessage({ messageId: 1, text: '来一段长的' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  hub.received.length = 0
  finishAssistantTurn(ctx, ctx.created[0].session.id, '一二三四。五六七八。九十')
  assert.equal(await until(() => hub.received.filter((frame) => frame.action === 'send_private_msg').length >= 3), true)
  const sent = hub.received
    .filter((frame) => frame.action === 'send_private_msg')
    .map((frame) => frame.params.message)
  assert.equal(sent.join(''), '一二三四。五六七八。九十')
  assert.equal(sent.every((piece) => [...piece].length <= 4), true)
})

test('replyChunking=false 时整段发一次', async (t) => {
  const { hub, ctx } = await boot(t, { replyChunking: false, replyChunkChars: 2 })
  hub.push(privateMessage({ messageId: 1, text: '来一段' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  hub.received.length = 0
  finishAssistantTurn(ctx, ctx.created[0].session.id, '一二三四五六')
  assert.equal(await until(() => hub.received.some((frame) => frame.action === 'send_private_msg')), true)
  await sleep(80)
  const sent = hub.received.filter((frame) => frame.action === 'send_private_msg')
  assert.equal(sent.length, 1)
  assert.equal(sent[0].params.message, '一二三四五六')
})

test('本地指令 /help 与 /status 不投给 agent，且会回一句', async (t) => {
  const { hub, ctx } = await boot(t)
  hub.received.length = 0
  hub.push(privateMessage({ messageId: 1, text: '/help' }))
  assert.equal(await until(() => hub.received.some((frame) => String(frame.params?.message ?? '').includes('可用指令'))), true)
  assert.equal(ctx.created.length, 0, '/help 不该建立会话')

  hub.push(privateMessage({ messageId: 2, text: '/status' }))
  assert.equal(await until(() => hub.received.some((frame) => String(frame.params?.message ?? '').includes('DSH 会话'))), true)
  assert.equal(ctx.created.length, 0)

  // 不认识的小写斜杠命令照常投给 agent
  hub.push(privateMessage({ messageId: 3, text: '/summarize 这段话' }))
  assert.equal(await until(() => ctx.created.length === 1), true)
  assert.equal(ctx.created[0].agent.followups[0].content[0].text, '/summarize 这段话')
})

test('mode B 关闭时 /status 路由仍然注册（host half 的存活证据）', async (t) => {
  const { ctx, config } = await boot(t)
  assert.equal(ctx.routes.length, 1)
  assert.equal(ctx.routes[0].path, '/onebot-relay/status')
  assert.equal(config.serverEnabled, false)

  // 路由本身要过鉴权
  const respond = (status) => ({ status, body: null, headers: null })
  const response = {
    writeHead(code, headers) { respond.status = code; respond.headers = headers },
    end(payload) { respond.body = payload },
  }
  ctx.routes[0].handler({ headers: {}, url: '/onebot-relay/status' }, response)
  assert.equal(respond.status, 401)

  const okResponse = {
    writeHead(code) { respond.status = code },
    end(payload) { respond.body = payload },
  }
  ctx.routes[0].handler(
    { headers: { authorization: `Bearer ${TOKEN}` }, url: '/onebot-relay/status' },
    okResponse,
  )
  assert.equal(respond.status, 200)
  const snapshot = JSON.parse(respond.body)
  assert.equal(snapshot.ok, true)
  assert.equal(snapshot.plugin, 'dsh-onebot-relay')
  assert.equal(snapshot.client.connected, true)
  assert.equal(snapshot.serverEnabled, false)
  assert.equal(snapshot.actions.allowed, 17)
  // 默认允许消费者调动作；这个值必须来自**路由器自己**，而不是另读一次配置
  assert.equal(snapshot.actions.sendAllowed, true)
})

test('serverAllowSend=false 会真的接到动作路由器上（不是只在 schema 里存在）', async (t) => {
  const { ctx } = await boot(t, { serverAllowSend: false })

  const respond = { status: 0, body: null }
  const response = {
    writeHead(code) { respond.status = code },
    end(payload) { respond.body = payload },
  }
  ctx.routes[0].handler(
    { headers: { authorization: `Bearer ${TOKEN}` }, url: '/onebot-relay/status' },
    response,
  )
  assert.equal(respond.status, 200)
  const snapshot = JSON.parse(respond.body)
  // 这就是"接线"的证据：值是从 routeAction.allowSend 读出来的
  assert.equal(snapshot.actions.sendAllowed, false)
})

test('卸载收尾：disposer 会停掉客户端与所有定时器', async (t) => {
  const hub = await startHub()
  const ctx = createFakeHost({ logSink: [] })
  const statePath = freshStatePath()
  const config = Config({
    selfId: '10001',
    meapetToken: TOKEN,
    meapetWsUrl: `ws://127.0.0.1:${hub.port}/onebot/v11/hub`,
    cwd: join(TMP, 'workspace'),
    statePath,
  })
  plugin.apply(ctx, config)
  assert.equal(await until(() => hub.clientCount() === 1), true)
  ctx.disposeAll()
  assert.equal(await until(() => hub.clientCount() === 0), true)
  await hub.close()
  rmSync(statePath, { force: true })
})

test('配置非法时 apply 立刻抛错（加载即失败，不静默降级）', async () => {
  const ctx = createFakeHost({ logSink: [] })
  const bad = Config({
    selfId: '10001',
    meapetToken: 't',
    meapetWsUrl: '',
    cwd: join(TMP, 'workspace'),
    clientEnabled: true,
  })
  assert.throws(() => plugin.apply(ctx, bad), /meapetWsUrl 必填/)
  ctx.disposeAll()
})
