/**
 * 打一个**可直接安装**的 `.tgz` 到 `dist/`，不需要 npm、不需要网络。
 *
 * ## 为什么不用 `npm pack`
 *
 * 两个原因，都是实测出来的：
 *   1. `npm pack` 要写 npm 缓存（默认在 `%LOCALAPPDATA%\npm-cache`）。在受限沙箱 /
 *      只读用户目录的部署里它会直接 `EPERM` 死掉——那和「包有问题」是两回事，
 *      但报错长得一样。
 *   2. 这是纯 ESM、**零构建步骤**的包，`npm pack` 除了一条 tar 命令什么都不做。
 *
 * 所以这里用 `node:zlib` + 手写 ustar 头自己打。顺带的好处是**纳入哪些文件
 * 完全由 `package.json` 的 `files` 字段决定**（与 npm 的口径同源），
 * 不会出现「以为打进 tgz 了、其实漏了 lib/xxx.js」这种静默缺文件。
 *
 * 用法：`node scripts/package-release.mjs [输出目录]`（默认 `dist/`）。
 */
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))

/** 把简单 glob（`*` / `**`）转成正则；用于解释 `files` 里的排除项。 */
function globToRegExp(pattern) {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*/g, '\u0000')
    .replace(/\*/g, '[^/]*')
    .replace(/\u0000/g, '.*')
  return new RegExp(`^${escaped}$`)
}

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.git' || entry === 'dist' || entry === '.tmp') continue
    const full = join(dir, entry)
    const stats = statSync(full)
    if (stats.isDirectory()) walk(full, out)
    else if (stats.isFile()) out.push(full)
  }
  return out
}

/**
 * 按 `package.json` 的 `files` 字段挑出要打包的文件。
 *
 * 口径与 npm 一致：`files` 是**白名单**；`!` 开头是排除；目录项表示整棵子树；
 * `package.json` / `README*` / `LICENSE*` 永远包含（npm 的硬规则）。
 */
function selectFiles() {
  const patterns = Array.isArray(pkg.files) ? pkg.files : []
  const includes = []
  const excludes = []
  for (const raw of patterns) {
    const pattern = String(raw).trim()
    if (pattern === '') continue
    if (pattern.startsWith('!')) excludes.push(globToRegExp(pattern.slice(1)))
    else includes.push(pattern)
  }
  const always = [/^package\.json$/, /^README(\..+)?$/, /^LICENSE(\..+)?$/, /^LICENCE(\..+)?$/]
  const excludePatterns = [...excludes]
  const selected = new Set()

  const all = walk(root)
  for (const full of all) {
    const rel = relative(root, full).replace(/\\/g, '/')
    if (excludePatterns.some((re) => re.test(rel))) continue
    if (always.some((re) => re.test(rel))) {
      selected.add(rel)
      continue
    }
    const hit = includes.some((pattern) => {
      const clean = pattern.replace(/\/+$/, '')
      if (clean.includes('*')) return globToRegExp(clean).test(rel)
      // 目录项：整棵子树；文件项：精确匹配
      return rel === clean || rel.startsWith(`${clean}/`)
    })
    if (hit) selected.add(rel)
  }
  return [...selected].sort()
}

/** 造一个 ustar 头（512 字节）。 */
function tarHeader({ name, size, mtimeSeconds, type = '0' }) {
  const nameBytes = Buffer.byteLength(name, 'utf8')
  if (nameBytes > 99) {
    throw new Error(`tar 路径超过 99 字节，本脚本不支持长名扩展（prefix 字段）：${name}`)
  }
  const header = Buffer.alloc(512, 0)
  const write = (text, offset, length) => {
    Buffer.from(text, 'utf8').copy(header, offset, 0, Math.min(Buffer.byteLength(text, 'utf8'), length))
  }
  write(name, 0, 100)
  write('0000644\0', 100, 8)          // mode
  write('0000000\0', 108, 8)          // uid
  write('0000000\0', 116, 8)          // gid
  write(`${size.toString(8).padStart(11, '0')}\0`, 124, 12)
  write(`${mtimeSeconds.toString(8).padStart(11, '0')}\0`, 136, 12)
  write('        ', 148, 8)           // checksum 先填空格，最后回填
  write(type, 156, 1)
  write('ustar\0', 257, 6)            // magic + version
  write('00', 263, 2)
  write('root', 265, 32)              // uname
  write('root', 297, 32)              // gname
  let sum = 0
  for (const byte of header) sum += byte
  write(`${sum.toString(8).padStart(6, '0')}\0 `, 148, 8)
  return header
}

function pad512(size) {
  const remainder = size % 512
  return remainder === 0 ? 0 : 512 - remainder
}

function buildTarGz(files) {
  const blocks = []
  const seenDirs = new Set()
  for (const rel of files) {
    const parts = rel.split('/')
    for (let i = 1; i < parts.length; i += 1) {
      const dir = `${parts.slice(0, i).join('/')}/`
      if (seenDirs.has(dir)) continue
      seenDirs.add(dir)
      blocks.push(tarHeader({ name: `package/${dir}`, size: 0, mtimeSeconds: 0, type: '5' }))
    }
    const full = join(root, rel)
    const content = readFileSync(full)
    const mtimeSeconds = Math.floor(statSync(full).mtimeMs / 1000)
    blocks.push(tarHeader({ name: `package/${rel}`, size: content.length, mtimeSeconds }))
    blocks.push(content)
    const padding = pad512(content.length)
    if (padding > 0) blocks.push(Buffer.alloc(padding, 0))
  }
  // tar 的结束标记：两个全零块
  blocks.push(Buffer.alloc(1024, 0))
  const tar = Buffer.concat(blocks)
  return { tar, gz: gzipSync(tar, { level: 9 }) }
}

const outDir = resolve(root, process.argv[2] ?? 'dist')
const files = selectFiles()
if (files.length === 0) {
  console.error('没有匹配到任何文件——检查 package.json 的 files 字段。')
  process.exitCode = 1
} else {
  const { tar, gz } = buildTarGz(files)
  mkdirSync(outDir, { recursive: true })
  const tgzName = `${pkg.name}-${pkg.version}.tgz`
  const tgzPath = join(outDir, tgzName)
  writeFileSync(tgzPath, gz)
  const sha256 = createHash('sha256').update(gz).digest('hex')

  console.log(`${pkg.name}@${pkg.version}`)
  console.log(`打包 ${files.length} 个文件 → ${tgzPath}`)
  console.log(`  tar  ${tar.length} 字节`)
  console.log(`  tgz  ${gz.length} 字节`)
  console.log(`  sha256 ${sha256}`)
  console.log('\n内容：')
  for (const rel of files) {
    const size = statSync(join(root, rel)).size
    console.log(`  ${String(size).padStart(8)}  ${rel}`)
  }
  console.log('\n安装（由操作者执行，本脚本不做安装）：')
  console.log(`  dsh plugin --profile web add ${tgzPath}`)
  console.log('\n本包是纯 ESM、无构建步骤，因此 tgz 安装路径不需要 pnpm 的 allowBuilds 授权。')
}
