/**
 * dsh-onebot-relay · 配置 schema 与加载期校验
 *
 * ## 两条设计原则（照抄 astrbot-relay 的 `lib/index.js` 与契约 §8 末尾）
 *
 * 1. **凡不同部署可能取不同值的参数都不许硬编码**——聊天里发多长的消息、
 *    多久算空闲、哪个是机器人自己的 QQ 号，都是部署取值。
 * 2. **非法配置在加载期失败，不静默降级**。「没人知道这个功能其实没生效」
 *    是最贵的故障形态；宁可插件加载即抛错，也不要一个跑起来但半死的网桥。
 *
 * ## 为什么 `meapetWsUrl` / `meapetToken` 不走 Schemastery 的 `required()`
 *
 * 这两个是 mode A（本插件当 OneBot **客户端**）的参数。只跑 mode B
 * （`serverEnabled: true`，等对端连进来）的部署根本不需要 `meapetWsUrl`，
 * 给它加 `.required()` 会让一种**合法**部署无法加载。
 * 因此 schema 层只给默认值，真正的「必填」判据放在 `assertConfigIsUsable` 里
 * 按模式逐条判——**仍然是加载期抛错**，只是判据写对了模式。
 *
 * `cwd` 与 `selfId` 与模式无关，直接 `.required()`。
 */
import Schema from '@deepseek-ai/schemastery'
import {
  MESSAGE_TYPE, PLUGIN_NAME, renderSessionTitle,
} from './contract.js'

/** 流式输出模式。 */
export const STREAM_MODE = Object.freeze({
  /** 只在 turn 结束时发一次**权威最终文本**。默认：不刷屏、不重复。 */
  FINAL: 'final',
  /** 边跑边发增量预览，结束时补发尾巴。best-effort，见 README 的说明。 */
  PREVIEW: 'preview',
})

/** 默认会话标题模板。占位符见 `contract.js` 的 `renderSessionTitle`。 */
export const DEFAULT_TITLE_TEMPLATE = '喵桥 · {messageType}/{sessionId}'

/** 非回环主机名集合（用于 mode B「允许远端连接」的显式闸门）。 */
const LOOPBACK_HOSTS = new Set(['127.0.0.1', '::1', 'localhost', '::ffff:127.0.0.1'])

