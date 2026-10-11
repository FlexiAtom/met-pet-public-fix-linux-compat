# dsh-onebot-relay（喵桥 · DSH 侧）

DeepSeek Harness 的 **OneBot v11 网桥 host 半边**。它把「QQ 里发来的一条消息」
翻译成一次 DSH agent 会话执行，并把流式输出、最终答复与**工具审批请求**推回聊天。

另一半是 MeaPet 桌宠（`../meapet/onebot/`），它已经是一个 OneBot v11 实现端。

- OneBot 规范（本地副本）：[`../../onebotv11.txt`](../../onebotv11.txt)
- 对端实现（行为真相）：`../meapet/onebot/**`（`service.py` / `events.py` / `server.py` / `ws_session.py`）
- 驱动 DSH 会话的方式（与 IM 无关的那半）：`Downloads/astrdsh/docs/BRIDGE-CONTRACT.md`
- 插件装配约定：`dsh-plugin-dev` skill 的 `references/`

**权威顺序**：OneBot 规范 → MeaPet 实际实现 → astrbot-relay 的契约与实测注释 → 本文档。
四者冲突时，**以靠前的为准**，并且应该改掉靠后的那一份。

## 当前状态

| 部位 | 状态 |
|---|---|
| 插件契约（`name` / `inject` / `Config` / `apply`） | ✅ 就位 |
| 配置校验（**加载即失败**，不静默降级） | ✅ 就位 |
| `state.json` 的原子读写、坏文件拒绝加载 | ✅ 就位（临时文件 + **fsync** + rename；`statePath` 与 `DSH_HOME` 都必须是绝对路径，否则抛错——相对路径会随 cwd 漂移，同一套配置读到两份映射） |
| **mode A**（本插件当 OneBot 客户端，连 MeaPet hub） | ✅ 就位（`ws` + Bearer/query 鉴权 + 心跳 + 退避重连） |
| **mode B**（本插件当 OneBot 服务端，自带端口） | ✅ 就位（正向 WS + HTTP API + `/status`，**upgrade 前鉴权**） |
| 动作层（白名单 → 透传 / 就地作答 / 如实失败） | ✅ 就位（`lib/rpc/actions.js`） |
| 会话驱动（`agents.create` / `resume` / `followup` / `whenIdle`） | ✅ 就位 |
| 助手输出流（`session/event` + `agent/assistant-stream` 双协议） | ✅ 就位（`text-delta` → 回复） |
| 会话标题反向定位（`sessionTitleTemplate`） | ✅ 就位 |
| 群聊 `@` 触发（`requireAt`） | ✅ 就位（数组与 CQ 码两种形态都认） |
| 消息去重（`message_id` LRU） | ✅ 就位 |
| 背压（`maxQueuedPerConversation`） | ✅ 就位 |
| **审批转发**（`approval/request` → 聊天里 `/approve <码>`） | ✅ 就位（自带超时，fail closed） |
| 空闲回收（`idleTtlMs`） | ✅ 就位（**释放 agent，保留会话映射**） |
| 回复切分（`replyChunking`） | ✅ 就位（**码点**级，不劈坏 emoji） |
| 卸载收尾（`cancel → whenIdle → flush → dispose`） | ✅ 就位 |
| `streamMode: preview`（边跑边发增量） | ⚠️ 就位但 **best-effort**，默认关 |
| 图片 / 语音入站 | ❌ 未实现（解析成 `[图片]` 占位符给模型） |
| `user-questions/request`（交互式提问转发） | ❌ 未实现（**fail-open**：交还框架默认链） |
| WebUI 客户端半边 | ❌ 未实现（本包只有 host half） |

## 两种模式

```
mode A（主用，默认开启）
  dsh-onebot-relay ──拨号──▶ MeaPet hub (ws://127.0.0.1:14321/onebot/v11/hub)
        同一连接上：收事件（post_type=message） + 发 {action,params,echo} + 按 echo 收响应

mode B（次用，默认关闭，自带端口）
  MeaPet forward_ws ──拨号──▶ dsh-onebot-relay  {pathPrefix}/ws
  其它消费者       ──POST──▶ dsh-onebot-relay  {pathPrefix}/api
        本端推事件给它（relayInboundToServer）、并应答它发来的 API 调用
```

