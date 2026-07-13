# AGENTS.md — MeaPet 桌宠

## Entry points
- `python pet.py` or `python -m meapet` → `meapet.desktop.app:main` (desktop pet)
- `python setup_wizard.py` → `wizard.app:main` (GUI config, first-run)
- Python `>=3.10,<3.13` (`.python-version` = 3.12). 3.12+ VITS needs `numpy<2` + `setuptools==69.5.1`.
- `config.example.json` is the only template (tracked); `config.json` is gitignored.

## Architecture

`meapet/desktop/app.py:93` — `MeaPet` class stacks 7 mixins + `QWidget` in MRO order:
`PetAudioMixin` → `PetWatcherMixin` → `PetChatFlowMixin` → `PetInteractionMixin` → `PetWindowChromeMixin` → `PetRenderHostMixin` → `PetConfigBridgeMixin`

| Path | Role |
|------|------|
| `meapet/desktop/` | App window, render host, mixins, workers, widgets, splash |
| `meapet/chat/engine.py` | LLM engine (Ollama / DeepSeek / MiMo), async httpx |
| `meapet/config/store.py` | Config loading + env-var resolution (`resolve_*_api_key`) |
| `meapet/memory/db.py` | SQLite memory + affection (`mea_memory.db`, `RLock`, `SCHEMA_VERSION=3`) |
| `meapet/tts/service.py` | TTS — MiMo (cloud HTTP) or local (GSV/VITS via `subprocess.run`) |
| `meapet/tts/engines/` | `gsv.py`, `mimo.py`, `vits.py` engine mixins |
| `meapet/watcher/screen.py` | Screen watch `QThread` + privacy gates |
| `meapet/desktop/workers.py` | `ChatWorker` / `TTSWorker` submit coroutines to asyncio daemon thread |
| `meapet/async_runtime.py` | Singleton asyncio event loop in a daemon thread |
| `meapet/http_async.py` | Shared `httpx.AsyncClient` on the async loop |
| `meapet/paths.py` | `PROJECT_ROOT` / `project_path()` helpers |
| `meapet/log.py` | `get_color_logger(name)` — console color + daily rolling file (7-day) |
| `meapet/ui_theme.py` | Font loading (bundled LXGW WenKai) + scaling |
| `wizard/` | Setup wizard pages (separate package) |
| `meapet/tools/` | `vits_infer.py`, `gsv_infer.py`, `precache_interactions.py`, `pre_render_voices.py` |
| `design-system/MASTER.md` | UI color tokens, typography, component rules, PyQt5 mapping |

## Critical gotchas

### Import order: `socket` before PyQt5
`app.py:10` and `engine.py:9` import `socket` before any PyQt5 import to avoid QtNetwork hook conflicts. Do not reorganize imports.

### Window flags & lifecycle
- `Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint` — not `SubWindow` (invisible on Windows)
- `WA_QuitOnClose = False` — close hides, does not quit. Real exit via tray menu.
- `app.setQuitOnLastWindowClosed(False)` + off-screen `keepalive` widget prevents early process reaping.

### Threading & async
- **No blocking calls in Qt main thread**. `ChatWorker` / `TTSWorker` submit coroutines to singleton asyncio daemon thread (`async_runtime.py`). Network I/O uses `httpx.AsyncClient`; blocking calls (local TTS subprocess) use `asyncio.to_thread`.
- Main thread polls workers every 100ms via `QTimer`.
- ScreenWatcher is a raw `QThread` subclass (not asyncio).

### Optional dependencies
`pyproject.toml` defines extras: `opengl`, `vits`, `webengine`, `win32`, `linux`. Only core deps (PyQt5, Pillow, requests, httpx) installed by default. `live2d-py` is optional; falls back to PNG rendering if missing. `MEAPETFORCE_PNG` env var forces PNG mode.

### Environment: `QT_MULTIMEDIA_PREFERRED_PLUGINS`
Defaults to `windowsmediafoundation` in `app.py:64`. QTWEBENGINE_DISABLE_SANDBOX=1 needed on Linux (set in `start.sh`).