export const Config = Schema.object({
  // ── mode A：本插件当 OneBot 客户端，连 MeaPet 的 hub ──────────────
  /** 关掉它就不拨号（只跑 mode B / 纯诊断）。 */
  clientEnabled: Schema.boolean().default(true),
  /** MeaPet hub 的 WebSocket 地址，如 `ws://127.0.0.1:14321/onebot/v11/hub`。 */
  meapetWsUrl: Schema.string().default(''),
  /** 共享密钥。MeaPet 侧是 `onebot.hub.access_token` / `forward_ws.access_token`。 */
  meapetToken: Schema.string().default(''),
  /**
   * 是否把 token 附成 `?access_token=`（规范 §2.5 的第二种鉴权方式）。
   * 默认 false：优先用 `Authorization` 头；某些只认 query 的实现端才打开。
   * 打开时**两种都会发**——MeaPet 先查头、再查 query，多发一个参数无害。
   */
  tokenInQuery: Schema.boolean().default(false),

  // ── 身份与工作目录 ────────────────────────────────────────────────
  /** 机器人自己的 QQ 号。`requireAt` 与 `@全体成员` 判定都靠它。 */
  selfId: Schema.string().required(),
  /** agent 工作目录，**必须是绝对路径**。 */
  cwd: Schema.string().required(),
  /**
   * 路径前缀。mode B 的两个 OneBot 端点（`/ws`、`/api`）与注册在 dsh webServer
   * 上的诊断路由 `/status` 都挂在它下面。**不要写尾斜杠**（prefix 匹配会错位）。
   */
  pathPrefix: Schema.string().default('/onebot-relay'),

  // ── 行为 ──────────────────────────────────────────────────────────
  /**
   * 群里是否必须先 `@` 机器人才响应。默认 true。
   * 私聊不受它影响（私聊没有「@」这个概念）。
   *
   * 关掉它等于把机器人丢进群里对**每一句**闲话都发起一次 LLM 调用——
   * 默认值之所以是 true，是因为这个默认值错了只花点耐心，反过来是花钱。
   */
  requireAt: Schema.boolean().default(true),
  /**
   * **谁能驱动 agent**。空数组 = 不限制（与 MeaPet 的 `allow.users` / `allow.group_ids` 同约定）。
   *
   * 为什么这里必须再有一道：MeaPet 的 `allow.*` 只在 `reply_mode` 含 `pet` 时生效，
   * 而 `reply_mode=hub` 时那个函数**一次都不会被调用**——那条路上的准入只能由本端决定。
   * 只认数字，写 `10001` 或 `10001 ` 都行，非数字字符会被忽略。
   *
   * ⚠️ 它管的是"能不能驱动 agent"，**不是**"能不能看到事件"：`relayInboundToServer`
   * 的转发仍然照旧（与 MeaPet「先广播、后过滤」的顺序一致）。
   */
  allowUsers: Schema.array(Schema.string()).default([]),
  /** 群号名单，语义同 `allowUsers`（群聊仍需满足 `requireAt`）。 */
  allowGroups: Schema.array(Schema.string()).default([]),
  /** 回复是否按长度切分（长回复在部分实现端会被截断或被包成合并转发）。 */
  replyChunking: Schema.boolean().default(true),
  /** 单条消息的最大**码点**数。默认 1500（OneBot 实现端常见的软上限）。 */
  replyChunkChars: Schema.number().default(1500),
  /**
   * DSH 会话标题模板——反向定位的抓手。占位符：
   * `{messageType}` `{sessionId}` `{conversation}` `{bridge}`
   */
  sessionTitleTemplate: Schema.string().default(DEFAULT_TITLE_TEMPLATE),
  /** 输出模式：`final`（默认）或 `preview`。 */
  streamMode: Schema.union([STREAM_MODE.FINAL, STREAM_MODE.PREVIEW]).default(STREAM_MODE.FINAL),
  /** `preview` 模式下，攒够多少字符才发一次预览。 */
  previewMinChars: Schema.number().default(60),
  /** `preview` 模式下，两次预览之间的最小间隔（毫秒）。 */
  previewIntervalMs: Schema.number().default(1500),

  // ── 会话映射 ──────────────────────────────────────────────────────
  /** 空 = `<DSH_HOME>/onebot-relay/state.json`；非空必须是绝对路径。 */
  statePath: Schema.string().default(''),
  /** 空闲回收阈值（毫秒）。**0 = 关闭回收**。 */
  idleTtlMs: Schema.number().default(86_400_000),

  // ── 背压 ──────────────────────────────────────────────────────────
  /** 单会话在途消息上限；超出即拒（不回重试，见 README）。 */
  maxQueuedPerConversation: Schema.number().default(4),

  // ── mode B：本插件当 OneBot 服务端（自带端口） ────────────────────
  /**
   * 是否起一个独立 HTTP 服务暴露 OneBot 端点。
   *
   * ⚠️ **为什么必须是自己起服务，而不是往 `ctx.webServer` 上注册路由**：
   * `ctx.webServer.register` 的 handler 签名是 `(request, response)`，
   * 没有 WebSocket upgrade 的位置；OneBot 的正向 WS 需要接管 upgrade，
   * 只能自己持有 `http.Server`。`serverPort` 这个配置项的含义就在这里。
   */
  serverEnabled: Schema.boolean().default(false),
  serverHost: Schema.string().default('127.0.0.1'),
  /** 0 = 由系统分配空闲端口（测试用）；生产请写定值。 */
  serverPort: Schema.number().default(6721),
  /** mode B 的共享密钥。空 = 退回 `meapetToken`。 */
  serverToken: Schema.string().default(''),
  /** 非回环 `serverHost` 必须显式打开它（对齐 MeaPet 的 `allow_insecure_remote`）。 */
  serverAllowRemote: Schema.boolean().default(false),
  /**
   * mode B 上连进来的消费者是否**允许调用动作**（对齐 MeaPet 的 `hub.allow_send`）。
   *
   * 默认 true。设成 false 时，白名单里的动作**一律**拒绝（retcode 103），包括
   * `get_status` / `get_version_info` 这类只读动作——因为白名单里的读动作包含
   * `get_msg` / `get_group_list` / `get_group_member_info`，那些能读到聊天记录，
   * 并不"无害"。所以这是个"要么能问、要么什么都别问"的开关，不是"只禁发消息"。
   */
  serverAllowSend: Schema.boolean().default(true),
  /**
   * 是否把 mode A 收到的上游事件**原样再广播**给 mode B 上连进来的消费者。
   * 默认 false：默认开着等于把你的 QQ 消息原样复制给任何持 token 的连接。
   */
  relayInboundToServer: Schema.boolean().default(false),

  // ── 传输参数 ──────────────────────────────────────────────────────
  /** WS ping 间隔（毫秒）。同时也是 mode B 对已连接客户端的 ping 间隔。 */
  heartbeatMs: Schema.number().default(15_000),
  /** 重连退避起点（毫秒）。 */
  reconnectInitialMs: Schema.number().default(1000),
  /** 重连退避上限（毫秒）。 */
  reconnectMaxMs: Schema.number().default(30_000),
  /** 单次 API 调用的等待上限（毫秒）。 */
  callTimeoutMs: Schema.number().default(15_000),

  // ── 审批转发 ──────────────────────────────────────────────────────
  /** 是否接管 `approval/request`，把审批搬到聊天里做。 */
  approvalEnabled: Schema.boolean().default(true),
  /**
   * 审批等待上限（毫秒）。
   *
   * ⚠️ 审批服务自身**零超时**：不自己加超时，没人回执就会**永久堵死该 turn**
   *    （astrbot-relay 的契约 §7.2 第 1 条实测过这条）。到点一律 resolve
   *    `rejected`（fail closed），**不是** `cancelled`——后者是「用户/上游取消」
   *    的语义，会被误当成正常中止。
   */
  approvalTimeoutMs: Schema.number().default(120_000),
})

