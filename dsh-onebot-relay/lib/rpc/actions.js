/**
 * dsh-onebot-relay · OneBot 动作层（mode B 收到的 API 调用怎么答）
 *
 * ## 这一层解决什么问题
 *
 * mode B 上连进来的对端会发 `{action, params, echo}`。这些调用**必须**由本插件
 * 给出 `{status, retcode, data}` 形态的应答——哪怕本端做不到，也要**如实失败**，
 * 而不是装作成功或把错误码说错。
 *
 * ## 三条口径（都是「不许混用」的那一类）
 *
 * 1. **白名单判据抄 MeaPet**：逐条对齐 `meapet/onebot/service.py:36-56`
 *    的 `HUB_ALLOWED_ACTIONS`。不在表里 → `retcode 103`（权限不足），
 *    与「本端还没实现」是两件事。
 * 2. **能转发的就转发**：mode A 已连上 MeaPet 时，白名单里的动作**原样透传**
 *    给它——本插件不是 QQ 实现端，自己实现 `get_group_list` 只会造出第二份真相。
 * 3. **转不出去时只就地答两个**：`get_status` / `get_version_info`
 *    （与 MeaPet `handle_hub_call` 的本地兜底恰好同两个），其余动作
 *    回 `status: failed, retcode: 1` + 一句人话说明「没有可用的发送通道」。
 *    **绝不返回假成功**（对照 astrbot-relay 契约 §8「未实现的端点必须如实失败」）。
 */
import {
  ACTION, ALLOWED_ACTIONS, BRIDGE_VERSION, PLUGIN_NAME,
  PROXIED_ACTIONS, RETCODE, STATUS, isOkResponse,
} from '../contract.js'

/** 白名单的 Set 形态（判定落在热路径上，不必每次线性扫数组）。 */
export const ALLOWED_ACTION_SET = new Set(ALLOWED_ACTIONS)

/** 就地作答（不转发）的两个动作，与 MeaPet `handle_hub_call` 的兜底同两个。 */
export const LOCAL_ACTIONS = Object.freeze([ACTION.GET_STATUS, ACTION.GET_VERSION_INFO])

/** 目前**只**能靠转发实现的动作（本端没有 QQ 连接）。 */
export const PASSTHROUGH_ONLY_ACTIONS = Object.freeze(
  ALLOWED_ACTIONS.filter((action) => !LOCAL_ACTIONS.includes(action)),
)

export function isAllowedAction(action) {
  return ALLOWED_ACTION_SET.has(String(action ?? ''))
}

/** 造一个成功信封。 */
export function ok(data = null) {
  return { status: STATUS.OK, retcode: RETCODE.OK, data: data === undefined ? null : data }
}

/** 造一个失败信封。 */
export function failed(retcode, error, data = null) {
  return {
    status: STATUS.FAILED,
    retcode: Number.isFinite(Number(retcode)) ? Number(retcode) : RETCODE.FAILED,
    data: data === undefined ? null : data,
    error: String(error ?? ''),
  }
}

/**
 * 把上游（MeaPet / 任何 OneBot 实现端）的原始响应帧收成内部信封。
 *
 * 上游可能给 `{status, retcode, data, echo}`（规范形态），也可能多带一个
 * `error`（MeaPet 的扩展字段）。这里只保留我们关心的四个键，
 * **不重算 ok**：`status`/`retcode` 的语义归上游，我们只搬运并做类型保护。
 */
export function normalizeEnvelope(payload) {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    return failed(RETCODE.FAILED, '上游返回了非对象响应')
  }
  const rawRetcode = Number(payload.retcode)
  const retcode = Number.isFinite(rawRetcode) ? rawRetcode : RETCODE.FAILED
  const status = typeof payload.status === 'string' && payload.status !== ''
    ? payload.status
    : (retcode === RETCODE.OK ? STATUS.OK : STATUS.FAILED)
  const envelope = {
    status,
    retcode,
    data: payload.data === undefined ? null : payload.data,
  }
  if (typeof payload.error === 'string' && payload.error !== '') envelope.error = payload.error
  return envelope
}

/**
 * 造默认的 `get_status` 本地应答（形状对齐 MeaPet `_status_payload` 的公开面）。
 *
 * 只暴露「对端判断本端是否可用」需要的字段：多写一个字段就多一份要维护的真相，
 * 而且 `/status` 路由已经有一个更全的诊断快照给运维看。
 */
