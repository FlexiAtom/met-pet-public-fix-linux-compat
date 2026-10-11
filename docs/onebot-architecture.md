# OneBot QQ 通道 — 总架构

本文是「MeaPet ⇄ QQ ⇄ dsh」这条链路的**总架构**：组件边界、线程模型、协议契约、失败模式、
扩展点、验证现状与部署/回滚手册。协议与配置字段的逐项参考见 [`onebot.md`](onebot.md)；
dsh 侧插件的自述见 [`../dsh-onebot-relay/README.md`](../dsh-onebot-relay/README.md)。

> 阅读顺序建议：先看 §2 组件总图与 §6 消息闭环，再按需查 §3/§4/§7。

---

## 1. 目标与边界

**目标**：让桌宠成为一个真正的 QQ 机器人，并且**既能自己回，也能把 QQ 交给 dsh 回**。

| 能力 | 归属 |
|------|------|
| OneBot v11 四种通信方式（发起端 / 接收端） | MeaPet `meapet/onebot` |
| QQ 事件 → 桌宠自己的人格与后端 → 回复回 QQ | MeaPet 桌宠会话流 |
| QQ 事件 → 下游消费者（dsh）→ 回复回 QQ | MeaPet `hub` + `dsh-onebot-relay` |
| 人格、TTS、表情、记忆、好感度 | 完全复用既有层，本功能**不新增呈现逻辑** |
| 配置界面 | `wizard/page_onebot.py` |

**明确的非目标**（写下来避免误期待）：

- **不是**新的回复后端。OneBot 是**通道**，回复仍由既有 `direct` / `agent` 后端生成。
- **不做** QQ 协议本身（登录、封包）；那由 NapCat / SnowLuma 这类「实现端」负责。
- **不落** 消息正文到日志（见 §10）。
- 图片 / 语音的**内容**不入模型，只以 `[图片]` / `[语音]` 占位符形式进入文本（见 §15）。

---

## 2. 组件总图

```text
┌──────────────────────────── QQ 侧（第三方） ────────────────────────────┐
│  NapCat (QQ NT 注入)              SnowLuma (协议端)                     │
│   正向 WS :6700 / HTTP :5700       正向 WS :3001 / HTTP :3000           │
│   反向 WS 客户端 / 事件 POST        反向 WS 客户端 / 事件 POST            │
└───────────────┬─────────────────────────────────────┬───────────────────┘
                │        OneBot v11（UTF-8 JSON）      │
                ▼                                      ▼
┌──────────────────────────── MeaPet 进程 ────────────────────────────────┐
│                                                                        │
│  ┌── meapet/onebot ──────────────────────────────────────────────────┐  │
│  │  协议层   types.py  cq.py  segments.py  events.py   （纯函数）     │  │
│  │  调用层   api.py（SenderRegistry / HttpApiSender）                 │  │
│  │  传输层   transport/ws_session.py   双向 WS + echo 关联            │  │
│  │           transport/forward_ws.py   发起端：连出去 + 退避重连       │  │
│  │           transport/server.py       接收端：一个 ASGI 应用          │  │
│  │  编排层   service.py（OneBotService）路由 / 队列 / hub / 统计       │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                ▲  轮询 take_ready_turn()      complete_turn() ▼          │
│  ┌── meapet/desktop/onebot_bridge.py (PetOneBotMixin) ───────────────┐  │
│  │  QTimer 500ms 轮询；回复回收；超时释放；菜单/状态文案               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                ▲  _do_chat(text)              _notify_channel_turn_... ▼ │
│  ┌── desktop/chat_flow.py（既有对话流，未改语义） ────────────────────┐  │
│  │  分段协议 / 气泡 / TTS / 表情 / 记忆 / 好感度 / 时间线              │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  asyncio 守护线程（async_runtime 单例 loop）：所有 socket / uvicorn      │
│  Qt 主线程：只做轮询与控件更新，从不碰 socket                            │
└───────────────┬────────────────────────────────────────────────────────┘
                │  hub 端点：ws://127.0.0.1:14321/onebot/v11/hub
                │  （下游消费者以 OneBot 客户端身份接入，双向）
                ▼
┌──────────────────── dsh 进程（host 插件） ──────────────────────────────┐
│  dsh-onebot-relay                                                      │
│   mode A  当 OneBot 客户端：连 MeaPet hub，事件下、API 上（主用）        │
│   mode B  当 OneBot 服务端：自持 http.Server（因框架路由无法接管 upgrade）│
│   会话映射 每个 QQ 会话 → 一个 dsh agent 会话（state.json 原子落盘）      │
│   事件过滤 按 agent.id 过滤（根 ctx 监听者会收到所有 agent 的事件）       │
└────────────────────────────────────────────────────────────────────────┘
```

**一句话总结数据流**：QQ 事件从上游进来 → `service` 解析/过滤 → 按 `reply_mode` 决定
「进桌宠轮次队列」和/或「广播给 hub」→ 回复要么由桌宠会话流产出、要么由 hub 消费者发 API
调用回来 → 经 `SenderRegistry` 选一条可用通道发回上游。

---

## 3. MeaPet 侧分层

分层规则（**改这个包时必须遵守**）：

1. **协议层不做 I/O，编排层不碰 Qt**。`types/cq/segments/events` 是纯函数，可脱网测试。
2. 只有一个模块知道「四条通信方式」的存在：`service.py`。传输层只暴露两个接口 ——
   `send(action, params) -> ApiResponse` 与「事件回调 `(payload, source)`」。
3. 桌面侧只通过 `service` 的三个方法交互：`take_ready_turn()` / `complete_turn()` /
   `fail_turn()`，全部 `threading.Lock` 保护，可从 Qt 主线程安全调用。

| 模块 | 职责 | 关键点 |
|------|------|--------|
| `types.py` | 规范数据结构 | `MessageSegment` / `IncomingMessage` / `ConversationTarget` / `ApiRequest` / `ApiResponse`；`IncomingMessage.source` 记住事件来自哪条上游连接，决定回复走哪条 |
| `cq.py` | CQ 码编解码 | 转义/还原顺序敏感：先还原 `&#91;` 等实体、**最后**还原 `&amp;`，否则 `&amp;#91;` 会被二次解码成 `[` |
| `segments.py` | 三种消息形态归一 | 字符串形态**要**反转义、数组形态**不能**反转义；非文本段转占位符供模型阅读 |
| `events.py` | 上报事件解析 | 全防御式：字段缺失/类型异常都退化成「不可路由事件」，不让实现端差异打挂桌宠；`is_api_response()` 用「有 echo/retcode/status 且**没有** post_type」区分响应与事件 |
| `api.py` | 发送端抽象 | `SenderRegistry.pick(source)`：精确来源 → fallback → 任意可用；`HttpApiSender` 先打 `{base}/{action}`，404/405 才退回 `{base}/` |
| `transport/ws_session.py` | 双向 WS 会话 | 同一对象服务正向与反向两种连接；`send()` 用 `echo` 关联响应并带超时；`close()` 让所有在途调用**立刻失败**，不留悬挂 future |
| `transport/forward_ws.py` | 发起端 | 指数退避重连；取消/断开时**显式关连接**（否则对端要等超时才发现我们走了） |
| `transport/server.py` | 接收端 | 把启用的接收端点按 `(host, port)` 归并成**一个** Starlette 应用；鉴权在 `accept()` **之前**完成 |
| `service.py` | 编排 | 路由、队列、hub 广播、动作白名单、统计；`PendingTurn.prompt` 给群聊带上说话人前缀 |

---

## 4. 线程与生命周期模型

### 4.1 线程归属

| 工作 | 线程 / 循环 |
|------|-------------|
| 所有 socket、uvicorn、websockets | `meapet/async_runtime.py` 的 asyncio 守护线程（单例 loop） |
| 桌面轮询、气泡、菜单 | Qt 主线程 |
| 二者之间的**唯一**通道 | `OneBotService` 内部受锁保护的队列 + 两个计数器方法 |

**绝不**在 Qt 线程做网络 I/O；**绝不**从网络线程碰控件。这是项目既有铁律，本功能没有例外。

### 4.2 启停钩子（这是最容易漏的地方）

```text
启动   MeaPet.__init__  →  _safe("renderer"…"control")  →  _safe("onebot", self._init_onebot)
                          （包在 _safe 里：OneBot 起不来只记日志，不影响桌宠其它部分）

配置重载 _apply_runtime_config()  →  _stop_onebot()  →  …  →  _init_onebot()
退出    _quit()（window_chrome）  →  _stop_control()  →  _stop_onebot()  →  …  →  QApplication.quit()
```

- `closeEvent` **只是隐藏窗口**，不是退出；`aboutToQuit` 只记一行日志。所以 OneBot 的
  `stop()` 必须挂在 `_quit()` 与 `_apply_runtime_config()` 这两处，否则进程活到 atexit。
- 所有调用点都写成 `getattr(self, "_stop_onebot", None)` 形式，这样只继承某几个 mixin 的
  测试宿主不会因为缺方法而炸。

### 4.3 关闭顺序（避免悬挂任务）

`OneBotServer.stop()` 刻意**不**设 `uvicorn.force_exit`：那会跳过 lifespan 收尾、留下悬挂任务，
在事件循环关闭时抛 `Event loop is closed`。正确顺序是：

```text
1. forward 客户端 stop()（取消任务 + 显式关连接）
2. 关闭全部上游 / hub 会话   ← 必须在停监听之前：否则 uvicorn 优雅收尾会等这些连接
3. 各 server stop()：should_exit = True，给 2s 宽限窗口；窗口后仍未退出才 cancel
```

---

## 5. 通信方式与连接角色

| 配置段 | 角色 | 我们做什么 | 对端做什么 | 端口（默认） |
|--------|------|-----------|-----------|-------------|
| `forward_ws` | 发起端 | 主动连出去 | 当 WS 服务端 | 对端：NapCat 6700 / SnowLuma 3001 |
| `reverse_ws` | 接收端 | 监听 `/onebot/v11/ws` | 以「反向 WS 客户端」连进来 | 我们：14321 |
| `http_api` | 发起端 | `POST {base}/{action}` | 提供 HTTP API | 对端：NapCat 5700 / SnowLuma 3000 |
| `http_post` | 接收端 | 监听 `/onebot/v11/http` 收事件 | POST 上报事件 | 我们：14321 |
| `hub` | 枢纽 | 监听 `/onebot/v11/hub`（WS）与 `/onebot/v11/hub/http`（API） | 当 OneBot 客户端接入 | 我们：14321 |

- 后三者的默认端口都是 **14321**，运行时按 `(host, port)` 归并成**一个** ASGI 应用、按 path 分流。
  只有显式写了不同端口才会起多个监听。
- **鉴权**统一按规范 §2.5：`Authorization: Bearer <token>` 优先，其次 `?access_token=`；
  token 为空表示不鉴权。WS 的鉴权在 `accept()` 之前完成（不合格直接拒绝，不建立连接）。
- `http_post` 只收不发；要能回消息必须同时启用 `http_api`（配置校验会提示）。
- 另有**事件 HTTP POST 上报**（`hub.push_urls`）：我们主动把事件 POST 给配置的地址，
  用的是 WS 上完全相同的帧，供只会 HTTP、不能维持长连接的消费方使用（等价于 SnowLuma 的
  `httpClients`）。它和 WS 广播共用 `hub.enabled` + `hub.publish_events` 这道开关。

---

## 6. 消息路由与回复闭环

### 6.1 入站过滤

```text
parse_event(payload)
  ├─ 非 message（notice / request / meta_event）→ 只按需广播给 hub
  └─ message
       └─ reply_mode ∈ {hub, both} 且 hub 启用且 publish_events → 广播原始 payload
            └─ reply_mode ∈ {pet, both} → _allowed() 过滤
                 ├─ allow.private / allow.groups 开关
                 ├─ allow.users / allow.group_ids 白名单（空 = 不限）
                 ├─ 群聊 require_at（默认要 @ 机器人）
                 └─ 文本非空
                      └─ 入队为 PendingTurn
```

**注意顺序**：广播（`_publish`）排在过滤（`_allowed`）**之前**。也就是说没 @ 机器人的群消息
**仍会**送到 hub —— 这是刻意的：hub 消费者（dsh）应当看到完整上下文，要不要回应由它自己判断
（插件侧有独立的 `requireAt`，那才是真正生效的那一道）。

**这条顺序的准入后果**（别漏读）：`_allowed()` 只在 `reply_mode ∈ {pet, both}` 下调用，
`reply_mode=hub` 时**一次都不会执行**。所以 MeaPet 的 `allow.users` / `allow.group_ids` /
`allow.private` / `allow.groups` **对 hub 这条路没有任何约束力**；驱动 dsh agent 的准入由插件侧的
`requireAt` + `allowUsers` / `allowGroups` 负责（见 §10）。

### 6.2 桌宠回复闭环

