# AGENTS.md — MeaPet 桌宠

Windows-first 的 **PyQt5 透明桌宠**：Live2D / PNG 双渲染、多后端 AI 对话（Ollama / DeepSeek / MiMo）、本地或云端 TTS、SQLite 记忆与好感度。

## Entry points

| What | Command / file |
|------|----------------|
| 启动桌宠 | `python pet.py` 或 `python -m meapet` (→ `meapet.desktop.app:main`) |
| 配置向导 | `python setup_wizard.py`（或右键菜单「⚙ 再次配置」→ `wizard.app:main`） |
| 用户配置 | `config.json`（**gitignore**；唯一模板 `config.example.json`） |
| Linux | `QT_QPA_PLATFORM=xcb python pet.py` |
| niri WM | window-rule: match title="mea-pet", open-floating true (见 README) |
| Fcitx5 | `QT_PLUGIN_PATH=/usr/lib/qt/plugins` |

## Key commands

```bash
pytest -q
pytest tests/test_ui_refactor.py tests/test_live2d_startup.py -q
ruff check meapet wizard scripts tests
compileall -q meapet wizard
```

- Ruff: only `E9` / `F63` / `F7` / `F82`; excludes `GPT-Sovits`, `live2d`, `models`, `vits_core`, `vits_models`
- Python **3.10–3.12** (`.python-version` = 3.12); VITS needs `numpy<2`, `setuptools==69.5.1`

## Architecture — MRO

`meapet/desktop/app.py` — `MeaPet` 以 7 mixin + `QWidget` 组成 MRO：

`PetAudioMixin` → `PetWatcherMixin` → `PetChatFlowMixin` → `PetInteractionMixin` → `PetWindowChromeMixin` → `PetRenderHostMixin` → `PetConfigBridgeMixin`

## Architecture — key modules

| Path | Role |
|------|------|
| `meapet/desktop/app.py` | 主窗口 + 启动生命周期 (`_init_chat`, `_apply_motion_preference`, `_show_context_menu`) |
| `meapet/desktop/*` | 聊天流、输入框、气泡、渲染、托盘/菜单、观察控制、splash |
| `meapet/config/store.py` | 配置加载、规范化、环境变量解析 (`resolve_*`) |
| `meapet/chat/engine.py` | LLM 引擎 (async httpx) |
| `meapet/http_async.py` | 后台 asyncio loop 共用的 `httpx.AsyncClient` |
| `meapet/async_runtime.py` | 单例 asyncio 事件循环 + 守护线程 (`submit`, `run`, `get_loop`) |
| `meapet/desktop/workers.py` | Chat / TTS 任务投递与主线程轮询 (`QTimer` ~100ms) |
| `meapet/memory/db.py` | SQLite 记忆/好感 (`RLock`, `SCHEMA_VERSION=3`) |
| `meapet/watcher/screen.py` | 截屏识图 `QThread` 与隐私门闩 |
| `meapet/tts/service.py` | MiMo HTTP / GSV+VITS subprocess |
| `meapet/tts/engines/` | `gsv.py`, `mimo.py`, `vits.py` |
| `meapet/paths.py` | `PROJECT_ROOT` / `project_path()` |
| `meapet/ui_theme.py` | 语义色 (`PALETTE`)、霞鹜文楷、字号缩放、44px 触控下限 |
| `meapet/desktop/status_language.py` | 统一状态/菜单短文案 (functions, not strings-in-code) |
| `meapet/desktop/theme.py` | 桌面浮窗 QSS |
| `wizard/` | 配置中心 (Tab: env/llm/tts/vision) |
| `meapet/tools/` | `vits_infer.py`, `gsv_infer.py`, `precache_interactions.py`, `pre_render_voices.py` |
| `design-system/MASTER.md` | UI 设计源 (semantic colors, typography, component rules) |

## Threading — DO NOT CHANGE

- `ChatWorker` / `TTSWorker` → submit coroutines to singleton asyncio daemon thread (`async_runtime.py`). Net I/O uses async httpx; blocking work (local TTS subprocess) goes through `asyncio.to_thread`.
- `ScreenWatcher` is a `QThread`.
- Main thread polls workers via `QTimer` (~100ms). **Never** block GUI thread with network I/O or TTS.
- `ensure_utf8_stdout()` is called once at app boot; other modules must not re-initialize.

