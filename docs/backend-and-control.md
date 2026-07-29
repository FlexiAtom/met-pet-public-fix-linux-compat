# 回复后端、呈现协议、Agent Link 与 Companion MCP

本文描述 MeaPet 当前实现的后端边界和配置契约。面向普通用户的入口见项目根目录 `README.md`；字段默认值以 `config.example.json` 与 `meapet/config/store.py` 为准。

## 1. 总体边界

一次只运行一个回复后端：

```text
用户输入
  ├─ direct → HTTP/NDJSON/SSE → 模型服务商
  │              （MeaPet 角色提示词 / SQLite 记忆）
  └─ agent  → 持久 WebSocket → Agent 自己的人设 / 记忆 / 模型 / 工具
                    ↓
             统一分段输出协议
                    ↓
      MeaPet 气泡 / 表情 / TTS / 时间线
```

两种模式共享同一个前端呈现层，但不共享对话作用域：

- `direct` 的长期记忆和对话历史由 MeaPet 管理。
- `agent` 的长期记忆由 Agent 管理；MeaPet 只保留可配置数量的最近时间线，以及为了恢复当前会话所需的标识。
- 后端或 Agent 会话变化时，MeaPet 会提升会话 generation。旧 generation 的网络增量、TTS 完成通知和截图结果会被丢弃。
- 保存配置会取消当前生成并热重建唯一后端。连接失败只报错，不自动切换到另一后端。

## 2. 直连模型服务商

### 2.1 配置

```json
{
  "llm": {
    "mode": "direct",
    "backend": "custom",
    "direct": {
      "provider": "custom",
      "protocol": "openai_responses",
      "api_base": "https://model.example.com/v1",
      "host": "",
      "model": "example-model",
      "api_key": "$MEAPET_API_KEY",
      "temperature": 0.7,
      "max_tokens": 512
    }
  }
}
```

`backend` / `direct.provider` 只是兼容字段，**不是**传输后端。直连模式下二者恒为 `custom`。实际请求由 `llm.direct.protocol` + `api_base`/`host`/`model`/`api_key` 决定。

程序按 API 地址自动识别协议与密钥环境变量（Ollama 本地地址 → `ollama_chat`；DeepSeek/MiMo/其它 → 默认 `openai_chat`；Anthropic 域名 → `anthropic_messages`）。MiMo TTS/识图联动也按地址识别，不再依赖 provider 品牌标签。

| `protocol` | Base URL 示例 | 实际路径 | 流格式 |
|------------|---------------|----------|--------|
| `ollama_chat` | `http://127.0.0.1:11434` | `/api/chat` | NDJSON |
| `openai_chat` | `https://api.example.com/v1` | `/v1/chat/completions` | SSE |
| `openai_responses` | `https://api.example.com/v1` | `/v1/responses` | SSE |
| `anthropic_messages` | `https://api.example.com/v1` | `/v1/messages` | SSE |

Base URL 已以 `/v1` 结尾时不会重复拼接。URL 不允许内嵌用户名、密码、query 或 fragment。

### 2.2 统一请求与流事件

供应商请求先转换成 `CanonicalChatRequest`，响应再转换成：

- `TextDelta`：允许进入分段解析器和气泡。
- `ReasoningDelta`：只供诊断，禁止进入气泡、TTS 和时间线正文。
- `UsageEvent`：用量元数据。
- `StreamDone`：正常流结束。

HTTP 认证、权限、限流、服务不可用和协议错误会转换成中性、安全的用户文案；原始响应正文默认不输出。
直连请求若在任何流事件到达前发生连接、超时、限流或 5xx 错误，会以 0.4 / 0.8 秒退避，总计最多请求 3 次；一旦已有事件交给上层便不再自动重试，避免把部分回复重复显示。

## 3. Agent 后端

### 3.1 通用配置

```json
{
  "llm": {
    "mode": "agent",
    "agent": {
      "kind": "hermes",
      "base_url": "ws://127.0.0.1:9119/api/ws",
      "auth_token": "$HERMES_DASHBOARD_SESSION_TOKEN",
      "device_id": "",
      "session_id": "",
      "session_key": "",
      "remote_session_id": "",
      "history_turns": 5,
      "timeout_seconds": 120,
      "allow_insecure_ws": false,
      "identity_path": "",
      "extensions": {},
      "tls": {
        "verify": true,
        "ca_file": ""
      }
    }
  },
  "ui": {
    "timeline_turns": 5
  }
}
```

