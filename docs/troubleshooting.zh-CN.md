# MeaPet 常见问题排查

本页适用于源码运行和 Windows PyInstaller onedir 发行包。先确认程序使用的
配置文件和 Python 环境，再按对应功能排查。完整配置说明见
[README](../README.md) 和 [后端与控制文档](backend-and-control.md)。

## 提交诊断信息前

- 运行日志位于源码根目录的 `logs/`，或 Windows 发行包的
  `MeaPet/_internal/logs/`。
- 只提供与故障时段相关、已经人工检查并脱敏的日志。显式启用 TRACK 调试时，
  日志可能含对话正文。
- 不要上传 `config.json`、API Key、Bearer Token、`mea_memory.db`、截图原图或
  未检查的完整日志目录。
- 同时说明运行方式、操作系统、Python 版本、所选后端和可复现步骤。

## 对话没有回复

1. 在“设置与数据 -> 打开配置页 -> 对话”确认 API 地址、模型名称和 Key。
2. 使用供应商当前文档列出的模型，不要照搬旧版本或第三方教程中的模型名。
3. 本地 Ollama 可先运行 `ollama list`，确认配置的模型已经下载且服务可访问。
4. 检查代理是否只作用于预期供应商，以及系统时间和 TLS 证书是否正常。

## 屏幕识图没有结果

1. 确认屏幕识图不是 `disabled`，并检查当前选择的是 `inherit` 还是 `relay`。
2. 本地 Ollama 模型必须出现在 `ollama list` 中；模型名应与配置完全一致。
3. 云端视觉需要有效凭据。每次截图还必须在本机确认，超时会自动取消。
4. 截图默认只在内存中传递；不要为了排障手动提交含私人内容的截图。

## 没有声音

1. 在“设置与数据 -> 打开配置页 -> 语音”确认 TTS 已启用并测试当前引擎。
2. GPT-SoVITS 需要独立的 Python 运行环境，以及已实际下载的 `.ckpt` / `.pth`
   模型文件；Git LFS pointer 不是可用模型。
3. 多语言参考音频应放在 `voice_cache/` 或使用绝对路径，并确保语言配置匹配。
4. TTS 失败时桌宠会保留文字回复。检查脱敏日志中的引擎状态和错误类型。

## 依赖安装失败

1. 确认安装命令使用的是启动 MeaPet 的同一个 Python：
   `python -m pip --version`。
2. 源码模式可重新运行启动脚本，或执行 `python -m pip install -r
   linux_requirements.txt`。
3. 项目面向简体中文用户，依赖安装默认使用清华 TUNA 公共镜像。可通过
   `MEAPET_PIP_INDEX_URL=https://pypi.org/simple` 切换到 PyPI 官方源，也可填写
   其他可信的自定义源。
4. 冻结版程序不能用自身的 `MeaPet.exe -m pip` 安装依赖。缺失的可选依赖或
   模型需要在构建环境准备后重新打包。

仍无法定位时，请在 GitHub Issues 中提供最小复现步骤和脱敏后的相关日志片段。
