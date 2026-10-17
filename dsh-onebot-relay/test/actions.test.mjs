/**
 * `lib/rpc/actions.js` 的单测 —— 动作层的「不许混用」口径。
 *
 * 重点：
 *   - 不在白名单 → 403/103（**不是**「还没实现」）
 *   - 上游连上时白名单动作原样透传，且失败也收成信封而不是抛出去
 *   - 上游没连时只就地答 get_status / get_version_info，其余如实失败（retcode 1）
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  ALLOWED_ACTION_SET, LOCAL_ACTIONS, PASSTHROUGH_ONLY_ACTIONS,
  actionSurface, createActionRouter, defaultStatusData, defaultVersionData,
  failed, isAllowedAction, normalizeEnvelope, ok,
} from '../lib/rpc/actions.js'
import { ALLOWED_ACTIONS, BRIDGE_VERSION, RETCODE, STATUS } from '../lib/contract.js'

/** 假上游：记录调用、按脚本回应。 */
function fakeOutbound({ connected = true, script = {} } = {}) {
  const calls = []
  return {
    calls,
    connected,
    async call(action, params) {
      calls.push({ action, params })
      const handler = script[action]
      if (handler === undefined) {
        return { status: STATUS.OK, retcode: RETCODE.OK, data: { echoOf: action }, echo: 'x' }
      }
      if (typeof handler === 'function') return handler(action, params)
      return handler
    },
  }
}

test('白名单与 MeaPet 的 HUB_ALLOWED_ACTIONS 同集合', () => {
  assert.equal(ALLOWED_ACTION_SET.size, ALLOWED_ACTIONS.length)
  assert.equal(isAllowedAction('send_private_msg'), true)
  assert.equal(isAllowedAction('set_group_ban'), false)
  assert.equal(isAllowedAction(''), false)
  assert.deepEqual([...LOCAL_ACTIONS], ['get_status', 'get_version_info'])
  assert.equal(PASSTHROUGH_ONLY_ACTIONS.includes('get_status'), false)
  assert.equal(PASSTHROUGH_ONLY_ACTIONS.includes('send_msg'), true)
})

test('信封构造器', () => {
  assert.deepEqual(ok({ a: 1 }), { status: 'ok', retcode: 0, data: { a: 1 } })
  assert.deepEqual(ok(), { status: 'ok', retcode: 0, data: null })
  const bad = failed(RETCODE.FORBIDDEN, '不行')
  assert.equal(bad.status, 'failed')
  assert.equal(bad.retcode, 103)
  assert.equal(bad.data, null)
  assert.equal(bad.error, '不行')
})

test('normalizeEnvelope 搬运而不重算语义', () => {
  assert.deepEqual(normalizeEnvelope({ status: 'ok', retcode: 0, data: { a: 1 } }), {
    status: 'ok', retcode: 0, data: { a: 1 },
  })
  // status 缺失时按 retcode 推
  assert.equal(normalizeEnvelope({ retcode: 0 }).status, 'ok')
  assert.equal(normalizeEnvelope({ retcode: 1 }).status, 'failed')
  // retcode 非数 → 1
  assert.equal(normalizeEnvelope({ status: 'ok', retcode: 'nope' }).retcode, 1)
  // data 缺省写 null
  assert.equal(normalizeEnvelope({ status: 'ok', retcode: 0 }).data, null)
  // error 只在非空字符串时带出
  assert.equal('error' in normalizeEnvelope({ status: 'ok', retcode: 0, error: '' }), false)
  assert.equal(normalizeEnvelope({ status: 'ok', retcode: 0, error: 'x' }).error, 'x')
  // 非对象一律 failed
  assert.equal(normalizeEnvelope('nope').status, 'failed')
  assert.equal(normalizeEnvelope(null).retcode, RETCODE.FAILED)
})

test('createActionRouter 参数校验：outbound 必须是 { connected, call }', () => {
  assert.throws(() => createActionRouter({ outbound: null }), /outbound 必须是/)
  assert.throws(() => createActionRouter({ outbound: {} }), /outbound 必须是/)
})

test('不在白名单的动作 → 103（权限），且不触碰上游', async () => {
  const outbound = fakeOutbound()
  const route = createActionRouter({ outbound })
  const result = await route('set_group_ban', { group_id: 1 })
  assert.equal(result.status, 'failed')
  assert.equal(result.retcode, RETCODE.FORBIDDEN)
  assert.match(result.error, /动作不在白名单内/)
  assert.equal(outbound.calls.length, 0)
})

test('allowSend=false → 103，且白名单判断在前', async () => {
  const outbound = fakeOutbound()
  const route = createActionRouter({ outbound, allowSend: false })
  assert.equal((await route('send_private_msg', {})).retcode, RETCODE.FORBIDDEN)
  assert.equal(outbound.calls.length, 0)
  // 不在白名单的仍是 103（同一个码，但理由不同——先白名单后 allowSend）
  const notAllowed = await route('set_group_ban', {})
  assert.match(notAllowed.error, /动作不在白名单内/)
})

