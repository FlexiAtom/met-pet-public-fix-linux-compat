/**
 * dsh-onebot-relay · OneBot v11 线上契约（纯函数，**零外部依赖**）
 *
 * ## 为什么要有这个文件
 *
 * 「线上长什么样」是两侧（本插件 ↔ MeaPet）唯一的接口真相。把「帧形状」与
 * 「解析规则」放在一个**不 import 任何东西**的模块里，是为了：
 *   1. `node --test` 能在**没装 dsh 依赖**的普通 Node 下直接把它跑穿
 *      （`lib/index.js` 需要 `@deepseek-ai/dsh-*`，测不动）；
 *   2. 线上格式只有一份权威——传输层（`lib/onebot.js`）、动作层
 *      （`lib/rpc/actions.js`）与编排层（`lib/index.js`）都从这里取，不各自抄一份。
 *
 * ## 已核实的对齐目标
 *
 * 对齐对象是 MeaPet 桌面端的 OneBot 实现（本机源码实读，路径均为仓库内相对路径）：
 *   - `meapet/onebot/service.py` 的 `HUB_ALLOWED_ACTIONS` → `ALLOWED_ACTIONS`
 *   - `meapet/onebot/events.py` 的 `is_api_response()` → `isApiResponse()`
 *     （判据逐字对齐：带 `echo`/`retcode`/`status` **且没有** `post_type`）
 *   - `meapet/onebot/api.py` 的 `send_target_params()` → `targetOf()`
 *   - `meapet/onebot/segments.py` 的 `segments_to_text()` / `is_at_self()`
 *     → `segmentsToText()` / `hasAtSelf()`（占位符表逐条对齐）
 *   - `meapet/onebot/api.py` 的 `make_echo()` → `newEcho()`
 *
 * 规范文本另有一份本地副本：仓库根的 `onebotv11.txt`（OneBot v11 端口标准）。
 */

/** 契约版本。每次**破坏性**改动线上形状时递增（新增可选字段不递增）。 */
export const BRIDGE_VERSION = '1'

/** 插件名。日志前缀、`source.plugin`、state 目录名都用它。 */
export const PLUGIN_NAME = 'dsh-onebot-relay'

/** state.json 的结构版本；不兼容变更时递增，旧版本一律拒绝加载。 */
export const STATE_VERSION = 1

/** mode B（本插件当 OneBot 服务端）暴露的路径，均挂在 `pathPrefix` 之下。 */
export const ROUTES = Object.freeze({
  /** OneBot 正向 WebSocket（对端连进来收发） */
  WS: '/ws',
  /** OneBot HTTP API（`POST {action,params,echo}`） */
  API: '/api',
  /** 本插件自己的诊断快照，注册在 **dsh webServer** 上（不是 mode B 那个端口） */
  STATUS: '/status',
})

/** `post_type` 取值。 */
export const POST_TYPE = Object.freeze({
  MESSAGE: 'message',
  NOTICE: 'notice',
  REQUEST: 'request',
  META_EVENT: 'meta_event',
})

/** `message_type` 取值。 */
export const MESSAGE_TYPE = Object.freeze({
  PRIVATE: 'private',
  GROUP: 'group',
})

/** 本插件会发出的 action（与 MeaPet `HUB_ALLOWED_ACTIONS` 取交集，见 ALLOWED_ACTIONS）。 */
export const ACTION = Object.freeze({
  SEND_PRIVATE_MSG: 'send_private_msg',
  SEND_GROUP_MSG: 'send_group_msg',
  SEND_MSG: 'send_msg',
  DELETE_MSG: 'delete_msg',
  GET_STATUS: 'get_status',
  GET_VERSION_INFO: 'get_version_info',
  GET_LOGIN_INFO: 'get_login_info',
})

/**
 * 收到的 action 白名单。
 *
 * 逐条抄自 MeaPet `meapet/onebot/service.py:36-56` 的 `HUB_ALLOWED_ACTIONS`
 * ——**包括我们暂时不代理的那些**。理由：白名单是「对端会发什么」的清单，
 * 不是「我们会实现什么」的清单；不实现的动作要**如实回 failed**，而不是因为
 * 不在白名单里回一个「权限不足」把排查方向带偏（对照 `docs/BRIDGE-CONTRACT.md`
 * §8 关于 `unsupported` 与 `not_implemented` 不能混用的那一段）。
 */