> ⚠️ **mode B 单独存在时不会驱动会话。** 它只有「推事件 + 应答 API 调用」两个方向，
> 没有事件来源（谁把 QQ 消息推给本端？没有）。真正的事件来源是 mode A。
> mode B 的定位是「把本端已经收到的事件再转给别的消费者」与「让 MeaPet 的
> `forward_ws` 有一个可用的上游」。这条限制是规范决定的，不是本实现的省事。

## 线上帧形状（照抄实现，不是设计稿）

**出站 API 调用**（mode A 发给 MeaPet；mode B 的对端发给本端）：

```json
{"action": "send_private_msg", "params": {"user_id": 10002, "message": "你好"}, "echo": "dsh-mv2h0xue-1"}
```

**响应**（`echo` 必须**逐字回传**；`status` 取 `ok` / `failed` / `async`）：

```json
{"status": "ok", "retcode": 0, "data": {"message_id": 4242}, "echo": "dsh-mv2h0xue-1"}
{"status": "failed", "retcode": 103, "data": null, "echo": "self-2", "error": "动作不在白名单内"}
```

`error` 是 MeaPet `types.ApiResponse` 的扩展字段；本插件**带上**它——
它对排查「为什么这次发送失败」是唯一的人话说明。对端忽略未知字段即可。

**事件上报**（MeaPet → 本插件；本插件 → mode B 消费者）：

```json
{
  "time": 1700000001, "self_id": 10001, "post_type": "message",
  "message_type": "private", "sub_type": "friend", "message_id": 555,
  "user_id": 10002, "message": "在吗", "raw_message": "在吗",
  "sender": {"user_id": 10002, "nickname": "阿米娅"}
}
```

**「响应」与「事件」的判别**（逐字对齐 MeaPet `events.py:is_api_response`）：
带 `echo`/`retcode`/`status` **且没有** `post_type` 的才是响应。
`post_type` 那一条是硬判据——少了它，一个恰好带 `status` 字段的事件会被当响应吞掉，
症状是「消息事件永远收不到」。

**发送一律用明文纯文本**，不用 CQ 码、也不用消息段数组：规范三种形态都收
（`onebotv11.txt` §3），明文是唯一不需要转义的一种。要发 `[`、`&`、`,` 时
CQ 码形态必须转义，而明文不必——少一次转义就少一类「谁的转义是对的」的故障。

## 动笔前必读的四条已核实事实（写错就废）

1. **`ctx.webServer.register` 注册的路由没有任何框架级鉴权**，而且它的 handler
   签名是 `(request, response)`——**没有 WebSocket upgrade 的位置**。
   两条后果：注册在 webServer 上的路由（本包的 `/status`）必须自己过
   `authorizeToken`；OneBot 的正向 WS 只能由本插件**自己持有 `http.Server`**
   ——这就是 `serverEnabled` / `serverPort` 存在的理由。
2. **`text/delta` 是瞬时事件**，没有订阅者时永久丢失。它有两个来源，随宿主版本分叉：
   ≤ `0.1.2-rc.1` 走 `session/event` 的 `assistant/chunk`；
   ≥ `0.1.5-rc.2` 走 `agent/assistant-stream` 的 `frame.chunk`（二者同形）。
   本包两条都挂、收敛到同一个出口（`forwardAssistantChunk`）。
   顺序上「先挂监听 → 再 `followup`」，且 `bridge.agent` 必须在 `followup`
   **之前**赋值，否则开头的 token 会落进一个查不到归属的桥。
3. **根 ctx 上未打 tag 的监听者会收到所有 agent 的事件**
   （`dsh-scope` 的 `if (tag === void 0) return true`）。必须自己按
   `agent.id` / `agent.session` 过滤，否则会串台到用户在 Web UI 里的会话。