export function defaultStatusData({ selfId = '', startedAt = 0, now = Date.now() } = {}) {
  return {
    app_name: PLUGIN_NAME,
    bridge_version: BRIDGE_VERSION,
    protocol_version: 'v11',
    online: true,
    good: true,
    self_id: selfId === '' ? null : selfId,
    uptime_ms: startedAt > 0 ? Math.max(0, now - startedAt) : 0,
  }
}

/** 造默认的 `get_version_info` 本地应答（形状对齐 MeaPet 的同名兜底）。 */
export function defaultVersionData() {
  return {
    app_name: PLUGIN_NAME,
    app_version: BRIDGE_VERSION,
    protocol_version: 'v11',
  }
}

/**
 * 建一个动作路由器。
 *
 * @param {object} options
 * @param {{ connected: boolean, call: Function }} options.outbound
 *   mode A 的 OneBot 客户端（未连接时 `connected` 为 false）。测试里塞假对象即可。
 * @param {() => object} [options.statusData]  本地 `get_status` 的数据源
 * @param {() => object} [options.versionData] 本地 `get_version_info` 的数据源
 * @param {boolean} [options.allowSend]        是否允许发送类动作（对齐 MeaPet 的 `hub.allow_send`）
 * @param {object}  [options.logger]
 * @param {string}  [options.tag]
 * @returns {(action: string, params: object) => Promise<object>}
 */
export function createActionRouter({
  outbound,
  statusData = () => defaultStatusData(),
  versionData = () => defaultVersionData(),
  allowSend = true,
  logger = null,
  tag = `[${PLUGIN_NAME}]`,
} = {}) {
  if (outbound === null || typeof outbound !== 'object' || typeof outbound.call !== 'function') {
    throw new Error('createActionRouter: outbound 必须是 { connected, call() }')
  }

  const route = async function route(action, params = {}) {
    const name = String(action ?? '').trim()

    // ① 白名单（权限门）——顺序排在「实现没实现」之前：
    //    对端最需要立刻知道的是「你根本不许调这个」，不是「你还没做这个」。
    if (!isAllowedAction(name)) {
      return failed(RETCODE.FORBIDDEN, `动作不在白名单内：${name}`)
    }
    if (!allowSend) {
      return failed(RETCODE.FORBIDDEN, '本端未允许发送（allowSend=false）')
    }

    // ② 能转发就转发：白名单里的动作一律透传给真正的 OneBot 实现端。
    if (outbound.connected === true) {
      try {
        const raw = await outbound.call(name, params)
        return normalizeEnvelope(raw)
      } catch (error) {
        // 线路层失败（超时 / 发送失败）在这里变成 failed 信封。
        // 不向上抛：调用方是网络对端，它需要一条**应答**而不是一个断掉的连接。
        const message = String(error?.message ?? error)
        logger?.warn?.(`${tag} 转发 ${name} 失败：${message}`)
        return failed(RETCODE.FAILED, `转发到上游失败：${message}`)
      }
    }

    // ③ 上游不在：只就地答两个只读动作，其余如实失败。
    if (name === ACTION.GET_STATUS) return ok(statusData())
    if (name === ACTION.GET_VERSION_INFO) return ok(versionData())
    const detail = LOCAL_ACTIONS.includes(name)
      ? '本端无法就地完成该动作'
      : '没有可用的 OneBot 发送通道（mode A 未连接上游）'
    return failed(RETCODE.FAILED, `${detail}：${name}`)
  }

  // 把**生效的**门留在路由器自己身上，`/status` 从这里读。
  // 这样「报告出去的开关」和「真正判据」不可能漂移——此前的实际故障正是
  // allowSend 压根没接线（恒为 true），而 JSDoc 却声称它对齐 MeaPet 的 hub.allow_send。
  route.allowSend = allowSend
  return route
}

/** 供 `/status` 与日志展示：本端动作面的能力分布。 */
export function actionSurface(router = null) {
  return {
    allowed: ALLOWED_ACTIONS.length,
    passthrough: PASSTHROUGH_ONLY_ACTIONS.length,
    local: [...LOCAL_ACTIONS],
    proxiedWhenConnected: [...PROXIED_ACTIONS],
    // 没有路由器可问时给 null（而不是乐观地报 true）
    sendAllowed: typeof router?.allowSend === 'boolean' ? router.allowSend : null,
  }
}

/** 判定一次上游响应是否成功（导出方便测试与调用点共用同一判据）。 */
export { isOkResponse }