Agent 段只接受 `ws://` 或 `wss://`。OpenAI、Ollama、Anthropic 等 HTTP 模型地址必须放在 `llm.direct`；Agent 握手失败时也不会自动回落到 direct。

配置加载时会迁移旧结构：本机 Hermes `http://127.0.0.1:8642` 改为原生 `ws://127.0.0.1:9119/api/ws`；旧 OpenClaw bridge 改为 Gateway WS；曾放在 `llm.agent` 的普通 OpenAI-compatible HTTP 模型地址则迁入 `llm.direct`，不会被拼成虚假的 WebSocket 路径。

- `session_id`：MeaPet 本机时间线的当前会话作用域；空值首次构造时自动生成并保存。
- `device_id`：自定义 Agent Link 使用的稳定桌面实例 ID；空值首次构造时自动生成并保存。
- `session_key`：OpenClaw 的 Gateway 会话路由键；空值首次构造 OpenClaw 时自动生成。
- `remote_session_id`：Hermes 返回的持久 `stored_session_id`。它由适配器写回，用户通常不需要手工填写。
- `history_turns`：创建新的 Hermes 会话时用于播种的最近本地对话轮数，范围 0–100。恢复已有 Hermes/OpenClaw 会话时由 Agent 自己维护上下文。
- `ui.timeline_turns`：本机时间线缓存，范围 0–100，默认 5；按后端和 Agent 会话隔离。
- `allow_insecure_ws`：只允许用户显式放行非回环明文 WS；远程连接默认要求 WSS。
- `extensions`：Agent Link 的命名空间自定义数据；不能改名或替换核心协议字段。

三个适配器都会跨轮次复用 WebSocket，并启用 ping/pong、单写者队列、100 MiB 收帧安全上限、请求 ID 关联和连接代际隔离。OpenClaw 还会执行握手返回的 `policy.maxPayload`，在本机拒绝超过 Gateway 上限的发送帧。取消当前生成分别调用 Hermes `session.interrupt`、OpenClaw `chat.abort` 或 Agent Link `chat.cancel`。

### 3.2 Hermes

Hermes 适配器连接 `hermes serve` 提供的 TUI Gateway，而不是 8642 端口的 HTTP API Server。建议先固定本机 WebSocket token：

```powershell
$env:HERMES_DASHBOARD_SESSION_TOKEN = "请生成一个足够长的随机值"
hermes serve --host 127.0.0.1 --port 9119
```

Linux/macOS 可使用：

```bash
HERMES_DASHBOARD_SESSION_TOKEN='请生成一个足够长的随机值' \
  hermes serve --host 127.0.0.1 --port 9119
```

MeaPet 将 token 仅在建连时编码到 `/api/ws?token=...`，配置中的 `base_url` 本身不得包含 query。这个静态 token 是 Hermes 回环地址的认证方式；当前 Hermes 的公网 bind 使用登录后签发的短时 WS ticket，MeaPet 不模拟浏览器登录。因此跨机器部署应把远端 `127.0.0.1:9119` 通过 SSH 隧道映射到本机回环地址，而不是直接暴露 Hermes 端口。线路使用原生 JSON-RPC 2.0：

- 收到 `gateway.ready` 后才把连接标记为可用。
- 新会话：`session.create`；保存其 `stored_session_id`。
- 恢复：`session.resume`；每次连接获得新的 runtime `session_id`。
- 发言：`prompt.submit`；流式接收 `message.delta` 和 `message.complete`。
- 截图：先通过 `image.attach_bytes` 上传有界的 base64 图片，再提交提示词。
- 取消：`session.interrupt`。
- 交互式授权：当前不会自动答复 `approval/clarify/sudo/secret` 请求；MeaPet 会中断该回合并返回安全提示，避免敏感操作被默认同意或界面无限等待。

