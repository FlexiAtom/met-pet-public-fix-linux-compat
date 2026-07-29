# Agent Link v1 WebSocket 协议

本文定义第三方 Agent 与 MeaPet 桌面端之间的通用双向协议。当前版本为
`agent-link.v1`，信封版本号为 `1.0`。

Agent Link 的目标是让第三方 Agent 不只充当聊天后端，还能在自己的 Agent
Loop 中看到并调用 MeaPet 提供的本机能力。MeaPet 主动连接第三方地址，聊天、
流式回复、Agent 主动消息、前端工具调用、取消和保活都复用同一条 WebSocket。

仅仅“能收发 WebSocket JSON”并不等于支持 Agent Link。第三方必须同时实现本
文的字段、状态、关联、幂等和重连约定。MeaPet 不再按 Agent 品牌转换协议，也
不要求额外部署独立的 Agent Link Gateway；适配逻辑由第三方 Agent 自己的连接
器承担。

## 1. 角色和连接方向

| 角色 | 职责 |
|---|---|
| MeaPet | WebSocket 客户端；主动连接、发起聊天、公布并执行前端工具 |
| 第三方 Agent | WebSocket 服务端；完成握手，把工具加入自己的 Agent Loop，并生成回复 |

典型配置：

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
      "timeout_seconds": 120,
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

- `device_id` 标识当前 MeaPet 安装实例，空值会在首次构造连接时生成并保存。
- `session_id` 标识当前 Agent 会话，空值会生成；新建 Agent 会话时会更换。
- `auth_token` 支持 `$AGENT_LINK_TOKEN`、`$MEAPET_AGENT_TOKEN` 或对应的
  `${...}` 形式。
- 非回环地址默认必须使用 `wss://`。可信内网使用明文 `ws://` 时，用户必须
  显式设置 `allow_insecure_ws=true`。
- `extensions` 是可选的厂商扩展配置，不用于改名或替换核心字段。

## 2. 固定消息信封

每条 WebSocket 消息必须是 UTF-8 JSON 文本，顶层必须是对象：

```json
{
  "version": "1.0",
  "type": "chat.submit",
  "id": "turn-018f...",
  "session_id": "meapet-4e8d...",
  "reply_to": "",
  "payload": {},
  "extensions": {
    "vendor.trace": {
      "enabled": true
    }
  }
}
```

| 字段 | 必需 | 类型 | 约束 |
|---|---:|---|---|
| `version` | 是 | string | 数字 `major.minor`；当前发送 `1.0`，接收方按主版本兼容，v1 不接受 v2 |
| `type` | 是 | string | 小写点分类型，如 `tool.call`；至少包含一个点 |
| `id` | 是 | string | 本条消息 ID；不超过 256 字符，不含换行或 NUL |
| `session_id` | 是¹ | string | 握手后的所有业务消息使用当前会话 ID |
| `reply_to` | 否 | string | 响应所关联的请求 `id`；无关联时为空字符串 |
| `payload` | 是 | object | 当前消息类型的业务字段 |
| `extensions` | 是 | object | 无扩展时为 `{}`；编码后最多 64 KiB |

¹ `control.hello` 可以携带待恢复的会话 ID；`control.ready` 必须回显相同
`session_id`。其他业务消息必须使用该 ID。

扩展键必须带命名空间，例如 `acme.trace`、`org_example.routing`。`trace` 这类
无命名空间键会被拒绝。接收方必须忽略未知的可选扩展；若某消息在
`payload.required_extensions` 中声明了接收方不支持的必需扩展，接收方应拒绝
该消息。MeaPet v1 当前不接受任何第三方必需扩展。

未知的非必需顶层字段会被忽略，以便同一主版本向前兼容。核心字段不能通过
配置重命名；若第三方 Agent 内部使用另一套消息结构，应在第三方连接器内部做
一次映射。`extensions` 只承载附加数据，不能改变核心状态语义。

## 3. 连接状态

```text
DISCONNECTED
    │ WebSocket 建连成功
    ▼
HANDSHAKING
    │ MeaPet → control.hello
    │ Agent  → control.ready
    ▼
SYNCING
    │ MeaPet → tools.snapshot
    ▼
ONLINE
    ├─ chat.* 与 tool.* 可双向并发
    ├─ 网络断开 → DISCONNECTED → 退避重连
    └─ 认证/版本/协议错误 → FATAL，等待修改配置或重建适配器
```

