/**
 * `lib/session-title.js` 的单测 —— 四条降级路径必须都不抛错。
 *
 * 起标题是锦上添花：任何一条失败路径都不该让一条 QQ 消息投不出去。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  TITLE_MAX_BYTES, TITLE_RESULT, applySessionTitle, titleByteLength,
} from '../lib/session-title.js'

const SESSION = { id: 'ob-1' }
const TEMPLATE = '喵桥 · {messageType}/{sessionId}'

/** 假 session-title 服务。 */
function fakeTitles(behavior = 'ok') {
  const calls = []
  return {
    calls,
    rename(session, title) {
      calls.push({ session, title })
      if (behavior === 'throw') throw new Error('boom')
      if (behavior === 'invalid') {
        const error = new Error('bad title')
        error.name = 'SessionTitleInvalidError'
        throw error
      }
      // 服务会按字节截断：这里模拟截到 5 个字符
      return { title: title.slice(0, 5), eventSeq: 1 }
    },
  }
}

test('正常路径：渲染交出去，落盘结果取服务返回值', () => {
  const titles = fakeTitles()
  const result = applySessionTitle({
    titles, session: SESSION, template: TEMPLATE, conversation: 'group:123',
  })
  assert.equal(result.ok, true)
  assert.equal(result.reason, TITLE_RESULT.OK)
  assert.equal(result.rendered, '喵桥 · group/123')
  // 落盘结果取**服务返回值**（它可能已被 dsh 侧按字节截断），rendered 才是我们交出去的原文
  assert.equal(result.title, result.rendered.slice(0, 5))
  assert.notEqual(result.title, result.rendered)
  assert.deepEqual(titles.calls, [{ session: SESSION, title: '喵桥 · group/123' }])
})

test('没有 live session → no-session（时序不对，不是错误）', () => {
  assert.equal(applySessionTitle({ titles: fakeTitles(), session: null }).reason, TITLE_RESULT.NO_SESSION)
  assert.equal(applySessionTitle({ titles: fakeTitles() }).reason, TITLE_RESULT.NO_SESSION)
})

test('空标题 → empty-title', () => {
  const result = applySessionTitle({ titles: fakeTitles(), session: SESSION, template: '   ', conversation: 'private:1' })
  assert.equal(result.ok, false)
  assert.equal(result.reason, TITLE_RESULT.EMPTY_TITLE)
})

test('服务缺席 → service-missing（容错分支，调用方应静默跳过）', () => {
  for (const titles of [undefined, null, {}, { rename: 'not-a-function' }]) {
    const result = applySessionTitle({ titles, session: SESSION, template: TEMPLATE, conversation: 'private:1' })
    assert.equal(result.ok, false)
    assert.equal(result.reason, TITLE_RESULT.SERVICE_MISSING)
    assert.equal(result.rendered, '喵桥 · private/1')
  }
})

test('服务抛 SessionTitleInvalidError → invalid-title；其他异常 → rename-failed', () => {
  const invalid = applySessionTitle({
    titles: fakeTitles('invalid'), session: SESSION, template: TEMPLATE, conversation: 'private:1',
  })
  assert.equal(invalid.reason, TITLE_RESULT.INVALID_TITLE)
  assert.equal(invalid.rendered, '喵桥 · private/1')

  const broken = applySessionTitle({
    titles: fakeTitles('throw'), session: SESSION, template: TEMPLATE, conversation: 'private:1',
  })
  assert.equal(broken.reason, TITLE_RESULT.RENAME_FAILED)
  assert.equal(String(broken.error), 'Error: boom')
})

test('titleByteLength 是 UTF-8 字节数（多字节字符按 3/2 字节算）', () => {
  assert.equal(titleByteLength('abc'), 3)
  assert.equal(titleByteLength('喵桥'), 6)
  assert.equal(titleByteLength('·'), 2)
  assert.equal(titleByteLength(''), 0)
  assert.equal(titleByteLength(null), 0)
  // 上限口径与 dsh 侧 session-title 的 maxTitleBytes 一致
  assert.equal(TITLE_MAX_BYTES, 80)
})