```text
Qt QTimer(500ms) ──▶ _poll_onebot()
   │  若已有 OneBot 轮次在跑：检查是否超时（超时则 fail_turn 释放），否则返回
   │  若 _awaiting_reply（例如用户正在手打）：直接返回，不取轮次
   └─ service.take_ready_turn()  ──▶ PendingTurn
        └─ self._onebot_turn_id = turn.turn_id
             └─ self._do_chat(turn.prompt)      ← 复用既有对话流（分段/TTS/气泡/记忆）
                  │
                  │  …既有流程产出回复…
                  ▼
        _notify_channel_turn_finished(reply)     ← 基类是 no-op，由 PetOneBotMixin 覆盖
             └─ service.complete_turn(turn_id, reply)
                  └─ SenderRegistry.send(send_group_msg/send_private_msg, source=turn.source)
                       └─ 发回 QQ
```

**回复挂钩点**（`chat_flow.py` 里 6 处，都是单行调用）：

| 位置 | 触发 | 传参 |
|------|------|------|
| `_on_chat_done` | direct 模式文字定稿 | `reply` |
| `_finish_agent_turn` | agent 模式文字定稿 | `reply` |
| `_fail_agent_turn` | agent 失败 | 中性文案，原因 `agent_turn_failed` |
| `_cancel_agent_turn` | 本机主动打断 | 空（**不回复 QQ**：这不是对端提问失败） |
| `_on_chat_error` | 直连错误 | 中性文案 `model_service_error()` |
| `_on_chat_timeout` | 直连超时 | 中性文案 `chat_timeout()` |

设计要点：**direct 模式在文字定稿时立刻回收，不等 TTS 音频**。QQ 侧要的是文字，早发一秒是一秒。

### 6.3 串行化与超时（为什么不会卡死）

- `ConversationOrchestrator.accepts()` 要求 `turn_id == _active_turn_id`，即**同时只有一轮**。
  因此 QQ 消息是排队（`queue.max_pending`，满了丢**最新**一条并计数），不是并发。
- **双侧超时**：通道侧 `queue.turn_timeout_s`（默认 180s）在 `PendingTurn.deadline` 上；
  桌面侧 `_onebot_turn_deadline` 在轮询里检查。任一侧发现超时都会 `fail_turn` 释放，
  不会出现「上游丢了回复 → 队列永久堵死」。
- 迟到事件由既有 `generation_id` 机制丢弃（配置保存会 `invalidate()`）。

### 6.4 hub 双向

- **下行（我们 → 消费者）**：事件原样广播（不改写任何字段）。两条通道：
  1. **WS 推送**：连上 hub WS 的消费者直接收；
  2. **HTTP POST 上报**（规范 §2.2）：`hub.push_urls` 里配置的地址会被逐个 POST，
     供只会 HTTP 的消费方使用（等价于 SnowLuma 的 `httpClients`）。鉴权用
     `hub.push_token`（留空复用 `hub.access_token`）。
- **快速操作（规范 §7）**：HTTP 上报的**响应体**可以回带与 API 参数同形的字段（目前支持
  `reply`），我们据此立刻把回复发回该事件所属会话；`hub.allow_send=false` 时忽略。
- **上行（消费者 → 我们）**：消费者在同一条 WS 上发 `{"action","params","echo"}`；
  `service.handle_hub_call()` 校验白名单后经 `SenderRegistry` 转发到上游。
- **动作白名单**（17 条，只读 + 发消息）：`send_private_msg` / `send_group_msg` / `send_msg` /
  `delete_msg` / `get_msg` / `get_forward_msg` / `get_*_info` / `get_*_list` /
  `can_send_image` / `can_send_record`。**踢人、禁言、全体禁言等管理动作一律 `retcode=103` 拒绝**，
  不透传到 QQ。
- `hub.allow_send=false` 可连发消息也禁掉，只保留事件广播。
- 没有上游连接时，`get_status` / `get_version_info` 由我们自己作答（便于消费者探活），
  有上游则透传。

---

## 7. 配置模型

### 7.1 Schema 归属

`onebot` 段的**唯一真相**是 `meapet/onebot/config.py` 的 `DEFAULT_ONEBOT` 与
`normalize_onebot_config()`；`meapet/config/store.py` 的 `_normalize_onebot()` 只是委托，
`normalize_config()` 里一行接线。这样协议实现自带 schema、可独立测试。

规范化是**幂等**的，任何脏输入都退化为安全默认值（端口越界回落、`reply_mode` 非法回落 `pet`、
整数列表去重排序、路径补前导 `/`）。

### 7.2 校验

`validate_onebot_config()` 只报**会导致功能不可用**的问题（返回人类可读列表）：

- `enabled=true` 却没有任何启用中的方式；
- 私聊与群聊同时禁用；
- `forward_ws.url` 不是 `ws://`/`wss://`、`http_api.api_base` 不是 `http(s)://`；
- 启用了 `http_post` 却没启用 `http_api`（只能收不能发）；
- 监听地址是非回环却**没有**显式打开 `allow_insecure_remote`（fail-closed）。

同一函数被三处复用：启动日志、配置向导的红点、运维脚本。

### 7.3 秘密

- 五个 `access_token` 在 `scrub_secrets()` 中会被清空，不会进诊断导出。
- OneBot 的 token **不走环境变量解析**（与 LLM/TTS 密钥不同）：它是对端约定的共享串，
  由配置或 profile patch 持有。这一点在文档里写清楚，避免有人去找 `MEAPET_ONEBOT_TOKEN`。

### 7.4 端口策略

默认所有接收端点都在 **14321**，因此默认只占一个端口。`forward_ws` 的默认 URL 指向
SnowLuma 的 3001 —— 那是**对端**的端口，改默认值时不要一起改。

---

## 8. dsh 插件侧架构

### 8.1 两种模式

| 模式 | 形态 | 对应 MeaPet 配置 | 说明 |
|------|------|------------------|------|
| A（默认，主用） | 插件当 OneBot **客户端** | `onebot.hub.*` | 连 hub，收事件、发 API 调用 |
| B（可选） | 插件当 OneBot **服务端** | `onebot.forward_ws.url` 指向它 | 自带 `http.Server` |

**为什么 mode B 必须自持 `http.Server`**：`ctx.webServer.register` 的 handler 签名是
`(request, response)`，**没有接管 WebSocket upgrade 的位置**，而 OneBot 正向 WS 必须接管 upgrade。
这是框架约束，不是省事。

### 8.2 关键约束（三条已核实事实）

1. `ctx.webServer.register` 注册的路由**没有任何框架级鉴权**，必须自己写。
2. `text/delta` 来自瞬时事件，**没有订阅者时永久丢失** → 必须先建立订阅再驱动会话，
   否则丢开头几个 token。
3. agent 事件虽经 `Scoped<Agent>` 派发，但根 ctx 上未打 tag 的监听者会收到**所有** agent 的事件
   → 必须按 `agent.id` 过滤，否则会串到用户在 Web UI 里的会话。

### 8.3 会话模型

- 会话键 `private:<user_id>` / `group:<group_id>` → 一个 dsh agent 会话；
  映射原子落盘在 `<DSH_HOME>/onebot-relay/state.json`。
- `requireAt`（默认 true）+ `maxQueuedPerConversation`（背压）+ `idleTtlMs`（空闲释放 live agent
  但保留映射）+ 幂等。
- 审批请求会转发到聊天侧（`approvalEnabled` / `approvalTimeoutMs`）。
- 配置用 schemastery 校验，**必填缺失时加载即失败，不静默降级**。

### 8.4 与既有 `dsh-astrbot-relay` 的关系

两者是**并列的 IM 网桥**，互不依赖，实现范式相同（都源自同一套已核实事实与契约习惯）。
同一 QQ 账号**只应接一套**，否则两边各驱动一次 dsh 会话 → 用户看到重复回复。

---

## 9. 线上契约（互操作面）

| 方向 | 帧 | 说明 |
|------|----|------|
| 事件 | `{"time","self_id","post_type":"message","message_type","sub_type","message_id","user_id","group_id","message","raw_message","sender":{…}}` | 原样透传，不改写字段 |
| API 调用 | `{"action":"send_private_msg","params":{"user_id":…,"message":…},"echo":"<唯一>"}` | `echo` 用于关联响应 |
| 成功响应 | `{"status":"ok","retcode":0,"data":{…},"echo":"<同上>"}` | 按 `echo` 归位，**不假设顺序** |
| 失败响应 | `{"status":"failed","retcode":103,"data":null,"echo":…}` | `103` = 动作不在白名单 |
| 鉴权失败 | HTTP 401 + `retcode=104`；WS 在 upgrade 前拒绝 | |
| 响应 vs 事件判别 | 有 `echo`/`retcode`/`status` 且**没有** `post_type` → 响应 | 抄自 `events.py:is_api_response` |
| 事件上报（我们主动推） | `POST <hub.push_urls[i]>`，body 与事件帧逐字相同，头 `Authorization: Bearer <push_token>` | 规范 §2.2；消费方只需能收 HTTP POST |
| 快速操作 | 上述 POST 的**响应体**：`{"reply": "文本", "auto_escape": false}` | 规范 §7；我们据此立刻发回该会话，`hub.allow_send=false` 时忽略 |

**发送一律用明文纯文本**：规范允许字符串 / 数组 / 单段三种形态，明文是唯一不需要转义的一种，
最不容易出错。`message` 字段的三种形态我们都**能收**（见 `segments.normalize_message`）。

**NapCat 与 SnowLuma 的差异**：

| 项 | NapCat | SnowLuma |
|----|--------|----------|
| 正向 WS | `ws://host:6700/`（WebUI 里开「WebSocket 服务器」） | `ws://host:3001/` |
| HTTP API | `http://host:5700` | `http://host:3000` |
| 反向 WS | 我们监听，NapCat 配「WebSocket 客户端」 | 配置里加 `wsClients` |
| 事件 POST | 我们监听，NapCat 配 `httpClients` | 配置里加 `httpClients` |
| **token** | HTTP 与 WS 通常同一个 | **HTTP token 与 WS token 是两个不同的值**，且账号/UIN 变化时都会变 |
| WS 角色 | 不区分（等同 Universal） | `wsServers.role` 可为 `Universal` / `Api` / `Event`；**建议保持默认 `Universal`**——拆开需要两条连接，本实现按单连接设计 |
| 应答信封 | 带 `echo` | 官方文档里的信封**不带 `echo`**（HTTP 一对一不需要）。WS 上我们做了「只有一个在途调用时按唯一性归位」的兜底，超过一个则丢弃并等超时 |
| 事件上报（`httpClients`） | 我们监听 `http_post` | 同名机制，`timeoutMs` 默认 5000（我们的 `push_timeout_s` 默认 5s） |

---

## 10. 安全模型

| 面 | 措施 |
|----|------|
| 入站鉴权 | 每个端点独立 `access_token`；WS 在 `accept()` 前校验；`secrets.compare_digest` 定长比较 |
| 暴露面 | 默认只绑 `127.0.0.1`；非回环地址需**显式** `allow_insecure_remote`，否则启动即报配置问题 |
| hub 权限 | 17 条白名单；管理类动作（踢人/禁言/改名片…）一律拒绝；`allow_send` 可整体关掉发送 |
| 日志隐私 | 只记长度与元数据（`events_in`、`message private:42 len=6 id=7` 这类）；**不记正文**、不记 token / Authorization；`events.summarize()` 被单元测试锁定不得含正文 |
| SSRF | 不下载 QQ 图片；`ImageAttachment` 只接受内联 base64，URL 图片在项目层被拒 |
| 请求体 | 上报 `POST` 有 8MB 上限；非 JSON 直接 400 |
| 秘密外泄 | `scrub_secrets()` 清空全部 token |
| **谁能驱动 agent** | ⚠️ **这是最需要看清的一条**：MeaPet 的 `allow.*` 只在 `reply_mode` 含 `pet` 时生效（`service.py:365`），而 `reply_mode=hub` 时 `_allowed()` **一次都不会被调用**——广播排在过滤之前（§6.1）。所以那条路上的准入**只能由下游把关**：插件侧有 `requireAt`（群聊必须 @）与 **`allowUsers` / `allowGroups`**（空 = 不限制，与 MeaPet 同约定）。没有名单时，**任何能给这个 QQ 号发私聊、或在群里 @ 它的人都能驱动 dsh agent**（agent 手里的工具调用另有审批兜着） |

**没有做的**：不限速（OneBot 侧通常自带）、不做 IP 白名单（token + 回环默认已够；
需要跨机时建议配反向代理 + TLS）、MeaPet 侧不对 hub 广播做过滤（刻意：hub 消费者应当看到
完整上下文，要不要回应由它自己判断——**但这也意味着别把 MeaPet 的白名单当成 hub 的准入**）。

---

## 11. 失败模式与兜底

