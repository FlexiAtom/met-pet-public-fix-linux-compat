#!/usr/bin/env node
/**
 * 一次性 dsh 实验场：**在不碰用户 profile 的前提下**验证本插件能被真实 dsh 加载。
 *
 * 为什么需要它：单元测试里 dsh 侧是替身，"语法对 + 类型定义里 API 存在" 都不等于
 * "真的挂得上"。而直接重启用户的 harness 代价大、还要审批。折中做法是**另起一个
 * DSH_HOME**（默认建在本包所在的目录里），从 shipped 模板拉一个一次性 profile，
 * 装上本插件，然后：
 *
 *   1. `--dump-config`：确认插件行在组合后的树里**恰好出现一次**、且没有
 *      "skipping profile bundle" 告警；
 *   2. 真起一个 harness（`--no-open --port <port>`），打 `{prefix}/status`：
 *      无 token 必须 401、带 token 必须 200 且 plugin 字段正确。
 *
 * 跑完默认把实验场删掉（`--keep` 保留）。
 *
 * ⚠️ 沙箱注意：Node 的 child_process 用默认 `stdio:'pipe'` 在受限沙箱下会 EPERM，
 * 所以这里捕获子进程输出走的是「重定向到文件」而不是管道。
 *
 * 用法：
 *   node scripts/verify-in-throwaway-harness.mjs \
 *     --self-id 123456789 --meapet-token <token> \
 *     --meapet-ws-url ws://127.0.0.1:14321/onebot/v11/hub
 */

import { spawn, spawnSync } from 'node:child_process';
import {
  closeSync, existsSync, mkdirSync, openSync, readFileSync, rmSync, writeFileSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PLUGIN_NAME = 'dsh-onebot-relay';

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) out[key] = true;
    else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

function log(message) {
  console.log(`[verify] ${message}`);
}

/**
 * 打印 URL 前抹掉查询串里的凭据。
 *
 * `client.url` 来自插件的 `/status`，而配置允许在 URL 上带 `?access_token=`
 * （插件有 `tokenInQuery` 开关）。这个脚本的输出经常被贴进报告/issue，
 * 直接原样打印等于把密钥抄进去——日志规范里"不记 token"同样适用于这里。
 */