## Config: environment variable > config.json
Supports `"$ENV_VAR"` and `${ENV_VAR}` placeholders in config.json. `resolve_secret()` in `store.py:123` handles resolution.

| Variable | Override for |
|----------|-------------|
| `DEEPSEEK_API_KEY` | `llm.api_key` (DeepSeek) |
| `MIMO_API_KEY` / `XIAOMIMIMO_API_KEY` | MiMo LLM / TTS / vision keys |
| `MEAPET_API_KEY` | Fallback catch-all |
| `TRANSLATE_API_KEY` | TTS Japanese translation (falls back to `DEEPSEEK_API_KEY`) |
| `GSV_PYTHON` | GPT-SoVITS conda `python.exe` path |
| `MEAPETFORCE_PNG` | Set to any value → force PNG rendering |
| `MEAPET_DEBUG` | `=1` enables payload-level debug logging |
| `MEAPET_ALLOW_DOWNLOAD` | `=1` allows `启动桌宠.bat` to auto-install uv |

## Key behaviors
- **Chat history**: max 8 messages (system prompt + system prompt + 6 pairs). Trimmed in `quick_chat` / `quick_chat_async` — look for `len(history) > 8` + `history[-6:]`.
- **Memory extraction**: triggers on Chinese keywords `记住`/`记下`/`别忘了`/`提醒我` or every 3rd message. Works with all backends.
- **Affection**: 0–100, starts at 5, daily cap of 15, +1 per chat. Tiers at 0/10/30/50/70/85/95.
- **Screen watching**: timer fires every 3–6 min randomly. Suppressed if user interacted <3 min ago or standby mode is on. Off by default.
- **Bubble durations**: `config.json` `bubble_duration_ms` (default/reply/watch/interaction/thinking).

## Commands
```bash
# Run
python pet.py                        # Windows
QT_QPA_PLATFORM=xcb python pet.py    # Linux (X11)
python -m meapet                     # module entry

# First-time setup
python setup_wizard.py               # GUI config wizard
# or: copy config.example.json → config.json, edit manually

# TTS test
python meapet/tools/vits_infer.py --text "测试" --output test.wav

# Package release (output: dist/mea-pet-*.zip + SHA-256)
python scripts/package_release.py
python scripts/package_release.py --dry-run
python scripts/package_release.py --include-optional-assets

# Linux deps
pip install -r linux_requirements.txt
# live2d-py from https://github.com/EasyLive2D/live2d-py (pre-built recommended)
```

## Tests & lint
```bash
python -m pytest                     # or: python -m unittest discover tests
python -m ruff check                 # select = E9, F63, F7, F82 only (see pyproject.toml)
```
- 12 test files in `tests/`, standard `unittest`. `pyproject.toml` has `[tool.pytest.ini_options]` with `addopts = "-ra"`.
- No CI, no formatter, no type checker, no pre-commit hooks.
- Coverage configured (`[tool.coverage.run]`) but not part of test command.
- Ruff excludes dirs: `GPT-Sovits`, `live2d`, `models`, `vits_core`, `vits_models`.

## Logs & diagnostics
- **Console**: colored logging via `meapet/log.py` `get_color_logger()` (VT escape sequences on Windows)
- **Files**: `logs/` dir, daily rolling (7-day retention)
- **Boot log**: `meapet_boot.log` (startup summary)
- **Fault log**: `meapet_fault.log` (native crashes via `faulthandler`, C++/OpenGL segfaults)
- **Chat errors**: `chat_errors.log` (LLM/TTS errors, auto-redacted)
- `MEAPET_DEBUG=1` enables full payload dumps to stderr

## Windows bat scripts
- `启动桌宠.bat` — auto-setup: create `.venv`, install deps (Tsinghua mirror → pypi.org fallback), run config wizard + pet
- `打包发布.bat` — calls `python scripts/package_release.py`