| 失败 | 兜底 |
|------|------|
| 上游断线（NapCat 重启） | 反向 WS：连接断开自动清理 sender，对端重连即恢复；正向 WS：指数退避重连（1s→30s） |
| 上游不回 API 响应 | `WsSession.send` 超时（默认 15s）→ 失败返回，不留悬挂 future |
| 上游丢回复 / 桌宠 LLM 卡死 | 通道侧 + 桌面侧**双侧超时**释放队列 |
| 同时多个群说话 | 队列串行 + `max_pending` 上限 + 丢新计数 |
| 往不存在的通道回发 | `SenderRegistry` 逐级回退；全不可用则失败计数，不抛异常 |
| OneBot 起不来 | 包在 `_safe()` 里，只记日志；桌宠其它功能不受影响 |
| 不可信的实现端字段 | 协议层全防御式解析，最坏退化成「不可路由事件」 |
| 超大 / 分块请求体 | 接收端自己累加并随时封顶（`MAX_BODY_BYTES` = 8 MiB），**不只看 `Content-Length`**——分块请求不带该头，`request.body()` 会把整个请求无上限读进内存。超限返回 413 |
| 非对象的 JSON 请求体 | 返回 **400**（而不是假装 200 ok）。事件必然是对象；回 ok 会让对端以为投递成功而其实被丢掉 |
| 同一 host:port 上路径撞车 | 配置校验直接报「会被路由遮蔽」。否则后注册的路由被前面的遮住，症状是某端点莫名鉴权失败/收不到事件 |
| `stop()` 抢在 `_start_all()` 前面（改配置触发 reload、或退出桌宠） | 先建对象、**最后一次性**判断 `_started` 再注册与监听；`start()` 也**先置位 `_started` 再提交协程**。否则会出现「已 append 未 start」或「已 start 但列表已被清空」的遗留监听，或把正常启动误判成被 stop 抢先 |
| 回复迟到、而服务已经换了一茬（配置 reload） | `complete_turn()` 校验 `turn_id`，对不上就静默返回；即使走到 `send_message`，`SenderRegistry` 也只会返回失败对象，不抛异常 |
| 插件加载失败（配置不合法） | 插件**加载即失败**（不静默降级）；配前可用 `validate-installed-config.mjs` 预检 |

---

## 12. 扩展点

**加第 5 种通信方式**：只在 `transport/` 加一个模块，实现
`send(action, params) -> ApiResponse` 与事件回调；在 `service._start_all()` 里按配置启动并
`senders.register(...)`；配置 schema 加一段；向导加一组控件。**协议层与桌面侧不用动。**

**加一个新的下游消费者（第二个 dsh 实例 / 别的框架）**：`hub` 支持多消费者（`hub_sessions` 是列表，
广播给所有）。只要它们各自实现 OneBot 客户端即可。

**加一个新的 hub 动作**：`service.HUB_ALLOWED_ACTIONS` 加一条。加之前先想清楚它的破坏力 ——
这是唯一的安全边界。

**改状态 / 菜单文案**：只改 `meapet/desktop/status_language.py`（"functions, not strings" 是项目约定），
别在窗口代码里写裸字符串。

---

## 13. 验证策略与现状

### 13.1 测试金字塔

| 层 | 文件 | 用例数 |
|----|------|--------|
| 协议 / 配置 / 编排 / 转发与解析诊断日志 | `tests/test_onebot_protocol.py` | 130 |
| 真实 socket 端到端（含事件 HTTP POST 上报） | `tests/test_onebot_integration.py` | 10 |
| 桌面接线 + http_api 发送端 | `tests/test_onebot_desktop_bridge.py` | 27 |
| 配置向导页面（含"保存→落盘→读回"往返、渐进披露的显隐规则） | `tests/test_wizard_onebot.py` | 19 |
| 运维脚本的纯判断（`--via-pet` 闸门、首次会话判据、活跃 profile 推断） | `tests/test_onebot_scripts.py` | 12 |
| **合计** | | **198** |

（另有 UI 回归 `tests/test_ui_refactor.py` 62 条，改了菜单/向导文案后必须一起跑。）

集成测试**不需要 QQ**：它自己扮演 NapCat（正向 WS 服务端 + 反向 WS 客户端），用真实 socket 与
真实 uvicorn 验证四种方式与 hub 全双工。它踩过并修掉的两个真问题：
`Server.wait_closed()` 会等所有连接结束（必须先显式关连接）、以及取消 uvicorn future 会留下悬挂任务。

### 13.2 已验证（有证据）

