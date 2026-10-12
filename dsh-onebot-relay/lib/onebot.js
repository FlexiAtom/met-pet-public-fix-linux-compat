/**
 * dsh-onebot-relay · OneBot v11 传输层
 *
 * 两种角色都在这里，共用同一套帧形状（`lib/contract.js`）：
 *
 * - **mode A `OneBotClient`** —— 本插件当 OneBot **客户端**，拨号到 MeaPet 的
 *   hub（或任何 OneBot 正向 WS）。同一条连接上：收事件、发 `{action,params,echo}`、
 *   按 `echo` 关响应。
 * - **mode B `OneBotServer`** —— 本插件当 OneBot **服务端**，自己起一个
 *   `http.Server`，暴露 `{pathPrefix}/ws`（正向 WS，含 upgrade 鉴权）与
 *   `{pathPrefix}/api`（HTTP API，`POST {action,params,echo}`）。
 *
 * ## 三个已核实的硬事实（写错就废）
 *
 * 1. **`ctx.webServer.register` 注册的路由没有任何框架级鉴权**，而且它的 handler
 *    签名是 `(request, response)`，**没有 WebSocket upgrade 的位置**。
 *    所以 mode B 必须自己持有 `http.Server`（这正是 `serverPort` 存在的理由），
 *    鉴权也必须自己写（`authorizeToken`）。
 * 2. **OneBot 鉴权有两种形态，且规范要求「头优先、其次 query」**
 *    （`onebotv11.txt` §2.5）。`extractToken` 逐条实现该顺序——
 *    实现端两种都可能只认一种（MeaPet 侧 `server.py:_authorized` 就是头优先 + query 兜底）。
 * 3. **响应不得按顺序关联，只能靠 `echo`**（规范 §2.1）。`call()` 用唯一 echo
 *    建表，收到同 echo 的帧才结算；孤儿响应的归宿是「记一条 debug 然后丢掉」，
 *    **绝不能**当成事件派发出去（那会凭空造出一个 `post_type` 为空的假事件）。
 *
 * 依赖只有 `ws`（+ `node:*` 内置）。没有 dsh 依赖 → 可以在普通 Node 下整条跑穿
 * （`test/onebot-server.test.mjs` / `test/onebot-client.test.mjs`）。
 */
import { createHash, timingSafeEqual as nodeTimingSafeEqual } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { createServer } from 'node:http'
import WebSocket, { WebSocketServer } from 'ws'
import {
  ROUTES, RETCODE, STATUS,
  isApiResponse, joinPath, makeActionFrame, makeResponse, newEcho,
} from './contract.js'

/** mode B 的 HTTP 请求体上限。8 MiB 与 MeaPet `server.py:MAX_BODY_BYTES` 同值。 */
export const MAX_BODY_BYTES = 8 * 1024 * 1024

/**
 * 超限后**继续读并丢弃**的上限（不再进内存）。
 *
 * 为什么不是超限就立刻断开：客户端还在上传时就把连接掐掉，它拿到的是
 * ECONNRESET 而不是我们想给的 413（实测）。把超出的部分读掉、让请求正常结束，
 * 客户端才能读到那条 413。超过这个上限才认定是恶意流，直接断开。
 */
export const MAX_DRAIN_BYTES = MAX_BODY_BYTES * 2

/** 鉴权失败时的 WS 关闭码（规范未定；1008 = policy violation，与 MeaPet 同值）。 */
export const WS_CLOSE_POLICY_VIOLATION = 1008

/** 解析请求 URL 的查询串。node 的 http 请求不带 base，需要一个占位 origin。 */
export function urlOf(request) {
  return new URL(request?.url ?? '/', 'http://onebot-relay.internal')
}

/**
 * 定长比较。
 *
 * `node:crypto` 的 `timingSafeEqual` 要求等长入参，因此先把两者各自 sha256 到
 * 固定 32 字节再比较——比较时间与输入长度、内容前缀都无关。
 * （不这么写就只能先比长度，那一步本身泄露长度。）
 */
export function timingSafeTokenEqual(a, b) {
  const ha = createHash('sha256').update(String(a ?? ''), 'utf8').digest()
  const hb = createHash('sha256').update(String(b ?? ''), 'utf8').digest()
  return nodeTimingSafeEqual(ha, hb)
}