export const ALLOWED_ACTIONS = Object.freeze([
  'send_private_msg', 'send_group_msg', 'send_msg', 'delete_msg',
  'get_msg', 'get_forward_msg', 'get_login_info', 'get_status',
  'get_version_info', 'can_send_image', 'can_send_record',
  'get_friend_list', 'get_group_list', 'get_group_info',
  'get_group_member_info', 'get_group_member_list', 'get_stranger_info',
])

/**
 * 连上上游（mode A）时**原样透传**的动作 = 整张白名单。
 *
 * 为什么不是「只挑我们要用的那七个」：本插件**不是 QQ 实现端**，
 * 自己实现 `get_group_list` / `get_group_member_list` 只会造出第二份会漂移的真相。
 * 白名单里 MeaPet 认的动作，MeaPet 自己能转发，我们就照转。
 * （未连接上游时的降级口径见 `lib/rpc/actions.js`。）
 */
export const PROXIED_ACTIONS = ALLOWED_ACTIONS

/** `status` 字段取值（规范 §2.1）。 */
export const STATUS = Object.freeze({ OK: 'ok', FAILED: 'failed', ASYNC: 'async' })

/** `retcode` 取值（规范 §2.1 列出 0/1/100/102/103/104/200，其余由实现端扩展）。 */
export const RETCODE = Object.freeze({
  OK: 0,
  FAILED: 1,
  MISSING_PARAM: 100,
  BAD_PARAM: 102,
  FORBIDDEN: 103,
  UNAUTHORIZED: 104,
  /** 「调用失败」：上游存在但执行失败。与 101/102/103 不是一件事。 */
  INTERNAL: 200,
  /** 本插件侧的扩展码：请求合法，但本端**尚未实现**（绝不能混进 102）。 */
  NOT_IMPLEMENTED: 501,
})

/** 审批结局白名单。`cancelled` / `unavailable` 是系统语义，不暴露给聊天侧。 */
export const APPROVAL_OUTCOME = Object.freeze({
  ALLOWED_ONCE: 'allowed-once',
  REJECTED: 'rejected',
})

export const APPROVAL_OUTCOME_ALLOWED = Object.freeze([
  APPROVAL_OUTCOME.ALLOWED_ONCE, APPROVAL_OUTCOME.REJECTED,
])

/** 审批验证码长度。 */
export const APPROVAL_CODE_LENGTH = 4

/** 校验码字母表：去掉 I/O/0/1，人工抄码时不容易认错（照抄 astrbot-relay 的口径）。 */
export const APPROVAL_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

/**
 * 非文本消息段在喂给模型的纯文本里的占位符。
 *
 * 逐条抄自 MeaPet `meapet/onebot/segments.py:16-35` 的 `SEGMENT_PLACEHOLDERS`：
 * 模型看不懂图片二进制，但「这里原本有一张图」这条语义必须保留，
 * 否则模型会把「[图片] 这是什么」当成一句普通的提问。
 */
export const SEGMENT_PLACEHOLDERS = Object.freeze({
  image: '[图片]',
  face: '[表情]',
  record: '[语音]',
  video: '[视频]',
  rps: '[猜拳]',
  dice: '[骰子]',
  shake: '[窗口抖动]',
  poke: '[戳一戳]',
  anonymous: '[匿名消息]',
  share: '[链接分享]',
  contact: '[推荐联系人]',
  location: '[位置]',
  music: '[音乐分享]',
  forward: '[合并转发]',
  node: '[转发节点]',
  xml: '[XML消息]',
  json: '[JSON消息]',
  reply: '',
})

// ──────────────────────────────────────────────────────────────────
// 消息段：规范化 / CQ 码 / 纯文本
// ──────────────────────────────────────────────────────────────────

const CQ_PATTERN = /\[CQ:([a-zA-Z0-9_]+)((?:,[^\]]*)?)\]/g

/**
 * CQ 码转义解码。
 *
 * ⚠️ 顺序有讲究：`&amp;` **必须最后**解。先解它的话，`&amp;#91;`
 * （用户真的想发一个字符串 `&#91;`）会被二次解码成 `[`，CQ 码就串味了。
 * 这条与 MeaPet `meapet/onebot/cq.py` 同口径。
 */