| 项 | 证据 |
|----|------|
| 真实 NapCat 入站 | `[probe] ← message group 900000002 id=…`、`← notice`、`← meta_event` |
| 真实 NapCat 出站 | `get_login_info → {"user_id":123456789,"nickname":"<机器人昵称>"}`、`get_status → online/good`、`get_version_info → NapCat.Onebot v11 4.18.13` |
| hub 鉴权 + 白名单 | 无 token 被拒（InvalidStatus）；带 token `get_status` 200；`set_group_ban` → `retcode=103` |
| 单端口合并 | `group_listeners` 把 reverse_ws + hub 归并为一个 `127.0.0.1:14321` 监听 |
| 桌宠整机接线 | `[init] onebot 初始化完成`；MRO 含 mixin；`upstream=1`（NapCat 连上）；运行 40s 后 `_quit` → `_onebot_service=None` |
| 插件自测 | `self-test.mjs` 40/40；`run-tests.mjs` 8 个文件全通过；`check-syntax` 23/23 |
| 插件在真实 profile 可解析 | `import("dsh-onebot-relay")` 成功，`apply` 是函数，peer 依赖全部解析 |
| 安装配置合法 | `validate-installed-config.mjs` 过 schema + 加载期校验（本轮**两个方向都实测过**：真实 profile → PASS；缺 `meapetToken` / `cwd` 相对 / `clientEnabled` 却空 `meapetWsUrl` / 非回环 host 未开 `serverRemote` → 四条各自 FAIL 并给出可操作的报错）。它的输出会**抹掉 URL 里的 `access_token`**，所以可以放心贴出来 |
| token 对齐 | MeaPet `hub.access_token` == 插件 `meapetToken` |
| **插件真的被 dsh 加载** | 一次性实验场（独立 `DSH_HOME`，不碰用户 profile）：`--dump-config` 里插件行**恰好 1 行、0 条 skip 告警**；真起 harness 后 `GET /onebot-relay/status` **无 token=401 / 带 token=200**，`plugin=dsh-onebot-relay`、`selfId=123456789`、`actions.allowed=17`。已固化为可重跑脚本 `dsh-onebot-relay/scripts/verify-in-throwaway-harness.mjs`（ALL PASS） |
| **插件连上 MeaPet hub** | 同一实验场 + MeaPet 探针：`client={url: ws://127.0.0.1:14321/onebot/v11/hub, connected:true, opened:1, lastError:""}` |
| **OneBot 事件 → dsh 会话** | 从探针推一条合成 `message`：插件 `conversations: 1`，`<DSH_HOME>/onebot-relay/state.json` 写入 `private:10001 → {"dshSessionId":"ob-mv2jdq8b-9b163b98"}`，并在 `sessions/` 下建出该会话目录 —— 即真的调用了 dsh 的 `agents.create` |
| **插件 → MeaPet（回发方向）** | 两进程联调（探针起 hub + 真实 dsh 起插件）：探针推一条 `/status`，插件**本地作答**后回发 `send_private_msg params={"user_id":10001,"message":"会话：private:10001\nDSH 会话：(尚未创建)…"} → retcode=1`。`retcode=1` 是对的——探针这时没有 QQ 上游，失败被正确回传而不是静默丢弃。消费者重连后再次触发，**可复现**（见 §13.7） |
| **配置界面：OneBot 页改为渐进披露（实测整页 −44% 高度）** | 用户要求"搞一下配置界面"。先量化现状：把该页离屏渲染成 PNG（**必须注册项目自带的 `meapet/assets/fonts/LXGWWenKai-Regular.ttf`**，否则 Qt 没有字体目录会把文字渲染成空框）→ 同一份配置下整页 **3546px** 高；而 `design-system/pages/wizard.md` 的 Progressive disclosure 四条规则里**没有 OneBot 页**（TTS 页早已"引擎详细设置默认折叠"）。<br>改法：五种通信方式的**"启用"勾选框提到分区外面**——这是关键，原来勾选框在分区**里面**，整段折叠会把启用入口一起藏掉（这正是我早先不敢做折叠的原因）；分区只在勾上时显示，分区说明同时挂到勾选框 tooltip（折叠后信息不丢）。`reply_mode=hub` 时准入规则整段不生效（§6.1 有据），因此折叠该分区并就地显示一句说明。总开关只决定"可用/置灰"，**不决定显示/隐藏**——否则用户关掉总开关后再勾选某方式会什么都不出现，看起来像界面坏了。<br>**实测：3546px → 1981px（−44%）**；`design-system/pages/wizard.md` 补了第 5 条规则。<br>验证：新增 5 条测试（折叠后勾选框仍可见 / 只显示已启用的分区 / 总开关关时"可见但置灰" / 准入规则随 `reply_mode` 折叠且留说明 / 折叠纯属显示层、取值不变）；**牙齿**：把折叠判据改回"按总开关"→"分区应当出现"那条**真的 FAIL**。既有 14 条向导测试全绿（说明没有测试依赖"分区必须可见"）。<br>⚠️ 如实说明：**我这个模型不能读图**（`read_image` 直接报 `model "deepseek-v4-flash" does not declare image input`），所以版式只能靠几何量测 + 结构断言验证；改前/改后两张 PNG 已渲染出来交给用户亲眼看 |
| **真实 QQ 消息走完全程（QQ → MeaPet → dsh → QQ）：目标里最后一环，已观测（2026-10-11 11:40）** | 用户在自己的 QQ 里 @ 机器人后，**每一跳都有据可查**：<br>· **QQ → NapCat**：NapCat 控制台 `接收 <- 群聊 [<群名A>(900000001)] [<用户昵称>(10001)] @<机器人昵称> (123456789) 在吗`；<br>· **NapCat → 桌宠**：`logs/onebot.log` 里 `收到 message 事件（解析前）：post_type=message message_type=group sub_type=normal user_id=… group_id=900000002 message_id=… message=[text]（1 段）`（本轮新加的解析前留痕第一次派上用场）；<br>· **桌宠 → hub**：`已转发事件：hub 消费者 1 个，group:900000002 msg=…`；<br>· **插件 → dsh**：`/status` 报 **`conversations=1`**；`<DSH_HOME>/onebot-relay/state.json` 生成 `"private:10001": {"dshSessionId": "<dsh-session-id>", …}`；`~/.dsh/sessions/--C-Users-<用户名>-…-mea-pet-onebot--` **改于同一时刻**（真的调了 dsh 的 `agents.create`）；<br>· **回复 → QQ**：用户确认收到回复，且桌宠日志里**没有任何** `回发失败` / `本轮未产生回复` / `超时` 告警。<br>**这次卡住的两个真凶（都不是这段代码）**：① 插件被装进了**不活跃的 profile**（`web`，而 harness 跑 `desktop`）⇒ 永远 404；② **NapCat 收到了消息却不上报**（API 调用正常、心跳正常、`get_group_member_list` 能返回真实成员，说明它的 API 侧是好的）⇒ **重启 NapCat 后立刻恢复**。两条都已进排障表（§14.4）。 |
| **事件 HTTP POST 上报 + 快速操作** | 单元测试 `tests/test_onebot_protocol.py::HttpPushTests`（10 项）：事件按同一帧 POST 且带 `Bearer`、`push_token` 留空时复用 `hub.access_token`、HTTP 4xx/5xx 与连接失败计入 `push_failures`、响应带 `reply` 时经 `SenderRegistry` 发回该会话、`allow_send=false` 时忽略、notice 事件同样上报、未配置时完全不动作。**真 socket 版**（不经 `MockTransport`）见下方「事件 HTTP POST 上报：真 socket 端到端」那条，以及 §13.1 金字塔里的 `test_onebot_integration.py` |
| **桌宠自己的回复闭环**（reply_mode=pet） | `scripts/onebot_pet_reply_e2e.py`：桩 LLM + 假 NapCat。桌宠真的调了两轮流式 `/v1/chat/completions`（第一轮 system 1243 字＝主回复；第二轮 system 429 字＝**格式修复**请求），并把回复以 `send_private_msg` 回发到**原连接**（`params.user_id=10001`，文案与桩模型一致）。反向 WS 用一次性随机 token，真实 NapCat 连不上，**不往 QQ 发任何东西** |
| **SnowLuma 对接面（按官方文档逐项核对）** | 对照其 API/配置文档：`POST /{动作名}` 命中我们发送端的**首选**路径、Bearer 与 `?access_token=` 都认、`wsClients`/`httpClients` 分别对应我们的 `reverse_ws`/`http_post`、`messageFormat: array` 与 `reportSelfMessage`（`message_sent`）行为明确。据此**发现并修掉**一处真实分歧：其应答信封不带 `echo` → `WsSession` 增加「仅一个在途调用时按唯一性归位」的兜底（新增 3 项测试） |
| **`404` 的真正成因：插件装进了「不活跃的 profile」（实测踩到，已做自动诊断）** | 用户重启 harness 后自检仍报 404。查进程与文件时间戳才看清：harness **确实重启了**（PID 11852，10:48:20），而 `~/.dsh/profiles/desktop/cordis.yml` **正好改于 10:48:20**（harness 启动时会重写活跃 profile 的它）—— 说明它跑的是 **`desktop`**；可插件被我装进了 **`web`**（`web/cordis.yml` 还停在 10-01）。即**配置全对、就是装错了 profile**，症状是永远 404 且毫无线索。此前几轮我把 `web` 当"用户的 profile"，是从最早的安装记录沿用的，**从没核实过哪个是活跃的**。<br>修法两层：① 装到 `desktop`——安装器正常（备份 + dependencies/bundles + patch 行，YAML 自检 PASS），但 **`dsh plugin --profile desktop add` 被拒**：`profile "desktop" is managed exclusively by the Electron application`，于是按一次性实验场脚本同一手法手工解包进 `desktop/node_modules/dsh-onebot-relay`；② **让工具自己诊断**：`onebot_postinstall_check.py` 现在**自动推断活跃 profile**（各 profile 里 `cordis.yml` 最新的那个，无需再传 `--profile`），404 时直接分三种情况说清：「装在 A、不在活跃的 B 里」/「还没装进任何 profile」/「已在活跃 profile ⇒ 多半是没重启」（实测输出正是最后一种）。<br>**重启前的验证**：落盘标志物正确（`state.js` 9268 含 `fsyncSync`、`index.js` 含 `serverAllowSend`）；**按 harness 的方式真 import 一次**（`node --input-type=module -e "await import('dsh-onebot-relay')"`，cwd=desktop profile）→ 成功且 `apply` 是函数（连带解析了依赖图）；`validate-installed-config --profile desktop` → 两项 PASS；**别家插件未被波及**（`ui-settings*`、`llm-pi-ai`、`preset-minimal` 与两条 `!!js` 行都在，我们的行只出现 1 次）。新增 4 条单测钉住判定（含**今天的真实形状**：插件在 `web`、活跃是 `desktop` ⇒ `installed=False`）。<br>⚠️ 两条如实说明：① `desktop` 由 Electron 应用独占管理，**手工放置的包将来可能被它的插件管理器清掉**（症状：404 又回来）——那时应改用应用自带的插件管理入口，或重跑这两步；② 有一条"不重启"偏方（`dsh --profile desktop --dump-config` 会重写 `cordis.yml`，其 docstring 记着"可能触发热重载"），我**故意没在运行中的 harness 上试**——那是对用户实时会话的未验证操作，宁可让他再重启一次 |
| **OneBot 通道的日志此前`哪都没有落`（本轮由"验证自己的建议"发现并修复）** | 我给用户的排查建议是"看桌宠日志里的 `[onebot] 已转发事件…`"。**去验证这条建议时发现它是错的**：`logs/` 下所有文件里 `[onebot]` 出现 **0 次**。根因是 logger 接线——本项目的约定是每个子系统用 `get_color_logger("<name>")` 建带文件 Handler 的 logger（于是有 `app.log` / `tts.log` / `chat_flow.log` / `watch_ctrl.log`…），而 OneBot 的四个模块（`onebot/service.py`、`transport/forward_ws.py`、`transport/server.py`、`desktop/onebot_bridge.py`）用的是**裸 `logging.getLogger(...)`**：既没有 Handler，又在 root 的 WARNING 之下 —— 于是 **INFO 级别连 stderr 都到不了**（`logging.lastResort` 只兜 WARNING+），WARNING 只会在有控制台时打到 stderr、**不进任何文件**。也就是说：本通道"上线/接入/转发/丢弃"这些最关键的运行事实，**此前完全没有落到磁盘**。<br>已修：四个模块统一改用 `get_color_logger("onebot")` → 生成 **`logs/onebot.log`**（与兄弟子系统的形态一致），并清掉随之不再需要的 `import logging`。<br>**实测**：跑一次探针 → `logs/onebot.log` 出现，内容为 `2026-10-11 09:13:52 [INFO] [onebot] [onebot] 已启动：reply_mode=hub modes=reverse_ws` / `…已停止` ✓。全量 248 用例仍通过（3 条诊断测试的 `assertLogs` 目标同步改成 `"onebot"`）。<br>⚠️ 两点如实说明：① 这不是"新功能"，是我上一轮**只看了源码里的 `_log.info(...)` 调用、没验证 logger 是否接了 Handler** 就下了结论——**建议和文档都得跟着改**（§14.4 已改成指向 `logs\onebot.log`，并注明该文件只在重启桌宠后出现）；② 日志行里会出现 `[onebot] [onebot] …` 的重复前缀（formatter 加的 logger 名 + 消息自带的子标签）。这是刻意的取舍：兄弟模块 `chat_flow.py` 的消息同样自带 `[chat]`/`[input]` 子标签，而机械清掉 20 处前缀的收益不抵改动风险，且文档里给的 grep 关键字仍能命中 |
| **自查一条"疑似缺口"，结论是「没有」（负结果，含本轮实跑）** | 我怀疑「事件 HTTP POST 上报 + 快速操作」只有 `httpx.MockTransport` 单测、从未经过真实 HTTP 栈（`MockTransport` 不建 socket，证明不了"真的发出了一个能收的 POST"）。**查证后否定**：`tests/test_onebot_integration.py::HttpPushIntegrationTest` **早已存在**（本轮实跑 **2/2 通过**），它起一个跑在后台线程里的真 `http.server` 当消费者，断言请求真的到达（路径 `/onebot/v11/http`、`Authorization: Bearer …`、`content-type`、帧体原样：`post_type`/`raw_message`/`user_id`），并断言响应体里的 `reply` **真的变成一次 `send_private_msg`**；另有一条「对端不在（端口没人监听）→ 记失败、不抛异常」。它自己的 docstring 就写着"这个特性原先只有 `MockTransport` 单测……这里补上"——是**早先某轮的我**补的。<br>本轮唯一改动：把 §13.2 那条特性行与「真 socket 版」和 §13.1 金字塔做了**交叉引用**（原先单看那一行会以为只有单测）。记下这个负结果，是为了以后不再花一轮重复假设同一个缺口 |
| **`reply_mode=hub` 下"消息到了没"此前在桌宠侧毫无痕迹（本轮补日志）** | 排查最后那条真实消息时先问了一个问题：**桌宠日志能不能证明它收到了消息？** 逐条核对 `meapet/onebot/*.py` 与 `desktop/onebot_bridge.py` 的 `_log.*` 调用后确认：**不能**。原因是结构性的——`reply_mode=hub` 时桌宠**不处理**消息（只转发给 hub 消费者），所以 `[onebot] 处理入站消息 turn=…` 那条根本不会出现（它只在 pet/both 模式、由 `_poll_onebot` 取到轮次时才打）；而 `_publish` 在**没有任何 hub 消费者**时是 `if not sessions and not urls: return` —— **消息静默消失，两边都不报错**。这恰好是最可能发生的失败（插件没连上 / harness 没重启）。<br>已补两条线（**只记元数据**：会话键 + `msg=<id>`，与探针 `--show-text` 同一口径）：转发成功 → `[onebot] 已转发事件：hub 消费者 N 个…`；没有消费者 → **WARNING** `[onebot] 收到事件但没有任何 hub 消费者，已丢弃：…`。<br>**牙齿**：把那条 WARNING 去掉（恢复成静默 return）→ 新增的 2 条测试**真的 FAIL**（`no logs of level WARNING ... triggered on meapet.onebot`）。顺带修正了我自己的一次测试失误：我先把 logger 名写成 `meapet.onebot.service`，实际模块用的是 `logging.getLogger("meapet.onebot")` —— `assertLogs` 直接报"没有日志"，**是它把这个错抓出来的**（不是静默通过） |
| **重启后自检现在会回答"到底跑通过一轮没有"（本轮补，两个分支都实测）** | 原来的 `onebot_postinstall_check.py` 只答"路由通不通 / 连没连上 hub"，而"桥通了"**不等于**"跑通过一轮"——最后那一步（真实 QQ 消息走完 QQ→MeaPet→dsh→QQ）需要人发消息，光看 401/200 看不出来。已加 `first_session_verdict()`（**纯函数**，便于单测）：按 `/status` 的 `conversations` / `pending` / `client.connected` / `statePath` 给出结论，并明确区分**故障**与**"还差你发一条消息"**（后者不记失败项、退出码仍 0——否则会让人误以为桥坏了）。<br>**两个分支都用合成 `/status` 服务实测过**（临时起一个假服务：无 Authorization→401、带了→200+合成 payload，`client.url` 指向真实 hub 端口以便连"端口一致"判据一起走）：<br>· `conversations=0` → 打印 `TODO …请在 QQ 里给机器人发一条消息（群聊需先 @ 它）` + `结论：插件侧一切就绪，还差一步（不是故障）`，**失败项 0**；<br>· `conversations=1` → `PASS 已观测到 1 个 QQ↔dsh 会话映射` + 文件未落盘时给 `WARN`。<br>假服务按 PID 收掉、端口确认释放。另加 5 条单测钉住判据（含"未连接"与"payload 缺失"两种退化情形；`tests/test_onebot_scripts.py` 3→8） |
| **`Get-NetTCPConnection` 在受限环境会漏报（本轮实测，已改文档）** | 我上一轮往 §14.4 排障表里写过"按端口查 PID 再按 PID 杀"，用的是 `Get-NetTCPConnection -State Listen \| Where-Object { $_.LocalPort -eq ... }`。本轮真去查 harness 的 19387 时它报**「空闲」**，可同一时刻 HTTP 探针**正常连上并返回 404** —— 也就是说这个 cmdlet 在受限沙箱里**会漏报监听**。排障指引里给错命令比不给更坏（用户会以为"服务没在跑"），已改成**首选 `netstat -ano \| findstr ":14321"`**（只读文本、不依赖 WMI），并写明"判断服务是否活着优先用 HTTP/连接探针，不要只信这个 cmdlet"。<br>同时修正了对 `--via-pet` 之外另一处同类判断的依据：本轮确认"harness 没重启"是靠 **HTTP 404**、不是靠端口表 |
| **模板同步与热重载：一条补了守卫，一条查证即闭（本轮核查）** | 上轮点名的两条缝逐条查：<br>① **`config.example.json` 与 schema 是否同步**——与插件侧 `cordis.patch.yml` 那个坑同型（模板落后 schema ⇒ 该功能"在模板与文档里都不存在"，且不报错）。实测**完全同步**：两边各 **51 个点号键**，缺失 0、多余 0。已补**永久守卫测试**（逐键比对，缺/多都点名）；**牙齿**：删掉模板里的 `hub.push_urls` → FAIL 且报 `模板缺少这些 OneBot 配置项：['hub.push_urls']`（还原后键数仍 51，`git diff` 仍是同一份 60 行新增 ⇒ 精确还原）。注意该模板相对 HEAD 有 60 行未提交新增（就是本会话加的 OneBot 段），所以**不能用 `git checkout` 还原**，牙齿测试用的是精确文本改回。<br>② **配置热重载**——查证后**否定**了我自己的怀疑：`_reload_onebot` 已有两条测试，且不敷衍：断言旧 service **已 stop**、旧 timer **已 stop**、新 service 是不同对象且 **started**；`enabled=false` 时 `_service is None`。再加上「启动/停止竞态（端口泄漏）」那条已有真插座测试，这条链不缺实质覆盖。<br>结论：两条缝都不需要改产品代码 |
| **向导「保存」的落盘半步此前是 mock 掉的（本轮补上往返测试）** | 脚本审完后找剩下最有价值的一条缝：配置**持久化**。既有测试把 `save_config` **mock 掉**、只断言"传给它的参数对不对"，于是 `collect_config → normalize_config → save_config` 里**真正写盘/读回的那半步从没被执行过**。<br>已补 `test_save_persists_to_disk_and_reloads`：不 mock `save_config`，用真实文件当磁盘底稿（这样连全局 `os.path.isfile` 都不用 patch），保存后 `json.loads` + 真实 `load_config` 读回，并**逐键点名**后加的 `hub.push_*` / `allow.*`（防止"整体相等"掩盖单个键被丢）。<br>**牙齿 + 一个反直觉的发现**：把 `collect()` 的 `self_ids` 临时改成返回 `set`（不可 JSON 化）→ 测试**真的 FAIL**。但失败形态不是"点保存时抛错"，而是 `self_ids` 变成了 **`[]`**——MeaPet 的规范化函数把不认识的类型**静默清成默认值**，也就是**用户保存后值悄悄没了**。所以这条测试挡的是"静默丢配置"，比崩溃更难察觉；测试文档字符串也按实测结论改了（我原本写的是"会当场报错"，实测不是） |
| **探针最危险的开关此前最没被提示（`--echo-reply`）** | 继续按同一标准审剩下三个 Python 脚本。`onebot_pet_smoke.py` 干净（**不注入任何事件** ⇒ 不触发回复路径；且它带 `--config` 可指向别的配置）；`onebot_postinstall_check.py` 只读、且 token 只打前 8 位（逐行核过所有 print）。<br>**但 `onebot_probe.py --echo-reply` 有问题**：它会把 `reply_mode` 切到 `pet`，于是接**真实 NapCat** 时，**任何**真实 QQ 消息都会被那句固定文本回掉——也就是**真的往 QQ 发消息**。而文档里对 `--call` 写了「建议只读动作」，对更危险的它却只有一句「收到消息固定回一句」，没有任何警示。<br>已修：① `--help` 与探针启动时各加一条醒目 `⚠️`（仅当 `--echo-reply` 且上游非 `none`）；② `onebot.md` 的脚本表补上同样的警示与「验回发链路请用 `--upstream none`」。实测三种情形：真实上游形态 → 出现警告；`--upstream none` → 不出现（不误报）；`--help` 里有全文 |
| **审自己的 Python 验证脚本：`--via-pet` 缺少一道闸门（本轮发现并补上）** | 插件那 7 个脚本审完后，按同一标准回头看 MeaPet 侧的 `scripts/onebot_*.py`——它们同样是"证据生成器"。查下来删除/写入路径都是**计算出来的**（`<root>/.tmp/...`，没有 `--home` 这类可由调用方指偏的口子），比插件侧安全。但发现一处**安全前提没被检查**：`--via-pet` 会**用真实 `config.json` 启动真实桌宠**，而它只检查了 `onebot.enabled`，**没检查 `reply_mode`**。含 `pet` 的模式下，脚本注入的合成消息会被桌宠**真的回复**，回复再经桌宠自己的上游（生产配置里就是**真实 NapCat**）发到注入用的 `user_id`（默认 10001 = 一个真实 QQ 号）。<br>已加 `pet_reply_mode_blocks_via_pet()`（**fail-closed**：只有 `hub` 放行）并在 `main()` 里拒绝。用真实 config 走一遍确认 `reply_mode='hub'` → 不拦（`--via-pet` 仍 ALL PASS）；并新增 `tests/test_onebot_scripts.py`（3 项）把这条判断钉住——**导入脚本是安全的**，因为它的副作用都在 `__main__` 守卫里。<br>⚠️ 如实说明：**拒绝分支没有端到端跑过**（那需要把用户的 `config.json` 改成 `pet`/`both`，我不想动它）；验的是判断函数本身 + 真实配置走同一函数不误伤 |
| **配置预检脚本此前只跑过"能过"的一半（本轮补齐 + 修掉一处回显密钥）** | `validate-installed-config.mjs` 是文档让用户重启前跑的预检，但我此前**从没验过它的失败路径**——而一个"只会说 PASS"的预检比没有更糟（给假信心）。本轮双向实测：<br>· 对**真实 profile** 跑 → exit 0，`PASS schema/加载期`，取值都正确（`selfId=123456789`、hub URL、cwd 都对；顺带可见 `pathPrefix` 来自 schema 默认值，与 §14.5 的判断一致）；<br>· 四条坏配置各自 FAIL 且报错可操作：缺 `meapetToken`（"它是唯一的鉴权防线"）、`cwd` 相对路径、`clientEnabled: true` 却空 `meapetWsUrl`、`serverHost: 0.0.0.0` 未开 `serverAllowRemote`。<br>**顺带修掉一处回显密钥**：它会打印 `meapetWsUrl`，而本插件支持 `tokenInQuery`——URL 上带 `?access_token=` 时就把密钥原样打了出来（而且代码里那个 `meapetToken ? '***'` 分支其实是死代码，`meapetToken` 根本没在打印列表里）。已对 `validate` 与 `verify` **两个脚本**加同一套 URL 脱敏；实测 `…?access_token=***`，明文不再出现（把脱敏去掉会**真的**出现 → 证明是它在起作用）。`client.url` 那行同理——它是会被贴进报告/issue 的输出 |
| **文档里那条 `dsh plugin … add` 此前从没真跑过（本轮实测跑通）** | 安装器最后会提示操作者跑 `dsh plugin --profile <p> add .\vendor\<tgz>`，而**这条命令我给用户写过很多次、自己却从没执行过**（一直手工 `tar` 解包）。本轮在一次性 profile 上真跑了：**exit 0**，`node_modules/dsh-onebot-relay/lib/index.js` 出现，随后 `--dump-config` **1 行、0 条 skipping** —— 整条文档路径（建 profile → 安装器写 dependencies/bundles/vendor → plugin add 建链接 → 组合树）完整贯通。<br>顺带弄清两个副作用并写进 §14.5：① 这一步**走 pnpm**（实测 `downloaded 5` + `using pnpm v11.22.0`），可能需要网络或缓存；② 它会建一个 **pnpm 内容寻址存储**，实测落在**仓库上一级**（`.pnpm-store\v11`，0.74 MB），已加进 `.gitignore`，并补了"网络/权限失败时手工 `tar` 解包"的等价做法（一次性实验场脚本用的正是它） |
| **纠正我自己的一条说法：insert 与 override 不是「几行」的问题（本轮实测）** | 早先本节与 §13.4 都写着「旧版安装器写出的双行形态会让同一 id 出现两行」。本轮用同一份 bundle 层 + 不同形态的 profile 层各跑一次 `--dump-config` 实测，**没能复现「两行」**：<br>· `bundle insert` + `profile insert` → **1 行**（组合器按 id 去重）<br>· `profile insert` + `profile override` → **1 行**，且 **override 的 config 胜出**<br>· `profile override` 但 **`dsh.profile.bundles` 里缺本插件** → **0 行**（插件根本没进组合树）<br>「两行」是没有根据的说法，已在安装器注释、安装器日志文案与本文件里一并改成实测结论。真正需要 override 形态的理由是两条可证的：**本安装器的 YAML 自检要求覆盖形态**（insert 形态一律判为不合格，安装器无法收敛），以及**只有覆盖形态能让重跑幂等**（检测到已有覆盖行就跳过）。<br>顺手记下一条更值得防的坑（已写进安装器注释）：**覆盖只改已有行、自己不会建行**——profile 的 `dsh.profile.bundles` 少一行时覆盖层**静默失效**（实测 0 行），症状是 `/onebot-relay/status` 404 而配置看上去完全正常。`verify` 脚本的判据 1 现在把「0 行」的排查方向直接写进错误信息 |
| **启动/停止竞态（自查发现并修复）** | 自查 `_start_all()` 时发现：它逐个 `append` + `start()`，而 `stop()` 会并发清空那两个列表 → 可能留下**没人负责关闭的监听端口**。改为「先全建好 → 检查一次 `_started` → 再注册并监听」。写这个 guard 时又发现自己引入的第二个 bug：`start()` 原本是 `submit()` 之后才置位 `_started`，事件循环可能先跑过协程开头那句判断，把**正常启动**误判成被 stop 抢先 → 已调整顺序。两项都有测试；顺序那条用「同步跑完协程体」的假 `submit` 复现，**把顺序改回错的会真的 FAIL**（已实测） |
| **接收端两处硬化（自查发现并修复）** | ① `_read_body` 原先只看 `Content-Length`，**分块请求不带该头**→ `request.body()` 无上限读进内存；改为自行累加封顶（8 MiB → 413）。实测：把实现换回旧的，分块测试**真的 FAIL**（400 而不是 413，说明旧代码确实把 192 字节全读了）。② 非对象 JSON 体原先仍返回 `200 ok`（对端会以为投递成功而事件被丢弃），改为 400。另新增「同端口路径撞车」的配置校验 |
| **插件 state 持久化（自查发现并修复）** | ① `resolveStatePath` 对**相对的 `statePath`** 抛错（正确），却**默默接受相对的 `DSH_HOME`** —— 同一个"随 cwd 漂移、读到两份映射"的坑。已改为同样抛错 + 4 条断言。② 文件头声称临时文件 + rename 能防"断电留半截 JSON"，但**没有 fsync 时并不成立**：rename 是元数据操作，可能先于数据块落盘。已改为 write → **fsync** → rename，失败路径清掉临时文件且不动原文件（新增 3 条测试，含真正进入 try 之后才失败的分支） |
| **插件动作权限门没接线（自查发现并修复）** | `createActionRouter({ allowSend })` 的能力做好了也有测试，但 `index.js` 里写死 `allowSend: true`，**启动时恒定**——而它的 JSDoc 与错误文案都声称"对齐 MeaPet 的 `hub.allow_send`"。也就是说 mode B（`serverEnabled: true`）对"消费者能不能调动作"**没有开关**，MeaPet 那一端却有。已新增 `serverAllowSend`（默认 true）接线，并让 `/status` 的 `actions.sendAllowed` **从路由器自身读取**（`route.allowSend`），使"报告值"与"实际判据"不可能再漂移。接线测试用驱动真实插件的方式断言；**把 `allowSend` 写回硬编码 `true` 会真的 FAIL**（已实测） |
| **插件 mode B 超大请求体（自查发现并修复）** | 只测过 `MAX_BODY_BYTES` 这个常量，**从没验过真实响应**。实测发现两种情形不一致：带 `content-length` 的超大请求得到干净的 413，而**分块**（不带该头）的超大请求得到 `ECONNRESET`——因为 `#readBody` 超限时 `request.destroy()` 把连接掐了，上层那条 413 根本发不出去。改为「超限后继续读并丢弃（上限 2×cap，不进内存）」，让请求正常收尾再回 413；实测两种情形现在都是 413。反面也验过：换回 `destroy()` 新测试**真的 FAIL** |
| **审计"证据生成器"本身：它会盲删 `--home`（自查发现并修复）** | `verify-in-throwaway-harness.mjs` 是大部分「已在真实 dsh 里验证过」结论的来源，所以它自己有洞就等于证据链有洞。首审发现：两处删除都是直接 `rmSync(home, { recursive: true, force: true })`，**没有任何护栏**——`--home` 一旦手滑指到真实 `DSH_HOME`（或调用方参数接错），那就是把用户的 profiles / 会话 / 凭据**整个删掉**。已加三道闸：① 不许是真实 `DSH_HOME` / 用户家目录 / 仓库根 / 盘根；② **只删本脚本自己建的**实验场（目录里必须有建场时写入的标记文件 `.onebot-verify-home`）；③ 两处删除点都必须先过前两道。<br>**负向实测**：`--home` 指向（假的）真实 DSH_HOME → 报 `拒绝删除真实 DSH_HOME：…`，目录存活；指向没有标记的目录 → 报 `…里面没有本脚本的标记文件…`，其中的 `important.txt` 存活。<br>**正向实测**：第二次运行能识别标记（`清理旧的实验场 …`）→ 判据 1/2 均 PASS → 自删成功（目录消失）；默认 `--home` 路径同样自删。<br>**过程中的一次自纠**：第一遍"验证自删"时我**忘了传 `--home`**，脚本用的是默认实验场、自删的也是它，我却去查另一个目录 —— 差点报出一个**假发现**（"脚本删不掉自己的实验场"）。正确传参重跑后确认行为无误 |
| **插件工具链首审：安装器会「静默重置用户配置」（自查发现并修复）** | `dsh-onebot-relay/scripts/*` 这 7 个脚本此前**零测试覆盖**，而安装器是唯一会改用户 `~/.dsh` 的工具。首审发现两处：① 本包自带的 `cordis.patch.yml` 号称「完整 config」，却**落后 schema 4 个键**（`pathPrefix`、`serverAllowSend`、`allowUsers`、`allowGroups`）——而**首次安装就是照它写 profile 的**。已补齐，并新增一条**永久守卫测试**（模板键集合必须与 schema 完全一致；故意删掉 `pathPrefix` 会 FAIL 并点名是哪个键）。② 从旧 insert 形态迁移时，安装器**整份用模板覆盖 config**：用户改过的 `requireAt` / `streamMode` / `statePath`、以及新加的 `allowUsers` 会被**无声重置**。已改为「以现有 config 为底 + 模板只补缺失字段」，并把缩进改成按 delta 整体平移（原先 `trim()` 重排会把嵌套列表**压平**，今天模板全是标量行才没炸）。<br>**用假 profile 实测**（旧 insert + 自定义值 + 嵌套 `allowUsers` 列表）：迁移后 `requireAt: false`、`streamMode: preview`、`statePath`、以及那个两级列表**全部原样保留**，同时补上 23 个模板字段，并打印「保留 N / 补充 M」。<br>**顺带抓到我自己引入的回归**：第一版合并让"现有值"全赢，于是 `--meapet-token` 等命令行必填项被旧值盖住——实测写出 `meapetToken: 'STALE-TOKEN'`，而日志看起来完全成功。已改为这四个键**永远以命令行为准** |
| **插件 preview 模式的「尾巴」逻辑：只测过顺风路（自查补齐，结论：与文档一致）** | `deliverReply` 的尾巴切点（`commonPrefixLength`）此前只有一条顺风测试（最终文本 = 已发内容 + 尾巴）。而代码注释里明写了一条**有意的不对称**：「模型重排文本时公共前缀会变短，于是会补发从切点起的全部内容——用户可能看到一点重复，但**绝不会漏**」。这条分支没人验。补 3 条：重排（公共前缀 0）→ 整个最终文本重新发；部分重叠 → 只补重叠之后；最终文本是已发内容的**前缀** → 一条都不再发（不刷空消息）。<br>**牙齿**：把它往「不重复」方向"优化"（公共前缀短于已发长度就 `return`，即**静默丢文本**）后——**恰好那 2 条 FAIL**，而顺风路那条与"无需补发"那条仍通过。也就是说测试锁住了这个方向的不对称：将来谁想"优化掉重复"，会立刻红 |
| **插件会话标题：接线与时序此前无测试（自查补齐，结论：接线正确）** | `renderSessionTitle` 与 `session-title.js` 的四条降级路径都有单测，但**驱动真实插件的那套测试根本没挂 `sessionTitle` 服务**——于是每次投递都走 `service-missing` 静默跳过，"到底写没写标题""写得是不是时候"全无人验。把服务挂进假 host（并记录**调用那一刻**的状态）后补 3 条：私聊渲染成 `喵桥 · private/10002`、群聊渲染成 `喵桥 · group/<群号>`、自定义模板 + 未知占位符原样保留；关键断言是**时序**——写标题时会话必须已经 live（否则 dsh 的 rename 会因"不 live"抛错 → 被归类 `rename-failed` → 症状是"标题永远没写上"），且必须早于 `followup`。<br>**牙齿**：把 `writeSessionTitle` 挪到 `followup` **之后** → **恰好 1 条 FAIL**（就是那条时序断言），另两条仍过；挪到 attach **之前** → 3 条全 FAIL（那时还取不到会话，`rename` 根本没被调用） |
| **事件 HTTP POST 上报：真 socket 端到端（自查补齐，结论：链路正确）** | 这个特性此前**只有 `httpx.MockTransport` 单测**——那条路径不经过真实 HTTP 栈，也证明不了"真的发出了一个能收的 POST"。新增 `tests/test_onebot_integration.py::HttpPushIntegrationTest`：起一个**后台线程里的真 `http.server`** 当消费者，断言请求真的到达（路径 `/onebot/v11/http`、`Authorization: Bearer push-token-abc`、`content-type` 含 `application/json`）、**事件帧逐字未改写**（`post_type` / `raw_message` / `user_id`）、以及响应里的快速操作 `reply` **变成了真实的 `send_private_msg`**（`events_pushed=1`、`quick_operations=1`）；另一条测对端不在时 `push_failures=1` 且不抛异常。<br>**牙齿**：把 `_post_event` 里那句 `send_message` 注掉后，测试**真的 FAIL**，报错是"响应里的快速操作 reply 应当变成一次真实发送"。<br>顺带修正：§13.1 的用例数早已过期（还是 132，实际 173），一并核准 |
| **MeaPet 侧 `allow.*` 的行为测试（自查补齐，结论：行为正确）** | 这是向导上唯一对外宣称的「准入规则」，但原先只有 1 条"被拦"用例：**没有正向对照**（只测被拦的话，一个恒假的实现也能通过），`group_ids` / `private` / `groups` / `require_at=false` 全都没测。补齐 8 条：正反对照、群名单双向、两类开关、`require_at=false`，以及两条把 §6.1 顺序钉死的——`reply_mode=both` 下被拒的消息**仍然广播**给 hub；`reply_mode=hub` 下 `_allowed()` 一次都不执行、`messages_denied` 恒为 0。<br>**牙齿**：把 `_allowed()` 改成恒真后 5 条**真的 FAIL**，而"应当放行"的 3 条仍通过（正是它们该有的表现）。<br>**过程中的自我纠错（两处，都是我的错）**：① 第一版把判据压在**异步**的 `session.push()` 列表上，那是一条看运气的测试——protocol 单跑挂 1 条、和另外 3 个文件合跑挂 2 条；改成同步计数 `events_published` 后连跑 3 遍稳定。② 我插入新测试类时曾把它插在旧类**中间**，导致旧类末尾 12 个方法被"吸"进新类；已移正（现为 14 + 8，逐类计数核对无交叉） |
| **准入：MeaPet 的 `allow.*` 管不到 hub 这条路（自查发现并补上）** | 读 `service.py:362-369` 逐行确认：`_publish` 在 `_allowed` **之前**，而 `_allowed()` 那句在 `if self.reply_mode in ("pet","both")` 里面——所以 `reply_mode=hub`（用户的配置）时它**一次都不会执行**。后果：向导上"准入规则"一栏（"谁可以触发机器人"）看着像访问控制，但它**对 dsh agent 完全没有约束力**；当时插件侧只有 `requireAt`，私聊则**毫无准入**——任何能私聊这个 QQ 号的人都能驱动带工具的 agent。<br>处置：① 向导标题/提示改写为"只作用于桌宠自己回复"，并明说拦不住 hub 这条路；② `docs/onebot.md` 的 `allow.*` 行加同样的警示；③ 插件新增 **`allowUsers` / `allowGroups`**（空 = 不限制，与 MeaPet 同约定；只认数字，`10001 ` / `qq:10001` 都匹配），接进入站路径并在 `/status` 报告**归一化后**的名单；④ 补 5 条行为测试（默认不限制、名单外私聊被拦、形式宽容、名单外群 @ 被拦、`/status` 报告）。<br>**牙齿**：把 `isAllowedAddress()` 改成恒真后，两条拦截测试**真的 FAIL**，而"默认不限制"那条仍通过（正是它该有的表现） |
| **插件审批流的安全属性（自查补齐，结论：属性成立）** | 原先只测过 `/approve` 正确码、错误码、超时、以及 `approvalEnabled=false` 透传。补齐 4 条**安全属性**测试：① **跨会话隔离**——A 会话的验证码在 B 会话里用不了（并带控制组证明不是"审批坏了"）；② **一次性**——重放同一个码只会得到"没有匹配"；③ `/reject <码>` 结算为 `rejected`；④ 超时后迟到的回执不会二次结算。全部通过。<br>**牙齿**：把 `resolveApproval(bridge, …)` 改成「在全局第一座桥上查」（模拟"图省事改成全局查找"的重构）后，隔离测试**真的 FAIL**（已实测）。<br>同时把**残留风险**写进 README：验证码是发进会话的，所以同会话里能看到提示的人都能照抄批准——挡的是"随口一个好"，不是恶意参与者 |
| **插件长回复切分的不变量（自查补齐，结论：机制正确）** | `chunkText` 原先只有几个点状用例。补成**不变量测试**：82 条语料（emoji/代理对/CJK/各类断点字符/超长/纯换行/纯空格 + 60 条固定种子伪随机）× 10 种 limit = **820 组合**，断言「不丢字、不重复、不超限、不产空段、不劈代理对」——**全部通过**。故意把 `rest.slice(cut)` 写成 `cut + 1`（经典丢字 off-by-one）后测试**真的 FAIL**，报错带 `limit/len/head` 可直接复现。<br>同类的「按字节截断标题」也核对了：`titleByteLength` 的注释明写「仅用于日志提示，不参与截断决策」，截断由宿主 `titles.rename` 返回的归一化 `accepted.title` 负责——**有意分工**，不是漏做，故不改 |
| **插件背压闸门此前无行为测试（自查补齐）** | 在途单 / `attaching` / `maxQueuedPerConversation` 是插件里最关键的不变量（代码注释称之为 astrbot-relay 实测过的「病根」，失效会导致**同一会话并发跑两轮、回复交错**），但**一条行为测试都没有**——只断言过配置默认值。补齐 3 条：在途期间第二条被挡住且明确告知、闸门由 `turn/end` 释放（不等 `whenIdle` 兜底）、`maxQueuedPerConversation=1` 时背压丢弃。**把 `attaching` 判断去掉会真的 FAIL**（已实测）。<br>另外顺手把「`whenIdle` 会不会提前结算放闸」这个疑虑查实了：dsh 的实现（`@deepseek-ai/dsh-agent-loop/lib/index.js:870`）是 `do/while` 等 `activityDone` 停稳的循环，而插件在 `followup` **之后**才调用它，所以那条兜底不会提前放闸——**结论是没问题**，但这是查过源码得出的，不是猜的 |