/** 从请求头里取 `Authorization: Bearer <token>`（头名大小写不敏感）。 */
export function bearerTokenOf(headers) {
  if (headers === null || typeof headers !== 'object') return ''
  let raw = ''
  for (const [key, value] of Object.entries(headers)) {
    if (String(key).toLowerCase() === 'authorization') {
      raw = Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')
      break
    }
  }
  if (!raw.toLowerCase().startsWith('bearer ')) return ''
  return raw.slice('bearer '.length).trim()
}

/**
 * 按规范 §2.5 的顺序取出调用方声明的 token：
 * `Authorization: Bearer` 优先，其次 `?access_token=`，都没有则空串。
 */
export function extractToken(request) {
  const fromHeader = bearerTokenOf(request?.headers)
  if (fromHeader !== '') return fromHeader
  try {
    return String(urlOf(request).searchParams.get('access_token') ?? '')
  } catch {
    return ''
  }
}

/**
 * 鉴权。
 *
 * ⚠️ token 为空字符串时**一律拒绝**（不是「不鉴权」）。
 * OneBot 规范里「token 为空表示不鉴权」是**实现端主动选择**的宽松模式；
 * 本插件是 DSH 的一侧，把「忘配 token」解释成「对全世界开放」是不能接受的默认。
 * 配置层已经把空 token 判为加载失败（`lib/config.js`），这里是第二道闸。
 */
export function authorizeToken(request, token) {
  const expected = String(token ?? '')
  if (expected === '') return false
  const supplied = extractToken(request)
  if (supplied === '') return false
  return timingSafeTokenEqual(supplied, expected)
}

/**
 * 把 token 附到 WS URL 的 query 上（`?access_token=`）。
 *
 * 用于 mode A：某些实现端只认 query。URL 里已经有 `access_token` 时**不覆盖**，
 * 尊重配置者显式写在 URL 里的值。
 */
export function withAccessToken(rawUrl, token) {
  const text = String(rawUrl ?? '')
  if (String(token ?? '') === '') return text
  const url = new URL(text)
  if (!url.searchParams.has('access_token')) url.searchParams.set('access_token', token)
  return url.toString()
}

// ──────────────────────────────────────────────────────────────────
// mode A：OneBot 客户端
// ──────────────────────────────────────────────────────────────────

/**
 * OneBot v11 客户端（正向 / 反向 WS 都适用：都用同一条连接收发）。
 *
 * 事件与生命周期用 `EventEmitter` 暴露：
 *   - `'event'` → `(payload)` 收到一条**非响应**帧（事件上报）
 *   - `'open'` / `'close'` → `({ attempts })` / `({ code, reason, attempts })`
 *   - `'frame'` → `(payload)` 所有解析成功的帧（调试用）
 *
 * **刻意不 emit `'error'`**：`EventEmitter` 在没有 `'error'` 监听者时会抛异常，
 * 而网络错误是**常态**（MeaPet 重启、端口没起）。错误一律进 logger。
 */
export class OneBotClient extends EventEmitter {
  #url
  #token
  #tokenInQuery
  #heartbeatMs
  #reconnectInitialMs
  #reconnectMaxMs
  #callTimeoutMs
  #log
  #tag

  #ws = null
  #stopped = true
  #attempts = 0
  #reconnectTimer = null
  #pingTimer = null
  #pongOk = true
  #pending = new Map()
  #waiters = []
  #lastError = ''
  #openedCount = 0

  constructor({
    url,
    token = '',
    tokenInQuery = false,
    heartbeatMs = 15_000,
    reconnectInitialMs = 1000,
    reconnectMaxMs = 30_000,
    callTimeoutMs = 15_000,
    logger = null,
    tag = '[dsh-onebot-relay]',
  }) {
    super()
    this.#url = String(url ?? '')
    this.#token = String(token ?? '')
    this.#tokenInQuery = tokenInQuery === true
    this.#heartbeatMs = Math.max(1000, Math.trunc(Number(heartbeatMs) || 15_000))
    this.#reconnectInitialMs = Math.max(100, Math.trunc(Number(reconnectInitialMs) || 1000))
    this.#reconnectMaxMs = Math.max(this.#reconnectInitialMs, Math.trunc(Number(reconnectMaxMs) || 30_000))
    this.#callTimeoutMs = Math.max(1000, Math.trunc(Number(callTimeoutMs) || 15_000))
    this.#log = logger
    this.#tag = tag
  }

