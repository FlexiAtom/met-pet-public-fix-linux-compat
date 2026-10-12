#!/usr/bin/env node
/**
 * 把 dsh-onebot-relay 安装进某个 dsh profile。
 *
 * 做四件事（幂等、可重复运行、能修好旧版本留下的错误形态）：
 *   1. 备份 profile 的 package.json 与 cordis.patch.yml；
 *   2. 把 dist/*.tgz 复制到 profile 的 vendor/；
 *   3. 在 package.json 里补 dependencies 条目与 dsh.profile.bundles 声明；
 *   4. 在 profile 的 cordis.patch.yml 里补**本插件行的 id 覆盖**（完整 config）。
 *
 * 刻意不做的事：不重启 harness、不修改 profile 的其它行、不碰别家插件。
 *
 * 用法：
 *   node scripts/install-into-profile.mjs \
 *     --profile web \
 *     --self-id 123456789 \
 *     --meapet-token <与 MeaPet onebot.hub.access_token 一致> \
 *     --cwd   "C:\\path\\to\\agent\\workspace" \
 *     --meapet-ws-url ws://127.0.0.1:14321/onebot/v11/hub
 *
 * ---------------------------------------------------------------------------
 * 两个实测踩过的坑（都在这份脚本里修掉了，别再踩）：
 *
 * 坑 1：shipped 模板的 cordis.patch.yml 内容是一个**顶层空数组文档** `[]`。
 *       直接 append 会在 `[]` 之后形成第二个文档而没有 `---` 分隔符，YAML 直接报
 *       "end of the stream or a document separator is expected"。→ 先删掉裸 `[]`。
 *
 * 坑 2：本包自带的 cordis.patch.yml（bundle patch 层）**已经 insert 了插件行**。
 *       profile patch 是**后应用**的覆盖层，这里应该写「按 id 覆盖 config」，
 *       而不是再 insert 一次。→ 写覆盖形式，并自动把旧版本留下的 insert 形态改掉。
 *
 *       ⚠️ 纠正一条早先写在这里的说法：曾以为「再 insert 一次会让同一 id 出现两行」，
 *       但**实测没能复现**——组合器按 id 去重，`--dump-config` 在
 *       「bundle insert + profile insert」下也只显示 1 行。真正的问题有两层：
 *         - 本安装器的 YAML 自检**要求**覆盖形态（insert 形态会被判为不合格），
 *           所以 insert 形态下安装器无法收敛；
 *         - 只有覆盖形态能让重跑幂等（检测到已有覆盖行就跳过）。
 *       更值得记住的坑反而是反面：**覆盖只改已有行、自己不会建行**。profile 的
 *       `dsh.profile.bundles` 少一行时，覆盖层会静默作用不到任何东西
 *       （实测：那种形态下组合树里本插件 0 行 → 插件根本没加载）。
 */

import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PLUGIN_NAME = 'dsh-onebot-relay';
/** profile 覆盖层里 config 的缩进（`- id:` 在 0、`config:` 在 2、字段在 4） */
const CONFIG_INDENT = 4;

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) {
      out[key] = true;
    } else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

function fail(message) {
  console.error(`[install] 失败：${message}`);
  process.exit(1);
}

function stamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
    `-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
  );
}

function indentOf(line) {
  return line.match(/^[ \t]*/)[0].length;
}

function idLineRe(name) {
  return new RegExp(`^[ \\t]*-[ \\t]*id:[ \\t]*${name}[ \\t]*$`);
}

/** 依次从 profile、profiles/ 级、本包解析 yaml；都找不到返回空串。 */
function resolveYamlModule(profileDir) {
  const bases = [
    join(profileDir, 'package.json'),
    join(profileDir, '..', 'package.json'),
    join(PACKAGE_ROOT, 'package.json'),
  ];
  for (const base of bases) {
    try {
      return createRequire(base).resolve('yaml');
    } catch {
      /* 继续找下一个 */
    }
  }
  return '';
}

/**
 * 从本包自带的 patch 里抽出本插件的 config 体，重排成 profile 覆盖层的缩进。
 * 返回的每一行都已带好 CONFIG_INDENT 个空格。
 */
function configBodyFromPackaged() {
  const packaged = readFileSync(join(PACKAGE_ROOT, 'cordis.patch.yml'), 'utf8');
  const lines = packaged.split(/\r?\n/);
  const rowIdx = lines.findIndex((line) => idLineRe(PLUGIN_NAME).test(line));
  if (rowIdx === -1) fail('本包 cordis.patch.yml 里找不到本插件的行');
  const cfgIdx = lines.findIndex((line, i) => i > rowIdx && /^[ \t]*config:[ \t]*$/.test(line));
  if (cfgIdx === -1) fail('本包 cordis.patch.yml 里找不到本插件的 config: 块');
  const cfgIndent = indentOf(lines[cfgIdx]);

  let end = cfgIdx + 1;
  while (end < lines.length) {
    const line = lines[end];
    if (line.trim() === '') {
      end += 1;
      continue;
    }
    if (indentOf(line) <= cfgIndent) break;
    end += 1;
  }
  const body = lines.slice(cfgIdx + 1, end);
  const delta = CONFIG_INDENT - fieldIndentOf(body);
  return body.map((line) => {
    if (line.trim() === '') return '';
    const trimmed = line.trim();
    if (/^selfId:/.test(trimmed)) return `${' '.repeat(CONFIG_INDENT)}selfId: '${selfId}'`;
    if (/^meapetToken:/.test(trimmed)) return `${' '.repeat(CONFIG_INDENT)}meapetToken: '${meapetToken}'`;
    // YAML 单引号里反斜杠**不是**转义字符，写 \\ 会得到两个字面反斜杠。
    if (/^cwd:/.test(trimmed)) return `${' '.repeat(CONFIG_INDENT)}cwd: '${cwd.replace(/'/g, "''")}'`;
    if (/^meapetWsUrl:/.test(trimmed)) return `${' '.repeat(CONFIG_INDENT)}meapetWsUrl: '${meapetWsUrl}'`;
    return ' '.repeat(Math.max(0, indentOf(line) + delta)) + trimmed;
  });
}

/**
 * 删掉旧版本安装器写下的 `- insert:` 块（若其中含本插件行）。
 * 若该块只含本插件行则整块删除；若还有别的行，只摘掉我们那一行。
 *
 * 返回 { text, removed }。
 */
function stripLegacyInsert(text, name) {
  const lines = text.split(/\r?\n/);
  const out = [];
  let removed = false;
  let i = 0;
  while (i < lines.length) {
    const match = /^([ \t]*)-[ \t]*insert:[ \t]*$/.exec(lines[i]);
    if (!match) {
      out.push(lines[i]);
      i += 1;
      continue;
    }
    const indent = indentOf(lines[i]);
    let j = i + 1;
    while (j < lines.length) {
      const line = lines[j];
      if (line.trim() === '') {
        j += 1;
        continue;
      }
      if (indentOf(line) <= indent) break;
      j += 1;
    }
    const block = lines.slice(i + 1, j);
    const ours = block.findIndex((line) => idLineRe(name).test(line));
    if (ours === -1) {
      out.push(...lines.slice(i, j));
      i = j;
      continue;
    }
    const rowStarts = block
      .map((line, k) => (idLineRe('.*').test(line) ? k : -1))
      .filter((k) => k !== -1);
    if (rowStarts.length <= 1) {
      removed = true; // 整块都是我们的 → 全删
    } else {
      const next = rowStarts.find((k) => k > ours) ?? block.length;
      out.push(lines[i], ...block.slice(0, ours), ...block.slice(next));
      removed = true;
    }
    i = j;
  }
  return { text: out.join('\n'), removed };
}

/** config 体里「字段层」的缩进：取非空行的最小缩进。 */
function fieldIndentOf(lines) {
  let min = Infinity;
  for (const line of lines) {
    if (line.trim() === '') continue;
    min = Math.min(min, indentOf(line));
  }
  return Number.isFinite(min) ? min : 0;
}

/**
 * 按 delta 整体平移缩进（**保留相对层级**）。
 *
 * 早先的实现是 `' '.repeat(4) + line.trim()` —— 那会把嵌套行压平：
 * 一旦模板里出现 `allowUsers:` 下面挂 `- '10001'` 这种结构，列表项会和键同缩进，
 * YAML 立刻不合法。今天模板全是标量行所以没炸，但那是运气，不是设计。
 */
function shiftIndent(lines, delta) {
  return lines.map((line) => {
    if (line.trim() === '') return '';
    const next = Math.max(0, indentOf(line) + delta);
    return ' '.repeat(next) + line.trim();
  });
}