MeaPet 在网络中断后按 0.25 秒起步、最多 5 秒的退避重连。重新握手成功后先
重新发送完整工具快照，再用原 `chat.submit.id` 重放仍未得到终态的聊天请求。
第三方必须按请求 ID 幂等处理，不能因此重复执行 Agent 内部工具。

## 4. 握手

### 4.1 `control.hello`

MeaPet 建连后首先发送：

```json
{
  "version": "1.0",
  "type": "control.hello",
  "id": "msg-hello",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "client": {
      "name": "MeaPet",
      "version": "1.0.0"
    },
    "device": {
      "id": "meapet-device-1"
    },
    "auth": {
      "scheme": "bearer",
      "token": "已配置的访问令牌"
    },
    "resume": {
      "session_id": "meapet-session-1"
    },
    "capabilities": {
      "chat": {
        "submit": true,
        "streaming": true,
        "cancel": true
      },
      "tools": {
        "dynamic": true,
        "call": true,
        "cancel": true,
        "list_changed": true
      },
      "assets": {
        "inline": true,
        "max_inline_bytes": 5242880,
        "media_types": [
          "image/jpeg",
          "image/png",
          "image/webp"
        ]
      }
    },
    "required_extensions": []
  },
  "extensions": {}
}
```

令牌位于第一条应用消息中，因此远程链路必须使用 WSS 才能避免明文泄露。

### 4.2 `control.ready`

第三方验证协议与令牌后必须返回：

```json
{
  "version": "1.0",
  "type": "control.ready",
  "id": "ready-1",
  "session_id": "meapet-session-1",
  "reply_to": "msg-hello",
  "payload": {
    "version": "1.0",
    "authenticated": true,
    "agent_name": "Example Agent",
    "server_version": "2.3.0",
    "capabilities": {
      "chat": {
        "submit": true,
        "streaming": true,
        "cancel": true
      },
      "tools": {
        "dynamic": true,
        "call": true,
        "cancel": true
      }
    },
    "required_extensions": []
  },
  "extensions": {}
}
```

MeaPet 只会进入 `ONLINE`，如果：

1. `reply_to` 等于 `control.hello.id`；
2. `authenticated` 严格为 `true`；
3. 服务端选择的协议主版本为 1；
4. `session_id` 与客户端提交的会话 ID 相同；
5. `capabilities.chat.submit=true`；
6. `capabilities.tools.dynamic=true` 且 `tools.call=true`；
7. 服务端没有要求 MeaPet 不支持的必需扩展。

认证或版本不满足时，服务端也可以返回 `control.error`：

```json
{
  "version": "1.0",
  "type": "control.error",
  "id": "error-1",
  "session_id": "meapet-session-1",
  "reply_to": "msg-hello",
  "payload": {
    "category": "authentication",
    "code": "INVALID_TOKEN",
    "safe_message": "访问令牌无效。",
    "retryable": false
  },
  "extensions": {}
}
```

## 5. 工具发现

握手成功后，MeaPet 立即发送完整 `tools.snapshot`：

```json
{
  "version": "1.0",
  "type": "tools.snapshot",
  "id": "tools-4",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "revision": 4,
    "tools": [
      {
        "name": "meapet.get_state",
        "description": "读取不含路径、密钥、记忆和全文的前端能力与状态摘要。",
        "input_schema": {
          "type": "object",
          "properties": {}
        },
        "output_schema": {
          "type": "object"
        }
      },
      {
        "name": "meapet.capture_screen",
        "description": "请求一次本机确认后的截图；授权不复用，截图不落盘。",
        "input_schema": {
          "type": "object",
          "properties": {
            "scope": {
              "type": "string",
              "default": "full_screen"
            },
            "region": {
              "anyOf": [
                {"type": "object"},
                {"type": "null"}
              ],
              "default": null
            },
            "application": {
              "type": "string",
              "default": ""
            }
          }
        },
        "result_modalities": ["image"]
      }
    ]
  },
  "extensions": {}
}
```

`tools` 中每项的字段：

| 字段 | 必需 | 说明 |
|---|---:|---|
| `name` | 是 | Agent 调用时原样填入 `tool.call.payload.name` |
| `description` | 是 | 给模型看的用途、限制和副作用说明 |
| `input_schema` | 是 | JSON Schema；Agent 必须据此生成参数 |
| `output_schema` | 否 | 结构化结果 Schema |
| `result_modalities` | 否 | 结果可能包含的媒体类型，例如 `image` |