function redactUrl(value) {
  return String(value ?? '').replace(/([?&](?:access_?token|token)=)[^&#\s]*/gi, '$1***');
}

function fail(message, code = 1) {
  console.error(`[verify] FAIL ${message}`);
  process.exit(code);
}

const args = parseArgs(process.argv.slice(2));
const profile = String(args.profile || 'verify');
const home = resolve(String(args.home || join(PACKAGE_ROOT, '.dsh-verify')));
const port = String(args.port || 19411);
const realHome = String(args['real-home'] || process.env.DSH_HOME_REAL || join(homedir(), '.dsh'));
const selfId = String(args['self-id'] || '').trim();
const meapetToken = String(args['meapet-token'] || '').trim();
const meapetWsUrl = String(args['meapet-ws-url'] || 'ws://127.0.0.1:14321/onebot/v11/hub').trim();
const cwd = String(args.cwd || resolve(PACKAGE_ROOT, '..')).trim();
const keep = Boolean(args.keep);
const bootWaitMs = Number(args['boot-wait-ms'] || 40000);
/** 判据跑完后继续把 harness 挂着多久（毫秒），用于从外部（如 MeaPet 探针）联调。 */
const holdMs = Number(args['hold-ms'] || 0);

if (!selfId || !meapetToken) fail('缺少 --self-id / --meapet-token');

// dsh CLI：优先 --dsh-cli，其次从真实 DSH_HOME 的 node_modules 解析
function resolveCli() {
  if (args['dsh-cli']) return resolve(String(args['dsh-cli']));
  for (const base of [join(realHome, 'profiles', 'package.json'), join(PACKAGE_ROOT, 'package.json')]) {
    try {
      return createRequire(base).resolve('@deepseek-ai/dsh/lib/bin.js');
    } catch {
      /* 继续找 */
    }
  }
  return '';
}

const cli = resolveCli();
if (!cli || !existsSync(cli)) {
  fail(`找不到 dsh CLI（试过 ${join(realHome, 'profiles', 'node_modules')}）；可用 --dsh-cli 显式指定`);
}
log(`dsh CLI: ${cli}`);

/** 子进程输出重定向到文件（不用管道，规避沙箱 EPERM）。 */
function runCaptured(command, argv, { cwd: workdir, env, logPath }) {
  const fd = openSync(logPath, 'w');
  const result = spawnSync(command, argv, {
    cwd: workdir,
    env,
    stdio: ['ignore', fd, fd],
  });
  closeSync(fd);
  return { status: result.status, output: readFileSync(logPath, 'utf8') };
}

// ---------------------------------------------------------------------------
// 删除任何目录之前的三道闸
//
// 这个脚本会 `rmSync(home, { recursive: true, force: true })`。`--home` 一旦指到
// **真实 DSH_HOME**（手滑、复制粘贴、或某个调用方把参数接错），那就是把用户的
// profiles / 会话 / 凭据整个删掉 —— 一次性实验场不该有这种爆炸半径。
//
// 闸 1：不许是真实 DSH_HOME / 用户家目录 / 仓库根 / 盘根；
// 闸 2：只删**本脚本自己建的**实验场（目录里有标记文件）；
// 闸 3：所有删除点都必须先过前两道（本文件里两处删除都调这个函数）。
// ---------------------------------------------------------------------------
const HOME_MARKER = '.onebot-verify-home';

function normalisePath(value) {
  return resolve(String(value)).replace(/[\\/]+$/, '').toLowerCase();
}

function assertSafeToDeleteHome(dir) {
  const target = normalisePath(dir);
  const banned = [
    ['真实 DSH_HOME', realHome],
    ['用户家目录', homedir()],
    ['仓库根目录', PACKAGE_ROOT],
  ];
  for (const [label, candidate] of banned) {
    if (candidate && normalisePath(candidate) === target) {
      fail(`拒绝删除${label}：${dir}（--home 请指向一次性实验场）`);
    }
  }
  if (normalisePath(resolve(dir, '..')) === target) {
    fail(`拒绝删除盘根/文件系统根：${dir}`);
  }
  if (!existsSync(join(dir, HOME_MARKER))) {
    fail(
      `拒绝删除 ${dir}：里面没有本脚本的标记文件 ${HOME_MARKER}，看起来不是实验场。\n`
      + '如果你确定要重建它，请自己先删掉，或换一个 --home。',
    );
  }
}

const env = { ...process.env, DSH_HOME: home };

// --- 准备实验场 ----------------------------------------------------------- //
if (existsSync(home)) {
  assertSafeToDeleteHome(home);
  log(`清理旧的实验场 ${home}`);
  rmSync(home, { recursive: true, force: true });
}
mkdirSync(join(home, 'profiles'), { recursive: true });
writeFileSync(join(home, HOME_MARKER), `created ${new Date().toISOString()}\n`, 'utf8');
log(`实验场 DSH_HOME=${home}`);

const logPath = join(home, 'verify.log');
let r = runCaptured(process.execPath, [cli, profile, '--from-default-profile', 'web', '--dump-config'], {
  cwd: PACKAGE_ROOT, env, logPath,
});
if (!existsSync(join(home, 'profiles', profile, 'package.json'))) {
  fail(`无法从 shipped 模板创建 profile（exit=${r.status}）\n${r.output.slice(-800)}`);
}
log(`已从 shipped web 模板创建 profile "${profile}"`);

// --- 用安装器装进去 ------------------------------------------------------- //
r = runCaptured(process.execPath, [
  join(PACKAGE_ROOT, 'scripts', 'install-into-profile.mjs'),
  '--profile', profile,
  '--self-id', selfId,
  '--meapet-token', meapetToken,
  '--cwd', cwd,
  '--meapet-ws-url', meapetWsUrl,
], { cwd: PACKAGE_ROOT, env, logPath });
if (r.status !== 0) fail(`安装器失败（exit=${r.status}）\n${r.output.slice(-1200)}`);
for (const line of r.output.split(/\r?\n/)) {
  if (/PASS patch 自检|WARN|覆盖行|已就地清理|失败/.test(line)) log(line.trim());
}

// --- 解包插件到 profile 的 node_modules（覆盖层需要 bundle 可解析） -------- //
const profileDir = join(home, 'profiles', profile);
const tgz = join(profileDir, 'vendor', `${PLUGIN_NAME}-0.1.0.tgz`);
if (!existsSync(tgz)) fail(`vendor 里没有 ${PLUGIN_NAME} 的 tgz：${tgz}`);
const pluginDir = join(profileDir, 'node_modules', PLUGIN_NAME);
mkdirSync(pluginDir, { recursive: true });
const tarResult = spawnSync('tar', ['-xzf', tgz, '-C', pluginDir, '--strip-components=1'], {
  stdio: 'inherit',
});
if (tarResult.status !== 0) fail(`解包失败（exit=${tarResult.status}）`);
if (!existsSync(join(pluginDir, 'lib', 'index.js'))) fail('解包后找不到 lib/index.js');
log(`插件已就位：${pluginDir}`);

// --- 判据 1：组合树里恰好一行、且无解析告警 -------------------------------- //
r = runCaptured(process.execPath, [cli, '--profile', profile, '--dump-config'], {
  cwd: PACKAGE_ROOT, env, logPath,
});
const idCount = (r.output.match(/id: dsh-onebot-relay/g) || []).length;
const skipped = (r.output.match(/skipping profile bundle/g) || []).length;
log(`--dump-config：插件行出现 ${idCount} 次，跳过 bundle 告警 ${skipped} 条`);
if (idCount !== 1) {
  fail(
    `组合树里本插件出现 ${idCount} 行（应为 1）。\n`
    + '  0 行通常意味着 **bundle 没被声明/解析**：profile 的 `dsh.profile.bundles` 少了本插件，'
    + '或 dependencies 没指向 vendor 里的 tgz。\n'
    + '  ⚠️ id 覆盖（override）只改**已有**行，它自己不会建行 —— 少了 bundle 声明时，'
    + '覆盖层会静默作用不到任何东西（实测：这种形态下 --dump-config 里 0 行）。',
  );
}
if (skipped !== 0) fail('组合树里出现 "skipping profile bundle"，说明插件没被解析到');
log('PASS 判据 1：组合树里本插件恰好一行且无告警');

// --- 判据 2：真起 harness，打 /status ------------------------------------- //
const logFd = openSync(logPath, 'a');
const child = spawn(process.execPath, [cli, '--profile', profile, '--port', port, '--no-open'], {
  cwd: PACKAGE_ROOT,
  env,
  stdio: ['ignore', logFd, logFd],
  detached: false,
});
closeSync(logFd);

const url = `http://127.0.0.1:${port}/onebot-relay/status`;
const deadline = Date.now() + bootWaitMs;
let noToken = 0;
let withToken = 0;
let payload = null;
let lastError = '';

try {
  while (Date.now() < deadline) {
    await new Promise((r2) => setTimeout(r2, 1000));
    try {
      const bare = await fetch(url);
      noToken = bare.status;
    } catch (error) {
      lastError = String(error?.cause?.code || error?.message || error);
      continue;
    }
    try {
      const withAuth = await fetch(url, { headers: { Authorization: `Bearer ${meapetToken}` } });
      withToken = withAuth.status;
      if (withAuth.ok) {
        payload = await withAuth.json();
        break;
      }
    } catch (error) {
      lastError = String(error?.message ?? error);
    }
  }

  if (withToken !== 200 || !payload) {
    fail(`带 token 的 /status 未返回 200（got ${withToken}, lastError=${lastError}）\n${readFileSync(logPath, 'utf8').slice(-1200)}`);
  }
  if (payload.plugin !== PLUGIN_NAME) fail(`/status 的 plugin 字段是 ${payload.plugin}`);  if (noToken !== 401) fail(`无 token 应当是 401，实际 ${noToken}`);
  log(`PASS 判据 2：无 token=${noToken}，带 token=${withToken}，plugin=${payload.plugin}，selfId=${payload.selfId}`);
  log(`         client.url=${redactUrl(payload.client?.url)} connected=${payload.client?.connected}`);
  log(`         actions.allowed=${payload.actions?.allowed}`);

  // --- 可选：**用同一个实例**继续挂住，方便外部（MeaPet 探针）联调 -------- //
  // 刻意不重启：重启会换掉实例，外部正推消息时可能正好推给已经要死的那个，
  // 表现为「插件没收到」。挂住就用当前这个 PID。
  if (holdMs > 0) {
    log(`按 --hold-ms 继续挂住 ${holdMs} ms（PID ${child.pid}），供外部联调…`);
    const until = Date.now() + holdMs;
    let lastSeen = null;
    while (Date.now() < until) {
      await new Promise((r2) => setTimeout(r2, 3000));
      try {
        const res = await fetch(url, { headers: { Authorization: `Bearer ${meapetToken}` } });
        if (res.ok) {
          const s = await res.json();
          const line = `connected=${s.client?.connected} opened=${s.client?.opened} `
            + `conversations=${s.conversations} pending=${s.pending?.length ?? 0}`;
          if (line !== lastSeen) {
            lastSeen = line;
            log(`  hold: ${line}`);
          }
        }
      } catch {
        /* harness 可能在忙，忽略这一次 */
      }
    }
  }
} finally {
  child.kill();
  await new Promise((r2) => setTimeout(r2, 800));
}

// --- 收尾 ----------------------------------------------------------------- //
if (!keep) {
  assertSafeToDeleteHome(home);
  rmSync(home, { recursive: true, force: true });
  log('实验场已删除（--keep 可保留）');
} else {
  log(`实验场保留在 ${home}`);
}
log('ALL PASS：本插件能被真实 dsh 加载、挂载路由并通过自鉴权。');
