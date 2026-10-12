/**
 * dsh-onebot-relay — OneBot v11 ↔ DSH 网桥（DSH 侧 / host half）
 *
 * 把「QQ 里的一条消息」翻译成一次 DSH agent 会话执行，并把流式输出、最终答复与
 * **审批请求**推回聊天。
 *
 * 真相来源（按权威顺序）：
 *   1. 仓库根的 `onebotv11.txt`（OneBot v11 端口标准）——线上形状
 *   2. MeaPet 侧源码 `meapet/onebot/**`——对端实际实现的行为
 *   3. `astrdsh/docs/BRIDGE-CONTRACT.md`——**驱动 DSH 会话的方式**（与 IM 无关的那半）
 *   4. `dsh-plugin-dev` skill 的 `references/`——插件声明、patch、装配约定
 *
 * ## 动笔前必读的四条已核实事实（写错就废）
 *
 * 1. **`ctx.webServer.register` 注册的路由没有任何框架级鉴权**，且 handler 签名是
 *    `(request, response)`——**没有 WebSocket upgrade 的位置**。因此：
 *    - 注册在 webServer 上的每个路由（包括 `/status`）都必须自己过 `authorizeToken`；
 *    - OneBot 的正向 WS 只能由本插件自己持有 `http.Server`（`serverPort` 的含义）。
 * 2. **`text/delta` 是瞬时事件**（`session/event` 的 `assistant/chunk` /
 *    `agent/assistant-stream` 的 `frame.chunk`）：没有订阅者时永久丢失。
 *    所以「先挂监听 → 再 followup」，且 `bridge.agent` 必须在 `followup` **之前**
 *    赋值，否则开头的 token 会落进一个查不到归属的桥。
 * 3. **根 ctx 上未打 tag 的监听者会收到所有 agent 的事件**
 *    （`dsh-scope` 的 `if (tag === void 0) return true`）。必须自己按
 *    `agent.id` / `agent.session` 过滤，否则会串台到用户在 Web UI 里的会话。
 * 4. **`agent/assistant-stream` 的 `start` 帧才带 `{turn, step}`**，`chunk` 帧不带。
 *    必须在 `start` 时按 `attemptId` 记下来，否则新宿主下 `step` 恒为 undefined。
 *
 * ## 与 astrbot-relay 的关系
 *
 * 这个插件的骨架（会话映射、串行化、在途单、空闲回收、审批 waterfall、卸载收尾）
 * 是照着 `dsh-astrbot-relay/lib/index.js` 写的。**改这些段落之前请先读那边的注释**
 * ——每一条怪异的写法背后都有一条实测出来的故障。
 * 有意不一致的地方都就地标了「⚠️ 与 astrbot-relay 不同」。
 */
import { randomInt } from 'node:crypto'
import { installModelSelection } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import {
  APPROVAL_CODE_ALPHABET, APPROVAL_CODE_LENGTH, APPROVAL_OUTCOME,
  BRIDGE_VERSION, PLUGIN_NAME, ROUTES, RETCODE, STATUS,
  chunkText, commonPrefixLength, conversationKeyOf, digitsOf, hasAtSelf, isOkResponse, joinPath,
  newSessionId, normalizeMessage, parseConversation,
  parseLocalCommand, segmentsToText, selfIdOf, targetOf,
} from './contract.js'
import {
  STREAM_MODE, Config, assertConfigIsUsable, configWarnings, normalizeIdList,
} from './config.js'
import { OneBotClient, OneBotServer, authorizeToken } from './onebot.js'
import { actionSurface, createActionRouter, isAllowedAction } from './rpc/actions.js'
import { TITLE_MAX_BYTES, TITLE_RESULT, applySessionTitle, titleByteLength } from './session-title.js'
import { loadState, newRecord, resolveStatePath, saveState } from './state.js'

export const name = PLUGIN_NAME

/**
 * 依赖服务。框架会等它们就绪后再跑 apply。
 *
 * ⚠️ 只列**必需**的四个。`agentPresets` / `sessionQuery` / `workspaceRegistry` /
 * `loader` / `sessionTitle` 一律走 `host.get(...)`：它们缺席时插件仍应能对话
 * （只是少工具、少标题），写进 `inject` 会让「被裁过的装配」直接加载不了。
 * 这与 astrbot-relay 把七个都列进 inject 的取舍不同——那边 web profile 必然齐备，
 * 这边要兼容只挂了 core 的 profile。
 */
export const inject = ['webServer', 'agents', 'sessions', 'agentDefaultModel']

export { Config }

/**
 * `@deepseek-ai/dsh-brand` 的 `brandString` 在运行时是**恒等函数**
 * （已核实：`dsh-brand/lib/index.js:20-22` 的 `function brandString(value) { return value }`，
 * 且该包自述「keeps no runtime identity」）。brand 只存在于类型层，
 * 没有任何运行时校验能察觉我们传了裸字符串。
 *
 * 因此这里用本地恒等函数，**不**为一次空操作多引一个运行时依赖。
 */
function brand(value) {
  return value
}

/** 取一个可选服务，缺席返回 undefined（绝不抛）。 */
function optionalService(scope, ctx, serviceName) {
  try {
    const from = typeof scope?.get === 'function' ? scope : ctx
    return from.get(serviceName)
  } catch {
    return undefined
  }
}

/** Windows 与 POSIX 通用的绝对路径判定（与 `lib/config.js` 同口径）。 */
function isAbsolutePath(value) {
  const text = String(value ?? '')
  return /^[a-zA-Z]:[\\/]/.test(text) || text.startsWith('/') || text.startsWith('\\\\')
}

/** 审批验证码：4 位、去掉易混字符。它只是「人确认」的凭据，不参与安全判定。 */
function randomApprovalCode() {
  let code = ''
  for (let i = 0; i < APPROVAL_CODE_LENGTH; i += 1) {
    code += APPROVAL_CODE_ALPHABET[randomInt(APPROVAL_CODE_ALPHABET.length)]
  }
  return code
}

/**
 * 插件入口。
 *
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {object} config 已由 Config schema 校验并填好默认值
 */