4. **`agent/assistant-stream` 只有 `start` 帧带 `{turn, step}`**，`chunk` 帧不带
   （`dsh-agent/lib/types/runtime-types.d.ts:107-137`）。必须在 `start` 时按
   `attemptId` 记下来，否则新宿主下 `step` 恒为 `undefined`。

另外两条与 OneBot 有关的：

5. **响应只能靠 `echo` 关联，不得假设顺序**（规范 §2.1）。孤儿响应必须丢弃，
   **绝不能**当事件派发出去（那会凭空造出一个没有 `post_type` 的假事件）。
6. **鉴权必须在 upgrade 之前做完**（规范 §2.5：「在连接建立前，也就是协议
   upgrade 前」）。先 accept 再 close 会让未鉴权的对端短暂拿到一条可用连接。
   本包对 WS 直接回 `HTTP/1.1 401` 后 `destroy()`，对 HTTP 回 `401` + `retcode 104`。

## 安装

本包是**纯 ESM、零构建步骤**，所以走 tgz 的安装路径**不需要** pnpm 的
`allowBuilds` 授权。

```powershell
# 1) 打包（不需要 npm、不需要网络；产物在 dist/）
node scripts/package-release.mjs

# 2) 交给 dsh 的插件管理器（它就是往 profile 里转发 pnpm 命令）
dsh plugin --profile web add .\dist\dsh-onebot-relay-0.1.0.tgz
```

也可以直接指向目录（本地开发常用）：

```powershell
dsh plugin --profile web add "C:\path\to\dsh-onebot-relay"
```

### 装完必须人工补的两处 profile 配置

`dsh plugin add` 只负责把包放进 profile 的 `node_modules`。另外两处要自己写：

**(a) `%DSH_HOME%\profiles\web\package.json`**

```jsonc
{
  "dependencies": {
    "dsh-onebot-relay": "file:vendor/dsh-onebot-relay-0.1.0.tgz"   // 或 "link:../../plugins/dsh-onebot-relay"
  },
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app",
        // …既有的包…
        "dsh-onebot-relay"          // ← 加这一行，否则插件永远不加载
      ]
    }
  }
}
```

**(b) `%DSH_HOME%\profiles\web\cordis.patch.yml`** —— 覆盖**必填**的部署取值：

```yaml
- id: dsh-onebot-relay
  config:
    selfId: '10001'
    meapetToken: '<与 MeaPet onebot.hub.access_token 一致的密钥>'
    meapetWsUrl: 'ws://127.0.0.1:14321/onebot/v11/hub'
    cwd: 'D:\AI\workspace'
    pathPrefix: '/onebot-relay'
    requireAt: true
```

> **patch 语义提醒（已核实）**：patch 是按顶层 key 赋值，写出的 `config:`
> 会**整体替换**该行的 config，**不是逐字段 merge**。
> 所以上面**必须写全**所有要保留的字段——只写 `selfId` 与 `meapetToken`，
> 其余字段会退回 schema 默认值（`requireAt` 悄悄变回 `true`、
> 你自定义的 `pathPrefix` 也一起没了）。本包自带的 `cordis.patch.yml`
> 是**完整 config**，可以整份抄过来改。

### 关于「加载即失败」

`selfId` / `meapetToken` / `cwd`（以及 `clientEnabled: true` 时的 `meapetWsUrl`）
缺失时，本插件**加载即抛错**，不静默降级；本包自带的 `cordis.patch.yml` 里这些
字段是空占位，**安装后必须先写完 (b) 再重启**，否则那一行会加载失败并在日志里
报出具体缺哪个字段。

这是刻意的取舍（照抄 astrbot-relay 的口径）：一个「装上了但半死」的网桥，
症状是「机器人不理我」，而排查的人会去查 QQ 侧、查 token、查端口——
就是不会想到「配置根本没生效」。让它在加载期就喊出来，是唯一能把排查方向
扳正的做法。**我没有实测过「插件加载失败会不会连带影响同一 profile 里别的插件」**
——这条请在你自己的 profile 上先确认再全量部署（见「未核实」一节）。

