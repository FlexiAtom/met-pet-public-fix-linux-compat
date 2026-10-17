# OneBot v11 适配器与接收器

本文件描述 MeaPet 的 QQ / OneBot 通道：它既是**发起端**（主动连出去）也是**接收端**
（等别人连进来），并且可以作为**中转枢纽**把 QQ 事件转发给下游消费者（例如 dsh 插件）。

> 组件边界、线程模型、消息闭环、失败模式、扩展点与部署/回滚手册见
> [`onebot-architecture.md`](onebot-architecture.md)；本文专注协议与配置字段。

面向普通用户的入口见根目录 `README.md`；后端边界与 Companion MCP 见
[`backend-and-control.md`](backend-and-control.md)。字段默认值以
`config.example.json` 与 `meapet/onebot/config.py` 为准。

## 1. 总体边界

OneBot 通道**不是**回复后端，而是一条**消息通道**：它决定「谁的消息进入桌宠、桌宠的回复发到哪里」。
回复仍由既有后端（`direct` 或 `agent`）生成，因此分段协议、表情、TTS 时序完全复用。

```text
QQ ←→ NapCat / SnowLuma（OneBot 实现端）
            ↕  OneBot v11（四种通信方式任选）
        MeaPet OneBot 通道
            ├─ reply_mode=pet  → 桌宠自己的人格/后端 → 回复回发 QQ
            └─ reply_mode=hub  → 转发给下游消费者（dsh 插件）→ 它的回复回发 QQ
```

一次只跑一条上游连接是最常见用法；四种通信方式可以同时启用，实际使用时按对端能力挑一种即可。

## 2. 四种通信方式 + hub

| 配置段 | 角色 | 方向 | 典型对端 |
|--------|------|------|----------|
| `forward_ws` | 发起端 | 我们主动连对端的 WS 服务端 | NapCat 正向 WS `:6700`、SnowLuma `ws://host:3001/` |
| `reverse_ws` | 接收端 | 我们监听，对端连进来 | NapCat / SnowLuma 的「反向 WebSocket」客户端 |
| `http_api` | 发起端 | 调对端的 HTTP API 发消息 | NapCat `:5700`、SnowLuma `http://host:3000/` |
| `http_post` | 接收端 | 接收对端的事件 HTTP POST（发送仍需 `http_api`） | NapCat / SnowLuma 的 HTTP 上报 |
| `hub` | 枢纽 | 对端以 OneBot 客户端身份接入（收事件 + 发消息）；也可由我们主动把事件 **POST 上报**给只会 HTTP 的消费方 | dsh 插件等下游消费者 |

`reverse_ws` / `http_post` / `hub` 默认共用 `127.0.0.1:14321`，运行时合并成**同一个 ASGI 应用**，
以不同 path 区分；只有显式写了不同端口才会起多个监听。

### 对接 NapCat

- 正向（我们连它，`forward_ws`）：在 NapCat WebUI 打开「WebSocket 服务器」，把 URL 填进
  `onebot.forward_ws.url`，例如 `ws://127.0.0.1:6700/`。
- 反向（它连我们，`reverse_ws`）：在 NapCat WebUI 新建「WebSocket 客户端」，URL 填
  `ws://<本机>:14321/onebot/v11/ws`，token 与 `onebot.reverse_ws.access_token` 一致。

### 对接 SnowLuma