`revision` 单调增加。运行中增加或移除能力时，MeaPet 会再次发送完整快照；
第三方应以新快照原子替换旧清单，并同步更新 Agent Loop 中可调用的工具。
第三方也可以发送 `tools.refresh` 请求最新快照。

MeaPet 当前内置：

| 工具 | 作用 |
|---|---|
| `meapet.say` | 排队一到多个完整回复分段，用于 Agent 主动向用户说话 |
| `meapet.express` | 请求当前前端明确支持的 mood 或 motion |
| `meapet.get_state` | 读取经过裁剪的前端能力和桌宠状态 |
| `meapet.capture_screen` | 请求一次本机确认后的截图，结果仅在内存中返回 |

## 6. 工具调用

第三方在 Agent Loop 选择某个前端工具后发送 `tool.call`。该消息可以在没有
进行中聊天的情况下发送，因此 Agent 主动消息不需要第二条连接。

```json
{
  "version": "1.0",
  "type": "tool.call",
  "id": "call-unique-001",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "name": "meapet.express",
    "arguments": {
      "mood": "happy",
      "motion": ""
    }
  },
  "extensions": {}
}
```

`tool.call.id` 同时是本次操作的幂等键。第三方重试时必须复用原 ID，不能生成
新 ID。MeaPet 会先确认接收：

```json
{
  "version": "1.0",
  "type": "tool.accepted",
  "id": "accepted-...",
  "session_id": "meapet-session-1",
  "reply_to": "call-unique-001",
  "payload": {
    "status": "accepted",
    "duplicate": false
  },
  "extensions": {}
}
```

成功终态：

```json
{
  "version": "1.0",
  "type": "tool.result",
  "id": "result-...",
  "session_id": "meapet-session-1",
  "reply_to": "call-unique-001",
  "payload": {
    "status": "succeeded",
    "result": {
      "status": "queued",
      "duplicate": false
    }
  },
  "extensions": {}
}
```

失败终态：

```json
{
  "version": "1.0",
  "type": "tool.error",
  "id": "error-...",
  "session_id": "meapet-session-1",
  "reply_to": "call-unique-001",
  "payload": {
    "status": "failed",
    "code": "INVALID_ARGUMENTS",
    "safe_message": "工具参数不符合 MeaPet 公布的 Schema。",
    "retryable": false
  },
  "extensions": {}
}
```

MeaPet v1 可能返回的工具错误码：

| `code` | 含义 |
|---|---|
| `TOOL_UNAVAILABLE` | 当前快照中没有该工具 |
| `INVALID_ARGUMENTS` | 参数不符合公布的 Schema |
| `INVALID_RESULT` | 工具返回了无法按协议发送的结果 |
| `EXECUTION_FAILED` | 本机执行失败 |
| `CANCELLED` | 调用已取消 |

同一个 `tool.call.id` 仍在执行时，MeaPet 再次返回
`tool.accepted` 且 `duplicate=true`；已有终态时直接重发缓存的
`tool.result` / `tool.error`，不会重复执行本机操作。终态缓存有界，第三方仍
应自行持久化请求状态。

取消工具调用：

```json
{
  "version": "1.0",
  "type": "tool.cancel",
  "id": "cancel-1",
  "session_id": "meapet-session-1",
  "reply_to": "call-unique-001",
  "payload": {
    "request_id": "call-unique-001"
  },
  "extensions": {}
}
```

取消是尽力而为；已经完成的操作不能回滚。

### 6.1 离线策略

WebSocket 不在 `ONLINE` 状态时，第三方不得缓存或延迟执行新的前端操作。它应
立即在自己的 Agent Loop 中把该工具调用结束为：

```json
{
  "status": "failed",
  "code": "OFFLINE",
  "safe_message": "MeaPet 当前离线，操作未执行。",
  "retryable": true
}
```

因为连接已经断开，这个 `OFFLINE` 是第三方连接器返回给其 Agent Loop 的本地
结果，不是发往 MeaPet 的 WebSocket 帧。恢复连接后也不能补发离线期间的旧
操作；若 Agent 仍需要该操作，必须重新决策并创建新的调用。

## 7. 聊天请求和流式回复

### 7.1 `chat.submit`

MeaPet 发起一轮聊天：

```json
{
  "version": "1.0",
  "type": "chat.submit",
  "id": "turn-unique-001",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "content": "包含角色状态、输出格式约束和本轮用户消息的完整模型输入",
    "user_text": "用户原始输入",
    "history": [],
    "frontend_context": {},
    "attachments": [],
    "response_format": "meapet-segments-v1",
    "idempotent": true
  },
  "extensions": {}
}
```