/** 从一行 config 里取字段名（注释行 / 空行得到 null）。 */
function keyOfConfigLine(line) {
  const m = /^[ \t]*([A-Za-z][A-Za-z0-9]*):/.exec(line);
  return m ? m[1] : null;
}

/** 找出某插件行自己的 config 体（id 覆盖形态与旧 insert 形态都能认）。 */
function existingConfigBody(text, name) {
  const lines = text.split(/\r?\n/);
  const rowIdx = lines.findIndex((line) => idLineRe(name).test(line));
  if (rowIdx === -1) return null;
  const rowIndent = indentOf(lines[rowIdx]);
  for (let i = rowIdx + 1; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.trim() === '') continue;
    if (indentOf(line) <= rowIndent) break;
    if (!/^[ \t]*config:[ \t]*$/.test(line)) continue;
    const cfgIndent = indentOf(line);
    let end = i + 1;
    while (end < lines.length) {
      const next = lines[end];
      if (next.trim() === '') {
        end += 1;
        continue;
      }
      if (indentOf(next) <= cfgIndent) break;
      end += 1;
    }
    return lines.slice(i + 1, end);
  }
  return null;
}

/**
 * 合并 config：**以现有的那份为底**（用户改过的值必须活下来），
 * 再把模板里有、现有那份没有的字段补上（取模板默认值）。
 *
 * 为什么不能直接用模板覆盖：重写只发生在「首次安装」与「从旧 insert 形态迁移」两种情况，
 * 而迁移的语义是**只改形态**。拿模板覆盖等于把用户手工调过的
 * `requireAt` / `streamMode` / `statePath`（以及新增的 `allowUsers`）悄悄重置 ——
 * 正是本脚本头部承诺"不修改其它行"时不该发生的事，而且**没有日志**。
 */
function mergeConfigBody(templateLines, existingLines, { forcedKeys = [] } = {}) {
  if (existingLines === null) {
    return {
      lines: templateLines,
      kept: [],
      added: templateLines.map(keyOfConfigLine).filter(Boolean),
    };
  }
  const forced = new Set(forcedKeys);
  const templateByKey = new Map();
  for (const line of templateLines) {
    const key = keyOfConfigLine(line);
    if (key !== null && !templateByKey.has(key)) templateByKey.set(key, line);
  }
  const delta = CONFIG_INDENT - fieldIndentOf(existingLines);
  const keptLines = [];
  const keptKeys = new Set();
  for (const line of shiftIndent(existingLines, delta)) {
    const key = keyOfConfigLine(line);
    if (key !== null && forced.has(key) && templateByKey.has(key)) {
      // 必填项以**命令行给的值**为准：否则迁移会留下过期的 token / 旧工作目录，
      // 而日志看起来一切正常（这正是本轮实测抓到的那个洞）。
      keptLines.push(templateByKey.get(key));
      keptKeys.add(key);
      continue;
    }
    keptLines.push(line);
    if (key !== null) keptKeys.add(key);
  }
  const addedLines = templateLines.filter((line) => {
    const key = keyOfConfigLine(line);
    return key !== null && !keptKeys.has(key);
  });
  return {
    lines:
      addedLines.length === 0
        ? keptLines
        : [
          ...keptLines,
          '',
          `${' '.repeat(Math.max(0, CONFIG_INDENT - 2))}# ── 安装器补齐：模板里有、你原来没有的字段（取默认值，按需修改）──`,
          ...addedLines,
        ],
    kept: [...keptKeys],
    added: addedLines.map(keyOfConfigLine).filter(Boolean),
  };
}

function findTgz(explicit) {
  if (explicit) {
    const p = resolve(explicit);
    if (!existsSync(p)) fail(`指定的 tgz 不存在：${p}`);
    return p;
  }
  const dist = join(PACKAGE_ROOT, 'dist');
  if (!existsSync(dist)) fail('dist/ 不存在，请先运行 node scripts/package-release.mjs');
  const candidates = readdirSync(dist)
    .filter((name) => name.startsWith(PLUGIN_NAME) && name.endsWith('.tgz'))
    .sort();
  if (candidates.length === 0) fail('dist/ 里没有 tgz，请先运行 node scripts/package-release.mjs');
  return join(dist, candidates[candidates.length - 1]);
}

const args = parseArgs(process.argv.slice(2));
const profile = String(args.profile || 'web');
const selfId = String(args['self-id'] || '').trim();
const meapetToken = String(args['meapet-token'] || '').trim();
const cwd = String(args.cwd || '').trim();
const meapetWsUrl = String(args['meapet-ws-url'] || 'ws://127.0.0.1:14321/onebot/v11/hub').trim();