  get connected() {
    return this.#ws !== null && this.#ws.readyState === WebSocket.OPEN
  }

  get url() {
    return this.#url
  }

  get pendingCount() {
    return this.#pending.size
  }

  /** 诊断快照，供 `/status` 与日志使用。 */
  snapshot() {
    return {
      url: this.#url,
      connected: this.connected,
      attempts: this.#attempts,
      opened: this.#openedCount,
      pending: this.#pending.size,
      lastError: this.#lastError,
    }
  }

  #warn(message) {
    this.#log?.warn?.(`${this.#tag} ${message}`)
  }

  #info(message) {
    this.#log?.info?.(`${this.#tag} ${message}`)
  }

  #debug(message) {
    this.#log?.debug?.(`${this.#tag} ${message}`)
  }

  /** 开始连接（幂等：重复调用不会开出第二条连接）。 */
  start() {
    if (!this.#stopped) return
    this.#stopped = false
    this.#connect()
  }

  /** 停止：关连接、清定时器、把在途调用全部 reject。 */
  stop() {
    if (this.#stopped && this.#ws === null) return
    this.#stopped = true
    this.#clearReconnect()
    this.#stopPing()
    const ws = this.#ws
    this.#ws = null
    this.#failPending(new Error('OneBot 连接已关闭'))
    if (ws !== null) {
      try { ws.removeAllListeners() } catch { /* 忽略 */ }
      try { ws.close(1000, 'plugin unload') } catch { /* 忽略 */ }
      // `ws.close()` 是优雅关闭，对端不回应就一直挂着；这里再补一刀 terminate，
      // 否则插件卸载时会留下一个不肯退出的 socket（拖住进程退出）。
      try { ws.terminate() } catch { /* 忽略 */ }
    }
  }

  #clearReconnect() {
    if (this.#reconnectTimer !== null) {
      clearTimeout(this.#reconnectTimer)
      this.#reconnectTimer = null
    }
  }

  #stopPing() {
    if (this.#pingTimer !== null) {
      clearInterval(this.#pingTimer)
      this.#pingTimer = null
    }
  }

  #failPending(error) {
    const pending = [...this.#pending.values()]
    this.#pending.clear()
    for (const entry of pending) {
      clearTimeout(entry.timer)
      try { entry.reject(error) } catch { /* 调用方自己的事 */ }
    }
  }

  #startPing(ws) {
    this.#stopPing()
    this.#pongOk = true
    this.#pingTimer = setInterval(() => {
      if (ws.readyState !== WebSocket.OPEN) return
      if (!this.#pongOk) {
        // 一个 ping 周期内没等到 pong：链路已经半死（对端进程没了但 TCP 还在）。
        // 必须 terminate 而不是 close——close 会等一个永不来的回应。
        this.#warn('心跳无回应，主动断开连接')
        ws.terminate()
        return
      }
      this.#pongOk = false
      try { ws.ping() } catch { /* 下一次 tick 会走 terminate 分支 */ }
    }, this.#heartbeatMs)
    this.#pingTimer.unref?.()
  }

  #connect() {
    if (this.#stopped) return
    const target = this.#tokenInQuery ? withAccessToken(this.#url, this.#token) : this.#url
    const headers = this.#token === '' ? undefined : { Authorization: `Bearer ${this.#token}` }
    let ws
    try {
      ws = new WebSocket(target, { headers, handshakeTimeout: this.#callTimeoutMs })
    } catch (error) {
      this.#lastError = String(error?.message ?? error)
      this.#scheduleReconnect()
      return
    }
    this.#ws = ws

    ws.on('open', () => {
      if (this.#stopped) {
        try { ws.terminate() } catch { /* 忽略 */ }
        return
      }
      this.#attempts = 0
      this.#openedCount += 1
      this.#lastError = ''
      this.#startPing(ws)
      this.#info(`已连接 ${this.#url}`)
      for (const waiter of this.#waiters.splice(0)) {
        clearTimeout(waiter.timer)
        waiter.resolve()
      }
      this.emit('open', { url: this.#url, attempts: this.#openedCount })
    })

    ws.on('message', (raw) => this.#onFrame(raw))

    ws.on('pong', () => { this.#pongOk = true })

    ws.on('error', (error) => {
      // 这里**不** emit 'error'（见类注释）。连接失败最常见的原因就是
      // 「MeaPet 还没起」，把它做成一条 warn 而不是异常，是让进程活着的前提。
      this.#lastError = String(error?.message ?? error)
      this.#warn(`连接出错：${this.#lastError}`)
    })

    ws.on('close', (code, reason) => {
      this.#stopPing()
      this.#failPending(new Error(`OneBot 连接已断开（code=${code}）`))
      for (const waiter of this.#waiters.splice(0)) {
        clearTimeout(waiter.timer)
        waiter.reject(new Error(this.#lastError || `连接已断开（code=${code}）`))
      }
      this.emit('close', { code, reason: String(reason ?? ''), attempts: this.#attempts })
      if (!this.#stopped) this.#scheduleReconnect()
    })
  }

  /**
   * 指数退避 + 抖动。
   *
   * 抖动是必须的：MeaPet 与 DSH 常常一起重启，没有抖动时两边会以**同一个**
   * 周期反复对撞（都是 1s、2s、4s…），连接窗口永远错开不了。
   */
  #scheduleReconnect() {
    this.#clearReconnect()
    this.#attempts += 1
    const base = Math.min(this.#reconnectMaxMs, this.#reconnectInitialMs * 2 ** (this.#attempts - 1))
    const delay = Math.round(base * (0.5 + Math.random() * 0.5))
    this.#warn(`将在 ${delay}ms 后重连（第 ${this.#attempts} 次）`)
    this.#reconnectTimer = setTimeout(() => {
      this.#reconnectTimer = null
      this.#connect()
    }, delay)
    this.#reconnectTimer.unref?.()
  }

  #onFrame(raw) {
    let payload
    try {
      payload = JSON.parse(typeof raw === 'string' ? raw : raw.toString('utf8'))
    } catch {
      this.#warn('收到无法解析的帧，已丢弃')
      return
    }
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      this.#warn('收到非对象帧，已丢弃')
      return
    }
    this.emit('frame', payload)

    if (isApiResponse(payload)) {
      // ⚠️ 硬事实第 3 条：响应只能靠 echo 关联，且**绝不能**当事件派发。
      const echo = payload.echo === undefined || payload.echo === null ? '' : String(payload.echo)
      const entry = this.#pending.get(echo)
      if (entry === undefined) {
        this.#debug(`孤儿响应（echo=${JSON.stringify(echo)}），已丢弃`)
        return
      }
      this.#pending.delete(echo)
      clearTimeout(entry.timer)
      entry.resolve(payload)
      return
    }

    this.emit('event', payload)
  }

  /** 等连接建立（用于「先连上、再投递」这类顺序要求）。 */
  whenConnected(timeoutMs = 10_000) {
    if (this.connected) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject, timer: null }
      waiter.timer = setTimeout(() => {
        const index = this.#waiters.indexOf(waiter)
        if (index >= 0) this.#waiters.splice(index, 1)
        reject(new Error(`等待 OneBot 连接超时（${timeoutMs}ms）`))
      }, Math.max(1, Math.trunc(timeoutMs)))
      waiter.timer.unref?.()
      this.#waiters.push(waiter)
    })
  }

  /**
   * 发一次 API 调用并等同 echo 的响应。
   *
   * **协议层失败不抛错**：`status: 'failed'` 是 OneBot 的正常应答形态
   * （规范 §8.4），把它变成异常会让每个调用点都要写 try/catch 去区分
   * 「对端拒绝」与「线路断了」——这两件事的处理方式完全不同。
   * 因此：能拿到响应就 resolve（调用方自己看 `status`/`retcode`），
   * **只有**「连不上 / 发送失败 / 等超时」才 reject。
   */
  async call(action, params = {}, { timeoutMs } = {}) {
    if (!this.connected) throw new Error(`OneBot 连接未建立，无法调用 ${action}`)
    const echo = newEcho('dsh')
    const frame = makeActionFrame(action, params, echo)
    const wait = Math.max(1000, Math.trunc(Number(timeoutMs) || this.#callTimeoutMs))
    const ws = this.#ws
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(echo)
        reject(new Error(`API 调用超时（${action}, ${wait}ms）`))
      }, wait)
      timer.unref?.()
      this.#pending.set(echo, { resolve, reject, timer, action })
      try {
        ws.send(JSON.stringify(frame), (error) => {
          if (error === undefined || error === null) return
          const entry = this.#pending.get(echo)
          if (entry === undefined) return
          this.#pending.delete(echo)
          clearTimeout(entry.timer)
          entry.reject(error)
        })
      } catch (error) {
        this.#pending.delete(echo)
        clearTimeout(timer)
        reject(error)
      }
    })
  }
}

// ──────────────────────────────────────────────────────────────────
// mode B：OneBot 服务端
// ──────────────────────────────────────────────────────────────────

/**
 * OneBot v11 服务端（正向 WS + HTTP API）。
 *
 * - `{pathPrefix}/ws`  — WebSocket，事件下行 + API 上行，**upgrade 前**先鉴权
 * - `{pathPrefix}/api` — `POST {action,params,echo}`，响应形状与 WS 完全一致
 * - `{pathPrefix}/status` — 只读诊断快照（GET，同样要鉴权）
 *
 * `onCall(action, params, meta)` 由调用方注入，**必须**返回 `{status, retcode, data}`
 * （多出的 `error` 字段会被带上）。把它做成注入点而不是内部写死，
 * 有两个理由：一是传输层不该知道 dsh 的事，二是**它让协议本身可以在没有 DSH、
 * 没有 MeaPet 的情况下被完整测试**（`scripts/self-test.mjs` 就是这么跑的）。
 */
export class OneBotServer {
  #host
  #port
  #pathPrefix
  #token
  #heartbeatMs
  #onCall
  #log
  #tag

  #http = null
  #wss = null
  #clients = new Set()
  #pingTimer = null
  #boundPort = 0
  #requestsServed = 0
  #eventsPushed = 0
  #authFailures = 0

  constructor({
    host = '127.0.0.1',
    port = 6721,
    pathPrefix = '/onebot-relay',
    token = '',
    heartbeatMs = 15_000,
    onCall,
    logger = null,
    tag = '[dsh-onebot-relay]',
  }) {
    if (typeof onCall !== 'function') {
      throw new Error('OneBotServer: onCall(action, params, meta) 是必填的')
    }
    this.#host = String(host ?? '127.0.0.1')
    this.#port = Number(port) || 0
    this.#pathPrefix = String(pathPrefix ?? '/onebot-relay').replace(/\/+$/, '')
    this.#token = String(token ?? '')
    this.#heartbeatMs = Math.max(1000, Math.trunc(Number(heartbeatMs) || 15_000))
    this.#onCall = onCall
    this.#log = logger
    this.#tag = tag
  }

  get wsPath() { return joinPath(this.#pathPrefix, ROUTES.WS) }
  get apiPath() { return joinPath(this.#pathPrefix, ROUTES.API) }
  get statusPath() { return joinPath(this.#pathPrefix, ROUTES.STATUS) }
  /** 实际监听端口（配置 0 时是系统分配的端口）。 */
  get port() { return this.#boundPort }
  get clientCount() { return this.#clients.size }

  snapshot() {
    return {
      listening: this.#http !== null,
      host: this.#host,
      port: this.#boundPort,
      wsPath: this.wsPath,
      apiPath: this.apiPath,
      clients: this.#clients.size,
      requestsServed: this.#requestsServed,
      eventsPushed: this.#eventsPushed,
      authFailures: this.#authFailures,
    }
  }

  #warn(message) {
    this.#log?.warn?.(`${this.#tag} ${message}`)
  }

  #info(message) {
    this.#log?.info?.(`${this.#tag} ${message}`)
  }

  /** 启动并 resolve 出实际绑定信息。已在监听则直接返回现状（幂等）。 */
  async start() {
    if (this.#http !== null) return this.address()
    const server = createServer((request, response) => {
      this.#handleHttp(request, response).catch((error) => {
        this.#warn(`HTTP 处理异常：${String(error?.message ?? error)}`)
        this.#writeJson(response, 500, makeResponse({
          status: STATUS.FAILED, retcode: RETCODE.INTERNAL, error: '内部错误',
        }))
      })
    })
    const wss = new WebSocketServer({ noServer: true })
    wss.on('connection', (ws, request) => this.#onConnection(ws, request))
    server.on('upgrade', (request, socket, head) => this.#handleUpgrade(request, socket, head))

    await new Promise((resolve, reject) => {
      const onError = (error) => { server.off('listening', onListening); reject(error) }
      const onListening = () => { server.off('error', onError); resolve() }
      server.once('error', onError)
      server.once('listening', onListening)
      server.listen(this.#port, this.#host)
    })

    this.#http = server
    this.#wss = wss
    this.#boundPort = Number(server.address()?.port ?? this.#port)

    // 单一 ping 循环照看所有已连接客户端（每客户端一个定时器会随连接数线性增长）。
    this.#pingTimer = setInterval(() => {
      for (const ws of this.#clients) {
        if (ws.isAlive !== true) {
          ws.terminate()
          continue
        }
        ws.isAlive = false
        try { ws.ping() } catch { /* 下一次 tick 走 terminate 分支 */ }
      }
    }, this.#heartbeatMs)
    this.#pingTimer.unref?.()

    this.#info(`mode B 已监听 ${this.#host}:${this.#boundPort}（ws=${this.wsPath}，api=${this.apiPath}）`)
    return this.address()
  }

  address() {
    return {
      host: this.#host,
      port: this.#boundPort,
      wsPath: this.wsPath,
      apiPath: this.apiPath,
      statusPath: this.statusPath,
    }
  }

  /** 停止：断所有客户端、关定时器、关服务。 */
  async stop() {
    if (this.#pingTimer !== null) {
      clearInterval(this.#pingTimer)
      this.#pingTimer = null
    }
    for (const ws of [...this.#clients]) {
      this.#clients.delete(ws)
      try { ws.close(1001, 'server stopping') } catch { /* 忽略 */ }
      try { ws.terminate() } catch { /* 忽略 */ }
    }
    const wss = this.#wss
    const server = this.#http
    this.#wss = null
    this.#http = null
    this.#boundPort = 0
    if (wss !== null) {
      try { wss.close() } catch { /* 忽略 */ }
    }
    if (server !== null) {
      await new Promise((resolve) => {
        try { server.close(() => resolve()) } catch { resolve() }
        // `server.close()` 只等空闲连接；keep-alive 的连接会拖着它，
        // 所以再补一个 closeAllConnections（Node >= 18.2）。
        try { server.closeAllConnections?.() } catch { /* 老版本没有 */ }
      })
    }
  }

  /**
   * 把一条事件推给所有已连接客户端。
   *
   * @returns {number} 实际写出去的连接数（0 表示没有消费者，**不是错误**：
   *   OneBot 的事件广播本来就是「没人在听就丢掉」，MeaPet 的
   *   `_publish()` 在 `hub_sessions` 为空时直接 return）。
   */
  broadcast(payload) {
    if (this.#clients.size === 0) return 0
    let text
    try {
      text = JSON.stringify(payload)
    } catch (error) {
      this.#warn(`事件无法序列化，已丢弃：${String(error?.message ?? error)}`)
      return 0
    }
    let sent = 0
    for (const ws of this.#clients) {
      if (ws.readyState !== WebSocket.OPEN) continue
      try {
        ws.send(text)
        sent += 1
      } catch (error) {
        this.#warn(`事件推送失败：${String(error?.message ?? error)}`)
      }
    }
    this.#eventsPushed += sent
    return sent
  }

  // ── WS ──────────────────────────────────────────────────────────

  #handleUpgrade(request, socket, head) {
    let pathname = ''
    try {
      pathname = urlOf(request).pathname
    } catch {
      pathname = ''
    }
    if (pathname !== this.wsPath) {
      this.#denyUpgrade(socket, 404, 'Not Found')
      return
    }
    // ⚠️ 硬事实第 2 条：鉴权必须在 **upgrade 之前**做完
    // （规范 §2.5：「在连接建立前，也就是协议 upgrade 前」鉴权）。
    // 先 accept 再 close 会让未鉴权的对端短暂拿到一条可用连接。
    if (!authorizeToken(request, this.#token)) {
      this.#authFailures += 1
      this.#warn(`WS 鉴权失败，已拒绝 ${pathname}`)
      this.#denyUpgrade(socket, 401, 'Unauthorized')
      return
    }
    this.#wss.handleUpgrade(request, socket, head, (ws) => {
      this.#wss.emit('connection', ws, request)
    })
  }

  #denyUpgrade(socket, status, reason) {
    try {
      socket.write(
        `HTTP/1.1 ${status} ${reason}\r\n`
        + 'connection: close\r\n'
        + 'content-length: 0\r\n'
        + '\r\n',
      )
    } catch { /* 对端可能已经走了 */ }
    try { socket.destroy() } catch { /* 忽略 */ }
  }

  #onConnection(ws, request) {
    ws.isAlive = true
    ws.on('pong', () => { ws.isAlive = true })
    this.#clients.add(ws)
    this.#info(`mode B：客户端已接入（当前 ${this.#clients.size} 条）`)
    ws.on('message', (raw) => {
      this.#handleClientFrame(ws, raw).catch((error) => {
        this.#warn(`帧处理异常：${String(error?.message ?? error)}`)
      })
    })
    ws.on('close', () => {
      this.#clients.delete(ws)
      this.#info(`mode B：客户端已断开（剩 ${this.#clients.size} 条）`)
    })
    // 同样不 emit 'error'：客户端异常断开是常态。
    ws.on('error', (error) => {
      this.#warn(`mode B：客户端连接出错：${String(error?.message ?? error)}`)
    })
  }

  /** 拿一条 WS 帧：只可能是 API 调用（事件是**我们**推给对端的）。 */
  async #handleClientFrame(ws, raw) {
    let payload
    try {
      payload = JSON.parse(typeof raw === 'string' ? raw : raw.toString('utf8'))
    } catch {
      this.#sendFrame(ws, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.BAD_PARAM, error: '帧不是合法 JSON',
      }))
      return
    }
    const { action, params, echo } = this.#readCall(payload)
    if (action === '') {
      this.#sendFrame(ws, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.MISSING_PARAM, echo, error: '缺少 action 字段',
      }))
      return
    }
    const envelope = await this.#dispatch(action, params, { transport: 'ws', ws })
    this.#sendFrame(ws, makeResponse({ ...envelope, echo }))
  }

  #sendFrame(ws, frame) {
    if (ws.readyState !== WebSocket.OPEN) return
    try {
      ws.send(JSON.stringify(frame))
    } catch (error) {
      this.#warn(`响应发送失败：${String(error?.message ?? error)}`)
    }
  }

  /**
   * 从入站帧里取出 `{action, params, echo}`。
   *
   * `params` 不是对象时**归为空对象而不是报错**：规范里 `params` 对无参动作
   * （`get_status`）可以省略，而无参动作的调用方常常干脆不写这个键。
   * echo 原样保留（可能是数字、字符串、甚至 null）——响应必须逐字回传它，
   * 否则调用方按 echo 关联的那张表永远命中不了。
   */
  #readCall(payload) {
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      return { action: '', params: {}, echo: null }
    }
    const action = typeof payload.action === 'string' ? payload.action.trim() : ''
    const rawParams = payload.params
    const params = rawParams !== null && typeof rawParams === 'object' && !Array.isArray(rawParams)
      ? rawParams
      : {}
    const echo = payload.echo === undefined ? null : payload.echo
    return { action, params, echo }
  }

  /** 调一次注入的 onCall，并把异常收成 failed 响应（绝不让它打穿到调用方）。 */
  async #dispatch(action, params, meta) {
    this.#requestsServed += 1
    try {
      const envelope = await this.#onCall(action, params, meta)
      if (envelope === null || typeof envelope !== 'object') {
        return { status: STATUS.OK, retcode: RETCODE.OK, data: null }
      }
      return {
        status: typeof envelope.status === 'string' ? envelope.status : STATUS.OK,
        retcode: Number.isFinite(Number(envelope.retcode)) ? Number(envelope.retcode) : RETCODE.OK,
        data: envelope.data === undefined ? null : envelope.data,
        ...(typeof envelope.error === 'string' && envelope.error !== '' ? { error: envelope.error } : {}),
      }
    } catch (error) {
      const message = String(error?.message ?? error)
      this.#warn(`动作 ${action} 执行失败：${message}`)
      return { status: STATUS.FAILED, retcode: RETCODE.INTERNAL, data: null, error: message }
    }
  }

  // ── HTTP ────────────────────────────────────────────────────────

  async #handleHttp(request, response) {
    let url
    try {
      url = urlOf(request)
    } catch {
      this.#writeJson(response, 400, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.BAD_PARAM, error: '请求 URL 不合法',
      }))
      return
    }

    if (url.pathname === this.statusPath) {
      if (!this.#guard(request, response)) return
      this.#writeJson(response, 200, { ok: true, ...this.snapshot() })
      return
    }

    if (url.pathname !== this.apiPath) {
      this.#writeJson(response, 404, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.BAD_PARAM, error: `未知路径 ${url.pathname}`,
      }))
      return
    }
    if (request.method !== 'POST') {
      this.#writeJson(response, 405, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.BAD_PARAM, error: 'OneBot HTTP API 只接受 POST',
      }))
      return
    }
    // ⚠️ 先鉴权再读体：未鉴权的请求不该有机会让本进程缓冲 8 MiB。
    if (!this.#guard(request, response)) return

    const body = await this.#readBody(request)
    if (body === null) {
      // 带上 connection: close：请求体还没读完就要拒，连接不能再复用
      this.#writeJson(response, 413, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.BAD_PARAM,
        error: `请求体读取失败或超过上限 ${MAX_BODY_BYTES} 字节`,
      }), { close: true })
      return
    }
    let payload
    try {
      payload = JSON.parse(body)
    } catch {
      this.#writeJson(response, 400, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.BAD_PARAM, error: '请求体不是合法 JSON',
      }))
      return
    }
    const { action, params, echo } = this.#readCall(payload)
    if (action === '') {
      this.#writeJson(response, 200, makeResponse({
        status: STATUS.FAILED, retcode: RETCODE.MISSING_PARAM, echo, error: '缺少 action 字段',
      }))
      return
    }
    const envelope = await this.#dispatch(action, params, { transport: 'http', request })
    this.#writeJson(response, 200, makeResponse({ ...envelope, echo }))
  }

  /** 鉴权闸。失败一律 401，且响应体**不区分**「token 错」与「token 缺失」。 */
  #guard(request, response) {
    if (authorizeToken(request, this.#token)) return true
    this.#authFailures += 1
    this.#writeJson(response, 401, makeResponse({
      status: STATUS.FAILED, retcode: RETCODE.UNAUTHORIZED, error: 'unauthorized',
    }))
    return false
  }

  async #readBody(request) {
    const declared = Number(request.headers?.['content-length'])
    if (Number.isFinite(declared) && declared > MAX_DRAIN_BYTES) {
      // 声明得远远超限（比如 1 GB）：没道理陪着读完
      request.destroy()
      return null
    }
    const chunks = []
    let size = 0
    let overflow = false
    let drained = 0
    try {
      for await (const chunk of request) {
        if (overflow) {
          // 超限之后的部分**读掉但丢弃**（不进内存），让请求正常收尾。
          // 否则客户端还在上传时连接就被掐断，它看到的是 ECONNRESET 而不是 413。
          drained += chunk.length
          if (drained > MAX_DRAIN_BYTES) {
            request.destroy()
            return null
          }
          continue
        }
        size += chunk.length
        if (size > MAX_BODY_BYTES) {
          overflow = true
          chunks.length = 0
          continue
        }
        chunks.push(chunk)
      }
    } catch {
      return null
    }
    if (overflow) return null
    return Buffer.concat(chunks).toString('utf8')
  }

  #writeJson(response, status, body, { close = false } = {}) {
    if (response.headersSent) return
    const payload = JSON.stringify(body)
    response.writeHead(status, {
      'content-type': 'application/json; charset=utf-8',
      'content-length': Buffer.byteLength(payload),
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      ...(close ? { connection: 'close' } : {}),
    })
    response.end(payload)
  }
}