export function unescapeCq(text) {
  return String(text ?? '')
    .replace(/&#91;/g, '[')
    .replace(/&#93;/g, ']')
    .replace(/&#44;/g, ',')
    .replace(/&amp;/g, '&')
}

/** CQ 码转义编码（反向）。本插件默认发明文，此函数供测试与扩展使用。 */
export function escapeCq(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/\[/g, '&#91;')
    .replace(/\]/g, '&#93;')
    .replace(/,/g, '&#44;')
}

/** 解析 `[CQ:type,k=v,...]` 串里的参数段（已含前导逗号）。 */
function parseCqParams(raw) {
  const data = {}
  const body = String(raw ?? '').replace(/^,/, '')
  if (body === '') return data
  for (const pair of body.split(',')) {
    if (pair === '') continue
    const eq = pair.indexOf('=')
    if (eq < 0) {
      // 无 `=` 的参数（少数实现端会发）：值记为 true，不丢信息。
      data[unescapeCq(pair)] = true
      continue
    }
    data[unescapeCq(pair.slice(0, eq))] = unescapeCq(pair.slice(eq + 1))
  }
  return data
}

/**
 * CQ 码字符串 → 消息段数组。
 *
 * 只有一个方向（字符串 → 段）：本插件**发送**时一律发明文纯文本
 * （见 `README.md` 的「为什么发送走明文」），所以不需要 build_cq。
 */
export function parseCq(text) {
  const src = String(text ?? '')
  const out = []
  let last = 0
  CQ_PATTERN.lastIndex = 0
  let match
  while ((match = CQ_PATTERN.exec(src)) !== null) {
    if (match.index > last) {
      const literal = unescapeCq(src.slice(last, match.index))
      if (literal !== '') out.push({ type: 'text', data: { text: literal } })
    }
    out.push({ type: match[1], data: parseCqParams(match[2]) })
    last = match.index + match[0].length
  }
  if (last < src.length) {
    const literal = unescapeCq(src.slice(last))
    if (literal !== '') out.push({ type: 'text', data: { text: literal } })
  }
  return out
}

/** 把任意 `data` 收成普通对象（数组 / null / 非对象一律视为空）。 */
function plainData(value) {
  if (value === null || value === undefined || typeof value !== 'object' || Array.isArray(value)) return {}
  return { ...value }
}

/**
 * 把 `message` 字段规范化为消息段数组。
 *
 * 规范 §3 说 `message` 允许三种形态：字符串（CQ 码）、消息段数组、单个消息段对象。
 * MeaPet `meapet/onebot/segments.py:55-77` 三种都收，这里逐条对齐。
 */
export function normalizeMessage(value) {
  if (value === null || value === undefined) return []
  if (typeof value === 'string') return parseCq(value)
  if (Array.isArray(value)) {
    const out = []
    for (const item of value) {
      if (typeof item === 'string') {
        out.push(...parseCq(item))
      } else if (item !== null && typeof item === 'object' && typeof item.type === 'string' && item.type !== '') {
        out.push({ type: item.type, data: plainData(item.data) })
      }
    }
    return out
  }
  if (typeof value === 'object') {
    if (typeof value.type === 'string' && value.type !== '') {
      return [{ type: value.type, data: plainData(value.data) }]
    }
    return []
  }
  return []
}

/**
 * 消息段 → 喂给模型的纯文本。
 *
 * @param {Array<{type: string, data: object}>} segments
 * @param {{ selfId?: string, stripAtSelf?: boolean }} [options]
 *   `stripAtSelf` 为真时，命中 `selfId`（或 `all`）的 `at` 段**不产出文本**：
 *   群聊里为了叫醒机器人而点的那一下不该进提示词，否则模型会以为用户在
 *   强调「@某人」。默认 true。
 */
export function segmentsToText(segments, options = {}) {
  const selfId = String(options.selfId ?? '')
  const stripAtSelf = options.stripAtSelf !== false
  const parts = []
  for (const segment of Array.isArray(segments) ? segments : []) {
    const type = String(segment?.type ?? '')
    if (type === 'text') {
      parts.push(String(segment?.data?.text ?? ''))
      continue
    }
    if (type === 'at') {
      const qq = String(segment?.data?.qq ?? '')
      if (stripAtSelf && (qq === 'all' || (selfId !== '' && qq === selfId))) continue
      parts.push(qq === 'all' ? '@全体成员' : `@${qq}`)
      continue
    }
    parts.push(SEGMENT_PLACEHOLDERS[type] ?? `[${type}]`)
  }
  return parts.join('').trim()
}

/**
 * 消息里是否 `@` 了机器人（`@全体成员` 也算）。
 *
 * 逐条对齐 MeaPet `meapet/onebot/segments.py:99-108` 的 `is_at_self()`。
 * `selfId` 为空时只认 `all`——空 selfId 不该把「@任何人」都当成「@了我」。
 */
export function hasAtSelf(segments, selfId) {
  const wanted = String(selfId ?? '').trim()
  for (const segment of Array.isArray(segments) ? segments : []) {
    if (String(segment?.type ?? '') !== 'at') continue
    const qq = String(segment?.data?.qq ?? '')
    if (qq === 'all' || (wanted !== '' && qq === wanted)) return true
  }
  return false
}

/** 从事件里取出机器人自己的 QQ 号（`self_id`）。 */
export function selfIdOf(event) {
  return digitsOf(event?.self_id)
}

// ──────────────────────────────────────────────────────────────────
// 会话键
// ──────────────────────────────────────────────────────────────────

/** 把 QQ 号（数字或字符串）收成十进制字符串；不合法返回 null。 */
export function digitsOf(value) {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || value < 0) return null
    return String(value)
  }
  if (typeof value === 'string') {
    const text = value.trim()
    return /^\d+$/.test(text) ? text : null
  }
  return null
}

