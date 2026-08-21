**以下是关于メア桌宠常见的问题解答喵**

TIP:Ctrl+F可以通过关键词快速查找您需要的问题喵\[如果有的话]

没有找到您的问题也不用担心喵，加Q群1083503687可以找猫娘解答喵～

最好可以把log打包过来，在\[你解压的地方]/MeaPet-vx.x.x/log，直接压缩即可喵。



**（注意：所有联网操作（拉取模型、调用API等）请确保网络通畅，若开启代理请检查是否放行了相关地址喵）**



**解压一定不要含有中文目录喵！**

**用识图时ollama要在后台挂着，要不然本地识图用不了**



撰写日期:2026/08/03
第一次修改:2026/8/21



正文:



**Q1:我的梅尔怎么不说话啊喵！**

A1:可能是API地址/APIKEY/模型名称错误喵～

右键梅尔 → 设置与数据 → 打开配置页→对话，查看API地址、APIKEY是否正确。各模型供应商都会有API文档，例如DeepSeek的API地址（兼容OpenAI格式）为 https://api.deepseek.com，模型名称通常为 deepseek-v4-flash（具体请以各厂商官方文档为准喵）。



**Q2:我的梅尔怎么看不到啊喵！**

A2:可能是本地/云端识图模型问题喵～

右键梅尔 → 设置与数据 →打开配置页→ 屏幕识图：

\*\*本地：\*\*检查是否成功安装了Ollama。请以管理员身份打开终端（WIN+X → 终端(管理员)），输入命令 ollama pull minicpm-v。如果执行后没有进度条，而是出现红色报错信息，可能是{Microsoft Visual C++ Redistributable for Visual Studio 2015-2022 (x64)}缺失\[链接:https://aka.ms/vc14/vc\_redist.x64.exe]，安装后可能需要重启电脑
如果还不行，请以管理员身份打开终端（WIN+X → 终端(管理员)），一个个输入一下命令
where.exe ollama
ollama --Version
ollama list
ollama pull minicpm-v
还不行就可以进群问了喵\[呜呜呜]

\*\*云端：\*\*检查APIKEY是否正确。

如果以上方法都不行，请检查是否开了代理喵～

**注意：不要被qwen3.5:4b误导了，qwen3.5:4b不能识图
用识图时ollama要在后台挂着，要不然本地识图用不了**



**Q3:我的梅尔怎么没有声音啊喵！**

A3:可能是语音设置的问题喵～

右键梅尔 → 设置与数据 → 打开配置页→语音，点击【显示引擎设置】。

v1.6.0 推荐使用 GPT-SoVITS-v2pro-20250604（下载地址：https://www.modelscope.cn/models/LeeAPeng/GPT-SoVITS-v2pro-20250604）

配置步骤：



1.选择语音引擎 → GPT-SoVITS；



2.点击“选择文件夹”，选中你下载好的 GPT-SoVITS-v2Pro-20250604 文件夹；



3.进入 runtime 文件夹，点击“Choose”，选择对应语言的音频文件（日语选 jp\_normal.wav，中文选 zh\_normal.wav）；



4.右下角点击“保存配置”；



5.右键梅尔 → 设置与数据 →打开配置页→ 语音 → 测试当前语音引擎。

如果正常了，尝试与梅尔说句话，有声音出现就成功了喵～



**Q4:我的环境监测说我的pip有问题喵！安装说失败！**

A4:如果程序能正常启动和运行，这个报错通常可以忽略喵～ 若后续遇到功能异常，可以带上log来群里找猫娘帮你细查哦



如果遇到错误代码，有实力的猫猫可以自查喵\~

| 状态码 | 类别 | 在模型 API 中的含义 | 常见触发场景 | 是否重试 |
| --- | --- | --- | --- | --- |
| 200 OK | 成功 | 请求成功，返回正常结果 | 调用正常，返回 completion / choices | — |
| 201 Created | 成功 | 资源创建成功 | 创建 Fine-tune 任务、创建向量库等 | — |
| 202 Accepted | 成功 | 请求已接受，异步处理中 | 提交异步批处理任务 | — |
| 204 No Content | 成功 | 请求成功但无返回体 | 删除模型、注销密钥 | — |
| 206 Partial Content | 成功 | 流式响应分段返回 | SSE 流式 chat/completions 的 chunk | — |
| 400 Bad Request | 客户端错误 | 请求格式错误或参数非法 | JSON 语法错、缺 model/messages、参数越界、prompt 超长、不支持的字段 | 否 |
| 401 Unauthorized | 客户端错误 | 认证失败 | API Key 缺失、无效、过期、Authorization 头格式错 | 否 |
| 402 Payment Required | 客户端错误 | 额度/余额不足 | 余额耗尽、到达月消费上限 | 否 |
| 403 Forbidden | 客户端错误 | 权限不足 | 当前套餐不支持该模型、账号受限、区域封禁 | 否 |
| 404 Not Found | 客户端错误 | 资源/接口不存在 | URL 路径拼错、模型名写错、接口已下线 | 否 |
| 405 Method Not Allowed | 客户端错误 | HTTP 方法不被允许 | 对只接受 POST 的端点发了 GET | 否 |
| 408 Request Timeout | 客户端错误 | 请求超时 | 客户端网络差、请求体未及时发送完 | 可重试 |
| 409 Conflict | 客户端错误 | 请求与服务器当前状态冲突 | 重复创建同名资源 | 视场景 |
| 415 Unsupported Media Type | 客户端错误 | 媒体类型不支持 | 未设置 Content-Type: application/json | 否 |
| 422 Unprocessable Entity | 客户端错误 | 语义校验失败 | 参数格式合法但业务上不成立（如邮箱格式正确但已被注册） | 否 |
| 429 Too Many Requests | 客户端错误 | 触发限流 | 超过 RPM/TPM、并发过高 | 是（指数退避，看 Retry-After） |
| 500 Internal Server Error | 服务端错误 | 服务器内部异常 | 服务端代码 bug、模型推理崩溃 | 是（最多 3 次） |
| 502 Bad Gateway | 服务端错误 | 上游网关错误 | 模型提供商 API 暂时不可用 | 是 |
| 503 Service Unavailable | 服务端错误 | 服务暂不可用 | 过载、维护中 | 是 |
| 504 Gateway Timeout | 服务端错误 | 上游响应超时 | 后端推理耗时过长、依赖服务卡死 | 是 |
| 524 | 服务端错误 | Cloudflare 上游超时 | 中转代理层超时 | 是 |