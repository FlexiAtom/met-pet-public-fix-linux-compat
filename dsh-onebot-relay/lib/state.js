/**
 * dsh-onebot-relay · 会话映射的持久化
 *
 * 权威映射（QQ 会话 → DSH 会话 + 工作目录）落在 state.json。
 *
 * 设计口径逐条照抄 astrbot-relay 的 `lib/state.js` + 其契约 §12.5：
 *   - **原子写**：写同目录临时文件再 rename。直接覆写在崩溃/断电时会留下半截
 *     JSON，而那正好会触发下面「解析失败即拒绝加载」的路径——自己给自己造故障。
 *   - **解析失败必须响亮**：坏 JSON / 版本不符 / 结构不对 → 抛错、插件加载即失败。
 *     **绝不静默重建**：静默重建会把所有 QQ 会话重新挂到新 DSH 会话上，历史全丢，
 *     而症状是「聊天记录莫名其妙没了」，几乎不可能事后归因。
 *   - **不用 `ctx.storage`**：该 API 未经核实，不进契约。
 *
 * 只依赖 node 内置模块 → 可在普通 Node 下单测（`test/state.test.mjs`）。
 */
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, isAbsolute, join } from 'node:path'
import { PLUGIN_NAME, STATE_VERSION } from './contract.js'

export const DEFAULT_STATE_DIRNAME = 'onebot-relay'
export const DEFAULT_STATE_FILENAME = 'state.json'

/**
 * 解析 state 文件路径。
 *
 * 配了 `statePath` 就用它（必须是绝对路径）；否则 `<DSH_HOME>/onebot-relay/state.json`。
 * 相对路径一律**抛错**：那会随进程 cwd 漂移，同一套配置在不同启动目录下
 * 读到两份不同的映射，是「会话莫名重置」的经典成因。
 */
export function resolveStatePath(configured, env = process.env) {
  const raw = String(configured ?? '').trim()
  if (raw !== '') {
    if (!isAbsolute(raw)) {
      throw new Error(`${PLUGIN_NAME}: statePath 必须是绝对路径，收到 ${JSON.stringify(raw)}`)
    }
    return raw
  }
  const home = String(env.DSH_HOME ?? '').trim() || join(homedir(), '.dsh')
  // DSH_HOME 也可能是相对路径，那和 statePath 写相对路径是同一个坑：
  // 会随进程 cwd 漂移，同一套配置在不同启动目录下读到两份映射。
  // 上面既然为 statePath 抛错，这里就不能悄悄放过。
  if (!isAbsolute(home)) {
    throw new Error(
      `${PLUGIN_NAME}: DSH_HOME 必须是绝对路径，收到 ${JSON.stringify(home)}`
      + '（否则 state 路径会随 cwd 漂移；或直接配 statePath 指定绝对路径）',
    )
  }
  return join(home, DEFAULT_STATE_DIRNAME, DEFAULT_STATE_FILENAME)
}

/** 取非空字符串，否则 null。 */
function optionalString(value) {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/** 取有限数字，否则 null。 */
function optionalNumber(value) {
  return Number.isFinite(value) ? value : null
}

/**
 * 取出记录里的 DSH 会话 id。
 *
 * ⚠️ 类型不对时**抛错，不降级为 null**。降级 null 等于「这条记录没有会话」，
 * 下一条消息就会静默新建 DSH 会话，老对话的历史从此失联。
 * 宁可加载失败让人当场看见，也不要一个悄悄把历史弄丢的网桥。
 */
function dshSessionIdOf(conversation, raw) {
  const value = raw.dshSessionId ?? raw.sessionId
  if (value === undefined || value === null) return null
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(
      `${PLUGIN_NAME}: state.conversations[${JSON.stringify(conversation)}].dshSessionId `
      + `必须是非空字符串，收到 ${JSON.stringify(value)}；拒绝降级为 null（那会静默新建会话）`,
    )
  }
  return value
}

/**
 * 把一条 state 记录规整成固定形状。
 *
 * state.json 是**人工可编辑**的，所以策略是「字段级宽容、结构级严格」：
 * 可有可无的字段类型不对 → 归为 null；但一旦错就会**静默丢历史**的
 * `dshSessionId`，宁可抛错。
 */
export function normalizeRecord(conversation, raw = {}) {
  return {
    conversation: String(conversation),
    dshSessionId: dshSessionIdOf(conversation, raw),
    cwd: optionalString(raw.cwd),
    createdAt: optionalNumber(raw.createdAt),
    lastActiveAt: optionalNumber(raw.lastActiveAt),
    seq: optionalNumber(raw.seq) ?? 0,
  }
}

/**
 * 造一条**新的**会话记录。
 *
 * 记录形状的唯一权威是上面的 `normalizeRecord`：新建也走它，
 * 让「读入」与「新建」共用同一份形状定义。手写一份字面量等于把字段白名单
 * 抄了第二遍，两边一漂移就是「写进去的字段重启后读不回来」。
 */
