"""桌宠统一状态文案。

角色在前：短句、可朗读、避免技术堆砌。系统状态用中文说明，
必要时在括号内补充隐私或恢复提示。
"""

from __future__ import annotations


def thinking() -> str:
    """LLM 请求进行中（可持久气泡）。"""
    return "梅尔正在思考…"


def thinking_busy() -> str:
    """用户在仍等待上一条回复时再次发送。"""
    return "还在想上一条…稍等喵"


def chat_timeout() -> str:
    return "唔…好像没响应喵。再试一次？"


def chat_error_prefix() -> str:
    return "出错啦："


def model_service_error() -> str:
    return "模型服务请求失败，请检查连接与配置后重试。"


def watching() -> str:
    return "偷偷看一眼…"


def watching_denied() -> str:
    return "好，这次不看了喵"


def cloud_vision_disabled() -> str:
    return "云端识图未授权：请在配置页允许云端识图"


def vision_mode_unavailable(reason: str = "") -> str:
    if reason == "main_model_vision_not_confirmed":
        return "主回复模型未确认支持图片，请选择独立视觉中转或关闭"
    if reason == "agent_relay_forbidden":
        return "Agent 模式不使用额外视觉模型，请改为由 Agent 读图"
    if reason == "relay_backend_not_configured":
        return "独立视觉中转尚未配置后端"
    return "当前识图配置不可用"


def standby_on() -> str:
    return "梅尔待机中…点击穿透，右键打开菜单可取消"


def standby_off() -> str:
    return "醒啦喵"


def watcher_enabled_local() -> str:
    return "屏幕观察已开启（本地识图）"


def watcher_enabled_cloud() -> str:
    return "屏幕观察已开启（云端，上传前会确认）"


def watcher_disabled() -> str:
    return "屏幕观察已关闭"


def watcher_not_enabled() -> str:
    return "屏幕观察尚未开启"


def watcher_uploading_cloud() -> str:
    return "本次截图已确认，正在上传识别…"


def watcher_uploading_local() -> str:
    return "正在本地识别屏幕内容…"


def watcher_silent() -> str:
    return "这次没有需要提醒你的内容喵"


def voice_input_enabled() -> str:
    return "语音输入已开启"


def voice_input_disabled() -> str:
    return "语音输入已关闭"


def voice_input_unavailable() -> str:
    return "语音输入依赖未安装，请在配置页下载"


def vision_failed(_reason: object = None) -> str:
    return "这次没看清，请稍后再试喵"


def vision_backend_switched(backend: object) -> str:
    return f"识图后端已切换为 {str(backend or '').strip() or '默认'}"


def vision_model_switched(model: object) -> str:
    return f"识图模型已切换为 {str(model or '').strip() or '默认'}"


def tts_failed() -> str:
    return "语音合成失败，文字还在喵"


def empty_memories() -> str:
    return "还没有重要回忆。双击梅尔说说话，她会慢慢记住你喵"


def ready_hint() -> str:
    return "梅尔准备好啦～双击对话 · 右键打开菜单"


def wizard_progressive_hint() -> str:
    return "先完成「环境」和「对话」即可开玩；语音与屏幕识图可稍后设置"


def menu_watch_enable() -> str:
    return "开启屏幕观察（可能截屏）"


def menu_watch_disable() -> str:
    return "关闭屏幕观察"


def menu_standby_enter() -> str:
    return "待机（暂停识图）"


def menu_standby_leave() -> str:
    return "取消待机"


def menu_render_to_live2d() -> str:
    return "切换到 Live2D（当前 PNG）"


def menu_render_to_png() -> str:
    return "切回 PNG 立绘（当前 Live2D）"


def menu_window_size(factor: float) -> str:
    """窗口大小分组标题，带当前百分比。"""
    return f"窗口大小 · {round(float(factor) * 100)}%"


def window_size_applied(factor: float) -> str:
    return f"窗口大小已调到 {round(float(factor) * 100)}%"


def first_run_hint() -> str:
    """仅首次启动展示的一次性引导。"""
    return "双击说话 · 右键打开菜单 · 托盘可找回"


def reduced_motion_enabled_hint() -> str:
    return "已开启减少动画"


def autostart_unsupported() -> str:
    return "开机自启目前仅支持 Windows"


def autostart_disabled() -> str:
    return "开机自启已关闭"


def autostart_enabled() -> str:
    return "开机自启已开启"


def timeline_empty() -> str:
    return "还没有可查看的对话。"


def recent_reply_missing() -> str:
    return "这轮完整回复已不在最近缓存中。"


def agent_session_failed() -> str:
    return "新建 Agent 会话失败，请检查配置。"


def agent_session_started() -> str:
    return "已开始新的 Agent 会话。旧时间线仍可查看。"


def control_token_missing() -> str:
    return "当前没有可复制的 Agent 控制令牌。"


def control_token_copied() -> str:
    return "Agent 控制令牌已复制。"


def control_token_regeneration_failed() -> str:
    return "令牌重新生成失败。"


def control_token_regenerated() -> str:
    return "已重新生成 Agent 控制令牌，旧令牌已失效。"


def config_open_failed(_reason: object = None) -> str:
    return "打开配置页失败，请检查 Python 环境与项目路径。"


def config_corrupt() -> str:
    return "配置文件无法读取，请打开配置中心检查。"


def config_apply_failed() -> str:
    return "新配置未能启动，请检查配置。"


def config_applied() -> str:
    return "新配置已应用。"


def render_png_enabled() -> str:
    return "已切回 PNG 立绘喵"


def render_live2d_enabled() -> str:
    return "已切换到 Live2D 喵"


def render_live2d_failed() -> str:
    return "Live2D 加载失败，已切回 PNG 喵"


def tray_recover_standby() -> str:
    return "取消待机并显示"


def tray_standby_tooltip() -> str:
    return "梅尔待机中 · 点击托盘可恢复"


def tray_running_tooltip() -> str:
    return "梅尔桌宠 · MeaPet"


def menu_section_interaction() -> str:
    return "互动"


def menu_section_system() -> str:
    return "系统"


def menu_voice_input_enable() -> str:
    return "开启语音输入"


def menu_voice_input_disable() -> str:
    return "关闭语音输入"
