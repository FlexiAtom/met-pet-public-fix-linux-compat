# Contributing

## Checks

Run these commands from the repository root before opening a change:

```bash
python -m pytest -q
python -m ruff check meapet wizard scripts tests
python -m compileall -q meapet wizard
```

The supported source interpreters are Python 3.10 through 3.13. The public
Tsinghua TUNA index is the centralized default for the project's primarily
Simplified Chinese audience, but every package index must remain overridable
through the environment. Keep model locations, proxy settings, and credentials
configurable; do not add developer-machine paths or mandatory proxy mirrors.

## Repository hygiene

`config.json`, credentials, databases, logs, screenshots, build output, ASR
model caches, and generated audio caches are local runtime data and must not be committed. The
pre-rendered interaction clips that ship with the application live under
`meapet/assets/interaction_voices/` and are distinct from the runtime
`voice_cache/` and `voice_asr/` directories. Older checkouts may have the ASR
cache under `models/voice_asr/`; that path is also ignored and is never bundled.

Before packaging, hydrate every Git LFS model referenced by `MeaPet.spec`.
Never package an LFS pointer file as if it were a model.

The wheel is a Python package artifact and does not include the full desktop
asset tree. Run the companion from a source checkout, or build the supported
Windows onedir distribution with `MeaPet.spec`; do not treat a wheel as a
standalone desktop release.

Project guidance belongs in this file or in `docs/`; tool-specific local files
such as `AGENTS.md` and `CLAUDE.md` are intentionally ignored.