SnowLuma 是「协议端」而不是机器人框架，HTTP API 与 OneBot v11 兼容
（[对接机器人](https://snowluma.github.io/zh/docs/guide/connect-bot)、
[API 参考](https://snowluma.github.io/zh/docs/api)、
[配置参考](https://snowluma.github.io/zh/docs/guide/configuration)）。

**逐项对照**（左列取自 SnowLuma 官方文档，右列是我们的字段；已按文档核对过）：

| SnowLuma | 我们 | 备注 |
|----------|------|------|
| `httpServers` 默认 `0.0.0.0:3000`，`POST /{动作名}` | `onebot.http_api.api_base = http://host:3000` | 我们的发送端**先**打 `{base}/{action}`，与它一致；404/405 才退回 `{base}/` |
| `wsServers` 默认 `0.0.0.0:3001`，`ws://host:3001/` | `onebot.forward_ws.url` | 同一条连接上收事件、调动作（`role: Universal`） |
| `accessToken`；`Authorization: Bearer` 或 `?access_token=` | 各段 `access_token` | 我们发送时用 Bearer 头；接收时两种都认 |
| `httpClients[].url`（主动上报事件） | `onebot.http_post`：我们监听 `/{我们的 path}` | 即它 POST 事件给我们 |
| `wsClients[].url`（主动连反向 WS） | `onebot.reverse_ws`：我们监听 `ws://<本机>:14321/onebot/v11/ws` | `reconnectIntervalMs` 最小 1000 |
| `messageFormat: array`（默认） | `normalize_message` 三种形态都收 | 段数组、CQ 字符串、单段对象 |
| `reportSelfMessage: true` → `post_type='message_sent'` | 解析为「非 message 事件」 | 会按需广播给 hub，但**不进入**桌宠轮次队列（正确的行为） |
| 应答信封 `{status, retcode, data, message, wording}` | `ApiResponse.from_payload` | 只认 `status`/`retcode`/`data`；多出来的 `message`/`wording` 忽略 |

> ⚠️ **token 是两个不同的值**：HTTP 与 WS 可能各有各的 `accessToken`，账号级
> `onebot_<uin>.json` 变化时都会变。分别填到 `http_api.access_token` 与
> `forward_ws` / `reverse_ws.access_token`。

> ⚠️ **`role` 建议保持 `Universal`**（这是默认值）。SnowLuma 允许把 `wsServers.role` 设成
> `Api` 或 `Event`（只服务动作 / 只推事件），那种拆分需要**两条**连接；本实现按规范的
> `Universal` 单连接设计，拆开时会拿不到另一半。

> ℹ️ SnowLuma 文档里的**应答信封不带 `echo`**（HTTP 一对一，本就不需要）。规范要求 WS 响应带
> `echo` 用于关联；为稳妥起见，我们在 WS 上遇到不带 `echo` 的响应时，若**只有一个**在途调用就
> 按唯一性归位，超过一个则宁可不猜（丢弃并等超时）。

### 端口与共存

三个「对端连我们」的端点默认都是 **14321**，所以默认部署只占**一个**端口：上游反向 WS
（`/onebot/v11/ws`）、上游事件 POST（`/onebot/v11/http`）与 hub（`/onebot/v11/hub`）跑在同一个
ASGI 应用上，按 path 区分。NapCat 的正向 WS 端口（默认 6700）与 SnowLuma 的（3001）是**对端**的
端口，与这里无关。

> ⚠️ **与 AstrBot 等其它 QQ 框架共存时**：AstrBot 的 `aiocqhttp` 反向 WS 通常占着 10234 一类的端口。
> 同一端口不能有两个监听者，而且**同一个 QQ 账号的同一个反向 WS 客户端只会连上其中一个** ——
> 若让 NapCat 同时连两边（两个 `websocketClients` 条目），两边都会拿到消息并各自驱动一次 dsh 会话，
> 表现为「机器人重复回复」。要共存，请给 MeaPet 用独立端口（默认 14321 就是为此留的），
> 并在 NapCat 里为它单独加一个反向 WS 客户端；不要把一个客户端同时指向两边。

## 3. 配置参考

```json
{
  "onebot": {
    "enabled": false,
    "self_ids": [],
    "reply_mode": "pet",
    "forward_ws": {
      "enabled": false,
      "url": "ws://127.0.0.1:3001/",
      "access_token": "",
      "reconnect": true,
      "reconnect_initial_ms": 1000,
      "reconnect_max_ms": 30000
    },
    "reverse_ws": {
      "enabled": false,
      "host": "127.0.0.1",
      "port": 14321,
      "path": "/onebot/v11/ws",
      "access_token": "",
      "allow_insecure_remote": false
    },
    "http_api": {
      "enabled": false,
      "api_base": "http://127.0.0.1:3000",
      "access_token": "",
      "timeout_s": 10.0
    },
    "http_post": {
      "enabled": false,
      "host": "127.0.0.1",
      "port": 14321,
      "path": "/onebot/v11/http",
      "access_token": "",
      "allow_insecure_remote": false
    },
    "hub": {
      "enabled": false,
      "host": "127.0.0.1",
      "port": 14321,
      "ws_path": "/onebot/v11/hub",
      "http_path": "/onebot/v11/hub/http",
      "access_token": "",
      "publish_events": true,
      "allow_send": true,
      "allow_insecure_remote": false,
      "push_urls": [],
      "push_token": "",
      "push_timeout_s": 5.0
    },
    "allow": {
      "private": true,
      "groups": true,
      "require_at": true,
      "users": [],
      "group_ids": []
    },
    "queue": {
      "max_pending": 16,
      "turn_timeout_s": 180.0
    }
  }
}
```

| 键 | 默认 | 说明 |
|----|------|------|
| `enabled` | `false` | 总开关；关闭时桌宠完全不碰网络 |
| `self_ids` | `[]` | 本机机器人 QQ 号，用于判断群聊里是否 @ 了自己；留空会从事件里**自动学习** |
| `reply_mode` | `"pet"` | `pet`=桌宠自己回复；`hub`=只转发给下游消费者；`both`=两者都做 |
| `allow.require_at` | `true` | 群聊必须 @ 机器人才响应（私聊不受影响） |
| `allow.users` / `allow.group_ids` | `[]` | 白名单；留空表示不限制。⚠️ **只作用于桌宠自己回复**（`reply_mode` 含 `pet` 时）。`reply_mode=hub` 下这些规则一次都不会生效——广播排在过滤之前，驱动 dsh agent 的准入由插件侧的 `requireAt` / `allowUsers` / `allowGroups` 负责 |
| `allow.private` / `allow.groups` | `true` | 关闭某一类会话 |
| `queue.max_pending` | `16` | 待处理消息上限；满了丢弃最新一条并计入统计 |
| `queue.turn_timeout_s` | `180` | 单轮回复超时；超时后释放队列，避免一条消息卡死整条链路 |

鉴权字段 `access_token` 为空表示不鉴权；非空时同时支持 `Authorization: Bearer <token>`
与 `?access_token=<token>`（规范 2.5）。所有 token 在 `scrub_secrets()` 中会被清空，
不会写进诊断导出。

`allow_insecure_remote` 是**显式**的非回环豁免：把 `host` 写成 `0.0.0.0` / 局域网地址
而不打开它会在启动时被拒绝并给出配置问题提示。

## 4. 端点一览

| 路径 | 方式 | 用途 |
|------|------|------|
| `reverse_ws.path`（默认 `/onebot/v11/ws`） | WebSocket | 上游反向 WS：收事件、发 API 调用 |
| `http_post.path`（默认 `/onebot/v11/http`） | `POST` | 上游事件上报 |
| `hub.ws_path`（默认 `/onebot/v11/hub`） | WebSocket | 下游消费者：收事件、发 API 调用 |
| `hub.http_path`（默认 `/onebot/v11/hub/http`） | `POST` | 下游消费者的 HTTP API（只发不收） |

同一连接上：

- 收到 `{"post_type": ...}` → 事件。
- 收到 `{"status","retcode","data","echo"}` → API 响应，按 `echo` 关联回等待中的调用。
- 发出 `{"action","params","echo"}` → API 调用。

`echo` 由进程内单调计数器生成，无需密码学强度。

### hub 的动作白名单

下游消费者只能调用**只读 + 发消息**的动作（`send_private_msg` / `send_group_msg` / `send_msg` /
`delete_msg` / `get_*` / `can_send_*` 等）；`set_group_kick`、`set_group_ban`、
`set_group_whole_ban` 这类管理动作一律以 `retcode=103`（权限不足）拒绝，不会透传到 QQ 侧。
`hub.allow_send=false` 则连发消息也禁止，只保留事件广播。

### 事件 HTTP POST 上报（规范 §2.2）

WS 之外还有一条**主动推送**通道：`hub.push_urls` 里每填一个 `http(s)://` 地址，我们就把事件
`POST` 过去（字段与 WS 上完全一致），等价于 SnowLuma 的 `httpClients` / NapCat 的 HTTP 上报。
这让**只会 HTTP、不能维持长连接**的消费方也能拿到事件。

- 鉴权：`Authorization: Bearer <hub.push_token>`；`push_token` 留空则复用 `hub.access_token`。
- 超时：`hub.push_timeout_s`（默认 5s）。
- 与 WS 广播同一开关：需要 `hub.enabled` **且** `hub.publish_events` 为真。
- **快速操作（规范 §7）**：消费方可以在响应体里直接带与 API 参数同形的字段来即时回话，
  目前支持 `reply`（可带 `auto_escape`）。我们收到后会立刻把它作为消息发回该事件所属会话；
  `hub.allow_send=false` 时忽略。失败与忽略都会计入统计（`events_pushed` / `push_failures` /
  `quick_operations`）。
- 校验器会在「配了 `push_urls` 但没开 hub 或没开 `publish_events`」以及
  「明文 `http://` 且主机不是回环」时给出提示。

> 我们作为**接收方**时（别人 POST 给我们，即 `http_post` 模式）并**不**在响应里返回快速操作，
> 见 §15 已知限制。

## 5. 与桌宠会话的接线

这是本功能最容易踩坑的地方，设计上做了三个明确取舍。

### 5.1 入站消息不进气泡

气泡是**角色在说话**，QQ 原文属于运营面。因此 QQ 消息不会以气泡形式回显；
只有桌宠自己的回复会出现在气泡里，并同时发往 QQ。

### 5.2 轮询而不是跨线程回调

网络全部跑在 `meapet.async_runtime` 的 asyncio 守护线程上。桌面侧用 **500 ms 的 `QTimer`**
轮询 `OneBotService.take_ready_turn()` 取待处理消息，拿到后交给既有的 `_do_chat()`；
回复定稿时由 `chat_flow` 调用 `_notify_channel_turn_finished(reply)`，经
`OneBotService.complete_turn()` 回发 QQ。整个过程中 Qt 主线程不碰 socket。

`PetChatFlowMixin._notify_channel_turn_finished` 的基础实现是 no-op，由 `PetOneBotMixin`
覆盖 —— 这样只继承 `PetChatFlowMixin` 的测试宿主不需要关心通道状态。

### 5.3 严格串行

`ConversationOrchestrator.accepts()` 要求 `turn_id == _active_turn_id`，即**同时只有一轮**。
因此多群多人同时说话时，QQ 消息会排队（`queue.max_pending`）而不是并发处理。
这一轮的超时由通道侧（`turn_timeout_s`）与桌面侧（`_onebot_turn_deadline`）双重兜底：
任一侧超时都会释放队列，不会永久卡死。

### 5.4 已知限制

- **QQ 会话与本地输入共用同一套时间线、记忆与好感度**。桌宠只有一个人格，多群场景下记忆会混在一起。
  需要隔离时应改用 `reply_mode=hub`，让 dsh 侧按会话维护上下文。
- 图片只以 `[图片]` 占位符进入模型，不会下载 QQ 图片（项目对 URL 图片有 SSRF 防护，且
  `ImageAttachment` 只接受内联 base64）。
- 「快速操作」只做了**我们作为上报方**的那一半（`hub.push_urls` → 读消费方响应里的 `reply`）；
  **我们作为接收方**时（`http_post` 模式，对端 POST 给我们）响应体里仍只返回空 `ok`，
  不返回快速操作字段。

## 6. 日志与隐私

- 只记录**长度与元数据**（`events_in` / `messages_in` / `replies_sent` 等计数，以及
  `message private:42 len=6 id=7` 这类摘要），**不记录消息正文**。
  `meapet/onebot/events.py::summarize` 刻意不含正文，并被单元测试锁定。
- 不记录 token / Authorization 头。
- 连接失败只记异常类型名，不回显响应正文。

## 7. dsh 插件：`dsh-onebot-relay`

仓库内 `dsh-onebot-relay/` 是一个纯 ESM、**无构建步骤**的 dsh host 插件（0.1.0），把 OneBot v11
接到 dsh agent 会话上：QQ 里的一条消息 → 一次 dsh 会话执行 → 流式/最终答复回到 QQ。

| 半边 | 形态 | 对应 MeaPet 配置 |
|------|------|------------------|
| mode A（默认，主用） | 插件当 OneBot **客户端**，连 MeaPet 的 hub | `onebot.hub.*` |
| mode B（可选） | 插件当 OneBot **服务端**（自带 `http.Server`） | `onebot.forward_ws.url` 指向它 |

mode B 之所以自持 `http.Server` 而不是挂 `ctx.webServer.register`：后者的 handler 签名是
`(request, response)`，**没有接管 WebSocket upgrade 的位置**，而 OneBot 正向 WS 必须接管 upgrade。

### 拓扑

```text
QQ ←→ NapCat / SnowLuma
          ↕
     MeaPet onebot (hub 端点)
          ↕  ws://127.0.0.1:14321/onebot/v11/hub
     dsh-onebot-relay (mode A)
          ↕  ctx.agents
        dsh agent 会话（每个 QQ 会话一个）
```

### 配置要点

必填：`selfId`（机器人 QQ 号）、`meapetToken`（与 `onebot.hub.access_token` 一致）、`cwd`
（agent 工作目录，绝对路径）、`meapetWsUrl`。缺失时插件**加载即失败**，不静默降级。

其余常用键：`requireAt`（群里是否必须 @，默认 `true`）、`pathPrefix`、`streamMode`
（`final` / `preview`）、`replyChunking` / `replyChunkChars`、`statePath`
（默认 `<DSH_HOME>/onebot-relay/state.json`）、`idleTtlMs`、`maxQueuedPerConversation`、
`clientEnabled` / `serverEnabled` / `serverPort`、`tokenInQuery`、`approvalEnabled` /
`approvalTimeoutMs`。完整默认值与注释见 `dsh-onebot-relay/cordis.patch.yml`。

> mode B 的 `serverPort` 默认 **6721**，刻意**不**用 14321：那是 MeaPet 的 OneBot 端口，
> 同一个进程/主机上两个监听者不能同端口。只有走「插件当服务端、MeaPet 用 `forward_ws` 连它」
> 这套拓扑时才开 mode B，此时 MeaPet 的 `reverse_ws` 应关掉。

> ⚠️ **patch 是整体替换**：在 profile 的 `cordis.patch.yml` 里覆盖该行时，写出的 `config:`
> 会整体替换目标 config，**未写出的字段会退回 schema 默认值**，不是逐字段 merge。
> 只写 `meapetToken` 和 `cwd` 会让 `pathPrefix`、`requireAt` 等一起被打回默认。

### 前置条件（容易踩）

MeaPet 只在 **`reply_mode ∈ {hub, both}` 且 `hub.enabled` 且 `hub.publish_events`** 时才向消费者
广播事件。若配成 `reply_mode: pet`，插件**一条消息都收不到** —— 这不是插件的 bug。

另外 `_on_upstream_event` 里事件广播（`_publish`）排在过滤（`_allowed`）**之前**，所以没 @ 机器人的
群消息仍会被广播到 hub；也就是说群聊「要不要响应」的判定实际由**插件侧的 `requireAt`** 生效。
（这是刻意设计：hub 消费者应看到完整上下文，是否回应由它决定。）

### 安装（需要操作者执行）

> ⚠️ **`<profile>` 要填"活跃"的那个**：harness 启动时会重写它正在跑的 profile 的
> `cordis.yml`，所以 **`cordis.yml` 最新的那个才是活跃 profile**。装错 profile 的症状是
> **永远 404、配置却看着全对**（本项目实测踩过：插件在 `web`，harness 跑 `desktop`）。
> 本机活跃的是 `desktop`。推荐直接用安装器（`dsh-onebot-relay/scripts/install-into-profile.mjs`），
> 它会把下面两处一起写好并自检。

```powershell
cd dsh-onebot-relay
node scripts/package-release.mjs          # 产物 dist/dsh-onebot-relay-0.1.0.tgz
dsh plugin --profile <活跃 profile> add .\dist\dsh-onebot-relay-0.1.0.tgz
# ⚠️ Electron 独占管理的 profile（如 desktop）会被拒：
#    error: profile "desktop" is managed exclusively by the Electron application
#    这时手工解包等价：
#    tar -xzf <profile>\vendor\dsh-onebot-relay-0.1.0.tgz -C <profile>\node_modules\dsh-onebot-relay --strip-components=1
```

然后**人工补齐两处**（否则插件永远不加载）：

1. `%DSH_HOME%\profiles\<活跃 profile>\package.json`：`dependencies` 加
   `"dsh-onebot-relay": "file:vendor/dsh-onebot-relay-0.1.0.tgz"`，`dsh.profile.bundles` 加
   `"dsh-onebot-relay"`。
2. `%DSH_HOME%\profiles\<活跃 profile>\cordis.patch.yml`：`- id: dsh-onebot-relay` + **完整** config。
   **必须先写完再重启**，因为包内 patch 的必填值是空占位，缺失即加载失败。

重启 harness 后按三项安装判定核对：插件目录存在、profile 的 `node_modules` 链接到它、
`dsh.profile.bundles` 声明了它；再加运行期判定：stdout 出现 `[dsh-onebot-relay]` 日志、
`GET http://127.0.0.1:<端口>/onebot-relay/status` 带 Bearer 返回 JSON、首次会话后出现
`%DSH_HOME%\onebot-relay\state.json`。

### 脚本

| 脚本 | 作用 |
|------|------|
| `scripts/onebot_probe.py` | 无界面探针：只把 OneBot 通道跑起来做连通性验证。`--upstream reverse_ws/forward_ws/none` 选上游，`--hub` 同时起 hub，`--call <action>` 上游连上后打一次真实 API（建议只读动作），`--echo-reply <文本>` 收到消息固定回一句，`--poke <文本>` 每次有 hub 消费者连上就推一条合成消息。**下游发回来的 API 调用也会打印**（`← hub 调用 send_private_msg …`），这是验证「插件 → MeaPet」方向的关键。默认只打印元数据，`--show-text` 才打印正文。<br>⚠️ **`--echo-reply` 会真的往 QQ 发消息**：它把 `reply_mode` 切到 `pet`，接真实 NapCat 时**任何**真实 QQ 消息都会被那句固定文本回掉。修回发链路请用 `--upstream none`（纯 hub）。探针启动时会就此打一条醒目的 `⚠️` |
| `scripts/onebot_bridge_e2e.py` | **一条命令验完整条桥**：本进程起 MeaPet hub，另起一次性真实 dsh（独立 `DSH_HOME`）让插件连过来，然后断言**下行**（推普通消息 → 插件建出 dsh 会话）与**上行**（推 `/status` → 插件回发 `send_private_msg` 且 `user_id`/文案正确）。不需要 QQ、不需要 LLM 凭据。退出码 0=全通 / 1=断言失败 / 2=环境不具备。加 `--via-pet` 则把 MeaPet 侧换成**真实桌宠进程**（离屏）+ 假 NapCat 注入，只做下行断言（原因见架构文档 §13.5） |
| `scripts/onebot_pet_reply_e2e.py` | **验桌宠自己的回复闭环**（`reply_mode=pet`）：起一个桩 LLM（最小 OpenAI 兼容 SSE）+ 假 NapCat，断言「QQ 消息 → 桌宠 LLM → 回发 `send_private_msg` 到原连接」。反向 WS 用一次性随机 token，真实 NapCat 连不上，**不会往 QQ 发任何东西** |
| `python scripts/onebot_pet_smoke.py` | 真起一次桌宠（离屏）验证整机接线：MRO 是否含 mixin、`_init_onebot` 是否起来、上游是否接入、`_quit` 是否把通道收干净 |
| `scripts/onebot_postinstall_check.py` | 重启后自检：打 `{prefix}/status`，区分「404 = 没加载」与「401/200 = 已加载且鉴权生效」；再读插件自报状态，核对 `hub.access_token` 与 `hub.port` 是否与 MeaPet 配置一致；**插件已加载但连不上 hub 时会直接给出下一步**（先启动桌宠）而不是只报一句失败 |
| `dsh-onebot-relay/scripts/verify-in-throwaway-harness.mjs` | **最强的一条**：在独立 `DSH_HOME` 里从 shipped 模板建一次性 profile、装插件、`--dump-config` 断言恰好一行、真起 harness 并打 `/status`（401/200），跑完自删。不需要重启用户的 harness。`--hold-ms` 会挂住一段时间，供与探针两进程联调（验证回发方向） |
| `dsh-onebot-relay/scripts/install-into-profile.mjs` | 幂等安装器：备份 profile → 复制产物到 `vendor/` → 补 `dependencies` 与 `dsh.profile.bundles` → 写入**按 id 覆盖**的完整 config。会自证（用 profile 的 yaml 解析，要求恰好一次且为覆盖形态），不满足则回滚；并能自动清理旧版留下的 `insert` 形态 |
| `dsh-onebot-relay/scripts/validate-installed-config.mjs` | 重启前自检：把 profile patch 里**实际写入的** config 过一遍插件自己的 schema 与加载期校验；只读，不重写 `cordis.yml`（比 `dsh --dump-config` 安全） |

### 已核实 / 未核实

**已核实**（本机实跑）：`node --check` 21/21；独立协议自测 `node scripts/self-test.mjs`
**40 项全通过**；`node scripts/run-tests.mjs` **8 个文件全通过**；tgz 解包后从解包副本重跑同样全通过。
自测覆盖 token 鉴权（header 与 query 两种）、无 token 拒绝、`echo` 关联与乱序归位、
事件原样透传、未知路径 404、上游断开自动重连。

**未核实（重要）**：该插件**从未在真实 dsh 里加载过** —— 写 `%DSH_HOME%` 与重启 harness 都需要
人工批准。因此 `apply` 的真实执行路径、以及 `agents.create` / `followup` 的运行时行为尚未实测；
测试里 dsh 侧是替身。另外「插件加载失败是否会拖垮同 profile 的其他插件」未知，首次部署建议
先备份 profile。

## 8. 测试

```bash
python -m unittest tests.test_onebot_protocol      # 协议层/配置/服务编排（无 socket）
python -m unittest tests.test_onebot_integration   # 四种通信方式的真实 socket 端到端
```

`test_onebot_integration` 不需要 NapCat / QQ：它自己扮演 NapCat（正向 WS 服务端）、
扮演反向 WS 客户端，并验证「上游事件 → hub 消费者」与「hub 消费者发消息 → 上游」的全双工链路。

真机验证用 `scripts/onebot_probe.py`（见上一节的脚本表）：它在指定端口起反向 WS，
让真实的 NapCat 连进来，可用 `--call` 打只读 API 证明收发两侧都通，而不必打扰任何 QQ 会话。
