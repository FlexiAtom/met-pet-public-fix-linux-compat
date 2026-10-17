/**
 * `lib/contract.js` 的纯函数单测。
 *
 * 这一层是**线上形状的唯一权威**，所以钉的是边界而不是平均值：
 * 转义顺序、`post_type` 守卫、代理对切分、两段式会话键解析。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  ALLOWED_ACTIONS, APPROVAL_OUTCOME, PLUGIN_NAME, RETCODE, STATUS,
  chunkText, commonPrefixLength, conversationKeyOf, digitsOf, escapeCq, hasAtSelf, isApiResponse,
  isOkResponse, joinPath, makeActionFrame, makeResponse, newEcho, newSessionId,
  normalizeMessage, numericId, parseApprovalCommand, parseConversation,
  parseCq, parseLocalCommand, renderSessionTitle, segmentsToText,
  selfIdOf, targetOf, unescapeCq,
} from '../lib/contract.js'

test('parseConversation 按**两段**切（不是 astrbot 的三段）', () => {
  assert.deepEqual(parseConversation('private:123'), { messageType: 'private', sessionId: '123' })
  assert.deepEqual(parseConversation('group:456'), { messageType: 'group', sessionId: '456' })
  // sessionId 里带冒号要完整保留
  assert.deepEqual(parseConversation('group:4:5'), { messageType: 'group', sessionId: '4:5' })
  assert.deepEqual(parseConversation(''), { messageType: '', sessionId: '' })
})

test('renderSessionTitle 支持本插件的占位符，未知占位符原样保留', () => {
  assert.equal(
    renderSessionTitle('喵桥 · {messageType}/{sessionId}', 'group:123'),
    '喵桥 · group/123',
  )
  assert.equal(renderSessionTitle('{conversation}', 'private:9'), 'private:9')
  assert.equal(renderSessionTitle('{bridge}', 'private:9'), PLUGIN_NAME)
  // 未知占位符保留 → 配置写错时看得见，而不是静默产出空标题
  assert.equal(renderSessionTitle('{nope}-{messageType}', 'private:9'), '{nope}-private')
})

test('conversationKeyOf 只认 message 事件，且按 message_type 判群/私聊', () => {
  assert.equal(conversationKeyOf({ post_type: 'message', message_type: 'private', user_id: 7 }), 'private:7')
  assert.equal(
    conversationKeyOf({ post_type: 'message', message_type: 'group', group_id: 8, user_id: 7 }),
    'group:8',
  )
  // 私聊事件里残留了 group_id：判据是 message_type，不能被 group_id 抢走
  assert.equal(
    conversationKeyOf({ post_type: 'message', message_type: 'private', user_id: 7, group_id: 8 }),
    'private:7',
  )
  assert.equal(conversationKeyOf({ post_type: 'notice', notice_type: 'friend_add', user_id: 7 }), null)
  assert.equal(conversationKeyOf({ post_type: 'meta_event' }), null)
  assert.equal(conversationKeyOf({ post_type: 'message', message_type: 'private' }), null)
  assert.equal(conversationKeyOf(null), null)
})

test('digitsOf / selfIdOf / numericId 对数字与字符串都稳', () => {
  assert.equal(digitsOf(7), '7')
  assert.equal(digitsOf(' 8 '), '8')
  assert.equal(digitsOf(-1), null)
  assert.equal(digitsOf(1.5), null)
  assert.equal(digitsOf(Number.MAX_SAFE_INTEGER + 2), null)
  assert.equal(selfIdOf({ self_id: 10001 }), '10001')
  assert.equal(selfIdOf({}), null)
  assert.equal(numericId('10002'), 10002)
  assert.equal(numericId('x'), 'x')
})

test('CQ 码：转义顺序（&amp; 最后解）与参数解析', () => {
  assert.equal(unescapeCq('a&#91;b&#93;c&#44;d&amp;e'), 'a[b]c,d&e')
  // `&amp;#91;` 是用户**真的**想发 `&#91;` 这四个字符，不能二次解码成 `[`
  assert.equal(unescapeCq('&amp;#91;'), '&#91;')
  assert.equal(escapeCq('a[b],c&d'), 'a&#91;b&#93;&#44;c&amp;d')
  assert.equal(escapeCq(unescapeCq('&#91;&#93;&#44;&amp;')), '&#91;&#93;&#44;&amp;')

  const segments = parseCq('前[CQ:at,qq=10001]中[CQ:image,file=a.jpg,url=http://x/y?z=1]后')
  assert.deepEqual(segments, [
    { type: 'text', data: { text: '前' } },
    { type: 'at', data: { qq: '10001' } },
    { type: 'text', data: { text: '中' } },
    { type: 'image', data: { file: 'a.jpg', url: 'http://x/y?z=1' } },
    { type: 'text', data: { text: '后' } },
  ])
  // 无 `=` 的参数不丢信息
  assert.deepEqual(parseCq('[CQ:rps]'), [{ type: 'rps', data: {} }])
  assert.deepEqual(parseCq(''), [])
})

test('normalizeMessage 收规范里允许的三种形态', () => {
  const fromString = normalizeMessage('[CQ:at,qq=1] hi')
  assert.deepEqual(fromString, [
    { type: 'at', data: { qq: '1' } },
    { type: 'text', data: { text: ' hi' } },
  ])
  const fromArray = normalizeMessage([{ type: 'text', data: { text: 'hi' } }, 'tail'])
  assert.deepEqual(fromArray, [
    { type: 'text', data: { text: 'hi' } },
    { type: 'text', data: { text: 'tail' } },
  ])
  const fromObject = normalizeMessage({ type: 'text', data: { text: 'solo' } })
  assert.deepEqual(fromObject, [{ type: 'text', data: { text: 'solo' } }])
  // data 不是对象 → 空对象，而不是把数组塞进去
  assert.deepEqual(normalizeMessage({ type: 'text', data: [1, 2] }), [{ type: 'text', data: {} }])
  assert.deepEqual(normalizeMessage(null), [])
})

test('hasAtSelf 与 segmentsToText 的口径（@全体成员、stripAtSelf）', () => {
  const atSelf = normalizeMessage('[CQ:at,qq=10001] 看这个')
  assert.equal(hasAtSelf(atSelf, '10001'), true)
  assert.equal(hasAtSelf(atSelf, '99'), false)
  assert.equal(hasAtSelf(normalizeMessage('[CQ:at,qq=all] 看这个'), '10001'), true)
  // selfId 为空时只认 all，不能把「@任何人」当成「@了我」
  assert.equal(hasAtSelf(atSelf, ''), false)
  assert.equal(hasAtSelf(normalizeMessage('[CQ:at,qq=all]'), ''), true)

  assert.equal(segmentsToText(atSelf, { selfId: '10001' }), '看这个')
  assert.equal(segmentsToText(atSelf, { selfId: '10001', stripAtSelf: false }), '@10001 看这个')
  assert.equal(
    segmentsToText(normalizeMessage('[CQ:image,file=a] 这是什么'), { selfId: '1' }),
    '[图片] 这是什么',
  )
  // reply 段（引用回复）不产出文本，与 MeaPet 的占位表一致
  assert.equal(segmentsToText(normalizeMessage('[CQ:reply,id=1] 好'), {}), '好')
})

test('isApiResponse 的 post_type 守卫（事件里恰好有 status 也不能被吞）', () => {
  assert.equal(isApiResponse({ status: 'ok', retcode: 0, echo: 'e' }), true)
  assert.equal(isApiResponse({ retcode: 0 }), true)
  assert.equal(isApiResponse({ echo: 'e' }), true)
  assert.equal(isApiResponse({ data: {} }), false)
  assert.equal(isApiResponse({ post_type: 'message', status: 'ok' }), false)
  assert.equal(isApiResponse(null), false)
  assert.equal(isApiResponse([]), false)
})

test('makeActionFrame / makeResponse / isOkResponse 的帧形状', () => {
  const frame = makeActionFrame('send_private_msg', { user_id: 1 }, 'e-1')
  assert.deepEqual(frame, { action: 'send_private_msg', params: { user_id: 1 }, echo: 'e-1' })
  // params 不是对象时收成 {}，而不是把 undefined 塞进 JSON（JSON.stringify 会丢键，
  // 对端读 `params.user_id` 就炸）
  assert.deepEqual(makeActionFrame('get_status', undefined, 'e-2').params, {})
  assert.deepEqual(makeActionFrame('get_status', [1], 'e-3').params, {})

  assert.deepEqual(makeResponse({ status: STATUS.OK, retcode: RETCODE.OK, data: { a: 1 }, echo: 'e' }), {
    status: 'ok', retcode: 0, data: { a: 1 }, echo: 'e',
  })
  // data / echo 缺省写 null（规范 §8.4 的失败示例就是 data: null）
  const failed = makeResponse({ status: STATUS.FAILED, retcode: RETCODE.BAD_PARAM })
  assert.equal(failed.data, null)
  assert.equal(failed.echo, null)
  assert.equal('error' in failed, false)
  assert.equal(makeResponse({ error: 'boom' }).error, 'boom')

  assert.equal(isOkResponse({ status: 'ok', retcode: 0 }), true)
  assert.equal(isOkResponse({ status: 'async', retcode: 0 }), false)
  assert.equal(isOkResponse({ status: 'ok', retcode: 1 }), false)
  assert.equal(isOkResponse(null), false)
})

test('newEcho 唯一（同毫秒内也不能撞）', () => {
  const echoes = new Set()
  for (let i = 0; i < 500; i += 1) echoes.add(newEcho('dsh'))
  assert.equal(echoes.size, 500)
  assert.match([...echoes][0], /^dsh-[0-9a-z]+-[0-9a-z]+$/)
})

test('targetOf 按会话键挑 action 与目标键', () => {
  assert.deepEqual(targetOf('private:10002', 'hi'), {
    action: 'send_private_msg', params: { user_id: 10002, message: 'hi' },
  })
  assert.deepEqual(targetOf('group:10003', 'hi'), {
    action: 'send_group_msg', params: { group_id: 10003, message: 'hi' },
  })
  // 超大 QQ 号（超出安全整数）退回字符串，绝不静默改数
  const huge = '99999999999999999999'
  assert.equal(targetOf(`private:${huge}`, 'x').params.user_id, huge)
})

test('chunkText 按码点切，不劈坏代理对，也不产空段', () => {
  const emoji = '🙂🙃😀😁😂🤣'
  const pieces = chunkText(emoji, 2)
  assert.equal(pieces.join(''), emoji)
  for (const piece of pieces) {
    // 每个片段都必须是完整的码点序列（Array.from 长度 == UTF-16 长度的一半关系不成立时说明劈了）
    assert.equal([...piece].join(''), piece)
    assert.equal(/\p{Surrogate}/u.test(piece), false)
  }

  assert.deepEqual(chunkText('', 10), [])
  assert.deepEqual(chunkText('abc', 10), ['abc'])
  // 优先在句末断
  const sentences = chunkText('一二三四五。六七八九十。', 8)
  assert.equal(sentences[0], '一二三四五。')
  // 极限：limit=1 也要能终止
  assert.equal(chunkText('abc', 1).join(''), 'abc')
})

test('chunkText 的不变量：不丢字、不重复、不超限、不产空段（语料 + 伪随机）', () => {
  // 断点字符本身也要进语料：切点算法正是围着它们转的
  const ALPHABET = [
    'a', 'Z', '0', ' ', '，', '。', '！', '？', '、', '）', ')',
    '\n', '\n', '\n', '喵', '好', '🙂', '🧪', '𐍈', '\u{1F600}', '　',
  ]
  const inputs = [
    '', ' ', '\n', '\n\n', '\n\n\n', 'abc', 'a'.repeat(1500),
    '喵'.repeat(3000), '🙂'.repeat(100), '\n\n' + 'x'.repeat(50),
    '一二三四五。'.repeat(200), 'a\n\nb', 'x' + '。'.repeat(10),
    '　' + '喵'.repeat(7), '🙂🙃😀😁😂🤣'.repeat(50),
    '。'.repeat(500), '\n'.repeat(500), ' '.repeat(500),
    'mixed 喵 🙂 。\n\n tail', 'a'.repeat(16), '喵'.repeat(16),
  ]
  // 固定种子的伪随机语料：可复现，失败时能照着重放
  let seed = 20261011
  const next = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648
    return seed
  }
  for (let index = 0; index < 60; index += 1) {
    const length = 1 + (next() % 200)
    let text = ''
    for (let j = 0; j < length; j += 1) text += ALPHABET[next() % ALPHABET.length]
    inputs.push(text)
  }

  const limits = [1, 2, 3, 7, 8, 13, 50, 64, 99, 1500]
  for (const text of inputs) {
    for (const limit of limits) {
      const pieces = chunkText(text, limit)
      const where = `limit=${limit} len=${[...text].length} head=${JSON.stringify(text.slice(0, 30))}`
      assert.equal(pieces.join(''), text, `丢字或重复：${where}`)
      if (text !== '') assert.equal(pieces.length > 0, true, `非空文本不该切成 0 段：${where}`)
      for (const piece of pieces) {
        assert.notEqual(piece, '', `不得产出空段：${where}`)
        assert.equal(
          [...piece].length <= limit, true,
          `片段超限：${[...piece].length} > ${limit}（${where}）`,
        )
        assert.equal(/\p{Surrogate}/u.test(piece), false, `不得劈坏代理对：${where}`)
      }
    }
  }
})

test('commonPrefixLength 是码点级（preview 模式的尾巴切点靠它）', () => {
  assert.equal(commonPrefixLength('abc', 'abd'), 2)
  assert.equal(commonPrefixLength('abc', 'abcdef'), 3)
  assert.equal(commonPrefixLength('xyz', 'abc'), 0)
  assert.equal(commonPrefixLength('', 'abc'), 0)
  // 码点级：emoji 不能被算成两个码元
  assert.equal(commonPrefixLength('🙂a', '🙂b'), 1)
  assert.equal(commonPrefixLength('🙂🙃', '🙂🙂'), 1)
})

test('parseApprovalCommand / parseLocalCommand', () => {
  assert.deepEqual(parseApprovalCommand('/approve K7Q2'), { kind: 'approve', code: 'K7Q2' })
  assert.deepEqual(parseApprovalCommand('/reject k7q2'), { kind: 'reject', code: 'K7Q2' })
  assert.deepEqual(parseApprovalCommand('/dsh approve ab12'), { kind: 'approve', code: 'AB12' })
  assert.equal(parseApprovalCommand('approve K7Q2'), null)
  assert.equal(parseApprovalCommand('/approve'), null)
  assert.equal(parseApprovalCommand('/approve too-long-code-here'), null)

  assert.deepEqual(parseLocalCommand('/help'), { kind: 'help' })
  assert.deepEqual(parseLocalCommand('/status'), { kind: 'status' })
  // 其余斜杠命令**照常投给 agent**——本插件不替 agent 决定哪些斜杠是「我的」
  assert.equal(parseLocalCommand('/clear'), null)
  assert.equal(parseLocalCommand('普通消息'), null)
})

test('常量表与 MeaPet 的白名单对齐（口径一致性守卫）', () => {
  assert.equal(ALLOWED_ACTIONS.length, 17)
  assert.equal(ALLOWED_ACTIONS.includes('send_private_msg'), true)
  assert.equal(ALLOWED_ACTIONS.includes('get_version_info'), true)
  // 不在 MeaPet 白名单里的动作不得混进来
  assert.equal(ALLOWED_ACTIONS.includes('set_group_ban'), false)
  assert.deepEqual(APPROVAL_OUTCOME, { ALLOWED_ONCE: 'allowed-once', REJECTED: 'rejected' })
})

test('newSessionId 带 ob- 前缀且不重复', () => {
  const ids = new Set()
  for (let i = 0; i < 200; i += 1) ids.add(newSessionId())
  assert.equal(ids.size, 200)
  assert.match([...ids][0], /^ob-[0-9a-z]+-[0-9a-f]{8}$/)
})

test('joinPath 恰好一个斜杠且无尾斜杠', () => {
  assert.equal(joinPath('/onebot-relay', '/ws'), '/onebot-relay/ws')
  assert.equal(joinPath('/onebot-relay/', 'ws'), '/onebot-relay/ws')
  assert.equal(joinPath('', '/ws'), '/ws')
})
