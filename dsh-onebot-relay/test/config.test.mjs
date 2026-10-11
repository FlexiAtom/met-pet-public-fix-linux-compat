/**
 * `lib/config.js` 的单测。
 *
 * 钉的是「加载即失败」这条口径：**必填项缺失必须抛错**，
 * 而且判据要按模式走（只跑 mode B 的部署不该被 meapetWsUrl 卡住）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  DEFAULT_TITLE_TEMPLATE, STREAM_MODE, Config, assertConfigIsUsable, configWarnings, isAbsolutePath,
} from '../lib/config.js'

const HERE = dirname(fileURLToPath(import.meta.url))

/**
 * 本包自带的 `cordis.patch.yml` 是「**完整的** config」（patch 语义是整体替换，
 * 不是逐字段 merge），而 installer 在首次安装时**就是照它写 profile 的**。
 * 于是它一旦落后于 schema，就会出现：模板没写的字段在 profile 里悄悄退回默认值。
 * 这条测试把它钉住——加新配置项却忘了补模板，这里会红。
 */
test('cordis.patch.yml 的 config 与 schema 的键集合完全一致', () => {
  const lines = readFileSync(join(HERE, '..', 'cordis.patch.yml'), 'utf8').split(/\r?\n/)
  const start = lines.findIndex((line) => /^\s*config:\s*$/.test(line))
  assert.notEqual(start, -1, '模板里应当有 config: 行')
  const baseIndent = lines[start].match(/^[ \t]*/)[0].length

  const templateKeys = []
  for (let i = start + 1; i < lines.length; i += 1) {
    const line = lines[i]
    if (line.trim() === '' || /^\s*#/.test(line)) continue
    if (line.match(/^[ \t]*/)[0].length <= baseIndent) break
    const m = /^\s*([A-Za-z][A-Za-z0-9]*):/.exec(line)
    if (m) templateKeys.push(m[1])
  }

  const schemaKeys = Object.keys(Config.dict ?? Config._dict ?? {})
  assert.equal(schemaKeys.length > 0, true, '应当能取到 schema 的键')
  const missing = schemaKeys.filter((key) => !templateKeys.includes(key))
  const extra = templateKeys.filter((key) => !schemaKeys.includes(key))
  assert.deepEqual(missing, [], `模板缺少这些配置项：${missing.join(', ')}`)
  assert.deepEqual(extra, [], `模板写了 schema 里没有的项：${extra.join(', ')}`)
})

/** 一份最小可用的 mode A 配置。 */
const BASE = {
  selfId: '10001',
  meapetToken: 'token-0123456789abcdef',
  meapetWsUrl: 'ws://127.0.0.1:14321/onebot/v11/hub',
  cwd: 'C:\\work',
}

/** 走一遍 schema（填默认值）再走一遍手工校验，等价于插件加载期。 */
function validate(patch) {
  const filled = Config({ ...BASE, ...patch })
  assertConfigIsUsable(filled)
  return filled
}

test('Config 的 schema 默认值符合 README 里写的那张表', () => {
  const filled = Config(BASE)
  assert.equal(filled.pathPrefix, '/onebot-relay')
  assert.equal(filled.requireAt, true)
  assert.equal(filled.replyChunking, true)
  assert.equal(filled.replyChunkChars, 1500)
  assert.equal(filled.sessionTitleTemplate, DEFAULT_TITLE_TEMPLATE)
  assert.equal(filled.streamMode, STREAM_MODE.FINAL)
  assert.equal(filled.statePath, '')
  assert.equal(filled.idleTtlMs, 86_400_000)
  assert.equal(filled.maxQueuedPerConversation, 4)
  assert.equal(filled.serverEnabled, false)
  assert.equal(filled.serverPort, 6721)
  assert.equal(filled.serverHost, '127.0.0.1')
  assert.equal(filled.clientEnabled, true)
  assert.equal(filled.approvalEnabled, true)
  assert.equal(filled.approvalTimeoutMs, 120_000)
  assert.equal(filled.tokenInQuery, false)
  assert.equal(filled.relayInboundToServer, false)
  assert.equal(filled.serverAllowRemote, false)
  // 默认允许消费者调动作（与 MeaPet 的 hub.allow_send 默认一致）
  assert.equal(filled.serverAllowSend, true)
})

test('serverAllowSend 可以被关掉（消费者只能看，不能问）', () => {
  const filled = validate({
    selfId: '10001', cwd: '/tmp', meapetToken: 't', serverAllowSend: false,
  })
  assert.equal(filled.serverAllowSend, false)
})

test('缺 selfId / cwd 被 schema 直接挡下（required）', () => {
  assert.throws(() => Config({ ...BASE, selfId: undefined }))
  assert.throws(() => Config({ ...BASE, cwd: undefined }))
})

test('selfId / meapetToken / cwd 的加载期判据', () => {
  assert.throws(() => validate({ selfId: 'abc' }), /selfId 必须是机器人的 QQ 号/)
  assert.throws(() => validate({ meapetToken: '' }), /meapetToken 不能为空/)
  assert.throws(() => validate({ cwd: 'relative\\dir' }), /cwd 必须是绝对路径/)
  assert.doesNotThrow(() => validate({ selfId: '1' }))
})

test('clientEnabled=true 时 meapetWsUrl 必填且必须是 ws/wss', () => {
  assert.throws(() => validate({ meapetWsUrl: '' }), /meapetWsUrl 必填/)
  assert.throws(() => validate({ meapetWsUrl: 'http://127.0.0.1:6700' }), /必须是 ws:\/\/ 或 wss:\/\//)
  assert.doesNotThrow(() => validate({ meapetWsUrl: 'wss://example.com/onebot/v11/hub' }))
})

test('只跑 mode B 的部署：clientEnabled=false 时不要求 meapetWsUrl', () => {
  const filled = validate({ clientEnabled: false, meapetWsUrl: '', serverEnabled: true })
  assert.equal(filled.clientEnabled, false)
  assert.equal(filled.serverEnabled, true)
})

test('serverEnabled 的判据（端口范围、回环闸门、token）', () => {
  assert.throws(() => validate({ serverEnabled: true, serverPort: 70000 }), /serverPort 必须在 0\.\.65535/)
  assert.throws(() => validate({ serverEnabled: true, serverHost: '' }), /serverHost 不能为空/)
  // 非回环监听必须显式认下
  assert.throws(
    () => validate({ serverEnabled: true, serverHost: '0.0.0.0' }),
    /是非回环地址，必须显式设置 serverAllowRemote: true/,
  )
  assert.doesNotThrow(() => validate({ serverEnabled: true, serverHost: '0.0.0.0', serverAllowRemote: true }))
  // 0 = 系统分配端口，是合法值（测试要用）
  assert.doesNotThrow(() => validate({ serverEnabled: true, serverPort: 0 }))
})

test('pathPrefix 与会话标题模板的加载期判据', () => {
  assert.throws(() => validate({ pathPrefix: '' }), /pathPrefix 不能为空/)
  assert.throws(() => validate({ pathPrefix: 'onebot-relay' }), /必须以 "\/" 开头/)
  assert.throws(() => validate({ pathPrefix: '/onebot-relay/' }), /不要以 "\/" 结尾/)
  assert.throws(() => validate({ sessionTitleTemplate: '   ' }), /sessionTitleTemplate 不能为空/)
  // 未知占位符原样保留 → 渲染结果非空 → **允许**：配置写错时看得见，
  // 比静默产出一个空标题（= 反向定位失效）好得多。
  assert.doesNotThrow(() => validate({ sessionTitleTemplate: '{unknownOnly}' }))
  assert.doesNotThrow(() => validate({ sessionTitleTemplate: '{unknownOnly}-{sessionId}' }))
})

test('数值参数的上下界', () => {
  assert.throws(() => validate({ idleTtlMs: -1 }), /idleTtlMs 必须是非负毫秒数/)
  assert.doesNotThrow(() => validate({ idleTtlMs: 0 }))
  assert.throws(() => validate({ maxQueuedPerConversation: 0 }), /必须是 >= 1 的整数/)
  assert.throws(() => validate({ replyChunkChars: 0 }), /replyChunkChars 必须是 >= 1/)
  assert.throws(() => validate({ heartbeatMs: 500 }), /heartbeatMs 必须 >= 1000/)
  assert.throws(() => validate({ callTimeoutMs: 10 }), /callTimeoutMs 必须 >= 1000/)
  assert.throws(() => validate({ approvalTimeoutMs: 10 }), /approvalTimeoutMs 必须 >= 1000/)
  assert.throws(
    () => validate({ reconnectInitialMs: 5000, reconnectMaxMs: 1000 }),
    /重连退避必须是 100 <= reconnectInitialMs <= reconnectMaxMs/,
  )
})

test('streamMode 与 preview 参数', () => {
  assert.throws(() => Config({ ...BASE, streamMode: 'streaming' }))
  assert.doesNotThrow(() => validate({ streamMode: STREAM_MODE.PREVIEW }))
  assert.throws(
    () => validate({ streamMode: STREAM_MODE.PREVIEW, previewMinChars: 0 }),
    /previewMinChars 必须 >= 1/,
  )
  assert.throws(
    () => validate({ streamMode: STREAM_MODE.PREVIEW, previewIntervalMs: 10 }),
    /previewIntervalMs 必须 >= 200/,
  )
  // final 模式下 preview 参数随便配都不拦（它们不生效）
  assert.doesNotThrow(() => validate({ streamMode: STREAM_MODE.FINAL, previewIntervalMs: 10 }))
})

test('isAbsolutePath 覆盖 Windows / POSIX / UNC', () => {
  assert.equal(isAbsolutePath('C:\\x'), true)
  assert.equal(isAbsolutePath('c:/x'), true)
  assert.equal(isAbsolutePath('/x'), true)
  assert.equal(isAbsolutePath('\\\\server\\share'), true)
  assert.equal(isAbsolutePath('x'), false)
  assert.equal(isAbsolutePath(''), false)
})

test('configWarnings 绝不抛错，只给提示', () => {
  const filled = Config({ ...BASE, meapetToken: 'short' })
  const warnings = configWarnings(filled)
  assert.equal(Array.isArray(warnings), true)
  assert.equal(warnings.some((line) => line.includes('meapetToken 只有')), true)

  const risky = Config({
    ...BASE,
    requireAt: false,
    idleTtlMs: 0,
    relayInboundToServer: true,
    approvalEnabled: false,
    streamMode: STREAM_MODE.PREVIEW,
    meapetWsUrl: 'ws://10.0.0.5:6700/onebot/v11/hub',
  })
  const riskyWarnings = configWarnings(risky)
  assert.equal(riskyWarnings.length >= 5, true)
  assert.equal(riskyWarnings.some((line) => line.includes('requireAt 已关闭')), true)
  assert.equal(riskyWarnings.some((line) => line.includes('明文 ws://')), true)
  assert.equal(riskyWarnings.some((line) => line.includes('streamMode=preview')), true)
  assert.equal(riskyWarnings.some((line) => line.includes('relayInboundToServer 已开启')), true)
  assert.equal(riskyWarnings.some((line) => line.includes('approvalEnabled 已关闭')), true)
  assert.equal(riskyWarnings.some((line) => line.includes('idleTtlMs=0')), true)
})
