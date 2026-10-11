/**
 * `lib/state.js` 的单测。
 *
 * 重点是三条**会静默丢历史**的路径：
 *   - 坏 JSON → 必须抛错（不是重建）
 *   - version 不符 → 必须抛错
 *   - dshSessionId 类型不对 → 必须抛错（不是降级 null）
 * 以及原子写的可回读性（写出去的必须读得回来，字段一个不多一个不少）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { STATE_VERSION } from '../lib/contract.js'
import {
  DEFAULT_STATE_DIRNAME, DEFAULT_STATE_FILENAME,
  loadState, newRecord, normalizeRecord, parseState, resolveStatePath, saveState, serializeState,
} from '../lib/state.js'

const here = dirname(fileURLToPath(import.meta.url))
const TMP = join(here, '.tmp')
mkdirSync(TMP, { recursive: true })

let counter = 0
function tmpFile() {
  counter += 1
  return join(TMP, `state-${process.pid}-${counter}.json`)
}

test('resolveStatePath：配置值必须是绝对路径，否则抛错', () => {
  assert.throws(() => resolveStatePath('relative/state.json'), /必须是绝对路径/)
  assert.equal(resolveStatePath('C:\\x\\state.json'), 'C:\\x\\state.json')
  assert.equal(resolveStatePath('/tmp/state.json'), '/tmp/state.json')
})

test('resolveStatePath：空值落到 <DSH_HOME>/onebot-relay/state.json', () => {
  const withHome = resolveStatePath('', { DSH_HOME: join('C:', 'dsh-home') })
  assert.equal(withHome, join('C:', 'dsh-home', DEFAULT_STATE_DIRNAME, DEFAULT_STATE_FILENAME))
  const withoutHome = resolveStatePath(undefined, {})
  assert.equal(withoutHome.endsWith(join(DEFAULT_STATE_DIRNAME, DEFAULT_STATE_FILENAME)), true)
})

test('resolveStatePath：相对的 DSH_HOME 也要抛错（否则同样随 cwd 漂移）', () => {
  assert.throws(
    () => resolveStatePath('', { DSH_HOME: 'relative-dsh-home' }),
    /DSH_HOME 必须是绝对路径/,
  )
  assert.throws(() => resolveStatePath('', { DSH_HOME: './x' }), /DSH_HOME 必须是绝对路径/)
  // 绝对路径照常
  assert.equal(
    resolveStatePath('', { DSH_HOME: '/abs/dsh' }),
    join('/abs/dsh', DEFAULT_STATE_DIRNAME, DEFAULT_STATE_FILENAME),
  )
  // 显式 statePath 仍然优先，且不受 DSH_HOME 影响
  assert.equal(
    resolveStatePath('/abs/explicit.json', { DSH_HOME: 'relative' }),
    '/abs/explicit.json',
  )
})

test('loadState：文件不存在 → 空映射且 existed=false（首次启动的正常情况）', () => {
  const path = tmpFile()
  const { records, existed } = loadState(path)
  assert.equal(existed, false)
  assert.equal(records.size, 0)
})

test('loadState：坏 JSON 必须抛错，绝不静默重建', () => {
  const path = tmpFile()
  writeFileSync(path, '{ this is not json', 'utf8')
  assert.throws(() => loadState(path), /无法解析/)
  rmSync(path, { force: true })
})

test('loadState：version 不符必须抛错（不做迁移猜测）', () => {
  const path = tmpFile()
  writeFileSync(path, JSON.stringify({ version: 99, conversations: {} }), 'utf8')
  assert.throws(() => loadState(path), /不支持的 state 版本/)
  rmSync(path, { force: true })
})

test('parseState：结构级校验逐条给出原因', () => {
  assert.equal(parseState(null).ok, false)
  assert.match(parseState([]).reason, /顶层必须是对象/)
  assert.match(parseState({ version: STATE_VERSION }).reason, /缺少 conversations/)
  assert.match(parseState({ version: STATE_VERSION, conversations: [] }).reason, /必须是「会话键 → 记录」/)
  assert.match(
    parseState({ version: STATE_VERSION, conversations: { 'private:1': 5 } }).reason,
    /必须是对象/,
  )
  assert.match(
    parseState({ version: STATE_VERSION, conversations: { 'private:1': null } }).reason,
    /必须是对象/,
  )
})

test('dshSessionId 类型不对必须抛错，不降级为 null', () => {
  assert.throws(() => normalizeRecord('private:1', { dshSessionId: 42 }), /必须是非空字符串/)
  assert.throws(() => normalizeRecord('private:1', { dshSessionId: '   ' }), /必须是非空字符串/)
  // 旧名 sessionId 作为一次性迁移别名可读入
  assert.equal(normalizeRecord('private:1', { sessionId: 'ob-old' }).dshSessionId, 'ob-old')
  // 缺省是 null（表示「这条记录还没有会话」），与「类型错」是两件事
  assert.equal(normalizeRecord('private:1', {}).dshSessionId, null)
})

test('newRecord 拒绝空 dshSessionId（否则下一条消息会静默新建会话）', () => {
  assert.throws(() => newRecord({ conversation: 'private:1', dshSessionId: '' }), /必须是非空字符串/)
  const record = newRecord({ conversation: 'private:1', dshSessionId: 'ob-1', now: 5 })
  assert.deepEqual(record, {
    conversation: 'private:1',
    dshSessionId: 'ob-1',
    cwd: null,
    createdAt: 5,
    lastActiveAt: 5,
    seq: 0,
  })
})

test('saveState → loadState 往返：字段一个不多一个不少', () => {
  const path = tmpFile()
  const records = new Map()
  records.set('group:200', newRecord({ conversation: 'group:200', dshSessionId: 'ob-a', now: 111 }))
  records.set('private:100', newRecord({ conversation: 'private:100', dshSessionId: 'ob-b', now: 222 }))
  saveState(path, records)

  const { records: loaded, existed } = loadState(path)
  assert.equal(existed, true)
  assert.deepEqual([...loaded.keys()], ['group:200', 'private:100'])
  assert.deepEqual(loaded.get('private:100'), {
    conversation: 'private:100',
    dshSessionId: 'ob-b',
    cwd: null,
    createdAt: 222,
    lastActiveAt: 222,
    seq: 0,
  })

  const raw = JSON.parse(readFileSync(path, 'utf8'))
  assert.deepEqual(Object.keys(raw).sort(), ['conversations', 'version'])
  // 落盘字段集合 = normalizeRecord 读入的字段集合（多一个少一个都是 bug）
  assert.deepEqual(
    Object.keys(raw.conversations['group:200']).sort(),
    ['createdAt', 'cwd', 'dshSessionId', 'lastActiveAt', 'seq'],
  )
  rmSync(path, { force: true })
})

test('serializeState 按会话键排序（让 diff 稳定）', () => {
  const serialized = serializeState([
    { conversation: 'private:2', dshSessionId: 'b' },
    { conversation: 'group:1', dshSessionId: 'a' },
  ])
  assert.deepEqual(Object.keys(serialized.conversations), ['group:1', 'private:2'])
})

test('saveState 是原子的：先写临时文件再 rename（不留半截 JSON）', () => {
  const path = tmpFile()
  saveState(path, new Map([['private:1', newRecord({ conversation: 'private:1', dshSessionId: 'ob-1' })]]))
  const text = readFileSync(path, 'utf8')
  assert.doesNotThrow(() => JSON.parse(text))
  assert.equal(text.endsWith('\n'), true)
  // 临时文件必须已经被 rename 掉
  assert.throws(() => readFileSync(`${path}.tmp-${process.pid}`), /ENOENT/)
  rmSync(path, { force: true })
})

test('saveState：重复写入不留临时文件残留，且内容始终可解析', () => {
  const path = tmpFile()
  const dir = dirname(path)
  const base = basename(path)
  for (let i = 0; i < 5; i += 1) {
    saveState(path, new Map([
      ['private:1', newRecord({ conversation: 'private:1', dshSessionId: `ob-${i}` })],
    ]))
  }
  const leftovers = readdirSync(dir).filter((name) => name.startsWith(`${base}.tmp-`))
  assert.deepEqual(leftovers, [], `不应残留临时文件，实际：${leftovers.join(', ')}`)
  const { records } = loadState(path)
  assert.equal(records.get('private:1').dshSessionId, 'ob-4')
  rmSync(path, { force: true })
})

test('saveState 失败时不动原文件（序列化期就抛错）', () => {
  const path = tmpFile()
  saveState(path, new Map([['private:1', newRecord({ conversation: 'private:1', dshSessionId: 'keep-me' })]]))
  const before = readFileSync(path, 'utf8')

  const boom = { conversations: new Map(), [Symbol.iterator]() { throw new Error('boom') } }
  assert.throws(() => saveState(path, boom), /boom/)

  assert.equal(readFileSync(path, 'utf8'), before)
  const leftovers = readdirSync(dirname(path))
    .filter((name) => name.startsWith(`${basename(path)}.tmp-`))
  assert.deepEqual(leftovers, [], `失败后不应残留临时文件，实际：${leftovers.join(', ')}`)
  rmSync(path, { force: true })
})

test('saveState 失败时不动原文件（进入 try 之后才失败）', () => {
  const path = tmpFile()
  saveState(path, new Map([['private:1', newRecord({ conversation: 'private:1', dshSessionId: 'keep-me' })]]))
  const before = readFileSync(path, 'utf8')

  // 把临时文件路径占成目录：openSync 会失败，从而真的走进清理分支
  const tmp = `${path}.tmp-${process.pid}`
  mkdirSync(tmp)
  try {
    assert.throws(() => saveState(path, new Map([
      ['private:1', newRecord({ conversation: 'private:1', dshSessionId: 'x' })],
    ])))
    assert.equal(readFileSync(path, 'utf8'), before, '原 state.json 必须原封不动')
  } finally {
    rmSync(tmp, { recursive: true, force: true })
    rmSync(path, { force: true })
  }
})