### 装完必须检查的三条成功判据

（来自 `dsh-plugin-dev` skill 的 `references/install-and-verify.md`；
**只複制目录不算装上**，三条缺一不可。）

1. **插件目录存在**：`%DSH_HOME%\plugins\dsh-onebot-relay\`。
2. **profile 链接了它**：`%DSH_HOME%\profiles\web\node_modules\dsh-onebot-relay`
   （通常是 junction / symlink，指回第 1 条那个目录）。
3. **profile 声明了它**：`profiles\web\package.json` 里
   `dsh.profile.bundles` 含 `dsh-onebot-relay`，且 `dependencies` 里有对应的
   `link:` / `file:` 条目。

再加三条**运行期**判据（比上面三条更能说明问题）：

4. 重启后 harness stdout 里出现 `[dsh-onebot-relay]` 开头的日志。host half 只要加载成功就一定会打。
5. `GET http://127.0.0.1:<harness端口>/onebot-relay/status` 带
   `Authorization: Bearer <meapetToken>` 能拿到 JSON（`ok: true`）。
   返回 404 → 路由没注册；返回 401 → 路由在但鉴权没过（说明确实是自己写的闸）。
6. 首个会话建立后，`%DSH_HOME%\onebot-relay\state.json` 出现。
   它一直不出现 = 插件从没跑过。

## 配置项

| 键 | 默认值 | 说明 |
|---|---|---|
| `clientEnabled` | `true` | 是否拨号到 MeaPet hub（mode A） |
| `meapetWsUrl` | `''` | **必填**（`clientEnabled` 时）：`ws://…/onebot/v11/hub` |
| `meapetToken` | `''` | **必填**：共享密钥。MeaPet 侧是 `onebot.hub.access_token` |
| `tokenInQuery` | `false` | 是否额外把 token 附成 `?access_token=`（只认 query 的实现端才开） |
| `selfId` | — | **必填**：机器人自己的 QQ 号（纯数字） |
| `cwd` | — | **必填**：agent 工作目录，**绝对路径** |
| `pathPrefix` | `/onebot-relay` | mode B 端点与 `/status` 的公共前缀，不要写尾斜杠 |
| `requireAt` | `true` | 群里必须先 `@` 机器人才响应。私聊不受影响 |
| `allowUsers` | `[]` | **谁能驱动 agent**（私聊）；空 = 不限制。⚠️ MeaPet 的 `allow.*` 在 `reply_mode=hub` 时一次都不会被调用（广播排在过滤之前），所以那条路上的准入只能由这里把关。只认数字，`10001 ` / `qq:10001` 都能匹配 |
| `allowGroups` | `[]` | 群号名单，语义同上（群聊仍需满足 `requireAt`）。名单管的是"能不能驱动 agent"，**不是**"能不能看到事件"——`relayInboundToServer` 的转发不受它影响 |
| `replyChunking` | `true` | 长回复是否切段 |
| `replyChunkChars` | `1500` | 单段最大**码点**数 |
| `sessionTitleTemplate` | `喵桥 · {messageType}/{sessionId}` | 占位符 `{messageType}` `{sessionId}` `{conversation}` `{bridge}` |
| `streamMode` | `final` | `final`（只发权威最终文本）/ `preview`（边跑边发增量） |
| `previewMinChars` | `60` | 仅 `preview`：攒够多少字符发一次 |
| `previewIntervalMs` | `1500` | 仅 `preview`：两次预览的最小间隔 |
| `statePath` | `''` | 空 = `<DSH_HOME>/onebot-relay/state.json`；非空必须绝对路径 |
| `idleTtlMs` | `86400000` | 空闲多久**释放 agent**（保留会话映射）；`0` = 关闭 |
| `maxQueuedPerConversation` | `4` | 单会话在途投递上限；超出即拒并回一句提示 |
| `serverEnabled` | `false` | 是否起 mode B 的独立 HTTP 服务 |
| `serverHost` | `127.0.0.1` | mode B 监听地址 |
| `serverPort` | `6721` | mode B 端口；`0` = 系统分配 |
| `serverToken` | `''` | mode B 密钥；空 = 与 `meapetToken` 共用 |
| `serverAllowRemote` | `false` | `serverHost` 非回环时必须显式打开 |
| `serverAllowSend` | `true` | mode B 消费者是否允许调动作（对齐 MeaPet 的 `hub.allow_send`）。关掉后白名单动作**一律** 103，含 `get_status`——因为读动作里有 `get_msg`/`get_group_list`，能读到聊天记录，不是"无害"，所以这是"要么能问、要么什么都别问" |
| `relayInboundToServer` | `false` | 是否把上游事件**原样**再广播给 mode B 的消费者 |
| `heartbeatMs` | `15000` | WS ping 间隔（两侧都用它） |
| `reconnectInitialMs` / `reconnectMaxMs` | `1000` / `30000` | 重连退避区间（指数 + 抖动） |
| `callTimeoutMs` | `15000` | 单次 API 调用的等待上限 |
| `approvalEnabled` | `true` | 是否把审批搬到聊天里做 |
| `approvalTimeoutMs` | `120000` | 审批等待上限。**审批服务自身零超时**，这里必须给值 |

