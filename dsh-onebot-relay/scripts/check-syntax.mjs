/**
 * 对包内每个 `.js` / `.mjs` 跑一次 `node --check`。
 *
 * 为什么要有它：`npm test` 的 `node --test` 只加载**被测试引用的**文件，
 * 而 `lib/index.js` 需要 dsh 依赖、测试里不会 import 它。没有这一步的话，
 * 一个纯语法错误能一路混到插件加载时才炸——那时报错在宿主日志里，
 * 排查成本高一个数量级。
 *
 * 用法：`node scripts/check-syntax.mjs`（失败时非零退出）。
 */
import { execFileSync } from 'node:child_process'
import { readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')

const SKIP_DIRS = new Set(['node_modules', '.git', 'dist'])

function collect(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue
    const full = join(dir, entry)
    const stats = statSync(full)
    if (stats.isDirectory()) out.push(...collect(full))
    else if (entry.endsWith('.js') || entry.endsWith('.mjs')) out.push(full)
  }
  return out
}

const files = collect(root).sort()
let failed = 0
for (const file of files) {
  const label = relative(root, file)
  try {
    // ⚠️ `stdio` **必须**是 inherit/ignore，不能用默认的 pipe：
    // 受限沙箱下子进程开不了命名管道，`pipe` 会直接 EPERM
    // （那不是语法错误，是权限边界）。inherit 让 node 自己把诊断打到终端。
    execFileSync(process.execPath, ['--check', file], { stdio: ['ignore', 'inherit', 'inherit'] })
    console.log(`PASS  node --check ${label}`)
  } catch (error) {
    failed += 1
    console.log(`FAIL  node --check ${label}  — 退出码 ${error?.status ?? '?'}`)
  }
}

console.log(`\n共 ${files.length} 个文件：${files.length - failed} PASS / ${failed} FAIL`)
if (failed > 0) process.exitCode = 1