export function apply(ctx, config) {
  const log = ctx.logger
  const tag = `[${name}]`

  // ① 配置要响亮：宁可加载失败，也不要运行期静默降级。
  assertConfigIsUsable(config)
  for (const warning of configWarnings(config)) log?.warn?.(`${tag} 配置警告：${warning}`)

  // ② state 在 apply 阶段就要定妥：路径非法或文件损坏都必须让**插件加载失败**，
  //    而不是等到第一条消息进来才炸（那会把「配置写错」伪装成「QQ 没消息」）。
  const statePath = resolveStatePath(config.statePath)
  const { records, existed: stateExisted } = loadState(statePath)

  const startedAt = Date.now()
  const selfId = String(config.selfId).trim()
  // 准入名单：空集合 = 不限制。MeaPet 的 allow.* 在 reply_mode=hub 时一次都不会被调用，
  // 所以"谁能驱动 agent"这件事只能由本端把关（见 config.js 的 allowUsers 注释）。
  const allowUsers = normalizeIdList(config.allowUsers)
  const allowGroups = normalizeIdList(config.allowGroups)
  // mode B 的共享密钥：显式配了 serverToken 就用它，否则与 mode A 共用同一个。
  const serverToken = String(config.serverToken ?? '').trim() || String(config.meapetToken)

  ctx.inject(['webServer', 'agents', 'sessions', 'agentDefaultModel'], (host) => {
    const scopedLog = host.logger ?? log
    const say = (level, message) => scopedLog?.[level]?.(`${tag} ${message}`)

    // ────────────────────────────────────────────────────────────────
    // 运行时状态（纯内存）
    // ────────────────────────────────────────────────────────────────

    /**
     * conversation → 桥实例。
     *
     * `queue` 只统计**在途**投递（已接受、尚未结算），用于背压。
     * 字段命名与 astrbot-relay 保持一致，便于两边对照排查。
     */
    const bridges = new Map()

    /**
     * 消息去重表：`message_id` → 记录时刻。
     *
     * 有界 LRU + TTL。**为什么必须有**：OneBot 侧在断线重连、上游重放、
     * 或 MeaPet 的 reverse_ws 与 hub 同时在线时，同一条消息事件可能被投递两次。
     * 没有这张表，用户会看到 agent 把同一个问题答两遍（而两次都真花了钱）。
     * 不持久化：进程重启即作废，这是可接受的（重启本身不重放历史）。
     */
    const seenMessages = new Map()
    const SEEN_LIMIT = 512
    const SEEN_TTL_MS = 10 * 60 * 1000

    function seenRecently(messageId) {
      const key = String(messageId ?? '')
      if (key === '' || key === '0') return false
      const at = seenMessages.get(key)
      const now = Date.now()
      if (at !== undefined && now - at <= SEEN_TTL_MS) return true
      seenMessages.delete(key)
      seenMessages.set(key, now)
      while (seenMessages.size > SEEN_LIMIT) {
        seenMessages.delete(seenMessages.keys().next().value)
      }
      return false
    }

    /** 落盘映射。失败只 warn：内存里的映射仍然是对的，不该因为一次磁盘错误丢掉本轮对话。 */
    function persistRecords() {
      try {
        saveState(statePath, records)
      } catch (error) {
        say('warn', `state 写入失败（已忽略，内存映射仍有效）：${String(error?.message ?? error)}`)
      }
    }

    /** 取（必要时新建）某会话的桥实例。 */
    function bridgeOf(conversation) {
      let bridge = bridges.get(conversation)
      if (bridge === undefined) {
        const { messageType, sessionId } = parseConversation(conversation)
        bridge = {
          conversation,
          messageType,
          sessionId,
          // 内存态直接用 state 的字段名（dshSessionId），避免「同一件事两个叫法」。
          dshSessionId: records.get(conversation)?.dshSessionId ?? '',
          agent: null,
          // AgentHandle 是**能力对象**（{agent, dispose}）：只有持有者能拆，
          // 所以 dispose 必须留着，卸载收尾时才拆得掉。
          dispose: null,
          // 「正在附着」标志：同一 DSH 会话不允许并发 resume，本端自己判定。
          attaching: false,
          queue: 0,
          inflight: null,
          // 审批：callId → { code, expiresAt, settle }
          approvals: new Map(),
          // 本轮回复的收集器（见 beginReply / deliverReply）。
          reply: null,
          turn: 0,
          // 新版助手流的 start 帧带 {attemptId, turn, step}，chunk 帧不带，
          // 所以必须在 start 时按 attemptId 记下来。
          attempt: null,
          lastError: '',
        }
        bridges.set(conversation, bridge)
      }
      return bridge
    }

    // ────────────────────────────────────────────────────────────────
    // 传输层：mode A 客户端 + mode B 服务端
    // ────────────────────────────────────────────────────────────────

    /** mode A 的客户端。`clientEnabled: false` 时为 null（此时只能靠 mode B 转发）。 */
    const client = config.clientEnabled
      ? new OneBotClient({
        url: String(config.meapetWsUrl).trim(),
        token: String(config.meapetToken),
        tokenInQuery: config.tokenInQuery === true,
        heartbeatMs: config.heartbeatMs,
        reconnectInitialMs: config.reconnectInitialMs,
        reconnectMaxMs: config.reconnectMaxMs,
        callTimeoutMs: config.callTimeoutMs,
        logger: scopedLog,
        tag,
      })
      : null

    /**
     * 出站能力的统一入口。
     *
     * 只有一条真实路径：mode A 连上 MeaPet，由它去执行动作。
     * 本插件**不是 QQ 实现端**，绝不在本地伪造 `send_*` 的成功。
     */
    const outbound = client ?? {
      connected: false,
      call: () => Promise.reject(new Error('clientEnabled=false：本端没有出站通道')),
    }

    // ⚠️ 这里对 `server` 是**前向引用**（它在下面才声明），这是有意的：
    //     server 的 onCall 需要 routeAction，routeAction 的 statusData 想报
    //     mode B 的连接数，两者互为依赖。statusData 是**惰性**回调（只在真的
    //     收到 get_status 时求值），所以 const 的 TDZ 不会命中。
    const routeAction = createActionRouter({
      outbound,
      statusData: () => ({
        app_name: name,
        bridge_version: BRIDGE_VERSION,
        protocol_version: 'v11',
        online: true,
        good: true,
        self_id: selfId,
        uptime_ms: Date.now() - startedAt,
        conversations: records.size,
        clients: server?.clientCount ?? 0,
      }),
      versionData: () => ({ app_name: name, app_version: BRIDGE_VERSION, protocol_version: 'v11' }),
      allowSend: config.serverAllowSend,
      logger: scopedLog,
      tag,
    })

    const server = config.serverEnabled
      ? new OneBotServer({
        host: String(config.serverHost).trim(),
        port: Number(config.serverPort),
        pathPrefix: config.pathPrefix,
        token: serverToken,
        heartbeatMs: config.heartbeatMs,
        onCall: (action, params) => routeAction(action, params),
        logger: scopedLog,
        tag,
      })
      : null

    /**
     * 把文本发回某个 QQ 会话。
     *
     * 长文本按码点切分（`replyChunking`）。切分**在码点级**做，
     * 不用 `String.slice`——后者会把 emoji 的代理对劈成两个孤立半边，
     * 在对端渲染成一个替换字符（`lib/contract.js` 的 `chunkText`）。
     *
     * @returns {Promise<{ok: boolean, sent: number, errors: string[]}>}
     *   绝不抛错：发送失败的上层是「一条消息没发出去」，不是「插件炸了」。
     */
    async function sendText(conversation, text) {
      const body = String(text ?? '')
      if (body.trim() === '') return { ok: true, sent: 0, errors: [] }
      const pieces = config.replyChunking
        ? chunkText(body, config.replyChunkChars)
        : [body]
      const errors = []
      let sent = 0
      for (const piece of pieces) {
        const target = targetOf(conversation, piece)
        try {
          const response = await outbound.call(target.action, target.params)
          if (isOkResponse(response)) {
            sent += 1
          } else {
            const reason = `${response?.status ?? 'failed'}/${response?.retcode ?? '?'}`
              + `${response?.error === undefined ? '' : ` ${response.error}`}`
            errors.push(`${target.action} 未成功：${reason}`)
          }
        } catch (error) {
          errors.push(`${target.action} 调用失败：${String(error?.message ?? error)}`)
        }
      }
      if (errors.length > 0) {
        say('warn', `回复 ${conversation} 有 ${errors.length}/${pieces.length} 段未发出：${errors.join(' | ')}`)
      }
      return { ok: errors.length === 0, sent, errors }
    }

    // ────────────────────────────────────────────────────────────────
    // 会话标题（反向定位）
    // ────────────────────────────────────────────────────────────────

    function titlesOf() {
      return optionalService(host, ctx, 'sessionTitle')
    }

    function writeSessionTitle(bridge) {
      const result = applySessionTitle({
        titles: titlesOf(),
        session: bridge.agent?.session,
        template: config.sessionTitleTemplate,
        conversation: bridge.conversation,
      })
      if (result.ok) {
        const bytes = titleByteLength(result.rendered)
        if (bytes > TITLE_MAX_BYTES) {
          say('warn', `会话标题 ${bytes} 字节超过上限 ${TITLE_MAX_BYTES}，已被 dsh 侧按字节截断：${result.title}`)
        }
        return result
      }
      if (result.reason === TITLE_RESULT.SERVICE_MISSING) return result
      say('warn', `写会话标题失败（${result.reason}，已忽略）：`
        + `${result.error === undefined ? '标题为空' : String(result.error)}`
        + ` / conversation=${bridge.conversation}`)
      return result
    }

    // ────────────────────────────────────────────────────────────────
    // 在途投递单（背压闸门的唯一释放点）
    // ────────────────────────────────────────────────────────────────
    //
    // 病根（astrbot-relay 实测过，这里照搬其修法）：`queue` / `attaching` 若只由
    // `whenIdle().finally()` 释放，而 `whenIdle` 的前提是 turn 闭合——turn 若永不闭合
    // （等待中的审批没人回执、模型卡住），闸门就永久锁死：进程活着，对话彻底哑掉。
    //
    // 修法：把「一次已接受的投递」实体化成**一张在途单**，结算权归 settleInflight 独占，
    // 由三条路触发——`turn/end`（正常）、`whenIdle`（兜底）、等待态超时（fail-closed）。
    // 超时**只为解闸门**，绝不替宿主撤销 turn（那会把「对话卡住」升级成
    // 「进程里堆着永不释放的 agent」）。

    function openInflight(bridge) {
      // 等待上限：审批超时 + 会话收尾余量。`approvalTimeoutMs` 是宿主侧唯一
      // 已知的「合法长等待」，比它短会把正在等人回执的 turn 误判成卡死。
      const timeoutMs = Math.max(30_000, Math.trunc(Number(config.approvalTimeoutMs) || 120_000) + 60_000)
      const ticket = { at: Date.now(), timer: null }
      ticket.timer = setTimeout(() => settleInflight(bridge, 'timeout'), timeoutMs)
      ticket.timer.unref?.()
      bridge.inflight = ticket
      return ticket
    }

    /** 结算当前在途单：幂等，且是 `queue` / `attaching` 的**唯一**释放点。 */
    function settleInflight(bridge, reason) {
      const ticket = bridge.inflight
      if (ticket === null || ticket === undefined) return false
      bridge.inflight = null
      if (ticket.timer !== null) clearTimeout(ticket.timer)
      bridge.queue = Math.max(0, bridge.queue - 1)
      bridge.attaching = false
      if (reason === 'timeout') {
        say('warn', `在途投递超过等待上限仍未出现 turn/end（${bridge.conversation}）：`
          + '按 fail-closed 释放附着闸门（不撤销宿主的 turn）')
      }
      return true
    }

    // ────────────────────────────────────────────────────────────────
    // 助手输出：流式预览 + 最终文本
    // ────────────────────────────────────────────────────────────────

    /** 开一轮回复收集器。必须在 `followup` **之前**调用（瞬时事件规则）。 */
    function beginReply(bridge, { userInitiated }) {
      bridge.reply = {
        userInitiated: userInitiated === true,
        at: Date.now(),
        // 流式累计（用于边跑边显示与尾巴计算）
        streamed: '',
        // 已经真的发到聊天里的文本（preview 模式用它算尾巴，防重复）
        sentText: '',
        lastPreviewAt: 0,
        // 权威最终文本：来自 `assistant/message`，**不是** deltas 拼出来的
        // （多 step 任务会把 deltas 叠加重复，astrbot-relay 契约 §4.1 第 3 条）。
        final: '',
        interrupted: false,
      }
    }

    /**
     * 处理一段文本增量。
     *
     * `final: 'final'` 模式下一次都不发——只在 turn/end 发权威最终文本。
     * `preview` 模式按「攒够 previewMinChars + 距上次够 previewIntervalMs」发增量。
     */
    function onTextDelta(bridge, text) {
      const reply = bridge.reply
      if (reply === null || reply === undefined) return
      const delta = String(text ?? '')
      if (delta === '') return
      reply.streamed += delta
      if (config.streamMode !== STREAM_MODE.PREVIEW) return
      const pending = Array.from(reply.streamed).length - Array.from(reply.sentText).length
      if (pending < Math.trunc(config.previewMinChars)) return
      const now = Date.now()
      if (now - reply.lastPreviewAt < Math.trunc(config.previewIntervalMs)) return
      reply.lastPreviewAt = now
      const unsent = Array.from(reply.streamed).slice(Array.from(reply.sentText).length).join('')
      if (unsent.trim() === '') return
      reply.sentText = reply.streamed
      // 预览**不 await**：await 会把事件回调挂住，后续 chunk 就进不来了。
      sendText(bridge.conversation, unsent).catch(() => { /* sendText 自己已经记了日志 */ })
    }

    /**
     * 一轮结束时把答复发出去。
     *
     * 权威文本一律取 `assistant/message` 的 `final`。preview 模式下已经发过前缀，
     * 这里只补**尾巴**：以「已发文本 vs 最终文本的最长公共前缀」为切点。
     * 模型重排文本时公共前缀会变短，于是会补发从切点起的全部内容——
     * 用户可能看到一点重复，但**绝不会漏**，这个方向的不对称是有意选的。
     */
    async function deliverReply(bridge, reply) {
      if (reply === null || reply === undefined) return
      const final = String(reply.final ?? '').trim()
      if (final === '') {
        if (reply.userInitiated) {
          say('warn', `本轮没有产出最终文本（${bridge.conversation}`
            + `${reply.interrupted ? '，turn 被中断' : ''}），不发回复`)
        }
        return
      }
      let outgoing = final
      if (config.streamMode === STREAM_MODE.PREVIEW && reply.sentText !== '') {
        const cut = commonPrefixLength(reply.sentText, final)
        outgoing = Array.from(final).slice(cut).join('')
      }
      if (outgoing.trim() === '') return
      const result = await sendText(bridge.conversation, outgoing)
      if (result.ok) {
        say('info', `已回复 ${bridge.conversation}（${Array.from(outgoing).length} 字，${result.sent} 段）`)
      }
    }

    /**
     * 助手流 chunk 的统一出口：旧宿主走 `session/event` 的 `assistant/chunk`，
     * 新宿主走 `agent/assistant-stream` 的 `frame.chunk`（二者同形）。
     * 收敛到一个出口，避免两条链路各写一份 switch（契约 §4.2）。
     *
     * ⚠️ **与 astrbot-relay 有意不同的一点**：那边对未知 chunk 变体直接抛错
     * （`assertNever`）。这里**只记一条警告**。理由：本函数跑在宿主的
     * `agent/assistant-stream` 派发里，往上抛等于让一个未知的流式分片打断
     * agent 的循环——「新宿主加了一种 chunk」是版本演进的常态，不该变成
     * 「升级 dsh 之后对话随机崩」。
     */
    function forwardAssistantChunk(bridge, chunk) {
      switch (chunk?.type) {
        case 'text-delta':
          onTextDelta(bridge, chunk.text)
          return
        case 'reasoning-delta':
          // 思考过程不进聊天：它既不是用户要的答复，也会暴露内部推理。
          return
        case 'tool-call-delta':
        case 'block-start':
        case 'block-end':
        case 'usage':
        case 'finish':
          return
        default:
          say('warn', `出现未覆盖的 assistant chunk 变体，已忽略：${JSON.stringify(chunk?.type)}`)
      }
    }

    /** 按 `agent.id` 反查桥（审批 waterfall 只拿得到 Agent，不给 session）。 */
    function bridgeByAgent(agentId) {
      if (!agentId) return null
      for (const bridge of bridges.values()) {
        if (bridge.agent?.id === agentId) return bridge
      }
      return null
    }

    /** 按 `agent.session` 的**对象身份**反查桥（不比 id，避免同名会话误伤）。 */
    function bridgeBySession(session) {
      if (!session) return null
      for (const bridge of bridges.values()) {
        if (bridge.agent?.session === session) return bridge
      }
      return null
    }

    // ── 旧协议入口（宿主 <= 0.1.2-rc.1）───────────────────────────────
    // session/event 是 append-only 火管：user/message、tool/result 等都会到这里。
    // 单监听器 + 内部 switch：多注册几个监听器只会让「谁先谁后」变成隐式契约。
    host.on('session/event', (session, event) => {
      const bridge = bridgeBySession(session)
      if (bridge === null) return
      const data = event?.data ?? {}
      switch (event?.type) {
        case 'turn/start': {
          bridge.turn = data.turn
          return
        }
        case 'assistant/chunk': {
          // 新宿主不再发这个事件，但分支保留即构成双协议兼容。
          forwardAssistantChunk(bridge, data.chunk)
          return
        }
        case 'assistant/message': {
          const content = Array.isArray(data.message?.content) ? data.message.content : []
          const text = content
            .filter((block) => block?.type === 'text')
            .map((block) => String(block.text ?? ''))
            .join('')
          if (bridge.reply !== null && bridge.reply !== undefined) {
            // 多 step 取**最后一条**已定稿的 assistant/message（与 astrbot-relay 同口径）。
            bridge.reply.final = text
            bridge.reply.interrupted = data.message?.interrupted === true
          }
          return
        }
        case 'turn/end': {
          const reply = bridge.reply
          bridge.reply = null
          bridge.turn = 0
          // 先结算闸门再发回复：反序会让下一封消息在本轮回复落地前抢进来。
          settleInflight(bridge, 'turn-end')
          deliverReply(bridge, reply).catch((error) => {
            say('warn', `回复发送异常（${bridge.conversation}）：${String(error?.message ?? error)}`)
          })
          return
        }
        default:
          // 不属于本插件关心的事件一律忽略——**不要**对未知 `type` 抛错，
          // 那会把正常事件（user/message、tool/result…）当 bug 抛。
          return
      }
    })

    // ── 新协议入口（宿主 >= 0.1.5-rc.2）───────────────────────────────
    host.on('agent/assistant-stream', (payload) => {
      // ⚠️ 硬事实第 3 条：必须按 agent.id 过滤，否则串台到 Web UI 的会话。
      const bridge = bridgeByAgent(payload?.agent?.id)
      if (bridge === null) return
      const frame = payload?.frame
      if (!frame) return
      if (frame.type === 'start') {
        bridge.attempt = { id: frame.attemptId, turn: frame.turn, step: frame.step }
        return
      }
      if (frame.type === 'end') {
        if (bridge.attempt?.id === frame.attemptId) bridge.attempt = null
        return
      }
      if (frame.type !== 'chunk') return
      // chunk 帧不带 turn/step，只有 start 帧带（已核实的类型定义）。
      if (bridge.attempt?.id !== frame.attemptId) {
        say('debug', `assistant 流 chunk 的 attemptId 对不上 start 帧（${bridge.conversation}），已忽略归属`)
      }
      forwardAssistantChunk(bridge, frame.chunk)
    })

    // ────────────────────────────────────────────────────────────────
    // 审批转发
    // ────────────────────────────────────────────────────────────────

    /**
     * 向聊天要一次审批，resolve 出 `ApprovalOutcome`。
     *
     * ⚠️ 审批服务自身**零超时**：不自己加超时就会永久堵死该 turn，
     *    所以必须 `setTimeout` + `unref`，且超时 resolve `rejected`（fail closed，
     *    **不是** `cancelled`——后者是「用户/上游取消」的语义，会被误当成正常中止）。
     * ⚠️ `finish` 只能生效一次：超时、signal abort、聊天回执三条路可能同时到达。
     */
    function askApproval(bridge, request) {
      const callId = request.callId ?? `ob-call-${Date.now()}-${randomInt(1_000_000)}`
      const timeoutMs = Math.max(1000, Math.trunc(Number(config.approvalTimeoutMs)))
      const expiresAt = Date.now() + timeoutMs
      const code = randomApprovalCode()
      return new Promise((resolve) => {
        let timer = null
        const finish = (outcome, via) => {
          // 幂等判据就是「表里还有没有这个 callId」：`Map.has` 是唯一入口，
          // 所以任何结算路径都**不能**先把表项删掉再调 finish（那会让 finish 变空操作，
          // promise 再也没人 resolve，agent 永久悬挂）。
          if (!bridge.approvals.has(callId)) return
          bridge.approvals.delete(callId)
          if (timer !== null) clearTimeout(timer)
          say('info', `审批 ${callId} 已结算：${outcome}（via=${via}，${bridge.conversation}）`)
          resolve(outcome)
        }
        timer = setTimeout(() => finish(APPROVAL_OUTCOME.REJECTED, 'timeout'), timeoutMs)
        timer.unref?.()
        bridge.approvals.set(callId, { code, expiresAt, settle: finish })

        const toolName = String(request.toolName ?? '(未知工具)')
        const reason = String(request.reason ?? '').trim()
        const prompt = [
          '⚠️ 需要审批',
          `工具：${toolName}`,
          reason === '' ? '' : `原因：${reason}`,
          `验证码：${code}（${Math.round(timeoutMs / 1000)} 秒内有效）`,
          '批准请回复：/approve ' + code,
          '拒绝请回复：/reject ' + code,
        ].filter((line) => line !== '').join('\n')
        sendText(bridge.conversation, prompt).catch(() => { /* sendText 自己记日志 */ })

        // turn 被取消 → 必须把它结算掉，否则这个审批会挂到进程结束。
        request.signal?.addEventListener?.('abort', () => finish('cancelled', 'abort'), { once: true })
      })
    }

    host.on('approval/request', (request, next) => {
      const bridge = bridgeByAgent(request?.agent?.id)
      // 不接管就让框架按默认策略处理（`next` 的返回值必须原样透出）。
      if (bridge === null || !config.approvalEnabled) return next()
      return askApproval(bridge, request)
    })

    /**
     * `user-questions/request`（交互式提问）**本版不接管**：它需要「多问题 + 多选项」
     * 的答案形状，而 QQ 侧只有一句自由文本，硬塞进去等于把多选题降级成猜谜。
     * 不接管 = 交还框架默认链（Web UI 上照常能答），是**fail-open** 而不是失败。
     * 明确记在这里，免得后来的人以为漏了。
     */

    /** 用聊天里的回执结算一个待审批。 */
    function resolveApproval(bridge, kind, code) {
      for (const [callId, pending] of bridge.approvals) {
        if (pending.code !== code) continue
        if (Date.now() > pending.expiresAt) {
          pending.settle(APPROVAL_OUTCOME.REJECTED, 'timeout')
          return { ok: false, callId, reason: 'expired' }
        }
        const outcome = kind === 'approve' ? APPROVAL_OUTCOME.ALLOWED_ONCE : APPROVAL_OUTCOME.REJECTED
        pending.settle(outcome, 'chat')
        return { ok: true, callId, outcome }
      }
      return { ok: false, callId: null, reason: 'not-found' }
    }

    // ────────────────────────────────────────────────────────────────
    // 会话驱动
    // ────────────────────────────────────────────────────────────────

    /**
     * 取该会话最终生效的工作目录。
     *
     * ⚠️ 与 astrbot-relay 不同：那边有「对话级 cwd 覆盖」的读写面（契约 §12.3），
     * 本版**没有**——OneBot 侧没有对应的控制面，留一个只有人工改 state.json
     * 才能生效的字段只会让人以为配了就会生效。这里用 state 记录里的 `cwd`
     * 只是给「人工编辑过 state.json 的部署」留一个后门，默认恒为 null。
     */
    function cwdOf(conversation) {
      const record = records.get(conversation)
      const override = record?.cwd
      if (typeof override === 'string' && override.trim() !== '') {
        if (!isAbsolutePath(override)) {
          say('warn', `state 里 ${conversation} 的 cwd 不是绝对路径（${JSON.stringify(override)}），忽略并回落到全局 cwd`)
        } else {
          return override
        }
      }
      return config.cwd
    }

    /**
     * 驱动一轮：取/建 DSH 会话 → followup → 由 turn/end 收尾。
     *
     * 顺序不可换（astrbot-relay 的契约 §3.1 与三条实测）：
     * 背压判定 → loader.await() → 建映射 → 解析预设/模型 → live 复用或 create/resume
     * → 写标题 → **开回复收集器** → followup。
     */
    async function driveTurn(conversation, text, meta = {}) {
      const bridge = bridgeOf(conversation)
      const maxQueued = Math.max(1, Math.trunc(config.maxQueuedPerConversation))

      if (bridge.queue >= maxQueued) {
        // 背压：明确告知不重试。重试只会加剧拥塞，由用户后续消息触发新尝试。
        say('warn', `${conversation} 在途消息已达上限 ${maxQueued}，本次丢弃`)
        await sendText(conversation, `上一轮还在处理中（已排队 ${bridge.queue} 条），这条先放一放吧。`)
        return
      }
      if (bridge.attaching) {
        // 同一会话的 attach 串行化：并发 resume 同一 sessionId 会各拿一个 handle，
        // 最后谁都拆不对（dsh 包内没有可依赖的 AgentBusy 类，只能本端判定）。
        say('warn', `${conversation} 已有一次 create/resume 在途，本次丢弃`)
        await sendText(conversation, '正在处理上一条消息，稍等一下。')
        return
      }

      bridge.attaching = true
      bridge.queue += 1
      // 额度与单子一一对应：开闸的同时开单，下面任何提前 return 的路径
      // 都已在 try 的 catch / finally 里结算，不会留下没结算的单。
      openInflight(bridge)

      try {
        // 1) boot 闸门：loader 未就绪时 agents.create 会失败。loader 缺席也不阻塞投递。
        await optionalService(host, ctx, 'loader')?.await?.()

        // 2) ⚠️ `hadMapping` 必须在**建映射之前**取样。写在后面的话它恒为 true
        //    （因为上面刚 set 进去），于是新会话会走 `agents.resume` 去加载一个
        //    从未落盘的 sessionId —— 症状是「第一条消息永远失败」。
        const hadMapping = records.has(conversation)

        // 3) 冷启动：没有映射就先分配 id 并落盘。
        if (bridge.dshSessionId === '') {
          bridge.dshSessionId = newSessionId()
          records.set(conversation, newRecord({
            conversation,
            dshSessionId: bridge.dshSessionId,
            cwd: null,
          }))
          persistRecords()
        }
        const cwd = cwdOf(conversation)

        // 4) 预设（agentPreset）是**工具行的唯一载体**：没加入预设 scope 的 agent
        //    落到空全局层，模型手里一支工具都没有（astrbot-relay 实测过的坑）。
        const presets = optionalService(host, ctx, 'agentPresets')
        if (presets === undefined) {
          say('warn', 'agentPresets 服务缺席：本次会话不挂预设，模型将没有任何工具')
        }
        let storedPresetId
        if (hadMapping && presets !== undefined) {
          const sessionQuery = optionalService(host, ctx, 'sessionQuery')
          const observe = sessionQuery?.observeSession
          if (typeof observe === 'function') {
            let observation
            try {
              observation = await observe.call(sessionQuery, brand(bridge.dshSessionId))
              storedPresetId = observation?.header?.agentPreset
            } catch { /* 读不到存档头就按默认预设挂，不值得让投递失败 */ } finally {
              // SessionObservation 是**租约**，不释放会 pin 住 prepared 缓存条目。
              observation?.[Symbol.dispose]?.()
            }
          }
        }
        const presetId = presets === undefined ? undefined : (await presets.resolve(storedPresetId)).id

        // 5) 每次投递取一份 selection 快照并随 agent 固定下来
        //    （installModelSelection 会把选中结果回写到 .assembled）。
        const selection = host.agentDefaultModel.currentSelection()
        const attachOptions = {
          agentOptions: { provider: selection.provider, model: selection.model },
          setup: async (agentCtx) => {
            installModelSelection(agentCtx, { current: selection, assembled: undefined })
            if (presets !== undefined) await presets.mount(agentCtx, presetId)
          },
        }

        // 6) live 优先，冷会话才 create / resume。
        //    宿主 resume 的前提是目标会话**不在 live registry**（否则抛
        //    `cannot prepare session "…" while it is live`），而本插件的 handle
        //    只在卸载期 dispose，live 占用不随 turn/end 释放——所以「有映射就 resume」
        //    会在同一个会话的第二封上稳定失败。
        const live = typeof host.agents.get === 'function' ? host.agents.get(brand(bridge.dshSessionId)) : undefined
        if (live !== undefined) {
          // 复用不产生新 handle：dispose 仍归真正建出它的那次 create/resume 的持有者。
          bridge.agent = live
        } else {
          const handle = hadMapping
            ? await host.agents.resume({
              resumeSessionId: brand(bridge.dshSessionId),
              ...attachOptions,
            })
            : await host.agents.create({
              sessionId: brand(bridge.dshSessionId),
              meta: {
                cwd,
                // 照抄官方 ensureSession 的写法：presetId 缺席时**不写这个键**，
                // 写 undefined 会让存档头凭空多出一个 null 字段。
                ...(presetId === undefined ? {} : { agentPreset: presetId }),
              },
              ...attachOptions,
            })
          bridge.agent = handle.agent
          bridge.dispose = handle.dispose
        }
        bridge.lastError = ''

        // 7) 反向定位：把来源 QQ 会话写进标题。排在 attach 之后（rename 第一步
        //    就校验会话 live），排在 followup 之前（列表里先认得出来）。
        writeSessionTitle(bridge)

        // 8) 开回复收集器 —— **必须**在 followup 之前：text/delta 是瞬时事件，
        //    迟一步开收集器就会丢掉开头几个 token。
        beginReply(bridge, { userInitiated: meta.userInitiated !== false })

        bridge.agent.followup(createUserMessage({
          content: [{ type: 'text', text: String(text ?? '') }],
          // 标**用户来源**：这确实是 QQ 里的某个人说的话。
          source: { kind: 'user' },
        }))

        // 9) 不阻塞调用方：空闲后 flush 会话记录（闸门归 settleInflight 独占，
        //    这里只作 turn/end 没派到本桥时的兜底）。
        //    ⚠️ agent 必须**先取到局部变量**：空闲回收（releaseAgent）会把
        //    `bridge.agent` 置空，回调里再读它就是 null.session。
        const agent = bridge.agent
        agent.whenIdle()
          .then(() => host.sessions.flush(agent.session))
          .catch((error) => say('warn', `会话收尾失败（${conversation}）：${String(error?.message ?? error)}`))
          .finally(() => settleInflight(bridge, 'idle'))

        const record = records.get(conversation)
        if (record !== null && record !== undefined) {
          record.lastActiveAt = Date.now()
          persistRecords()
        }
      } catch (error) {
        settleInflight(bridge, 'error')
        const message = String(error?.message ?? error)
        bridge.lastError = message
        say('warn', `投递失败（${conversation}）：${message}`)
        await sendText(conversation, `这次没能跑起来：${message}`)
      }
    }

    // ────────────────────────────────────────────────────────────────
    // 入站事件
    // ────────────────────────────────────────────────────────────────

    /** 会话的人话描述，用于日志（**不回显消息正文**，避免把聊天内容写进日志）。 */
    function describe(address) {
      return address.messageType === 'group'
        ? `group:${address.groupId}`
        : `private:${address.userId}`
    }

    /** 准入判定：群看群号名单，私聊看用户名单；名单为空表示不限制。 */
    function isAllowedAddress(address) {
      if (address.messageType === 'group') {
        return allowGroups.size === 0 || allowGroups.has(address.groupId)
      }
      return allowUsers.size === 0 || allowUsers.has(address.userId)
    }

    async function handleInboundEvent(payload) {
      if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) return

      // 非消息事件（notice / request / meta_event）：本插件不消费，
      // 但按配置可以原样转给 mode B 的消费者。
      const conversation = conversationKeyOf(payload)
      if (conversation === null) {
        if (config.relayInboundToServer && server !== null) server.broadcast(payload)
        return
      }
      if (config.relayInboundToServer && server !== null) server.broadcast(payload)

      // 去重：`message_id` 是 OneBot 侧唯一的幂等抓手。
      if (seenRecently(payload.message_id)) {
        say('debug', `重复的 message_id=${payload.message_id}，已忽略（${conversation}）`)
        return
      }

      const segments = normalizeMessage(payload.message)
      const sender = payload.sender !== null && typeof payload.sender === 'object' ? payload.sender : {}
      const eventSelfId = selfIdOf(payload)
      const text = segmentsToText(segments, { selfId, stripAtSelf: true })
      const { messageType } = parseConversation(conversation)
      const address = {
        messageType,
        userId: digitsOf(payload.user_id),
        groupId: digitsOf(payload.group_id),
        nickname: String(sender.card || sender.nickname || payload.user_id || ''),
      }
      if (eventSelfId !== null && eventSelfId !== selfId) {
        // 事件里的 self_id 与配置不一致：多半是**多账号**部署里另一个号的事件
        // 混进了同一条连接。按配置为准处理，但要说出来。
        say('debug', `事件 self_id=${eventSelfId} 与配置 selfId=${selfId} 不一致（${describe(address)}）`)
      }

      // 群聊触发规则：必须 @ 机器人（requireAt，默认开）。
      if (messageType === 'group' && config.requireAt && !hasAtSelf(segments, selfId)) {
        say('debug', `${describe(address)} 未 @ 机器人，按 requireAt 规则跳过`)
        return
      }

      // 准入：谁能**驱动 agent**。空名单 = 不限制（与 MeaPet 同约定）。
      // ⚠️ 位置在 relay 广播之后：与 MeaPet「先广播、后过滤」的顺序一致——
      //    名单管的是"能不能驱动 agent"，不是"能不能看到事件"。
      if (!isAllowedAddress(address)) {
        say('warn', `${describe(address)} 不在准入名单内，已忽略（allowUsers/allowGroups）`)
        return
      }

      if (text.trim() === '') {
        // 文本真的是空（例如只带一个被映射成空串的 `reply` 段）：跳过。
        // ⚠️ 注意**不要**把「只发了一张图」也算进来——图片会变成 `[图片]` 占位符
        // （与 MeaPet 的占位表对齐），那是有语义的，必须投给模型。
        say('debug', `${describe(address)} 解析后文本为空，跳过`)
        return
      }

      const bridge = bridgeOf(conversation)

      // 本地指令在投给 agent **之前**接走：审批回执若被当成普通消息投进去，
      // 模型会去「回答」一串验证码，而审批其实还挂着。
      const command = parseLocalCommand(text)
      if (command !== null) {
        await handleLocalCommand(bridge, command)
        return
      }

      say('info', `收到消息（${describe(address)}，${Array.from(text).length} 字）`)
      await driveTurn(conversation, text, { userInitiated: true })
    }

    async function handleLocalCommand(bridge, command) {
      const conversation = bridge.conversation
      switch (command.kind) {
        case 'approve':
        case 'reject': {
          const result = resolveApproval(bridge, command.kind, command.code)
          if (result.ok) {
            say('info', `${conversation} 的审批 ${result.callId} 由聊天侧结算为 ${result.outcome}`)
            return
          }
          const why = result.reason === 'expired'
            ? '这个验证码已经过期，审批按拒绝处理了。'
            : '没有匹配的待审批项（验证码打错，或它已经处理过了）。'
          await sendText(conversation, why)
          return
        }
        case 'help': {
          await sendText(conversation, [
            '可用指令：',
            '/approve <验证码> — 批准一次待审批的工具调用',
            '/reject <验证码> — 拒绝它',
            '/status — 看这条会话的桥接状态',
            '/help — 这条帮助',
            '其余消息一律交给 DSH agent 处理。',
          ].join('\n'))
          return
        }
        case 'status': {
          const record = records.get(conversation)
          await sendText(conversation, [
            `会话：${conversation}`,
            `DSH 会话：${record?.dshSessionId ?? '(尚未创建)'}`,
            `live agent：${bridge.agent === null ? '否' : '是'}`,
            `在途/已排队：${bridge.queue}`,
            `待审批：${bridge.approvals.size}`,
            `工作目录：${cwdOf(conversation)}`,
          ].join('\n'))
          return
        }
        default:
          return
      }
    }

    // 事件订阅要在**任何** followup 之前完成（瞬时事件规则）。
    // clientEnabled=false 时没有事件来源，订阅也就不必挂。
    if (client !== null) {
      client.on('event', (payload) => {
        handleInboundEvent(payload).catch((error) => {
          say('warn', `处理入站事件失败：${String(error?.message ?? error)}`)
        })
      })
      client.on('close', () => {
        say('info', '上游连接已断开，等待重连')
      })
    }

    // ────────────────────────────────────────────────────────────────
    // 空闲回收
    // ────────────────────────────────────────────────────────────────

    /**
     * 释放一个桥持有的 live agent。
     *
     * 收尾序与 astrbot-relay 逐字一致：
     * 摘引用 → cancel({kind:'user'}) → whenIdle() → sessions.flush() → dispose()。
     *
     * ⚠️ **与 astrbot-relay 不同**：那边（on-demand 策略）会把会话映射也摘掉、
     * 下次新建一个会话；这里**保留映射**，下次消息走 `agents.resume` 把同一个会话
     * 接回来。理由：QQ 会话是长期存在的，把「内存回收」做成「话题重置」会让用户
     * 莫名其妙地发现机器人不记得刚才聊过什么。回收的是内存，不是上下文。
     * ⚠️ `dispose()` 会把会话从 store 里删掉，所以它只能在**真正要放掉 handle** 时调；
     * 放进每轮的 `.finally()` 会让下一轮的 resume 前提被拆掉（每轮都变新会话）。
     */
    async function releaseAgent(bridge, reason) {
      const agent = bridge.agent
      const dispose = bridge.dispose
      bridge.agent = null
      bridge.dispose = null
      if (agent === null || agent === undefined) return false
      try {
        agent.cancel({ kind: 'user' })
        await agent.whenIdle()
        await host.sessions.flush(agent.session)
      } catch (error) {
        // 排空失败也必须继续 dispose，否则 handle 永远留在 registry 里。
        say('warn', `${reason}：${bridge.conversation} 排空失败，仍继续拆除：${String(error?.message ?? error)}`)
      }
      try {
        if (typeof dispose === 'function') await dispose()
      } catch (error) {
        say('warn', `${reason}：${bridge.conversation} dispose 失败：${String(error?.message ?? error)}`)
      }
      return true
    }

    /**
     * 卸载收尾：cancel → whenIdle → flush → dispose，再落一次盘。
     *
     * ⚠️ 先摘引用再收尾：收尾期间新到的事件会重新 attach，不会撞上这次拆除。
     */
    async function shutdownBridges(reason) {
      const pending = []
      for (const bridge of bridges.values()) {
        if (bridge.agent === null) continue
        for (const entry of [...bridge.approvals.values()]) {
          // 卸载时把待审批结算成拒绝：留着它们会挂住正在等的 turn。
          entry.settle(APPROVAL_OUTCOME.REJECTED, 'unload')
        }
        pending.push(releaseAgent(bridge, reason))
      }
      await Promise.allSettled(pending)
      persistRecords()
    }

    const idleTtlMs = Math.max(0, Math.trunc(Number(config.idleTtlMs)))
    if (idleTtlMs > 0) {
      // 扫描周期取 TTL 的 1/10，夹在 [15s, 5min]：太密是白烧 CPU，
      // 太稀则「TTL 已过」与「真的回收」之间会差出一个数量级。
      const sweepIntervalMs = Math.min(300_000, Math.max(15_000, Math.floor(idleTtlMs / 10)))
      let sweeping = false
      ctx.effect(() => {
        const timer = setInterval(() => {
          if (sweeping) return
          sweeping = true
          sweepIdleBridges().catch((error) => {
            say('warn', `空闲回收异常：${String(error?.message ?? error)}`)
          }).finally(() => { sweeping = false })
        }, sweepIntervalMs)
        timer.unref?.()
        return () => clearInterval(timer)
      }, `${name}: idle-sweep`)
      say('info', `空闲回收已启用：TTL ${idleTtlMs}ms，每 ${sweepIntervalMs}ms 扫描一次`)
    }

    async function sweepIdleBridges() {
      const now = Date.now()
      for (const bridge of bridges.values()) {
        if (bridge.agent === null) continue
        if (bridge.inflight !== null) continue
        if (bridge.approvals.size > 0) continue
        const record = records.get(bridge.conversation)
        const lastActiveAt = Number(record?.lastActiveAt) || 0
        if (lastActiveAt <= 0 || now - lastActiveAt < idleTtlMs) continue
        say('info', `${bridge.conversation} 空闲超过 ${idleTtlMs}ms，释放 live agent（保留会话映射）`)
        await releaseAgent(bridge, '空闲回收')
      }
    }

    // ────────────────────────────────────────────────────────────────
    // 路由（注册在 dsh webServer 上）
    // ────────────────────────────────────────────────────────────────
    //
    // ⚠️ 硬事实第 1 条：这里注册的路由**没有任何框架级鉴权**，必须自己过闸。
    // ⚠️ 重复 (kind, path) 会抛错，必须降级为 no-op 而不是把 host 搞崩。
    //
    // OneBot 的两个端点在 **mode B 自己的端口**上（见 ROUTES 与 serverEnabled），
    // 这里只放一个诊断快照 /status —— 它同时是「host half 是否真的加载了」的证据。

    function handleStatus(request, response) {
      if (!authorizeToken(request, serverToken)) {
        const payload = JSON.stringify({
          status: STATUS.FAILED, retcode: RETCODE.UNAUTHORIZED, data: null, error: 'unauthorized',
        })
        response.writeHead(401, {
          'content-type': 'application/json; charset=utf-8',
          'content-length': Buffer.byteLength(payload),
          'cache-control': 'no-store',
        })
        response.end(payload)
        return
      }
      const pending = []
      for (const bridge of bridges.values()) {
        if (bridge.agent === null && bridge.queue === 0 && bridge.approvals.size === 0) continue
        pending.push({
          conversation: bridge.conversation,
          dshSessionId: bridge.dshSessionId,
          attaching: bridge.attaching,
          queue: bridge.queue,
          approvals: bridge.approvals.size,
          turn: bridge.turn,
          lastError: bridge.lastError,
        })
      }
      const body = JSON.stringify({
        ok: true,
        plugin: name,
        bridgeVersion: BRIDGE_VERSION,
        uptimeMs: Date.now() - startedAt,
        pathPrefix: config.pathPrefix,
        cwd: config.cwd,
        statePath,
        stateExisted,
        conversations: records.size,
        selfId,
        requireAt: config.requireAt,
        // 报告**归一化后**的名单（空数组 = 不限制）：诊断"为什么它不理某人"时要看这个
        allowUsers: [...allowUsers],
        allowGroups: [...allowGroups],
        streamMode: config.streamMode,
        replyChunking: config.replyChunking,
        replyChunkChars: config.replyChunkChars,
        idleTtlMs,
        maxQueuedPerConversation: config.maxQueuedPerConversation,
        approvalEnabled: config.approvalEnabled,
        approvalTimeoutMs: config.approvalTimeoutMs,
        clientEnabled: config.clientEnabled,
        client: client === null ? null : client.snapshot(),
        serverEnabled: config.serverEnabled,
        server: server === null ? null : server.snapshot(),
        actions: actionSurface(routeAction),
        pending,
      }, null, 2)
      response.writeHead(200, {
        'content-type': 'application/json; charset=utf-8',
        'content-length': Buffer.byteLength(body),
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
      })
      response.end(body)
    }

    ctx.effect(() => {
      const disposers = []
      const routes = [
        { kind: 'exact', path: joinPath(config.pathPrefix, ROUTES.STATUS), handler: handleStatus },
      ]
      for (const route of routes) {
        try {
          disposers.push(host.webServer.register(route))
        } catch (error) {
          say('warn', `路由注册失败，已跳过 ${route.path}：${String(error?.message ?? error)}`)
        }
      }
      say('info', `已挂载诊断路由 ${joinPath(config.pathPrefix, ROUTES.STATUS)}`
        + `（bridgeVersion=${BRIDGE_VERSION}）`)
      return () => {
        for (const dispose of disposers.reverse()) {
          try { dispose() } catch { /* 卸载期异常不得逃逸 */ }
        }
      }
    }, `${name}: routes`)

    // 配置自检：把「本端实际在跑哪几个角色」在加载期就说清楚。
    // 一个装了但两边都不通（clientEnabled=false 且 serverEnabled=false）的插件
    // 只会安静地什么都不做——那是「功能像没装」的失败形态，必须变成一条明确警告。
    if (!config.clientEnabled && !config.serverEnabled) {
      say('warn', 'clientEnabled 与 serverEnabled 都为 false：本插件不会建立任何 OneBot 连接，'
        + '既不会驱动会话，也不会收到 API 调用。只想要 /status 诊断请忽略这条。')
    }
    say('info', `state=${statePath}（${stateExisted ? `已载入 ${records.size} 条映射` : '新建'}），`
      + `cwd=${config.cwd}，selfId=${selfId}`)
    if (config.serverEnabled && server !== null) {
      say('info', `mode B：对端应连 ${server.wsPath} 或 POST ${server.apiPath}`
        + `（host=${config.serverHost} port=${config.serverPort}）`)
    }
    if (client !== null) {
      say('info', `mode A：将拨号 ${config.meapetWsUrl}`)
    }

    // ────────────────────────────────────────────────────────────────
    // 副作用登记（全部经 ctx.effect，卸载时能还原）
    // ────────────────────────────────────────────────────────────────

    ctx.effect(() => {
      // mode A：开始拨号。这里是**唯一**启动客户端的地方。
      client?.start()
      return () => {
        try { client?.stop() } catch { /* 卸载期异常不得逃逸 */ }
      }
    }, `${name}: client`)

    ctx.effect(() => {
      let started = false
      // mode B 的 start() 是异步的；用一条自己接住的 promise，
      // 绝不让它的 rejection 变成 unhandled rejection 把宿主带走。
      const starting = server === null
        ? Promise.resolve()
        : server.start().then(() => { started = true }).catch((error) => {
          say('warn', `mode B 启动失败：${String(error?.message ?? error)}`)
        })
      void starting
      return () => {
        if (server === null) return
        // disposer 不等 Promise，只能挂出去；必须接住 rejection。
        Promise.resolve(starting)
          .then(() => (started ? server.stop() : undefined))
          .catch((error) => say('warn', `mode B 关闭异常：${String(error?.message ?? error)}`))
      }
    }, `${name}: server`)

    ctx.effect(() => () => {
      // Cordis 的 disposer 不等 Promise，所以异步收尾只能挂出去；
      // 必须接住 rejection，否则算 unhandled。
      shutdownBridges('卸载收尾').catch((error) => {
        say('warn', `卸载收尾异常：${String(error?.message ?? error)}`)
      })
    }, `${name}: shutdown`)

    // 动作面自检：白名单里如果有本版既不转发、也不本地作答的表项，
    // 说明 `lib/contract.js` 与 `lib/rpc/actions.js` 漂移了。
    if (!isAllowedAction('get_status')) {
      say('warn', '动作白名单自检失败：get_status 不在白名单里（contract.js 被改坏了？）')
    }
  })
}