## 行为说明（容易误解的几处）

- **触发规则**：私聊总是响应；群聊在 `requireAt: true` 时必须 `@` 机器人
  （`@全体成员` 也算）。判定同时认消息段数组与 CQ 码字符串两种入站形态。
- **非文本消息段变成占位符**（`[图片]` `[表情]` `[语音]`…，占位表逐条抄自
  MeaPet `segments.py:SEGMENT_PLACEHOLDERS`）。所以「只发了一张图」会变成一句
  `[图片]` 投给模型——这是**有意**的：静默丢掉会让用户以为模型没看懂，
  而模型收到 `[图片]` 至少能如实说「我看不到图」。
  只有**文本真的是空**的消息（例如只带一个被映射成空串的 `reply` 段）才跳过。
- **回复文本只取自 `assistant/message`**（权威最终文本），**不是**把
  `text/delta` 拼起来——多 step 任务会把增量叠加重复。`reasoning/delta` 不进聊天。
- **切分在码点级做**：`String.slice` 会把 emoji 的代理对劈成两个孤立半边，
  在对端渲染成一个替换字符。
- **审批**：`approval/request` 被接管后，向**发起该 turn 的会话**发一条带 4 位
  一次性验证码的消息；用户回 `/approve <码>` 或 `/reject <码>`。
  只认「明确命令 + 验证码」，不接受自然的「是 / 否」——群聊里任何人打一句「好」
  都可能把审批放行，那是把审批做成摆设。
  - **会话隔离**：验证码只在**发起该 turn 的那条会话**里有效。别的会话即使知道码也用不了
    （回执只在本会话的待审批表里匹配）。
  - **一次性**：结算即从表里移除，重放同一个码只会得到「没有匹配的待审批项」。
  - **残留风险（请自己权衡）**：验证码是**发进会话**的，所以同一会话里能看到提示的人
    都能照抄批准。它挡的是「随口一个好」，**不是**恶意参与者。私聊天然没这个问题；
    群聊请据此决定要不要把 `approvalEnabled` 关掉（关掉后审批回到 Web UI）。
  到点无人应答一律 resolve `rejected`（fail closed），**不是** `cancelled`
  （后者是「用户/上游取消」的语义，会被误当成正常中止）。
- **空闲回收只释放 agent，不改会话映射**。下次消息走 `agents.resume` 把同一个
  会话接回来。这一点**与 astrbot-relay 有意不同**：那边（on-demand 策略）会把映射
  也摘掉、下次新建会话；QQ 会话是长期的，把「内存回收」做成「话题重置」会让用户
  莫名其妙地发现机器人不记得刚才聊过什么。