### 13.3 **未验证**（重要，别当成已完成）

1. ~~**运行中的那个 harness 还没有带着插件重启过**~~ **已完成（2026-10-11 11:0x，实测）**：
   插件已装进**活跃 profile `desktop`**，重启后 `GET /onebot-relay/status` **无 token=401 / 带 token=200**，
   `client.connected=true`。判据与安装方式见 §14.5（活跃 profile 的判定：各 profile 里 `cordis.yml` 最新的那个；
   插件此前误装在 `web`，症状就是永远 404）。
2. ~~**同一条真实 QQ 消息走完全程**（QQ → MeaPet → dsh → QQ）未走通。~~
   **已走通（2026-10-11 11:40，实测）**，见 §13.2 那条「真实 QQ 消息走完全程」。桥的每一段此前都已单独验证
   （QQ ↔ MeaPet 用真 NapCat；MeaPet ↔ dsh 两个方向用真实 dsh；桌宠回复闭环用桩 LLM），
   最后缺的就是"同一轮里既有真实 QQ 上下行、又有真实模型产出"——现已观测到。
3. ~~**用户 profile 里现在是旧版安装器写的 `insert` 形态**~~ **已修**：两个 profile 都已是 id 覆盖形态
   （§14.5 记录了活跃 profile 的判定与 Electron 独占 profile 的安装方式）。