Hermes 的 `prompt.submit` 没有可由外部客户端提供的幂等键。因此 MeaPet 在提交前先记录远端 assistant 历史基线；断线后不会直接重发提示词，而是先用持久 ID 执行 `session.resume`。若回合仍运行则继续等待事件；若已完成则用 `session.history` 取回基线之后的最终 assistant 消息；若服务端保留了终态错误则立即转成安全失败；只有无法证明结果时才返回可重试错误，避免把写文件、发消息等工具动作执行两次。

### 3.3 OpenClaw

```json
{
  "llm": {
    "mode": "agent",
    "agent": {
      "kind": "openclaw",
      "base_url": "ws://127.0.0.1:18789",
      "auth_token": "$OPENCLAW_GATEWAY_TOKEN",
      "session_id": "",
      "session_key": "",
      "allow_insecure_ws": false,
      "identity_path": "",
      "tls": {
        "verify": true,
        "ca_file": ""
      }
    }
  }
}
```

OpenClaw 使用 Gateway WebSocket v4，连接后执行 `connect.challenge` / `connect` 握手，并请求 `operator.read`、`operator.write`。客户端身份使用官方 `gateway-client` / `backend` 标识，Ed25519 设备身份材料保存在本机独立文件中；远程 Gateway 应使用 `wss://`。非回环 `ws://` 默认拒绝，只有显式打开 `allow_insecure_ws` 才允许在可信内网使用。

每轮通过 `chat.send` 发送 MeaPet 输出约束、当前请求和可选图片附件，`idempotencyKey` 固定为 MeaPet `turn_id`；流式接收 `chat` 的 `delta/final/error/aborted` 状态。连接中断时重新完成挑战握手并用相同幂等键重发，Gateway 可安全地返回正在进行或已经完成的同一回合；若终态事件恰好在断线期间丢失，则通过 `chat.history` 对账恢复。取消使用 `chat.abort`，并在已知时携带 `runId`。

OpenClaw 配对、认证、权限、限流、超时和服务不可用会映射为稳定错误类别。设备配对仍需在 OpenClaw 侧批准，MeaPet 不绕过其权限模型。

### 3.4 自定义 Agent Link

```json
{
  "llm": {
    "mode": "agent",
    "agent": {
      "kind": "agent_link",
      "base_url": "wss://192.0.2.10:8766/agent-link",
      "auth_token": "$AGENT_LINK_TOKEN",
      "device_id": "",
      "session_id": "",
      "history_turns": 5,
      "allow_insecure_ws": false,
      "extensions": {},
      "tls": {
        "verify": true,
        "ca_file": ""
      }
    }
  }
}
```

Agent Link 是 MeaPet 主动建立的一条通用双向 WebSocket。聊天、流式回复、取消、
Agent 主动消息和前端工具调用均走这条连接；选择它时不会再启动 Companion MCP
HTTP 监听。握手后 MeaPet 会公布当前工具 Schema，第三方必须把它们注册进自己
的 Agent Loop。Agent 主动说话就是调用 `meapet.say`，不需要另建消息通道。

仅支持任意 WebSocket JSON 收发并不足够；第三方必须实现固定信封、握手、工具
快照、请求关联、幂等、取消、离线丢弃和重连约定。完整字段与逐帧示例见
[`agent-link-v1.md`](agent-link-v1.md)。

## 4. 最终回复协议

直连模型和 Agent 都收到相同的最终输出约束：

```text
<MEAPET_SEGMENT>
<DISPLAY>给用户看的本段文字</DISPLAY>
<META>{"voice_text":"本段朗读文本","voice_language":"zh-CN","mood":"neutral","tts_style":"轻声"}</META>
</MEAPET_SEGMENT>
<MEAPET_DONE />
```

一个回复可以包含任意个 `MEAPET_SEGMENT`。每段五个字段均必须存在：

| 字段 | 约束 |
|------|------|
| `display_text` | 非空；只进入气泡和时间线正文 |
| `voice_text` | 非空；可与显示文本不同，但不得加入未显示的新事实 |
| `voice_language` | 非空 BCP-47 风格语言码，例如 `zh-CN`、`ja`、`en` |
| `mood` | 应从前端能力摘要中的支持列表选择；未知值在呈现前变为 `neutral` |
| `tts_style` | 必须存在，允许空字符串；由支持的 TTS 引擎解释 |

协议解析器允许任意网络分块，不会把半个 XML 标签泄露进流式气泡。若最终结果严重缺字段：