- **去重靠 `message_id`**（有界 LRU + 10 分钟 TTL，不持久化）。断线重连、
  上游重放、或 MeaPet 的 `reverse_ws` 与 hub 同时在线时，同一条事件可能来两次；
  没有这张表，用户会看到同一个问题被答两遍（两次都真花了钱）。
- **背压**：同一会话在途超过 `maxQueuedPerConversation` 时**丢弃并回一句提示**，
  不排队、不重试（重试只会加剧拥塞，对照 astrbot-relay 契约 §6.3）。
- **动作层**：白名单逐条抄自 MeaPet `service.py:HUB_ALLOWED_ACTIONS`。
  不在表里 → `retcode 103`（权限不足）。连上上游时白名单动作**原样透传**；
  上游不在时只就地答 `get_status` / `get_version_info`（与 MeaPet 的本地兜底
  同两个），其余如实回 `status: failed, retcode: 1` + 一句人话说明——
  **绝不返回假成功**。

## 自测与测试

```powershell
node scripts/check-syntax.mjs     # 对每个 .js/.mjs 跑 node --check
node scripts/self-test.mjs        # 独立协议自测：不需要 dsh，也不需要 MeaPet
node scripts/run-tests.mjs        # node:test 单测（8 个文件，末尾会打出累计用例数）
```

> `run-tests.mjs` 除了看退出码，还**要求每个文件至少跑过 1 个用例**
> （解析输出里的 `pass N`）。因为「一个用例都没跑」的文件也是退出 0 ——
> 那是假绿，而且真实发生过（一次编辑把用例挪进了别的类，数量变了没人发现）。

### 真加载验证（不需要动用户的 harness）

上面三条都还只是「本包自己」。要证明**能被真实 dsh 加载并挂载路由**，用：

```powershell
node scripts/verify-in-throwaway-harness.mjs `
  --self-id <机器人QQ号> --meapet-token <与 MeaPet hub 一致> `
  --meapet-ws-url ws://127.0.0.1:14321/onebot/v11/hub --cwd <agent 工作目录>
```

它会在本包目录下建一个**一次性 `DSH_HOME`**，从 shipped 模板拉 profile、装本包、断言
`--dump-config` 里本插件恰好一行且无 skip 告警，然后真起一个 harness 打 `/status`
（无 token=401 / 带 token=200），跑完自动删除。不写 `%DSH_HOME%`、不重启用户 harness。

加 `--hold-ms <毫秒>` 会在判据跑完后**用同一个实例**继续挂住一段时间（刻意不重启：重启会换掉实例，
外部正推消息时可能正好推给要死的那个，表现为「插件没收到」），方便和 MeaPet 探针两进程联调
（验证「插件 → MeaPet」回发方向：推一条 `/status`，插件本地作答后应回发 `send_private_msg`）。

上游（MeaPet）侧已经把这两个方向串成了一条命令，见仓库根的
`scripts/onebot_bridge_e2e.py`：它会自起 hub + 本脚本，断言插件加载、下行建会话、上行回发三件事。

> `test/` 与 `scripts/` **都在 tgz 里**：这个包必须能在部署现场自证
> （网桥出问题时，「先跑一遍自测」应该是运维的第一反应，而不是先来找源码）。
> 代价是 tgz 大几十 KB，值得。

`scripts/self-test.mjs` 会真起端口、真连 WS、真发 HTTP，逐行打印 `PASS` / `FAIL`，
失败时**非零退出**。它覆盖：

- mode B：无 token / 错 token 被拒、header 与 `?access_token=` 两种鉴权都能连、
  `send_private_msg` 的应答 echo 一致且形状合法、事件推送方向、
  HTTP API 走同一套帧、未鉴权 401 + `retcode 104`
- mode A：握手带 Bearer 头、入站 `message` 事件解析成会话键、
  出站帧形状与 echo 唯一性、**乱序响应仍按 echo 归位**、孤儿响应被丢弃、
  超时与自动重连