4. **SnowLuma 未实机验证**（本机没有 SnowLuma，也没有可用的第二个 QQ 协议端）。已做的是：
   按它的官方 API / 配置文档**逐项核对**字段与路径（对照表见 §9 与 `onebot.md`），
   并把核对中发现的一处分歧（应答信封不带 `echo`）修掉 + 加测试。但**没有**跑过一次真实
   SnowLuma 进程，所以「文档描述与实现一致」这一步仍是推断。
5. **「插件加载失败是否会拖垮同 profile 的其他插件」仍未实测**。首次部署建议先备份 profile
   （安装器已自动备份）。
6. 图片 / 语音**内容**不入模型；`http_post` 的「快速操作」响应未实现；
   插件没有 WebUI 客户端半边。

### 13.4 这套验证本身修出来的两个真 bug

都不是"看起来不对"，是**真实 dsh 直接拒绝**的硬错误：

1. **模板 patch 的顶层空数组文档 `[]`**：profile 模板的 `cordis.patch.yml` 正文是 `[]`，
   直接 append `- insert:` 会形成第二个文档，`dsh --dump-config` 报
   `YAMLException: end of the stream or a document separator is expected`。
2. **插件行的形态写错**：bundle patch 层**已经 insert 了插件行**，profile patch 是后应用的
   覆盖层，应当写「按 id 覆盖 config」而不是再 insert 一次。
   > ⚠️ **本条的措辞在 §13.2 里被实测纠正过**：早先这里写「否则同一 id 出现在组合树里两次」，
   > 但实测**没能复现两行**——组合器按 id 去重（`bundle insert + profile insert` 也只显示 1 行，
   > 且 `profile insert + profile override` 是 override 的 config 胜出）。真正立得住的理由是：
   > 安装器的 YAML 自检**要求**覆盖形态（insert 形态无法收敛），且只有覆盖形态能让重跑幂等。
   > 反面那条坑更值得记：**覆盖只改已有行**，`dsh.profile.bundles` 缺声明时它会静默失效（实测 0 行）。

两个都在 `install-into-profile.mjs` 里修掉了，并给安装器加了**自证**：写出后用 profile 自己的 `yaml`
解析，要求"恰好一次且是 id 覆盖形态"，不满足就回滚并报错。另外它会**自动清理**旧版留下的 insert 形态。

### 13.5 一条命令验完整条桥（推荐）

```powershell
cd <repo>
.\.venv\Scripts\python.exe -X utf8 -u scripts\onebot_bridge_e2e.py
```

它自己把两侧拉起来：本进程起 MeaPet 的 hub；另起一个**独立 `DSH_HOME`** 的真实 dsh 让插件连过来；
然后断言两件事并打印 `PASS`：

```
[e2e] PASS 插件已连上 hub（消费者数=1）
[e2e] PASS 上行方向：插件把回复以正确的 OneBot 帧发回 MeaPet
[e2e] PASS 下行方向：插件建出会话 conversations=1 pending=1
[e2e] ALL PASS：meapet ↔ dsh 双向都通（下行建会话 + 上行回发 OneBot 帧），且插件被真实 dsh 加载
```

要点：用 `/status` 这个**本地指令**做上行验证，它不经过模型，所以不需要 LLM 凭据也能把这一侧验穿；
下行验证会真的走 `agents.create` 建出会话（随后因为实验场没有模型凭据而停在 `pending`，属预期）。
不需要 QQ、不需要凭据、跑完自清理。

#### 更接近真实部署：`--via-pet`

```powershell
.\.venv\Scripts\python.exe -X utf8 -u scripts\onebot_bridge_e2e.py --via-pet
```

这一模式把 MeaPet 侧换成**真实的桌宠进程**（离屏启动 `pet.py`，用它自己的 `config.json`），
再用一个假 NapCat 客户端连到桌宠的**反向 WS 端点**（`onebot.reverse_ws.*`）注入一条私聊消息。
于是整条下行链走的是真实应用代码：桌宠 `_init_onebot` → 真实 uvicorn → 真实 hub →

