/**
 * `@deepseek-ai/dsh-agent` 的最小替身。
 *
 * 只在 `test/index-drive.test.mjs` 里、通过 `module.registerHooks` 的解析钩子
 * 挂上去——**不会**污染真实的 node_modules，也不会进 tgz。
 *
 * 替身只实现本插件真正调用的那一个导出（`installModelSelection`）。
 * 语义按已核实的类型定义还原：它把「选中的模型」写进 agent 的组装结果
 * （`dsh-agent/lib/types/model-selection.d.ts`），本插件不读回写结果，
 * 所以替身只记录调用、并把快照挂到 agentCtx 上供断言。
 */
export const installCalls = []

export function installModelSelection(agentCtx, options) {
  installCalls.push({ agentCtx, options })
  if (agentCtx !== null && typeof agentCtx === 'object') {
    agentCtx.modelSelection = options?.current ?? null
  }
  return undefined
}

/** 让每个用例从干净状态开始。 */
export function __resetInstallCalls() {
  installCalls.length = 0
}