> `node --test test/` 在你自己的机器上也能跑。但在**受限沙箱**里它会死在
> `Error: spawn EPERM`：`node --test <目录>` 会为每个测试文件 spawn 子进程并
> **捕获它的输出**，而受限沙箱不允许子进程建命名管道。
> 那是权限边界，不是测试失败——所以 `npm test` 走的是 `scripts/run-tests.mjs`
> （逐个 `stdio: 'inherit'` 跑，等价但不建管道）。

## 已知缺口 / 未核实（写下来，别让下一个人重踩）

**未实现（明确的功能缺口）**

1. **图片 / 语音入站**没有做。图片在纯文本里变成 `[图片]` 占位符给模型看
   （与 MeaPet 的占位表逐条对齐），但模型拿不到图本身。
   astrbot-relay 侧有完整的图片链路（`lib/index.js` 的 `readImageInputs` +
   `admitEncodedImages`），要补的话照那条路走。
2. **`user-questions/request`（交互式提问）没有接管**。它是 waterfall，
   答案形状是「多问题 + 多选项」，QQ 侧只有一句自由文本，硬塞进去等于把多选题
   降级成猜谜。不接管 = **fail-open**，交还框架默认链（Web UI 上照常能答）。
3. **WebUI 客户端半边没有做**。本包只有 host half，因此 `package.json` 里
   **没有** `dsh.client` 字段（有客户端半边时它必须声明 `dsh.client.platform`）。

**未核实（我没有在这台机器上观察到）**

4. ~~**本插件从未在真实 dsh 里加载过**~~ → **已核实（2026-10-10 补测）**：用
   `scripts/verify-in-throwaway-harness.mjs` 从 shipped 模板建了一个**一次性 `DSH_HOME`**，
   装上本包并真起了一个 harness，观察到：
   - `--dump-config` 组合树里本插件**恰好 1 行、0 条 `skipping profile bundle` 告警**；
   - 真起后 `GET {prefix}/status` **无 token=401 / 带 token=200**，返回
     `plugin=dsh-onebot-relay`、`selfId`、`actions.allowed=17`；
   - 与 MeaPet 的 hub 探针对接时 `client.connected=true`、`opened=1`；
   - 推一条合成 `message` 后 `conversations=1`、`state.json` 落盘 `private:10001 → ob-…`，
     并在 `sessions/` 下真的建出了该会话 —— 即 `agents.create` 真的被调用过。

   仍未覆盖：**用户自己那份 profile 重启后的表现**，以及**带真实 LLM 的完整一轮**
   （一次性实验场里没有模型凭据，那一轮停在 `pending`）。
5. **「插件加载失败会不会连带影响同一 profile 里别的插件」未实测**。
   `assertConfigIsUsable` 是刻意要抛的（加载即失败），但抛出之后宿主是只废掉
   这一行、还是整个 profile 起不来，我不知道。**首次部署请先在一个备份 profile
   上试**，或者先把必填值写全再重启。
6. **MeaPet hub 的 `publish_events` 与 `reply_mode` 是前置条件**。
   MeaPet 只会在 `onebot.reply_mode ∈ {hub, both}` **且** `hub.enabled`
   **且** `hub.publish_events` 时才向 hub 消费者广播事件
   （`meapet/onebot/service.py:_should_publish`）。也就是说 mode A 要收到消息，
   MeaPet 侧的 `reply_mode` 必须是 `hub` 或 `both`——配成 `pet` 时本插件**一条消息
   都收不到**，而这不是本插件的 bug。这一点我是从源码读出来的，没有实机验证。
7. **`@` 判定在「数组形态 + `qq` 是数字」时**：`hasAtSelf` 用
   `String(seg.data.qq)` 比较，数字 `10001` 与字符串 `'10001'` 都能命中。
   但若某个实现端把 `qq` 写成 `10001.0` 或带前导零，比较会失败——未遇到，未处理。