export function newRecord({ conversation, dshSessionId, cwd = null, now = Date.now() }) {
  if (typeof dshSessionId !== 'string' || dshSessionId.trim() === '') {
    throw new Error(`newRecord: dshSessionId 必须是非空字符串，收到 ${JSON.stringify(dshSessionId)}`)
  }
  return normalizeRecord(conversation, {
    dshSessionId, cwd, createdAt: now, lastActiveAt: now, seq: 0,
  })
}

/**
 * 校验并解析 state 的顶层结构。
 *
 * @returns {{ok: true, records: object[]} | {ok: false, reason: string}}
 */
export function parseState(raw) {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, reason: 'state 顶层必须是对象' }
  }
  if (raw.version !== STATE_VERSION) {
    return {
      ok: false,
      reason: `不支持的 state 版本 ${JSON.stringify(raw.version)}（期望 ${STATE_VERSION}）`,
    }
  }
  const table = raw.conversations
  if (table === undefined) return { ok: false, reason: 'state 缺少 conversations 字段' }
  if (table === null || typeof table !== 'object' || Array.isArray(table)) {
    return { ok: false, reason: 'state.conversations 必须是「会话键 → 记录」的对象' }
  }
  const records = []
  try {
    for (const [conversation, value] of Object.entries(table)) {
      if (value === null || typeof value !== 'object' || Array.isArray(value)) {
        return { ok: false, reason: `state.conversations[${JSON.stringify(conversation)}] 必须是对象` }
      }
      records.push(normalizeRecord(conversation, value))
    }
  } catch (error) {
    return { ok: false, reason: error.message }
  }
  return { ok: true, records }
}

/** 反向：把记录集合序列化成 state.json 的结构。接受 Map 或数组。 */
export function serializeState(records) {
  const conversations = {}
  const list = records instanceof Map ? [...records.values()] : [...(records ?? [])]
  // 按会话键排序，使 state.json 的 diff 稳定（便于人工 review 与版本控制）。
  for (const record of list.sort((a, b) => a.conversation.localeCompare(b.conversation))) {
    conversations[record.conversation] = {
      dshSessionId: record.dshSessionId ?? null,
      cwd: record.cwd ?? null,
      createdAt: record.createdAt ?? null,
      lastActiveAt: record.lastActiveAt ?? null,
      seq: record.seq ?? 0,
    }
  }
  return { version: STATE_VERSION, conversations }
}

/**
 * 读取并校验 state。
 *
 * 文件不存在 → 空映射（首次启动的正常情况），`existed: false`。
 * 文件存在但损坏 → **抛错**（理由见文件头）。
 *
 * @returns {{records: Map<string, object>, existed: boolean}}
 */
export function loadState(path) {
  if (!existsSync(path)) return { records: new Map(), existed: false }

  let raw
  try {
    raw = JSON.parse(readFileSync(path, 'utf8'))
  } catch (error) {
    throw new Error(`${PLUGIN_NAME}: state 文件无法解析（${path}）：${error.message}`)
  }

  const parsed = parseState(raw)
  if (!parsed.ok) {
    throw new Error(`${PLUGIN_NAME}: state 文件不合法（${path}）：${parsed.reason}`)
  }
  return {
    records: new Map(parsed.records.map((record) => [record.conversation, record])),
    existed: true,
  }
}

/**
 * 原子写入 state：先写同目录临时文件、**fsync**、再 rename。
 *
 * 临时文件名带 pid：同一进程内串行调用不会撞，多进程（理论上不该有）
 * 也各写各的，最后 rename 的赢——比带着半截 JSON 崩掉好得多。
 *
 * 为什么要显式 fsync：只「写 + rename」并不能防断电。rename 只是元数据操作，
 * 可能先于数据块落盘，崩溃后就留下**零长度或半截**的 state.json——那正好会撞上
 * 「解析失败即拒绝加载」，也就是文件头说要避免的那种自造故障。落盘顺序由
 * fsync 保证：数据先持久化，rename 才生效。
 */
export function saveState(path, records) {
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.tmp-${process.pid}`
  const payload = `${JSON.stringify(serializeState(records), null, 2)}\n`
  let fd
  try {
    fd = openSync(tmp, 'w')
    writeSync(fd, payload, null, 'utf8')
    fsyncSync(fd)
  } catch (error) {
    // 写失败时清掉半截临时文件；原 state.json 自始至终没被碰过
    if (fd !== undefined) {
      try {
        closeSync(fd)
      } catch {
        /* 关不上就算了 */
      }
      fd = undefined
    }
    try {
      unlinkSync(tmp)
    } catch {
      /* 本来就没建起来 */
    }
    throw error
  } finally {
    if (fd !== undefined) closeSync(fd)
  }
  renameSync(tmp, path)
}
