/**
 * 测试驱动：逐个跑 `test/*.test.mjs`。
 *
 * ## 为什么不用 `node --test test/`
 *
 * `node --test <目录>` 会**为每个测试文件 spawn 一个子进程并捕获它的输出**。
 * 在受限沙箱里，子进程开不了命名管道，于是整条命令直接死在
 * `Error: spawn EPERM` 上——那不是测试失败，是权限边界（见
 * `dsh-plugin-dev` skill 的 references 与运行环境的说明）。
 *
 * 这里的做法等价但可移植：用 `stdio: 'inherit'` 逐个跑（inherit 不建管道），
 * 自己汇总退出码。在不受限的环境里，`node --test test/` 也能跑，两种都行。
 *
 * 用法：`node scripts/run-tests.mjs`
 */
import { execFileSync } from 'node:child_process'
import { closeSync, mkdirSync, openSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const testDir = join(root, 'test')

const files = readdirSync(testDir)
  .filter((entry) => entry.endsWith('.test.mjs'))
  .sort()

/**
 * 把子进程输出落到文件而不是管道（沙箱下管道会 EPERM），读回来既展示也**解析**。
 *
 * 为什么要解析：只看退出码的话，「**一个测试都没跑**」的文件也算通过
 * （node:test 在没有用例时正常退出 0）。这类假绿真实发生过——一次编辑把用例
 * 挪进/挪出别的类，数量变了却不会有人发现。所以这里额外要求每个文件
 * `pass >= 1` 且 `fail == 0`。
 */
const scratchDir = join(testDir, '.tmp')
const logPath = join(scratchDir, 'run-tests.log')

function runOne(file) {
  mkdirSync(scratchDir, { recursive: true })
  const fd = openSync(logPath, 'w')
  let status = 0
  try {
    execFileSync(process.execPath, [join(testDir, file)], { stdio: ['ignore', fd, fd] })
  } catch (error) {
    status = error?.status ?? 1
  } finally {
    closeSync(fd)
  }
  const output = readFileSync(logPath, 'utf8')
  process.stdout.write(output)
  const pass = Number(/^ℹ pass (\d+)$/m.exec(output)?.[1] ?? 0)
  const fail = Number(/^ℹ fail (\d+)$/m.exec(output)?.[1] ?? 0)
  return { status, pass, fail }
}

if (files.length === 0) {
  console.log('test/ 下没有 *.test.mjs，什么都没跑。')
  process.exitCode = 1
} else {
  let failed = 0
  let totalPass = 0
  const failures = []
  for (const file of files) {
    const label = relative(root, join(testDir, file))
    console.log(`\n══════ ${label} ${'═'.repeat(Math.max(0, 40 - label.length))}`)
    const { status, pass, fail } = runOne(file)
    totalPass += pass
    if (status !== 0 || fail > 0) {
      failed += 1
      failures.push(`${label}（退出码 ${status}，fail ${fail}）`)
    } else if (pass === 0) {
      failed += 1
      failures.push(`${label}（退出码 0 但**一个用例都没跑**——多半是用例被挪走/注释掉了）`)
    }
  }
  console.log('\n════════════════════════════════════════════════════════')
  console.log(`共 ${files.length} 个测试文件：${files.length - failed} 通过 / ${failed} 失败；累计 ${totalPass} 个用例`)
  for (const label of failures) console.log(`  FAIL  ${label}`)
  if (failed > 0) process.exitCode = 1
}