8. **准入是两套独立的东西，各管一段**。MeaPet 侧的 `allow.require_at` / `allow.users` /
   `allow.group_ids` **只在 `reply_mode` 含 `pet` 时生效**：`_on_upstream_event` 里
   `_publish` 排在 `_allowed` 之前（已按 `meapet/onebot/service.py:362-369` 逐行核对），
   而 `_allowed()` 那句在 `if self.reply_mode in ("pet", "both")` 里面——所以 `reply_mode=hub`
   时它**一次都不会执行**，MeaPet 的名单对这条路没有任何约束力。
   驱动 agent 的准入由本端负责：`requireAt`（群聊 @）+ `allowUsers` / `allowGroups`（空 = 不限制）。
   没有名单时，任何能私聊这个号、或在群里 @ 它的人都能驱动 agent。
9. **WS ping/pong 与 MeaPet 的行为**：MeaPet 的接收端是 Starlette，
   标准实现会自动回 pong；`ws` 客户端的 `ping()` 期望对端回 `pong` 帧。
   若某个中间代理吃掉 pong 帧，本插件会在一个心跳周期后主动 `terminate` 并重连
   （这是有意的 fail-fast，但会表现为「周期性重连」）。未在代理后实测。
10. ~~**`npm install` / `dsh plugin add` 在本机没有跑过**~~
    **已更正（实测跑通）**：`dsh plugin --profile <p> add .\vendor\<tgz>` 在一次性 profile 上
    执行成功（exit 0，`node_modules/dsh-onebot-relay/lib/index.js` 就位，随后 `--dump-config`
    为 1 行、0 条 skipping）。两点实测副作用请事先知道：
    - 它**走 pnpm**（输出 `using pnpm v11.22.0`、`downloaded 5`），因此**可能需要网络或 pnpm 缓存**
      （那 5 个是 profile 自己的依赖；本插件是纯 ESM、零运行时依赖）。
    - 它会建一个 pnpm **内容寻址存储**，实测落在仓库**上一级**的 `.pnpm-store\v11`（约 0.74 MB），
      不在 profile 里；删掉无妨，仓库 `.gitignore` 已收录。
    若这一步因网络/权限失败，等价做法是手工解包（一次性实验场脚本用的正是它）：
    `tar -xzf <tgz> -C <profile>\node_modules\dsh-onebot-relay --strip-components=1`。

**本地开发用的一次性动作（不进 tgz，也不该提交）**

为了在本机跑测试，我把 `ws` 与 `@deepseek-ai/schemastery`（及其依赖
`@deepseek-ai/cosmokit`、`@standard-schema/spec`）从
`%DSH_HOME%\profiles\node_modules\` **复制**到了本包的 `node_modules/`。
它已经在 `.gitignore` 里，`scripts/package-release.mjs` 也显式跳过 `node_modules`。
在正式部署里这些包由 profile 的依赖解析提供，**不需要**这一步。

## 参考范例（照抄对象，全是已核实路径）

| 目的 | 文件 |
|---|---|
| 同型 host 插件（会话驱动 / 审批 / 在途单 / 卸载收尾） | `Downloads/astrdsh/dsh-astrbot-relay/lib/index.js` |
| 状态原子读写与拒绝加载 | `Downloads/astrdsh/dsh-astrbot-relay/lib/state.js` |
| 标题反向定位 | `Downloads/astrdsh/dsh-astrbot-relay/lib/session-title.js` |
| 插件声明与 patch 约定 | `Downloads/astrdsh/dsh-astrbot-relay/{package.json,cordis.patch.yml}` |
| 手写 SSE（本包不用，但鉴权与 header 读法可参考） | `dsh-client-hmr/lib/index.js:114-158` |
| 最小会话驱动 | `dsh-headless/lib/index.js:127-167` |
| 含 resume 的会话驱动 | `dsh-acp/lib/index.js:692-730` |
| 审批转发（async 等外部回执） | `dsh-acp/lib/index.js:1115-1138` |