| `payload` 字段 | 必需 | 说明 |
|---|---:|---|
| `content` | 是 | 第三方应提交给 Agent 的完整输入，包含 MeaPet 输出约束 |
| `user_text` | 是 | 用户原始文本，仅用于界面或审计；不能代替 `content` |
| `history` | 是 | MeaPet 本机保留的最近对话；第三方可按自己的会话策略使用 |
| `frontend_context` | 是 | 当前支持的 mood、motion、TTS、角色状态等摘要 |
| `attachments` | 是 | 当前为内联图片数组；无附件时为 `[]` |
| `response_format` | 是 | v1 固定为 `meapet-segments-v1` |
| `idempotent` | 是 | v1 固定为 `true`，要求服务端按 `chat.submit.id` 去重 |

图片附件：

```json
{
  "type": "image",
  "media_type": "image/png",
  "file_name": "capture.png",
  "data": "base64..."
}
```

第三方可以先返回可选的 `chat.accepted`，其 `reply_to` 必须等于
`chat.submit.id`。

### 7.2 `chat.delta`

```json
{
  "version": "1.0",
  "type": "chat.delta",
  "id": "delta-1",
  "session_id": "meapet-session-1",
  "reply_to": "turn-unique-001",
  "payload": {
    "seq": 1,
    "text": "<MEAPET_SEGMENT><DISPLAY>你好",
    "replace": false
  },
  "extensions": {}
}
```

- `reply_to` 必须关联原 `chat.submit.id`。
- `seq` 应为单调递增整数；重复或倒退的序号会被 MeaPet 丢弃。
- `replace=false` 表示追加增量。
- `replace=true` 表示 `text` 是截至当前的完整替换文本，适合只能提供快照式
  输出的后端。

安全的 Agent 内部工具状态可以使用 `chat.tool_status`：

```json
{
  "version": "1.0",
  "type": "chat.tool_status",
  "id": "status-1",
  "session_id": "meapet-session-1",
  "reply_to": "turn-unique-001",
  "payload": {
    "state": "started",
    "safe_text": "正在使用 Agent 能力处理请求。"
  },
  "extensions": {}
}
```

不得在 `safe_text` 中包含内部工具参数、密钥、推理过程或未经清洗的工具结果。

### 7.3 `chat.final`

所有增量完成后返回：

```json
{
  "version": "1.0",
  "type": "chat.final",
  "id": "final-1",
  "session_id": "meapet-session-1",
  "reply_to": "turn-unique-001",
  "payload": {},
  "extensions": {}
}
```

若此前没有发送 `chat.delta`，可以在 `payload.text` 中一次性返回完整结果。
最终文本必须符合：

```text
<MEAPET_SEGMENT>
<DISPLAY>给用户看的本段文字</DISPLAY>
<META>{"voice_text":"本段朗读文本","voice_language":"zh-CN","mood":"neutral","tts_style":""}</META>
</MEAPET_SEGMENT>
<MEAPET_DONE />
```

严重缺字段或完全没有可展示文本时，MeaPet 会把本轮结束为协议错误。

### 7.4 失败、取消和重连

Agent 执行失败时返回：

```json
{
  "version": "1.0",
  "type": "chat.error",
  "id": "chat-error-1",
  "session_id": "meapet-session-1",
  "reply_to": "turn-unique-001",
  "payload": {
    "category": "backend_unavailable",
    "code": "MODEL_UNAVAILABLE",
    "safe_message": "Agent 暂时无法生成回复。",
    "retryable": true
  },
  "extensions": {}
}
```

稳定 `category` 包括：

- `authentication`
- `permission`
- `rate_limit`
- `backend_unavailable`
- `connection`
- `timeout`
- `protocol`
- `cancelled`
- `internal_error`

MeaPet 取消时发送 `chat.cancel`，其 `reply_to` 和
`payload.request_id` 都指向原 `chat.submit.id`。第三方完成取消后可返回
`chat.cancelled`。

网络重连后，未得到终态的 `chat.submit` 会用原 ID 重发。第三方必须：

1. 将 `chat.submit.id` 当作幂等键；
2. 若原任务仍运行，继续把事件关联到同一 ID；
3. 若原任务已经完成，重放其最终结果；
4. 不得重新执行已经发生的 Agent 内部副作用。

## 8. Agent 主动消息