/** Windows 与 POSIX 通用的绝对路径判定。 */
export function isAbsolutePath(value) {
  return /^[a-zA-Z]:[\\/]/.test(value) || String(value ?? '').startsWith('/') || String(value ?? '').startsWith('\\\\')
}

/**
 * 把号码名单归一化成纯数字字符串集合。
 *
 * 与 MeaPet 的 `digitsOf()` 同一口径：非数字字符一律丢掉，空串丢弃、去重。
 * 这样 `10001`、` 10001 `、`user_10001` 都等价，用户从别处粘过来不用手改。
 */
export function normalizeIdList(values) {
  const out = new Set()
  if (!Array.isArray(values)) return out
  for (const value of values) {
    const digits = String(value ?? '').replace(/\D+/g, '')
    if (digits !== '') out.add(digits)
  }
  return out
}

/**
 * 配置可用性校验。
 *
 * 这些约束无法用 Schemastery 表达（需要跨字段 / 与运行环境比较），
 * 因此手工检查，并且**加载即失败**——抛出的错误会直接让插件加载失败。
 */
export function assertConfigIsUsable(config) {
  if (config === null || typeof config !== 'object') {
    throw new Error(`${PLUGIN_NAME}: 缺少配置`)
  }

  // ── 与模式无关的必填项 ──
  if (typeof config.selfId !== 'string' || !/^\d+$/.test(config.selfId.trim())) {
    throw new Error(`${PLUGIN_NAME}: selfId 必须是机器人的 QQ 号（纯数字），收到 ${JSON.stringify(config.selfId)}`)
  }
  if (typeof config.meapetToken !== 'string' || config.meapetToken.trim() === '') {
    throw new Error(`${PLUGIN_NAME}: meapetToken 不能为空（它是唯一的鉴权防线，见 README）`)
  }
  if (typeof config.cwd !== 'string' || !isAbsolutePath(config.cwd)) {
    throw new Error(`${PLUGIN_NAME}: cwd 必须是绝对路径，收到 ${JSON.stringify(config.cwd)}`)
  }

  // ── mode A ──
  if (config.clientEnabled) {
    const url = String(config.meapetWsUrl ?? '').trim()
    if (url === '') {
      throw new Error(`${PLUGIN_NAME}: clientEnabled 为 true 时 meapetWsUrl 必填`
        + '（形如 ws://127.0.0.1:14321/onebot/v11/hub）；只跑 mode B 请设 clientEnabled: false')
    }
    if (!/^wss?:\/\//i.test(url)) {
      throw new Error(`${PLUGIN_NAME}: meapetWsUrl 必须是 ws:// 或 wss:// 地址，收到 ${JSON.stringify(url)}`)
    }
  }

  // ── 路径与模板 ──
  if (typeof config.pathPrefix !== 'string' || config.pathPrefix.trim() === '') {
    throw new Error(`${PLUGIN_NAME}: pathPrefix 不能为空`)
  }
  if (!config.pathPrefix.startsWith('/')) {
    throw new Error(`${PLUGIN_NAME}: pathPrefix 必须以 "/" 开头，收到 ${JSON.stringify(config.pathPrefix)}`)
  }
  if (config.pathPrefix.endsWith('/')) {
    throw new Error(`${PLUGIN_NAME}: pathPrefix 不要以 "/" 结尾（prefix 匹配会因此错位）`)
  }
  if (typeof config.sessionTitleTemplate !== 'string' || config.sessionTitleTemplate.trim() === '') {
    throw new Error(`${PLUGIN_NAME}: sessionTitleTemplate 不能为空（它承担反向定位，空标题等于定位失效）`)
  }
  // 渲染一次做冒烟：未知占位符会原样保留，这里只保证渲染结果非空。
  const probe = renderSessionTitle(config.sessionTitleTemplate, `${MESSAGE_TYPE.PRIVATE}:0`)
  if (probe.trim() === '') {
    throw new Error(`${PLUGIN_NAME}: sessionTitleTemplate 渲染结果为空`)
  }

  // ── 数值参数 ──
  if (!Number.isFinite(Number(config.idleTtlMs)) || Number(config.idleTtlMs) < 0) {
    throw new Error(`${PLUGIN_NAME}: idleTtlMs 必须是非负毫秒数（0 = 关闭空闲回收），`
      + `收到 ${JSON.stringify(config.idleTtlMs)}`)
  }
  if (!Number.isInteger(Number(config.maxQueuedPerConversation)) || Number(config.maxQueuedPerConversation) < 1) {
    throw new Error(`${PLUGIN_NAME}: maxQueuedPerConversation 必须是 >= 1 的整数，`
      + `收到 ${JSON.stringify(config.maxQueuedPerConversation)}`)
  }
  if (!Number.isFinite(Number(config.replyChunkChars)) || Number(config.replyChunkChars) < 1) {
    throw new Error(`${PLUGIN_NAME}: replyChunkChars 必须是 >= 1 的数，收到 ${JSON.stringify(config.replyChunkChars)}`)
  }
  if (!Number.isFinite(Number(config.heartbeatMs)) || Number(config.heartbeatMs) < 1000) {
    throw new Error(`${PLUGIN_NAME}: heartbeatMs 必须 >= 1000，收到 ${JSON.stringify(config.heartbeatMs)}`)
  }
  if (!Number.isFinite(Number(config.callTimeoutMs)) || Number(config.callTimeoutMs) < 1000) {
    throw new Error(`${PLUGIN_NAME}: callTimeoutMs 必须 >= 1000，收到 ${JSON.stringify(config.callTimeoutMs)}`)
  }
  if (!Number.isFinite(Number(config.approvalTimeoutMs)) || Number(config.approvalTimeoutMs) < 1000) {
    throw new Error(`${PLUGIN_NAME}: approvalTimeoutMs 必须 >= 1000（审批服务自身零超时，这里必须给值），`
      + `收到 ${JSON.stringify(config.approvalTimeoutMs)}`)
  }
  const initial = Number(config.reconnectInitialMs)
  const max = Number(config.reconnectMaxMs)
  if (!Number.isFinite(initial) || initial < 100 || !Number.isFinite(max) || max < initial) {
    throw new Error(`${PLUGIN_NAME}: 重连退避必须是 100 <= reconnectInitialMs <= reconnectMaxMs，`
      + `收到 ${JSON.stringify([config.reconnectInitialMs, config.reconnectMaxMs])}`)
  }

  // ── 输出模式 ──
  if (config.streamMode !== STREAM_MODE.FINAL && config.streamMode !== STREAM_MODE.PREVIEW) {
    throw new Error(`${PLUGIN_NAME}: streamMode 只能是 ${STREAM_MODE.FINAL} / ${STREAM_MODE.PREVIEW}，`
      + `收到 ${JSON.stringify(config.streamMode)}`)
  }
  if (config.streamMode === STREAM_MODE.PREVIEW) {
    if (!Number.isFinite(Number(config.previewMinChars)) || Number(config.previewMinChars) < 1) {
      throw new Error(`${PLUGIN_NAME}: previewMinChars 必须 >= 1，收到 ${JSON.stringify(config.previewMinChars)}`)
    }
    if (!Number.isFinite(Number(config.previewIntervalMs)) || Number(config.previewIntervalMs) < 200) {
      throw new Error(`${PLUGIN_NAME}: previewIntervalMs 必须 >= 200（再小就是刷屏），`
        + `收到 ${JSON.stringify(config.previewIntervalMs)}`)
    }
  }

  // ── mode B ──
  if (config.serverEnabled) {
    const port = Number(config.serverPort)
    if (!Number.isInteger(port) || port < 0 || port > 65535) {
      throw new Error(`${PLUGIN_NAME}: serverPort 必须在 0..65535（0 = 系统分配），收到 ${JSON.stringify(config.serverPort)}`)
    }
    const host = String(config.serverHost ?? '').trim()
    if (host === '') throw new Error(`${PLUGIN_NAME}: serverEnabled 为 true 时 serverHost 不能为空`)
    // 非回环监听必须显式认下：OneBot 端点上没有任何框架级鉴权，
    // 把它开到 0.0.0.0 等于把「谁的 QQ 消息都收得到」的能力交给整个局域网。
    if (!LOOPBACK_HOSTS.has(host.toLowerCase()) && !config.serverAllowRemote) {
      throw new Error(`${PLUGIN_NAME}: serverHost=${JSON.stringify(host)} 是非回环地址，`
        + '必须显式设置 serverAllowRemote: true 才允许（它会把 OneBot 端点暴露到网络上）')
    }
    if (String(config.serverToken ?? '').trim() === '' && String(config.meapetToken ?? '').trim() === '') {
      throw new Error(`${PLUGIN_NAME}: serverEnabled 为 true 时必须有 serverToken 或 meapetToken`)
    }
  }
}