test('allowSend=false 也拒绝只读动作（读动作能读聊天记录，不是"无害"）', async () => {
  const outbound = fakeOutbound()
  const route = createActionRouter({ outbound, allowSend: false })
  for (const action of ['get_status', 'get_version_info', 'get_msg', 'get_group_list']) {
    const result = await route(action, {})
    assert.equal(result.retcode, RETCODE.FORBIDDEN, `${action} 应当被拒绝`)
    assert.match(result.error, /未允许发送/)
  }
  assert.equal(outbound.calls.length, 0)
})

test('路由器把生效的门留在自己身上，actionSurface 读它（报告值与判据不会漂移）', async () => {
  const outbound = fakeOutbound()
  const open = createActionRouter({ outbound })
  const closed = createActionRouter({ outbound, allowSend: false })
  assert.equal(open.allowSend, true)
  assert.equal(closed.allowSend, false)
  assert.equal(actionSurface(open).sendAllowed, true)
  assert.equal(actionSurface(closed).sendAllowed, false)
  // 没有路由器可问时给 null，而不是乐观地报 true
  assert.equal(actionSurface().sendAllowed, null)
  assert.equal(actionSurface(null).sendAllowed, null)
})

test('上游连上时白名单动作原样透传', async () => {
  const outbound = fakeOutbound({
    script: { get_group_list: { status: STATUS.OK, retcode: RETCODE.OK, data: { items: [1, 2] } } },
  })
  const route = createActionRouter({ outbound })
  const result = await route('get_group_list', {})
  assert.deepEqual(result, { status: 'ok', retcode: 0, data: { items: [1, 2] } })
  assert.deepEqual(outbound.calls, [{ action: 'get_group_list', params: {} }])
})

test('上游回的失败信封被原样搬运（含 error）', async () => {
  const outbound = fakeOutbound({
    script: { send_private_msg: { status: STATUS.FAILED, retcode: 1, data: null, error: '风控' } },
  })
  const route = createActionRouter({ outbound })
  const result = await route('send_private_msg', { user_id: 1, message: 'x' })
  assert.equal(result.status, 'failed')
  assert.equal(result.retcode, 1)
  assert.equal(result.error, '风控')
})

test('上游抛错时收成信封，而不是把异常抛给网络对端', async () => {
  const logs = []
  const outbound = fakeOutbound({
    script: { send_private_msg: () => { throw new Error('线路断了') } },
  })
  const route = createActionRouter({ outbound, logger: { warn: (line) => logs.push(line) }, tag: '[t]' })
  const result = await route('send_private_msg', { user_id: 1, message: 'x' })
  assert.equal(result.status, 'failed')
  assert.equal(result.retcode, RETCODE.FAILED)
  assert.match(result.error, /线路断了/)
  assert.equal(logs.length, 1)
})

test('上游不在时只就地答两个只读动作，其余如实失败', async () => {
  const outbound = fakeOutbound({ connected: false })
  const route = createActionRouter({
    outbound,
    statusData: () => ({ app_name: 'dsh-onebot-relay', self_id: '10001', online: true }),
    versionData: () => ({ app_name: 'dsh-onebot-relay', protocol_version: 'v11' }),
  })
  const status = await route('get_status', {})
  assert.equal(status.status, 'ok')
  assert.equal(status.data.self_id, '10001')

  const version = await route('get_version_info', {})
  assert.equal(version.status, 'ok')
  assert.equal(version.data.protocol_version, 'v11')

  // 其余动作：如实失败，retcode 1，**绝不返回假成功**
  const send = await route('send_private_msg', { user_id: 1, message: 'x' })
  assert.equal(send.status, 'failed')
  assert.equal(send.retcode, RETCODE.FAILED)
  assert.match(send.error, /没有可用的 OneBot 发送通道/)

  const groupList = await route('get_group_list', {})
  assert.equal(groupList.status, 'failed')
  assert.equal(outbound.calls.length, 0)
})

test('默认 status/version 数据的形状（对齐 MeaPet 的本地兜底）', () => {
  const status = defaultStatusData({ selfId: '10001', startedAt: Date.now() - 1000 })
  assert.equal(status.app_name, 'dsh-onebot-relay')
  assert.equal(status.bridge_version, BRIDGE_VERSION)
  assert.equal(status.protocol_version, 'v11')
  assert.equal(status.online, true)
  assert.equal(status.self_id, '10001')
  assert.equal(status.uptime_ms >= 0, true)

  const noSelf = defaultStatusData({})
  assert.equal(noSelf.self_id, null)

  const version = defaultVersionData()
  assert.deepEqual(Object.keys(version).sort(), ['app_name', 'app_version', 'protocol_version'])
})

test('actionSurface 是给 /status 用的只读投影', () => {
  const surface = actionSurface()
  assert.equal(surface.allowed, ALLOWED_ACTIONS.length)
  assert.equal(surface.passthrough, PASSTHROUGH_ONLY_ACTIONS.length)
  assert.deepEqual(surface.local, ['get_status', 'get_version_info'])
})
