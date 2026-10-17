#!/usr/bin/env node
/**
 * 重启前自检：把 profile patch 里**实际写入的** config 过一遍插件自己的 schema 与
 * 加载期校验，提前抓住「一重启就加载失败」的问题。
 *
 * 这比 `dsh --dump-config` 更安全：dump 会重写 cordis.yml，可能触发 live 热重载；
 * 本脚本只读配置文件，不写任何东西。
 *
 * 用法（在 profile 目录里跑，或直接跑本脚本）：
 *   node scripts/validate-installed-config.mjs [--profile web]
 */

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const PLUGIN = 'dsh-onebot-relay';

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i].startsWith('--')) {
      const key = argv[i].slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) out[key] = true;
      else {
        out[key] = next;
        i += 1;
      }
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const profile = String(args.profile || 'web');
const dshHome = process.env.DSH_HOME || join(homedir(), '.dsh');
const profileDir = join(dshHome, 'profiles', profile);
const patchPath = join(profileDir, 'cordis.patch.yml');

// ESM 的裸包名按**脚本所在目录**解析，所以要从 profile 里解析依赖，
// 这样 yaml 与插件本身都走 profile 的 node_modules（与真实加载路径一致）。
const requireFromProfile = createRequire(join(profileDir, 'package.json'));
const { parse: parseYaml } = await import(
  pathToFileURL(requireFromProfile.resolve('yaml')).href
);

console.log(`[validate] patch: ${patchPath}`);
const doc = parseYaml(readFileSync(patchPath, 'utf8'));

// patch 里本插件是一个 `- insert: [...]` 行里的对象（也可能被写成顶层行）
const rows = [];
for (const item of doc ?? []) {
  if (item && Array.isArray(item.insert)) rows.push(...item.insert);
  else if (item && typeof item === 'object') rows.push(item);
}
const row = rows.find((r) => r && r.id === PLUGIN);
if (!row) {
  console.error(`[validate] FAIL patch 里找不到 id: ${PLUGIN}`);
  process.exit(1);
}
console.log(`[validate] 找到行 id=${row.id} name=${row.name}`);

const { Config, assertConfigIsUsable, configWarnings } = await import(
  pathToFileURL(requireFromProfile.resolve(`${PLUGIN}/config`)).href
);

let coerced;
try {
  // schemastery 的 Schema 是可调用的：调用即校验并填默认值
  coerced = Config(row.config ?? {});
} catch (error) {
  console.error(`[validate] FAIL schema 校验失败：${error?.message ?? error}`);
  process.exit(1);
}
console.log('[validate] PASS schema 校验通过（已填默认值）');

try {
  assertConfigIsUsable(coerced);
} catch (error) {
  console.error(`[validate] FAIL 加载期校验不通过：${error?.message ?? error}`);
  process.exit(1);
}
console.log('[validate] PASS 加载期校验通过（必填项齐全、取值合法）');

const warnings = typeof configWarnings === 'function' ? configWarnings(coerced) : [];
for (const warning of warnings) console.log(`[validate] WARN ${warning}`);

/**
 * 打印 URL 前把查询串里的凭据抹掉。
 *
 * `meapetWsUrl` 允许带 `?access_token=`（本插件就有 `tokenInQuery` 开关，
 * 有些实现端只认这种鉴权），而这是个会给用户看、会被贴进 issue 的诊断脚本——
 * 直接把 URL 原样打出来等于回显密钥。日志规范里"不记 token"同样适用于这里。
 */
function redactUrl(value) {
  return String(value ?? '').replace(/([?&](?:access_?token|token)=)[^&#\s]*/gi, '$1***');
}

console.log('[validate] 关键取值：');
// meapetToken / serverToken **一概不打印**（连"打印时再遮"的口子都不留）
for (const key of ['clientEnabled', 'meapetWsUrl', 'selfId', 'cwd', 'pathPrefix', 'requireAt',
  'serverEnabled', 'serverPort', 'statePath']) {
  const value = coerced[key];
  const shown = key === 'meapetWsUrl' ? redactUrl(value) : value;
  console.log(`  ${key} = ${JSON.stringify(shown)}`);
}
console.log('[validate] 结论：配置可以安全重启加载。');