/** 会话键的前缀 → `message_type`。 */
export const CONVERSATION_PREFIX = Object.freeze({
  private: MESSAGE_TYPE.PRIVATE,
  group: MESSAGE_TYPE.GROUP,
})

/**
 * 事件 → 会话键：`private:<user_id>` / `group:<group_id>`。
 *
 * 非消息事件、缺目标 id、`message_type` 认不出来 → `null`（调用方忽略该事件）。
 * 判据取 `message_type` 而不是「有没有 group_id」：规范里群消息必带 `group_id`，
 * 但私聊事件在少数实现端会**残留**一个 `group_id`，按「有 group_id 就是群」
 * 会把私聊折进群里（astrbot-relay 的契约 §2.1 记录过同型取舍）。
 */
export function conversationKeyOf(event) {
  if (event === null || typeof event !== 'object') return null
  if (String(event.post_type ?? '').trim() !== POST_TYPE.MESSAGE) return null
  const type = String(event.message_type ?? '').trim().toLowerCase()
  if (type === MESSAGE_TYPE.GROUP) {
    const groupId = digitsOf(event.group_id)
    return groupId === null ? null : `${MESSAGE_TYPE.GROUP}:${groupId}`
  }
  if (type === MESSAGE_TYPE.PRIVATE) {
    const userId = digitsOf(event.user_id)
    return userId === null ? null : `${MESSAGE_TYPE.PRIVATE}:${userId}`
  }
  return null
}

/**
 * 会话键 → `{ messageType, sessionId }`。
 *
 * ⚠️ **不要**照抄 astrbot-relay 的 `parseConversation`：那里按
 * `{platform_id}:{MessageType}:{session_id}` **三段**切（第 3 段才是 sessionId），
 * 而本插件的键只有**两段** `private:<id>`。抄错会让 `{sessionId}` 恒为空、
 * 会话标题变成「喵桥 · private/」，而这是最难当场发现的一类错。
 */
export function parseConversation(conversation) {
  const text = String(conversation ?? '')
  const at = text.indexOf(':')
  if (at < 0) return { messageType: text, sessionId: '' }
  return { messageType: text.slice(0, at), sessionId: text.slice(at + 1) }
}

/**
 * 渲染 DSH 会话标题（反向定位：在 Web UI 的会话列表里认出这条会话来自哪个 QQ 会话）。
 *
 * 占位符：`{messageType}` `{sessionId}` `{conversation}` `{bridge}`。
 * **未知占位符原样保留**（不替换成空串）——配置写错时要看得见，
 * 静默产出空标题等于定位功能被做没。
 */
export function renderSessionTitle(template, conversation) {
  const { messageType, sessionId } = parseConversation(conversation)
  return String(template ?? '').replace(/\{(\w+)\}/g, (whole, key) => {
    switch (key) {
      case 'messageType': return messageType
      case 'sessionId': return sessionId
      case 'conversation': return String(conversation ?? '')
      case 'bridge': return PLUGIN_NAME
      default: return whole
    }
  })
}

// ──────────────────────────────────────────────────────────────────
// API 帧
// ──────────────────────────────────────────────────────────────────