```
[e2e] PASS 插件已连上 hub（client.connected=true）
[e2e] PASS 下行方向：插件建出会话 conversations=1 pending=1（经由真实桌宠进程）
[e2e] ALL PASS：MeaPet（真实桌宠进程）↔ 真实 dsh 双向验证通过
```

桌宠自己的日志里同时会出现 `[init] onebot 初始化完成`。

> ⚠️ **`--via-pet` 刻意只做下行断言。** 上行那条要推 `/status`，插件会回发 `send_private_msg`，
> 桌宠会把它转发给**真实 NapCat** —— 那就真的往一个 QQ 号发了消息。所以这一模式跳过上行，
> 并在输出里明确说明。

### 13.6 桌宠自己的回复闭环（``reply_mode=pet``，桩 LLM）

前面所有验证里，`reply_mode=hub` 只走「转发给下游」。桌面侧那条**回发挂钩**
（`_notify_channel_turn_finished` → `complete_turn` → `send_private_msg`）此前只在单元测试里
用替身验过。用桩 LLM 把它补上：

```powershell
cd <repo>
.\.venv\Scripts\python.exe -X utf8 -u scripts\onebot_pet_reply_e2e.py
```

它做的事：起一个最小的 OpenAI 兼容 SSE 桩（只回一句固定文本）→ 基于真实 `config.json` 生成一份
**临时配置**（`onebot.reply_mode=pet`、`llm.direct.api_base` 指向桩、`tts/watcher` 关掉）→
离屏构造 `MeaPet(临时配置)` → 假 NapCat 连上反向 WS 发一条私聊 → 断言回发。

```
[reply-e2e] OneBot 服务已起：reply_mode=pet modes=reverse_ws，反向 WS 监听 54456
[reply-e2e] 桩 LLM 收到 2 次请求：
[reply-e2e]   #1 {"path": "/v1/chat/completions", "stream": true, "messages": 2, "systemChars": 1243, "lastRole": "user"}
[reply-e2e]   #2 {"path": "/v1/chat/completions", "stream": true, "messages": 2, "systemChars": 429,  "lastRole": "user"}
[reply-e2e] PASS 桌宠把 LLM 的回复回发到了原会话：'梅尔收到啦，这是桩模型的回复喵'
[reply-e2e] ALL PASS：reply_mode=pet 的完整闭环成立
```

两个值得记下的细节：

- `#2` 的 system 提示明显更短（429 字 vs 1243 字），是**格式修复**那一轮 —— 首轮桩返回的是纯文本、
  不符合分段格式，于是触发了「单次隔离修复请求」，最终按回退路径接受。也就是说这条验证顺带把
  §6 里那条「格式修复 + 回退」路径也真跑了一遍。
- **安全性设计**：临时配置把反向 WS 的 `access_token` 换成一次性随机值，于是**只有脚本的假 NapCat
  能连上**，真实 NapCat 连不上；再加上桌宠按 `IncomingMessage.source` 选回发通道，回复只可能回到
  我们这条连接。全程不会往真实 QQ 发任何消息。临时配置写在 `.tmp/` 下并自动删除，
  **真实 `config.json` 自始至终没有被改动**。

### 13.7 手工两进程联调（排障时用）

自动化脚本出问题时，用两个终端手工复现同样的链路：

```powershell
# 终端 A：MeaPet 侧只起 hub，并让它在每次有消费者连上时推一条 /status
cd <repo>
.\.venv\Scripts\python.exe -X utf8 -u scripts\onebot_probe.py --upstream none --hub `
  --hub-port 14321 --hub-token <meapetToken> --self-id 123456789 --poke "/status" --seconds 200

# 终端 B：一次性真实 dsh，跑完判据后**用同一个实例**挂住一段时间供 A 联调
cd dsh-onebot-relay
node scripts\verify-in-throwaway-harness.mjs --self-id 123456789 `
  --meapet-token <meapetToken> --meapet-ws-url ws://127.0.0.1:14321/onebot/v11/hub `
  --cwd <agent 工作目录> --port 19415 --hold-ms 35000
```

终端 A 应当出现：

```
[probe] → 向 1 个 hub 消费者推送合成消息：'/status'
[probe] ← message private 10001 id=900001 src=probe
[probe] ← hub 调用 send_private_msg params={"user_id": 10001, "message": "会话：private:10001…"} → …
```

> ⚠️ `--hold-ms` **复用同一个 harness 实例**，不再重启——重启会换掉实例，外部正在推消息时
> 可能正好推给要死的那个，表现为「插件没收到」。这个坑在自动化脚本第一版上真实踩过。

---

### 13.8 工具链（`dsh-onebot-relay/scripts/`）审计小结

7 个脚本此前**零测试覆盖**，而它们既会改用户的 `~/.dsh`，又是大部分"已验证"结论的来源。
逐个人工审计 + 实测后的结论：

| 脚本 | 审出什么 | 现状 |
|------|---------|------|
| `install-into-profile.mjs` | ① 从旧 insert 形态迁移时**整份用模板覆盖 config** → 用户改过的值被静默重置；② 自带模板落后 schema **4 个键**；③ `trim()` 重排会把嵌套列表压平（今天没炸是运气） | ① 改为「以现有为底 + 补缺失」，CLI 必填项强制生效（我第一版留下的回归，实测抓到）；② 补齐 + **永久守卫测试**（模板键集合 == schema）；③ 改按 delta 平移缩进 |
| `verify-in-throwaway-harness.mjs` | **盲删 `--home`**：指到真实 `DSH_HOME` 就会删掉用户整个 harness home | 三道闸（禁真实 home/家目录/仓库根/盘根；只删带标记文件的实验场；两处删除都必须过）；负向+正向均实测 |
| `validate-installed-config.mjs` | 只被验过"能过"的那一半；**回显 URL 里的 `access_token`** | 双向实测（真实 profile PASS；四条坏配置各自 FAIL 且报错可操作）；加 URL 脱敏（去掉脱敏明文会真的出现） |
| `run-tests.mjs` | 「**一个用例都没跑**」的文件也算通过（假绿——真实发生过） | 解析输出里的 `pass`/`fail`，要求每文件 `pass ≥ 1`、`fail = 0`，末尾报**累计用例数**（当前 130） |
| `package-release.mjs` | 手写 ustar 头 + `files` 白名单 | 逐项核对：校验和/99 字节名限（留 NUL）/目录项都正确；产物 33 条，无 scratch、无 `dist`、无 state |
| `check-syntax.mjs` | — | 递归 + `inherit` stdio + 传播失败；未覆盖 YAML，但那条路由安装器自检与真实 `--dump-config` 兜住 |
| `self-test.mjs` | — | 分段 try/catch（抛错记 FAIL 而非崩）+ 汇总退出码 + 60s 看门狗 |

**外加一条**：文档让操作者跑的 `dsh plugin --profile <p> add <tgz>` 此前**从未被执行过**
（端到端一直手工 `tar` 解包）。本轮实测跑通（exit 0 → 链接就位 → 组合树 1 行 0 告警），
并记下两个副作用：走 pnpm（可能联网/用缓存）、在仓库上一级建 `.pnpm-store`（已 gitignore）。

---

## 14. 部署与回滚 Runbook

### 14.1 一次性配置（已完成）

- MeaPet `config.json` 的 `onebot` 段：`enabled`、`self_ids=[123456789]`、`reply_mode=hub`、
  `reverse_ws` 监听 14321 路径 `/ws`、`hub` 同端口。
- NapCat（账号 123456789「<NapCat 客户端名>」）的 `websocketClients[0].url` = `ws://localhost:14321/ws`。
- dsh profile：插件已装（`vendor/` + `node_modules/dsh-onebot-relay/` + `dsh.profile.bundles` + patch 行，
  均为 **id 覆盖**形态），两个 profile 都有各自的时间戳备份：
  - **`desktop`（活跃）** —— `.install-backup-dsh-onebot-relay-20261011-105857`；
    `node_modules` 是**手工解包**放置的（Electron 独占管理，`dsh plugin add` 会拒绝，见 §14.5）。
  - `web` —— `.install-backup-dsh-onebot-relay-20261011-090103`（浏览器会话用）。

### 14.2 每次启动

```powershell
# 1) 起桌宠（它会监听 14321 并提供 hub）
cd <repo>
.\启动桌宠.bat
```

### 14.3 让插件生效（**已完成**，2026-10-11）

> ✅ **现状：插件已加载、桌宠已连上 hub、真实 QQ 消息走完全程**（§13.2 那条）。
> 下面留下的步骤作为**重装/换机/排障**时的复核清单。

**先跑一次端到端验证**（不碰用户 profile，一条命令，几分钟，可随时重跑）：

```powershell
cd <repo>
.\.venv\Scripts\python.exe -X utf8 -u scripts\onebot_bridge_e2e.py
# 期望最后一行：ALL PASS：meapet ↔ dsh 双向都通…
```

它会自起 MeaPet hub + 一次性真实 dsh，验证插件加载、下行建会话、上行回发三个断言。
只想单独确认插件能被 dsh 加载时，也可以只跑 `dsh-onebot-relay` 里那个脚本（见 §13.6/13.7）。

然后重启真 harness：

```powershell
# 重启 harness 后：
cd <repo>
.\.venv\Scripts\python.exe scripts\onebot_postinstall_check.py
#   期望：无 token → 401（路由存在）；带 token → 200 + JSON
#   仍是 404 → 脚本会自动推断活跃 profile 并说明成因（装错 profile / 没重启），见 §14.5
#   200 但 connected=False → 插件已加载、桌宠没跑（脚本会直接提示先启动桌宠）
# 然后给机器人发私聊，或在群里 @ 它；首次会话后出现 %DSH_HOME%\onebot-relay\state.json
```

> ⚠️ 实测补充：**消息一直不进来时，先怀疑实现端**（NapCat 这次就是"API 正常、事件不推"，
> 重启它即恢复）——判据与做法见 §14.4 里那条「NapCat 收到了消息却不上报」。

### 14.4 排障脚本