const missing = [];
if (!selfId) missing.push('--self-id');
if (!meapetToken) missing.push('--meapet-token');
if (!cwd) missing.push('--cwd');
if (missing.length) {
  fail(
    `缺少必填参数 ${missing.join(' ')}。` +
      '这些值在插件里是 required，缺失会导致加载即失败，所以安装器拒绝写出不完整的 config。',
  );
}
if (!/^[0-9]+$/.test(selfId)) fail('--self-id 必须是纯数字 QQ 号');

const dshHome = process.env.DSH_HOME || join(homedir(), '.dsh');
const profileDir = join(dshHome, 'profiles', profile);
if (!existsSync(profileDir)) fail(`profile 目录不存在：${profileDir}`);

const pkgPath = join(profileDir, 'package.json');
const patchPath = join(profileDir, 'cordis.patch.yml');
const vendorDir = join(profileDir, 'vendor');
for (const p of [pkgPath, patchPath]) {
  if (!existsSync(p)) fail(`缺少 ${p}`);
}

const tgzSource = findTgz(args.tgz);
const tgzName = tgzSource.split(/[\\/]/).pop();
const vendorTarget = join(vendorDir, tgzName);

// --- 1. 备份 --------------------------------------------------------------- //
const backupDir = join(profileDir, `.install-backup-${PLUGIN_NAME}-${stamp()}`);
mkdirSync(backupDir, { recursive: true });
copyFileSync(pkgPath, join(backupDir, 'package.json'));
copyFileSync(patchPath, join(backupDir, 'cordis.patch.yml'));
console.log(`[install] 已备份 → ${backupDir}`);

// --- 2. vendor ------------------------------------------------------------- //
mkdirSync(vendorDir, { recursive: true });
copyFileSync(tgzSource, vendorTarget);
console.log(`[install] 已复制产物 → ${vendorTarget}`);