/**
 * 返回「能跑但不该这么配」的提示列表，供调用方打 warning。
 *
 * 与 `assertConfigIsUsable` 的分工：**这里绝不抛错**。
 * 判据只有一条——「这条配置会让安全性或可用性悄悄降级，但仍然是合法配置」。
 */
export function configWarnings(config) {
  const warnings = []
  const token = String(config.meapetToken ?? '')
  if (token.length < 16) {
    warnings.push(`meapetToken 只有 ${token.length} 个字符；建议 >= 32 字节随机串`)
  }
  if (config.clientEnabled && !String(config.meapetWsUrl ?? '').trim().toLowerCase().startsWith('wss://')) {
    // 明文 WS 只在回环上安全。跨机部署必须 wss://（对齐 astrbot-relay 契约 §5.1）。
    let host = ''
    try { host = new URL(String(config.meapetWsUrl).trim()).hostname.toLowerCase() } catch { host = '' }
    if (!LOOPBACK_HOSTS.has(host)) {
      warnings.push(`meapetWsUrl 走的是明文 ws:// 且主机 ${JSON.stringify(host)} 不是回环地址；跨机部署请用 wss://`)
    }
  }
  if (config.clientEnabled && !config.requireAt) {
    warnings.push('requireAt 已关闭：群里每一句闲话都会触发一次 LLM 调用')
  }
  if (config.streamMode === STREAM_MODE.PREVIEW) {
    warnings.push('streamMode=preview：预览是 best-effort，模型重排文本时聊天里可能留下与最终答复不一致的片段')
  }
  if (config.relayInboundToServer) {
    warnings.push('relayInboundToServer 已开启：上游 QQ 消息会被原样广播给 mode B 上任何持 token 的连接')
  }
  if (!config.approvalEnabled) {
    warnings.push('approvalEnabled 已关闭：敏感工具审批交回 DSH 默认策略（Web UI），聊天里看不到')
  }
  if (Number(config.idleTtlMs) === 0) {
    warnings.push('idleTtlMs=0：会话永不回收，长时间运行会把每个 QQ 会话的 agent 都留在内存里')
  }
  return warnings
}
