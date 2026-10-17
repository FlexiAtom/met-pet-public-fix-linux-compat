/**
 * `@deepseek-ai/dsh-llm` 的最小替身。
 *
 * 只实现本插件调用的 `createUserMessage`。真实实现会做 deep-freeze 与来源标注
 * （`{kind:'user'}` 走 `MessageSourceMap`），本插件不依赖那些副作用，
 * 只需要一个能把 `content` / `source` 原样带下去的对象。
 */
export function createUserMessage(input) {
  return { __userMessage: true, ...input }
}