// --- 3. package.json ------------------------------------------------------ //
const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
pkg.dependencies = pkg.dependencies || {};
const wantSpec = `file:vendor/${tgzName}`;
const before = pkg.dependencies[PLUGIN_NAME];
pkg.dependencies[PLUGIN_NAME] = wantSpec;
pkg.dsh = pkg.dsh || {};
pkg.dsh.profile = pkg.dsh.profile || {};
const bundles = Array.isArray(pkg.dsh.profile.bundles) ? pkg.dsh.profile.bundles : [];
const hadBundle = bundles.includes(PLUGIN_NAME);
if (!hadBundle) bundles.push(PLUGIN_NAME);
pkg.dsh.profile.bundles = bundles;
writeFileSync(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`, 'utf8');
console.log(
  `[install] package.json：dependencies ${before ? `更新（原 ${before}）` : '新增'} → ${wantSpec}；` +
    `bundles ${hadBundle ? '已存在' : '新增'} ${PLUGIN_NAME}`,
);

// --- 4. cordis.patch.yml（id 覆盖层，不是再 insert） ----------------------- //
const originalPatch = readFileSync(patchPath, 'utf8');

// 先无条件清理旧版安装器留下的 insert 形态（即使覆盖行已存在也要清干净）。
// 理由不是"会留两行"（实测组合器按 id 去重，见文件头坑 2 的纠正），而是：
// 安装器的 YAML 自检要求覆盖形态，且只有覆盖形态能让重跑幂等。
const stripped = stripLegacyInsert(originalPatch, PLUGIN_NAME);
if (stripped.removed) {
  console.log('[install] 检测到旧版安装器留下的 insert 形态，已清理并改写为 id 覆盖形态');
}
const hasOverride = new RegExp(`^-[ \\t]*id:[ \\t]*${PLUGIN_NAME}[ \\t]*$`, 'm').test(stripped.text);

if (hasOverride && !stripped.removed) {
  console.log('[install] cordis.patch.yml 已是 id 覆盖形式，跳过（如需改配置请手工编辑）');
} else {
  let base = stripped.text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== '[]') // 坑 1：去掉模板的顶层空数组文档
    .join('\n');
  if (!base.endsWith('\n')) base += '\n';

  // 迁移语义是「只改形态」：**以你现有的 config 为底**，模板只补它没有的字段。
  // 只有在完全找不到本插件行时（首次安装）才整份用模板。
  const merged = mergeConfigBody(
    configBodyFromPackaged(),
    existingConfigBody(originalPatch, PLUGIN_NAME),
    // 这四个是命令行必填项，永远以命令行为准（合并时不许被旧值盖住）
    { forcedKeys: ['selfId', 'meapetToken', 'cwd', 'meapetWsUrl'] },
  );
  const mergedConfig = merged.lines;
  if (merged.kept.length > 0) {
    console.log(
      `[install] config 合并：保留你原有的 ${merged.kept.length} 个字段`
        + (merged.added.length > 0
          ? `；补上模板里有而你没有的 ${merged.added.length} 个（取默认值）：${merged.added.join(', ')}`
          : ''),
    );
  }

  const newText = hasOverride
    ? base
    : `${base}\n# --- ${PLUGIN_NAME}（由 scripts/install-into-profile.mjs 追加，按 id 覆盖）---\n`
      + [`- id: ${PLUGIN_NAME}`, '  config:', ...mergedConfig].join('\n')
      + '\n';
  writeFileSync(patchPath, newText, 'utf8');

  // 自证：用 yaml 解析刚写出的文件；不合法就回滚并报错（不信退出码）。
  const yamlPath = resolveYamlModule(profileDir);
  if (!yamlPath) {
    const text = readFileSync(patchPath, 'utf8');
    const bad = text.split(/\r?\n/).some((line) => line.trim() === '[]');
    if (bad || !text.includes(PLUGIN_NAME)) {
      copyFileSync(join(backupDir, 'cordis.patch.yml'), patchPath);
      copyFileSync(join(backupDir, 'package.json'), pkgPath);
      fail('patch 结构自检不通过（仍有裸 [] 或找不到本插件行），已回滚到备份');
    }
    console.log('[install] WARN 环境里找不到 yaml 模块，跳过严格解析自检（已做结构自检）');
  } else {
    try {
      const { parse } = await import(pathToFileURL(yamlPath).href);
      const doc = parse(readFileSync(patchPath, 'utf8'));
      if (!Array.isArray(doc)) throw new Error(`patch 顶层不是数组，而是 ${typeof doc}`);
      const ours = doc.filter((entry) => entry && typeof entry === 'object'
        && (entry.id === PLUGIN_NAME
          || (Array.isArray(entry.insert)
            && entry.insert.some((row) => row && row.id === PLUGIN_NAME))));
      if (ours.length !== 1) {
        throw new Error(`本插件在 patch 里出现 ${ours.length} 次（应为 1 次）`);
      }
      if (ours[0].id !== PLUGIN_NAME) {
        throw new Error('仍停留在 insert 形态（应为 id 覆盖形式）');
      }
      console.log('[install] PASS patch 自检：可被 YAML 解析，且本插件恰好出现 1 次（id 覆盖）');
    } catch (error) {
      copyFileSync(join(backupDir, 'cordis.patch.yml'), patchPath);
      copyFileSync(join(backupDir, 'package.json'), pkgPath);
      fail(`写出的 cordis.patch.yml 不合法，已回滚到备份：${error?.message ?? error}`);
    }
  }
  console.log(hasOverride
    ? `[install] cordis.patch.yml：已清理旧的 insert 形态（保留原有覆盖行）`
    : `[install] cordis.patch.yml：已写入 ${PLUGIN_NAME} 的完整 config 覆盖行`);
}

// --- 5. 安装判定自检 ------------------------------------------------------ //
console.log('\n[install] 文件级安装判定：');
console.log(`  1. 产物在 vendor/：${existsSync(vendorTarget) ? 'PASS' : 'FAIL'}  ${vendorTarget}`);
const links = join(profileDir, 'node_modules', PLUGIN_NAME);
console.log(
  `  2. node_modules 链接：${existsSync(links) ? 'PASS' : '待办'}\n` +
    `     → 由包管理器建立，请运行：dsh plugin --profile ${profile} add .\\vendor\\${tgzName}`,
);
const pkg2 = JSON.parse(readFileSync(pkgPath, 'utf8'));
const declared = (pkg2.dsh?.profile?.bundles || []).includes(PLUGIN_NAME);
console.log(`  3. profile 声明：${declared ? 'PASS' : 'FAIL'}（dsh.profile.bundles）`);
console.log('\n[install] 未做：重启 harness。配置文件改动需要重启才生效。');