1. 先发出格式修复状态。
2. OpenClaw 可使用独立会话做一次有长度上限的纯格式转换。
3. Hermes 不允许为修复偷偷回落到 HTTP 模型接口；当前保留解析器能安全提取的结果。
4. 完全没有显示文本时终止本轮并报中性错误。

推理、内部工具参数和内部工具结果禁止进入最终协议。安全的工具状态采用 `started`、`succeeded`、`failed` 等状态事件进入时间线；Agent 原始工具细节只存在于受控日志。

## 5. 呈现状态机

### 5.1 TTS 关闭

```text
SegmentStarted → 创建气泡
TextDelta      → 持续增长当前气泡
SegmentDone    → 定稿并应用 mood
TurnDone       → 写入完整本轮时间线
```

多个分段可同时留在气泡栈中，不强制合并或限制为三段。气泡点击操作和「对话时间线…」都可以打开完整本轮窗口。

### 5.2 TTS 开启

```text
SegmentDone → 提交 TTS → 音频就绪
                            ├─ 定稿并显示该段气泡
                            └─ 同时开始播放
音频结束    → 处理下一段
```

在音频就绪前不显示最终回复气泡，避免“先说完文字、随后才响起语音”。持续时间至少为 `bubble_duration_ms.reply`，有有效 WAV 时至少覆盖音频时长并额外保留 500ms。

若 TTS 不可用、语言策略决定跳过、任务异常或未产生 WAV，该段立即按文字回退，后续分段继续处理。

## 6. TTS 语言路由

每段先按 `voice_language` 做纯路由决策：

1. 当前引擎直接支持该语言：直接合成。
2. 不支持且未开启翻译：跳过语音。
3. 不支持、已开启翻译，但机器翻译组件不可用：跳过语音。
4. 不支持、已开启翻译且组件可用：在固定非 LLM 服务池中最多尝试 3 次，翻译到配置的受支持语言，再合成。

“优先模型输出目标语朗读”开启时，还会将 `voice_language` 和 `voice_text` 与配置的目标朗读语言核对；确认不一致时使用同一机器翻译服务池。翻译不是对话、视觉或 TTS 请求失败后的通用兜底。

GPT-SoVITS 的固定参考音频按规范化语言查找：

```json
{
  "tts": {
    "reference_audios": {
      "ja": {"path": "D:/voices/mea-ja.wav", "text": "参考文本"},
      "zh": {"path": "D:/voices/mea-zh.wav", "text": "参考文本"}
    }
  }
}
```

每种语言最多使用一条固定参考音频。路径不会通过 Agent 前端状态或 MCP `get_state` 暴露。

## 7. 视觉路由与授权

### 7.1 模式

- `disabled`：关闭观察和识图。
- `inherit`：把用户确认的截图作为本轮附件直接传给主回复后端，一次完成理解和回复。自定义直连模型或 Agent 必须显式确认 `main_model_supports_images=true`。
- `relay`：独立 Ollama / MiMo 视觉模型先生成有界 `VisionObservation`，再由主回复模型基于该 JSON 回复。当前仅用于直连模式；Agent 模式应使用 Agent 自身视觉能力的 `inherit`，否则关闭识图。

中转观察只包含有长度上限的：摘要、应用名、活动类别、少量显著文本和敏感标记。主回复模型被明确告知观察可能不完整，不得假装看见未提及细节。

### 7.2 截图同意

无论截图来自定时观察还是 `meapet.capture_screen`：

- 每次都弹出本机确认，超时等同拒绝。
- 同意仅对当前请求有效，不提供“本次会话一直允许”。
- 请求可以建议全屏、区域或应用；用户在确认窗口中作最终选择。
- 默认全屏；区域必须有正尺寸，应用必须明确选择。
- 新截图链路只传内存数据，不写 `screenshots/`。
- 云端链路还要求 `watcher.allow_cloud=true`。

## 8. Companion MCP

本节是 Hermes / OpenClaw 等原生后端的兼容控制通道。自定义 Agent Link 已在
同一条 WebSocket 上自动交换相同的前端能力，不使用本节的第二个 HTTP 监听。

### 8.1 服务配置