Agent 主动说话不需要另一种消息协议，也不需要第二条连接。第三方在任意
`ONLINE` 时刻调用当前快照中的 `meapet.say`：

```json
{
  "version": "1.0",
  "type": "tool.call",
  "id": "proactive-001",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {
    "name": "meapet.say",
    "arguments": {
      "segments": [
        {
          "display_text": "该休息一下啦。",
          "voice_text": "该休息一下啦。",
          "voice_language": "zh-CN",
          "mood": "happy",
          "tts_style": "轻声"
        }
      ]
    }
  },
  "extensions": {}
}
```

MeaPet 会将消息放入有界队列；用户正在等待正常聊天回复时不会被主动消息抢占。

## 9. 保活和刷新

应用层保活是可选的；底层 WebSocket 已启用 ping/pong。第三方若发送：

```json
{
  "version": "1.0",
  "type": "control.ping",
  "id": "ping-1",
  "session_id": "meapet-session-1",
  "reply_to": "",
  "payload": {},
  "extensions": {}
}
```

MeaPet 返回 `control.pong`，并以 `reply_to=ping-1` 关联。

第三方发送 `tools.refresh` 时，MeaPet 返回当前完整 `tools.snapshot`。未知的
可选消息会被忽略；若未知消息在 `payload.required=true`，MeaPet 返回
`control.error`，错误码为 `UNSUPPORTED_MESSAGE`。

## 10. MeaPet 能力如何转为 MCP 和 Agent Link Tool

MeaPet 不解析 Markdown 技能描述来猜测可执行代码。一个 skill 若要成为模型可
调用能力，必须有明确的异步 Python 入口、类型标注和说明，然后注册到统一的
`CapabilityRegistry`：

```python
async def open_browser(url: str) -> dict[str, object]:
    """在前端打开已校验的网址并返回稳定状态。"""
    ...

registry.add_tool(
    open_browser,
    name="meapet.open_browser",
    description=open_browser.__doc__ or "",
)
```

注册表使用函数签名生成并校验 JSON Schema，隐藏传输层的 `request_id`，然后：

- Companion MCP 启动时从同一注册表注册标准 MCP Tool；
- Agent Link 发送同一工具定义，并在注册表变化后发送新快照；
- 调用结果统一经过 JSON 序列化检查和稳定错误分类。

因此以后增加 MeaPet 能力时，不应分别手写一份 MCP Schema 和一份 WebSocket
Schema；应只在能力注册表注册一次。若是纯提示词 skill、没有可执行入口，则它
不能自动变成 Tool，需要先提供明确的执行函数。

`CapabilityRegistry` 不根据工具名称或参数猜测操作是否敏感。输入限制、权限
检查以及是否需要本机确认，都由具体能力的执行函数负责；Agent Link 和
Companion MCP 只复用该执行函数，不得绕过它已有的授权流程。

## 11. 第三方 Agent 的最低实现要求

第三方要被 MeaPet 视为完整 Agent Link 后端，必须满足：

- [ ] 提供可访问的 `ws://` 或 `wss://` 服务端地址；
- [ ] 验证 `control.hello` 中的 Bearer Token；
- [ ] 返回匹配的 `control.ready`，并声明聊天和动态工具调用能力；
- [ ] 把最新 `tools.snapshot` 原子注册进自己的 Agent Loop；
- [ ] 允许 Agent 在没有用户聊天的情况下主动发送 `tool.call`；
- [ ] 正确关联 `reply_to`，并等待 `tool.result` 或 `tool.error`；
- [ ] 按 `chat.submit.id` 和 `tool.call.id` 实现幂等；
- [ ] 正确处理聊天流、最终结果、错误和取消；
- [ ] 断线时立即向 Agent Loop 返回 `OFFLINE`，不排队旧操作；
- [ ] 重连后接受完整工具快照，并恢复或重放同一聊天请求的结果；
- [ ] 不把认证令牌、推理、内部工具参数或截图内容写入普通日志；
- [ ] 远程部署使用 WSS，并限制来源、载荷、频率和并发。

OpenClaw、Hermes 或其他 Agent 的原生协议不会因为“也是 WebSocket”就自动识别
这些字段。它们若要完整接入，必须在自己的插件、扩展或连接器中实现上述契约，
并把 `tools.snapshot` 转成各自原生的动态 Tool 注册。这个适配属于第三方 Agent
侧；MeaPet 只实现并维护一套稳定的 Agent Link 协议。