/** 进程内自增的 echo 计数器。 */
let echoCounter = 0

/**
 * 生成唯一 echo。
 *
 * 规范 §2.1：`echo` 用于把响应关回请求。**不得**假设响应按顺序到达，
 * 所以关联只能靠 echo，而 echo 必须唯一。
 * 时钟 + 进程内计数器足够（MeaPet 侧用的是 `meapet-<n>`，同理）。
 */
export function newEcho(prefix = 'dsh') {
  echoCounter += 1
  return `${prefix}-${Date.now().toString(36)}-${echoCounter.toString(36)}`
}

/** 造一个 API 调用帧：`{action, params, echo}`。 */
export function makeActionFrame(action, params, echo = newEcho()) {
  return {
    action: String(action ?? ''),
    params: params !== null && typeof params === 'object' && !Array.isArray(params) ? { ...params } : {},
    echo: String(echo),
  }
}

/** 造一个 API 响应帧。`data` 缺省写 null（规范 §8.1 的失败示例就是 `data: null`）。 */
export function makeResponse({ status = STATUS.FAILED, retcode = RETCODE.FAILED, data = null, echo = null, error } = {}) {
  const frame = {
    status: String(status),
    retcode: Number.isFinite(Number(retcode)) ? Number(retcode) : RETCODE.FAILED,
    data: data === undefined ? null : data,
    echo: echo === undefined ? null : echo,
  }
  // `error` 是 MeaPet 侧的扩展字段（`types.ApiResponse`）。本插件带上它，
  // 因为它对排查「为什么这次发送失败」是唯一的人话说明；对端忽略未知字段即可。
  if (typeof error === 'string' && error !== '') frame.error = error
  return frame
}

/**
 * 判断一帧是「API 响应」还是「事件上报」。
 *
 * 逐字对齐 MeaPet `meapet/onebot/events.py:116-122`：
 * 带 `echo`/`retcode`/`status` **且没有** `post_type`。
 * `post_type` 那一条是硬判据：某实现端的事件里恰好有个 `status` 字段时，
 * 少了它就会把事件当响应吞掉（症状是「消息事件永远收不到」）。
 */
export function isApiResponse(payload) {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) return false
  if (payload.post_type) return false
  return 'retcode' in payload || 'status' in payload || 'echo' in payload
}

/** 响应是否成功（`status === 'ok'` 且 `retcode === 0`）。 */
export function isOkResponse(payload) {
  return String(payload?.status ?? '') === STATUS.OK && Number(payload?.retcode) === RETCODE.OK
}

/** 会话键 → 发送一条文本所需的 `{action, params}`。 */
export function targetOf(conversation, text) {
  const { messageType, sessionId } = parseConversation(conversation)
  const id = numericId(sessionId)
  if (messageType === MESSAGE_TYPE.GROUP) {
    return { action: ACTION.SEND_GROUP_MSG, params: { group_id: id, message: String(text ?? '') } }
  }
  return { action: ACTION.SEND_PRIVATE_MSG, params: { user_id: id, message: String(text ?? '') } }
}

/** 十进制字符串 → 数字（安全范围内）或原字符串。OneBot 期望 `user_id` 是数字。 */
export function numericId(value) {
  const text = digitsOf(value)
  if (text === null) return value
  const num = Number(text)
  return Number.isSafeInteger(num) ? num : text
}

// ──────────────────────────────────────────────────────────────────
// 文本切分
// ──────────────────────────────────────────────────────────────────

/** 优先在这里断：空行（段落边界）。 */
const HARD_BREAKS = new Set(['\n'])
/** 次选：句末标点。 */
const SENTENCE_BREAKS = new Set(['。', '！', '？', '…', '.', '!', '?', ';', '；'])
/** 再次：顿挫与空格。 */
const SOFT_BREAKS = new Set(['，', ',', '、', ' ', '）', ')', '」', '』', '"'])

/**
 * 在 `chars` 里挑一个断点（返回**码点下标**）。
 *
 * 从后往前找优先级最高的边界；但断点必须落在 `max` 的**后半段**，
 * 否则「第一句特别短」的长文会被切成很多碎句（比硬切更难读）。
 */