```json
{
  "agent_control": {
    "enabled": true,
    "listen_host": "192.168.1.10",
    "port": 8765,
    "allowed_agent_ip": "192.168.1.20",
    "auth_token": "$MEAPET_CONTROL_TOKEN",
    "allow_insecure_http": true,
    "cert_file": "",
    "key_file": "",
    "ca_file": ""
  }
}
```

示例显式允许可信内网明文 HTTP；请求和 Token 会以明文经过局域网。更安全的远程配置应提供 `cert_file` + `key_file`，服务地址随之变为 HTTPS。只配置证书或只配置私钥会拒绝启动。进一步配置 `ca_file` 后会启用 mTLS，Agent 必须提供由该 CA 签发的客户端证书；它不只是服务端信任链提示。

`listen_host` 和 `allowed_agent_ip` 都必须是具体 IP 字面量，不能使用 `0.0.0.0`、网段或主机名。一个实例只允许一个 Agent 来源 IP。

### 8.2 鉴权与网络门槛

在进入 MCP SDK 前，中间件按顺序执行：

1. 来源 IP 必须等于 `allowed_agent_ip`。
2. Host 必须是当前监听地址和端口；回环监听同时接受 `localhost`。
3. 有 Origin 时必须与监听源匹配。
4. `Authorization: Bearer <token>` 必须通过常量时间比较。
5. 每个 IP 的请求频率不得超过限制。
6. 请求体不得超过大小限制。

Token 少于 32 个字符时服务拒绝启动。空 Token 会自动生成高熵随机值并写回本机配置；手工轮换会先停止旧监听，再以新 Token 重启。

### 8.3 工具输入输出

工具定义来自统一 `CapabilityRegistry`。新增 MeaPet 可执行能力时应在该注册表
注册一次，由 Companion MCP 和 Agent Link 共用函数签名、Schema 生成源、校验与
错误契约，不应分别维护两份协议定义。Agent Link 投影会隐藏仅供传输层注入的
`request_id`，避免第三方模型覆盖幂等键。

#### `meapet.say`

输入一个或多个包含五个必需字段的 `segments`。命令进入有界队列；用户回复正在生成时不抢占。`request_id` 用于短期幂等，相同 ID 不会重复说话。

#### `meapet.express`

输入 `mood` 和/或 `motion`。值必须出现在 `get_state` 返回的支持列表中；未知值返回 `unsupported`，不做含糊映射。

#### `meapet.get_state`

只返回：

- `frontend_capabilities`：renderer、支持的 mood/motion、TTS 开关与语言、流式和多分段能力。
- `companion_state`：好感度等级、角色状态、当前 mood、busy。

它不返回 API Key、Token、文件路径、截图、记忆内容或对话全文。

#### `meapet.capture_screen`

`scope` 可为 `full_screen`、`region`、`application`。工具会等待本机用户同意；超时返回稳定错误。重复的 `request_id` 共享当前待确认请求，避免反复弹窗。

## 9. 时间线、会话和清除行为

终态轮次持久化到 SQLite `conversation_turns`，每个会话只保留配置数量的最近轮次。时间线保存：

- 用户文本与最终分段正文。
- 安全的工具状态和系统错误。
- 后端类型、Agent 类型与会话标签。
- 完成、失败或取消终态。

不保存推理流、内部工具参数、截图 base64 或 TTS 音频正文。

「清除记忆」行为：

- 直连模式：清除 MeaPet 记忆、对话历史、持久时间线与内存时间线。
- Agent 模式：不声称能删除 Agent 内部长期记忆；确认后创建新的 `session_id` / `session_key`，旧时间线保留只读。

## 10. 日志和故障诊断

默认日志如实记录用户输入、模型可见回复和 TTS 翻译文本，并按日滚动保留 7 天；API Key、认证头、推理、内部工具参数/结果和截图内容禁止进入日志。`MEAPET_DEBUG=1` 仅增加协议级诊断。提交问题前可提供：

- `logs/` 中对应日期的脱敏日志。
- `meapet_boot.log` 启动摘要。
- `meapet_fault.log` 原生崩溃信息。
- 所选模式、协议和模型 ID。

不要提供 `config.json`、Bearer Token、API Key、截图原图、`mea_memory.db` 或未经检查的完整日志；日志可能包含私人对话正文。