## Window flags & lifecycle

- Main window: `Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint`. Do **not** use `SubWindow` (invisible on Windows).
- `WA_QuitOnClose = False`: close hides, exit goes through tray menu.
- `app.setQuitOnLastWindowClosed(False)` + offscreen keepalive `QWidget` prevent early process exit.
- Import `socket` **before** any PyQt path (QtNetwork hook conflict). Enforced in `app.py` and `engine.py`.

## Config & secrets

- **Env var > config.json** (see `store.py:resolve_secret`). Supports `"$ENV_VAR"` and `${ENV_VAR}` placeholders.
- Key env vars: `DEEPSEEK_API_KEY`, `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY`, `MEAPET_API_KEY` (fallback), `TRANSLATE_API_KEY` (falls back to `DEEPSEEK_API_KEY`), `GSV_PYTHON`, `MEAPET_FORCE_PNG`, `MEAPET_DEBUG`, `MEAPET_ALLOW_DOWNLOAD`, `MEAPET_REDUCED_MOTION`.
- `config.json` = single source of truth. `config_settings.json` is **never** read by the app.

## TTS subprocess model

- GSV / VITS: `subprocess.run()` with timeout. Subprocess Python must have its own dependencies.
- `GSV_PYTHON` env var → GPT-SoVITS python.exe path.
- MiMo: cloud HTTP (no local deps). Voice cache → `voice_cache/`, temp audio → `audio_cache/`.
- `tts.sync_with_audio`: when true, reply bubble waits for TTS playback before showing. Falls back to immediate text on failure.

## Key behaviors (code verifiable)

- **Chat history**: max 16 msgs; keeps system + last 14 on overflow.
- **Memory extraction**: triggers immediately on "记住 / 记下 / 别忘了 / 提醒我"; otherwise every 3 turns.
- **Affection**: 0–100, start=5. Per turn: +1/2/3 by message length. Daily cap=15. Tiers in `AFFECTION_TIERS` (db.py).
- **Screen watcher**: random interval (min_ms/max_ms in config). Suppressed when idle or recently interacted. Off by default. Cloud vision requires `allow_cloud=true` + per-run confirmation.
- **Bubble duration**: `config.json` → `bubble_duration_ms` with keys `default/reply/watch/interaction/thinking`.
- **Bubble/TTS**: reply bubble waits for TTS audio if `sync_with_audio=true`; falls back to immediate text on failure.

## UI conventions

- Design tokens: `meapet/ui_theme.py` (colors via `PALETTE`, font scaling via `display.font_scale`).
- Bubble = character speech; input panel = operational surface. Never mix styles.
- Status text → `meapet/desktop/status_language.py` (functions, not raw strings).
- Menu: root = frequent actions, submenus = grouping. Dangerous actions (reset memory, quit) isolated.
- Screen observation: off by default; cloud vision requires confirmation each session (timeout → cancel).
- Motions: 150–300ms; reduced when `MEAPET_REDUCED_MOTION=1` or `display.reduced_motion=true`.
- Icons: system operations = text only; emoji = character/mood accent, never sole meaning.

## Testing

- 11 test files in `tests/`. `pyproject.toml` has `[tool.pytest.ini_options]`.
- After UI changes: run `tests/test_ui_refactor.py` (menu assertions) + `tests/test_live2d_startup.py`.
- Key test files: `test_mixin_contracts.py`, `test_memory_advanced.py`, `test_core_fixes.py`, `test_http_async.py`.
- Ruff is minimal (E9/F63/F7/F82 only); no type checker configured.

## Agent working notes

1. Before editing pet UI, read `design-system/MASTER.md` + `meapet/ui_theme.py`.
2. When changing menu text, update assertions in `tests/test_ui_refactor.py`.
3. Status prompts → edit `meapet/desktop/status_language.py` (not raw strings).
4. Don't commit `config.json`, `.env`, `mea_memory.db`, `screenshots/`, `logs/`.
5. Cloud vision path: guard with `watcher.allow_cloud` + per-run confirmation, timeout → cancel.