function chooseCut(chars, max) {
  const limit = Math.min(max, chars.length)
  const floor = Math.max(1, Math.floor(limit / 2))

  for (let i = limit - 1; i >= floor; i -= 1) {
    if (chars[i - 1] === '\n' && chars[i] === '\n') return i + 1
  }
  for (let i = limit - 1; i >= floor; i -= 1) {
    if (HARD_BREAKS.has(chars[i - 1])) return i
  }
  for (let i = limit - 1; i >= floor; i -= 1) {
    if (SENTENCE_BREAKS.has(chars[i - 1])) return i
  }
  for (let i = limit - 1; i >= floor; i -= 1) {
    if (SOFT_BREAKS.has(chars[i - 1])) return i
  }
  return limit
}

/**
 * 把长文本按 `limit` 个**码点**切段。
 *
 * 用码点（`Array.from`）而不是 `String.slice`：后者按 UTF-16 码元切，
 * 会把 emoji（代理对）劈成两个孤立半边，发出去是一个替换字符。
 * 空串返回 `[]`（调用方据此跳过发送）。
 */
export function chunkText(text, limit) {
  const chars = Array.from(String(text ?? ''))
  const max = Math.max(1, Math.trunc(Number(limit)) || 1)
  if (chars.length === 0) return []
  const out = []
  let rest = chars
  while (rest.length > max) {
    const cut = chooseCut(rest, max)
    const piece = rest.slice(0, cut).join('')
    if (piece !== '') out.push(piece)
    rest = rest.slice(cut)
  }
  const tail = rest.join('')
  if (tail !== '') out.push(tail)
  return out
}

// ──────────────────────────────────────────────────────────────────
// 聊天侧指令（审批回执等）
// ──────────────────────────────────────────────────────────────────

/**
 * 解析审批回执指令。
 *
 * 只认**明确命令 + 一次性验证码**，不接受自然的「是 / 否」：
 * 群聊里任何人打一句「好」都可能把审批放行，那是把审批做成摆设
 * （astrbot-relay 的契约 §7.2 第 5 条就是这条取舍）。
 *
 * 接受：`/approve K7Q2`、`/reject K7Q2`、`/dsh approve K7Q2`、`/dsh reject K7Q2`。
 */
export function parseApprovalCommand(text) {
  const raw = String(text ?? '').trim()
  const match = /^\/(?:dsh\s+)?(approve|reject)\s+([A-Za-z0-9]{2,16})$/.exec(raw)
  if (match === null) return null
  return {
    kind: match[1].toLowerCase() === 'approve' ? 'approve' : 'reject',
    code: match[2].toUpperCase(),
  }
}

/** 解析本插件的本地指令（不投给 agent 的那些）。 */
export function parseLocalCommand(text) {
  const raw = String(text ?? '').trim()
  const approval = parseApprovalCommand(raw)
  if (approval !== null) return { kind: approval.kind, code: approval.code }
  const bare = /^\/(help|status)$/i.exec(raw)
  if (bare !== null) return { kind: bare[1].toLowerCase() }
  // 其余 `/xxx` 一律**照常投给 agent**：聊天里的斜杠命令大多是想让模型做的事
  // （`/clear`、`/summarize`…），本插件不该替 agent 决定哪些斜杠是「我的」。
  return null
}

/**
 * 两个字符串的**码点**级最长公共前缀长度。
 *
 * 用途：`streamMode: 'preview'` 下「已发出去的文本」与「权威最终文本」的切点。
 * 用码点而不是 UTF-16 码元，理由与 `chunkText` 相同：代理对劈开会算错长度。
 */
export function commonPrefixLength(a, b) {
  const left = Array.from(String(a ?? ''))
  const right = Array.from(String(b ?? ''))
  const limit = Math.min(left.length, right.length)
  let i = 0
  while (i < limit && left[i] === right[i]) i += 1
  return i
}

// ──────────────────────────────────────────────────────────────────
// 状态快照
// ──────────────────────────────────────────────────────────────────

/** 生成一个 DSH 会话 id（`ob-` 前缀 = OneBot 来源，便于在会话列表里认出来）。 */
export function newSessionId(now = Date.now()) {
  const random = Math.random().toString(16).slice(2, 10)
  return `ob-${now.toString(36)}-${random}`
}

/** join 基址与路径：恰好一个斜杠，且无尾斜杠。 */
export function joinPath(prefix, path) {
  const left = String(prefix ?? '').replace(/\/+$/, '')
  const right = String(path ?? '').replace(/^\/+/, '')
  return `${left}/${right}`
}