| 目的 | 命令 |
|------|------|
| **验整条桥（推荐，一条命令）** | `python scripts\onebot_bridge_e2e.py`（自起 hub + 一次性真实 dsh，验加载/下行/上行） |
| 只验通道连通（不起桌宠） | `python scripts\onebot_probe.py --upstream reverse_ws --port 14321 --path /ws --token <t>` |
| 验出站 API | 上面的命令加 `--call get_login_info --call get_status` |
| 验 hub 鉴权/白名单 | 加 `--hub --hub-token <hubToken>` |
| 验桌宠整机接线 | `python scripts\onebot_pet_smoke.py --seconds 40`（只启动+报告，不注入事件） |
| **从桌宠日志定位"消息卡在哪一段"** | 看 **`logs\onebot.log`**（本通道专用日志文件；该文件只在**重启过桌宠**之后才存在）。`reply_mode=hub` 下桌宠只是转发，所以关键就是这几行（都只记元数据，不含正文）：<br>`[onebot] 已转发事件：hub 消费者 N 个… private:10001 msg=…` → 收到了且转发成功；<br>`[onebot] 收到事件但没有任何 hub 消费者，已丢弃：…` → **收到了但没人接**（插件没连上 / harness 没重启）；<br>完全没有这两行 → 消息**没到** MeaPet（NapCat 那一段的问题，看同文件里有没有 `反向 WS 已接入`）。<br>⚠️ 这个文件里**也会有测试输出**：本项目各子系统在模块 import 时就挂了文件 Handler，所以跑测试会往 `logs/<子系统>.log` 写（例如 `RuntimeError: kaboom` 是 `test_onebot_desktop_bridge.py` 里伪造的异常）。与当时操作无关的行按测试输出看待即可 |
| **NapCat 日志报 `连接意外关闭` / `连接错误 AggregateError [ECONNREFUSED]`** | 那是**桌宠没在跑或正在重启**——NapCat 每 **15 秒**自动重连，桌宠一旦监听就会在下一个周期接上。判据看桌宠侧 `logs\onebot.log` 有没有 `反向 WS 已接入：reverse_ws:upstream:…`，以及 14321 上有没有 ESTABLISHED。实测时间线：桌宠 11:09:42 停 → NapCat 11:09:57 / 11:10:12 报 ECONNREFUSED → 桌宠 11:10:12 开始监听 → **11:10:27 自动接上**（无需人工干预） |
| **NapCat 收到了消息却不上报（实测踩到：重启 NapCat 即恢复）** | 症状：QQ 群里能 @ 到机器人、NapCat 控制台明明打印了 `接收 <- 群聊 … @<机器人昵称>`，但桌宠 `logs\onebot.log` 里**一条 message 事件都没有**（只有心跳）。判据要点是**区分两侧**：<br>· 桌宠侧可证清白——`events.is_api_response()` 第一条就是 `if payload.get("post_type"): return False`（消息事件不可能被当响应吞掉），且新加的**解析前**日志会对任何 message 帧打一条 INFO：`收到 message 事件（解析前）：post_type=message message_type=group … message=[text]（1 段）`。**日志里没有它 ⇒ 就是上游没推。**<br>· NapCat 侧：`consoleLog` 开着（控制台能看到 `接收 <-`），但 `fileLog` 默认 **false** ⇒ `NapCat.Shell\logs\` 是空的。把核心配置 `config\napcat_123456789.json` 的 `fileLog` 改成 `true`（`fileLogLevel` 已是 `debug`）并**重启 NapCat**，它就开始写日志文件。<br>**实测结论：重启 NapCat 后立刻恢复正常**（11:40 起消息连续进来、会话建立、回复发出）。所以这类"API 正常但事件不推"的偏瘫，先重启实现端——比在桥这侧翻代码快得多。<br>（本次改动留了备份：`config\napcat_123456789.json.bak-<时间戳>`，只改了 `fileLog` 这一个布尔值。） |
| 重启后验插件 | `python scripts\onebot_postinstall_check.py`（现在还会回答"**到底跑通过一轮没有**"：`conversations=0` 时告诉你"还差一步：去发条消息"，`≥1` 时报 PASS；**两者退出码都是 0**——"还没人发消息"不是故障） |
| **端口被残留探针/桌宠占着** | **按 PID 杀，不要按进程名杀**。先查占用者：<br>`netstat -ano \| findstr ":14321"` ← **首选**：只读文本、不依赖 WMI；<br>`Get-NetTCPConnection -State Listen \| Where-Object { $_.LocalPort -eq 14321 }` ← 备选。<br>⚠️ 实测在受限沙箱里 `Get-NetTCPConnection` 会**漏报**（harness 明明在 19387 上监听并正常应答 HTTP，它却报"空闲"），所以**判断服务是否活着优先用 HTTP/连接探针，别只信这个 cmdlet**。<br>拿到 PID 后 `Stop-Process -Id <pid>`。⚠️ `Get-Process node,python \| Stop-Process` 这种按名字杀的写法会**误杀同机上的其它 Node/Python 程序**——NapCat 的宿主是 `QQ` 进程，而 AstrBot 是 Python。14321 上通常只是你自己的桌宠或探针，按 PID 精确处理即可 |
| 重启前验插件配置 | `node dsh-onebot-relay\scripts\validate-installed-config.mjs`（不传 `--profile` 则按需指定；**活跃 profile 见 §14.5 的第一步**） |

### 14.5 安装/刷新插件到**活跃** profile（已完成，留档）

> **第一步永远是确认活跃 profile。** 装错 profile 的症状是"配置全对却永远 404"——
> 本项目**实测踩过**（插件装在 `web`，而 harness 跑的是 `desktop`，见 §13.2）。
> 判据：harness 启动时会重写它正在跑的 profile 的 `cordis.yml`，所以
> **`cordis.yml` 最新的那个就是活跃 profile**。`onebot_postinstall_check.py`
> 现在会**自动推断**并打印出来（不用再传 `--profile`）。
> 手工看：
> ```powershell
> Get-ChildItem $env:USERPROFILE\.dsh\profiles\*\cordis.yml | Sort-Object LastWriteTime -Descending | Select-Object -First 3
> ```
> **这台机器上活跃的是 `desktop`**（`web` 只在浏览器会话时用）。两个 profile 现在都已装好。

```powershell
cd <repo>\dsh-onebot-relay
# 1) 写 profile 声明（dependencies + dsh.profile.bundles）+ patch 覆盖行
#    安装器会：自动备份、保留你现有配置、只补模板里缺的键、把旧 insert 形态清成 id 覆盖
node scripts\install-into-profile.mjs --profile desktop --self-id 123456789 `
  --meapet-token <MeaPet 的 onebot.hub.access_token> `
  --cwd <repo> `
  --meapet-ws-url ws://127.0.0.1:14321/onebot/v11/hub
# 期望看到：已备份 / 已复制产物 / config 合并：保留 N 个字段 / PASS patch 自检（恰好 1 行 id 覆盖）

# 2) 建 node_modules 链接
#    普通 profile：交给插件管理器（走 pnpm）
#      dsh plugin --profile <p> add .\vendor\dsh-onebot-relay-0.1.0.tgz
#    ⚠️ Electron 独占管理的 profile（desktop 就是）会被直接拒绝：
#      error: profile "desktop" is managed exclusively by the Electron application
#    这时手工解包等价（一次性实验场脚本用的就是这个手法）：
$p = "$env:USERPROFILE\.dsh\profiles\desktop"
New-Item -ItemType Directory -Force -Path "$p\node_modules\dsh-onebot-relay" | Out-Null
tar -xzf "$p\vendor\dsh-onebot-relay-0.1.0.tgz" -C "$p\node_modules\dsh-onebot-relay" --strip-components=1

# 3) 重启 harness（配置改动只在启动时生效），然后自检：
cd <repo>
.\.venv\Scripts\python.exe scripts\onebot_postinstall_check.py
#   期望：无 token → 401；带 token → 200；404 时脚本会直接说明成因（装错 profile / 没重启）
```

> ⚠️ **第 2 步同时会把插件代码一起更新**：profile 里的插件是**安装当时的快照**，
> 插件源码改动不会自动生效。下表是**当时**（§13 复核）的逐文件比对，留作方法示例；
> **现在两个 profile 都已是新快照**（`lib/state.js` 9268 含 `fsyncSync`、
> `lib/index.js` 含 `serverAllowSend`、`vendor/*.tgz` 123,805），可用同样两招自查：
> `state.js` 里有没有 `fsyncSync`、`index.js` 里有没有 `config.serverAllowSend`：
>
> | 文件 | profile 里（当时） | 源码 | profile 里有本轮的修复吗 |
> |------|-----------|------|--------------------------|
> | `lib/state.js` | 7823 | 9268 | ❌→✅（已刷新） |
> | `lib/index.js` | 58319 | 58348 | ❌→✅（已刷新） |
> | `lib/config.js` | 17180 | 17765 | ❌→✅（已刷新） |
> | `lib/rpc/actions.js` | 7595 | 8112 | ❌→✅（已刷新） |
> | `lib/onebot.js` | 31483 | 32713 | ❌→✅（已刷新） |
> | `vendor/*.tgz` | 102,679 | 123,805 | — |
>
> 对你当前的部署**没有功能影响**（`DSH_HOME` 本来就是绝对路径；`serverAllowSend` 的默认值就是
> 现状的"恒真"；fsync 只是抗断电强化），但要让这些修复真正生效，就得跑上面那条安装命令重装一次。
>
> 另外注意：tgz 的版本号一直是 `0.1.0`，**同名不同内容**，所以别用文件名判新旧。
> 最快的两个判据：`lib/state.js` 里有没有 `fsyncSync`、`lib/index.js` 里有没有 `config.serverAllowSend`。

**安装器的第二步（`dsh plugin … add`）本轮实测跑通了**，顺带弄清它的副作用：

```powershell
# 安装器最后会提示你跑这一条（就是在 profile 里建立 node_modules 链接）：
dsh plugin --profile <活跃 profile> add .\vendor\dsh-onebot-relay-0.1.0.tgz
# ⚠️ Electron 独占管理的 profile（desktop）会被拒：managed exclusively by the Electron application
#    → 手工解包等价，见 §14.5
```

- **它走 pnpm**：实测输出 `resolved 5, reused 0, downloaded 5` + `Done in 5.1s using pnpm v11.22.0`。
  所以这一步**可能用到网络或 pnpm 缓存**（我们这侧 5 个"下载"的是 profile 自己的依赖；
  插件本身是纯 ESM、零运行时依赖）。
- **它会建一个 pnpm 内容寻址存储**：实测落在**仓库的上一级**
  （`…\mea-pet-public-1.4.1\.pnpm-store\v11`，约 0.74 MB），不在 profile 里。删掉无妨，
  已加进 `.gitignore`。
- 跑完后 `--dump-config` 实测 **1 行、0 条 skipping** —— 也就是说这条文档路径
  从「安装器」到「链接」到「组合树」是完整通的（此前我只手工 `tar` 解包过，从没跑过这条命令）。

> 如果这一步因网络/权限失败：手工解包等价（`tar -xzf <tgz> -C <profile>\node_modules\dsh-onebot-relay --strip-components=1`），
> 一次性实验场脚本用的就是这个办法。

### 14.6 回滚

> ⚠️ **先把 `<profile>` 换成活跃 profile**（本机是 `desktop`；装错 profile 等于没回滚，见 §14.5 第一步）。
> 两个 profile 都装过的话，各自有一份 `.install-backup-dsh-onebot-relay-<时间戳>`：

```powershell
$p = "$env:USERPROFILE\.dsh\profiles\desktop"   # ← 活跃 profile
# 1) 还原 profile 两个文件（备份目录按时间挑最近的那份）
copy "$p\.install-backup-dsh-onebot-relay-*\package.json" "$p\"
copy "$p\.install-backup-dsh-onebot-relay-*\cordis.patch.yml" "$p\"
# 2) 删掉插件目录（可选，留着也不加载）
rd /s /q "$p\node_modules\dsh-onebot-relay"
# 3) 重启 harness
```

关掉 MeaPet 侧只需把 `config.json` 的 `onebot.enabled` 设为 `false`。

---

## 15. 已知限制

- **QQ 会话与本地输入共用同一套时间线 / 记忆 / 好感度**：桌宠只有一个人格，多群场景记忆会混。
  需要隔离就用 `reply_mode=hub`，让 dsh 侧按会话维护上下文。
- 图片 / 语音只以占位符进入模型，模型看不到内容本身。
- **空闲回收的扫描仍无行为测试**（`sweepIdleBridges` 的三个闸门：TTL 未到、有在途投递、
  有待审批）。它定义在 `apply` 闭包内、不导出，直接调用需要重构；而扫描周期下限是 15s，
  端到端等待也不划算。**已读过代码确认三个闸门都在**（`index.js` 的 in-flight /
  `approvals.size > 0` / `lastActiveAt` 判定），但"读过"不等于"测过"——如实记在这里。
- **向导的 OneBot 页偏长**：全部展开时 `sizeHint` 约 **820×3299**（56 个交互控件）。
  已做几何审计：无重叠、无横向越界、分组禁用生效，且它位于滚动 tab 内（满足
  `design-system/MASTER.md` 的「小窗口可滚动、不裁切主要操作」）。但按
  `design-system/pages/wizard.md` 「详细设置默认折叠」的精神，这一页也可以再加一个折叠开关；
  之所以没做，是因为五个通信方式的启用复选框与明细字段在**同一个 section 容器**里，
  折叠会连模式名和启用开关一起藏起来，需要先重构容器结构——留作后续改进。
- 「快速操作」只做了**我们作为上报方**的那一半（`hub.push_urls` → 读消费方响应里的 `reply`）；
  我们作为**接收方**时（`http_post` 模式，对端 POST 给我们）响应体里仍只返回空 `ok`。- 群聊默认必须 @；改 `allow.require_at=false` 会让机器人对群里每句话都有反应（配合串行队列会更明显）。
- 同一 QQ 账号不要同时接 AstrBot 与 MeaPet 两套桥（重复回复）。
- 插件 mode B 的 `serverPort` 默认 6721，**刻意**与 MeaPet 的 14321 分开：同机两个监听者不能同端口。

---

## 16. 文件索引

| 层 | 位置 |
|----|------|
| 协议实现（12 模块，~1940 行） | `meapet/onebot/`（`types/cq/segments/events/api/config/service` + `transport/{ws_session,forward_ws,server}`） |
| 桌面接线 | `meapet/desktop/onebot_bridge.py`（`PetOneBotMixin`） |
| 对话流挂钩 | `meapet/desktop/chat_flow.py`（`_notify_channel_turn_finished` 基类 no-op + 6 处调用） |
| 生命周期接线 | `meapet/desktop/app.py`（MRO + `_init_onebot`）、`config_bridge.py`、`window_chrome.py` |
| 状态 / 菜单文案 | `meapet/desktop/status_language.py`、`window_chrome.py` |
| 配置 | `meapet/onebot/config.py`（schema 真相）、`meapet/config/store.py`（委托 + 接线）、`config.example.json` |
| 配置向导 | `wizard/page_onebot.py`、`wizard/app.py`、`wizard/pages.py` |
| 测试（176 用例） | `tests/test_onebot_{protocol,integration,desktop_bridge,scripts}.py`、`tests/test_wizard_onebot.py` |
| 运维/验证脚本 | `scripts/onebot_probe.py`（连通性探针）、`onebot_bridge_e2e.py`（一条命令验整条桥，含 `--via-pet`）、`onebot_pet_reply_e2e.py`（桩 LLM 验桌宠回复闭环）、`onebot_pet_smoke.py`（整机接线冒烟）、`onebot_postinstall_check.py`（重启后自检） |
| dsh 插件 | `dsh-onebot-relay/`（`lib/` + `cordis.patch.yml` + `scripts/` + `test/`） |
| 文档 | `docs/onebot-architecture.md`（本文）、`docs/onebot.md`（协议与配置参考） |
