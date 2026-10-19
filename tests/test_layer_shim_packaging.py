"""打包面与加载面的对齐：`liblayer_shell_shim.so` 必须既在包里、又被找得到。

本文件能证明什么（L0，agents-rules §10）
----------------------------------------
* **spec 真的把桥接层收进 `Analysis.binaries`**：把 `MeaPet.spec` 当作**可执行输入**
  跑一遍（PyInstaller 的 `Analysis/PYZ/EXE/COLLECT` 换成记账假件），读它实际传给
  `Analysis` 的 `binaries`。这条断言针对的是本件要修的那个缺口：该 `.so` 由 ctypes
  直读、没有任何 Python `import` 指向它 ⇒ 依赖分析看不见它 ⇒ 不显式列就静默缺件，
  而缺件只在真机上暴露，离线一侧原本全绿。
* **Linux 打包时缺件是响亮失败**：把 spec 的 `SPECPATH` 指向一个没有产物的目录，
  要求它 `SystemExit` 并点名 `build_layer_shell.sh`（而不是打出一个没有穿透模式的包）。
* **加载面的候选表覆盖两种形态**：源码态第一条 = 仓库根；冻结态第一条 = 打包目录
  （`sys._MEIPASS`）。后者是关键那条——只按"本文件上三级"找，冻结版能否加载取决于
  PyInstaller 给 `__file__` 配的恰好是 `_MEIPASS`，那是它的实现细节不是本仓库的接口。
* **候选名与 spec 收的名是同一个**：断言 `binaries` 里的 basename == `SHIM_NAME`，
  两侧任一处改名都会在这里红。
* **PyOpenGL 的平台模块按包名**整族**收**：`collect_submodules("OpenGL.platform")` 的
  结果必须接进 `Analysis(hiddenimports)`。这条判的是"整族收"这个**形状**，不是某个
  写死的名字清单——构建机是不是 Wayland、装没装 PyOpenGL 都不该让它漂移（事故实况见
  该测试自己的 docstring）。
* **失败形态报出试过的每一条路径**：`open_shim()` 全灭时异常文本含所有候选。

本文件**不能**证明什么
----------------------
* 不证产物可用（符号集合、init 返回值、生命周期收支在 `tests/test_layer_bridge_abi.py`）。
* 不证 `pyinstaller MeaPet.spec` 真能构建成功（那是 L3 的构建机行为，本文件只跑 spec 的
  数据段、不跑 PyInstaller）。因此"spec 里 `binaries` 被 PyInstaller 正确搬到
  `_internal/`"这一步是**约定 + 官方文档**，不是这里量到的；量到的是"我们把它交给了
  `Analysis`，且落在 `.`"。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = REPO_ROOT / "MeaPet.spec"
_REAL_FIND_SPEC = importlib.util.find_spec


# ---------- 把 spec 当输入跑一遍 ----------

class _Stub:
    """PyInstaller 构建对象的替身：任意属性访问都返回自身，链式用法不炸。"""

    def __getattr__(self, _name):
        return self

    def __iter__(self):
        return iter(())


def _run_spec(
    spec_dir: Path,
    calls: list,
    *,
    fidus_present: bool = True,
    submodule_requests: list | None = None,
) -> dict:
    """执行 MeaPet.spec，返回它传给 Analysis(...) 的关键字参数。

    spec 顶层 `from PyInstaller.utils.hooks import collect_data_files`，而本仓库的
    测试环境不装 PyInstaller —— 连它一起塞假模块，只保 `collect_data_files` 可调用。
    `collect_submodules` 同理，并把**被请求的包名**记进 `submodule_requests`：spec 里
    那条 PyOpenGL 规则要判的是"整族按包名收"，不是某个写死的名字清单。
    `find_spec` 也一并接管：spec 里那条 fidus 在场性断言读的是**构建机**环境，而本
    文件要判的是规则本身，不该随测试机装没装 fidus 漂移。
    """
    src = SPEC_FILE.read_text(encoding="utf-8")
    requests = submodule_requests if submodule_requests is not None else []
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda pkg: []
    hooks.collect_submodules = lambda pkg: (requests.append(pkg), [f"{pkg}.__probe__"])[1]
    utils = types.ModuleType("PyInstaller.utils")
    utils.hooks = hooks
    pkgroot = types.ModuleType("PyInstaller")
    pkgroot.utils = utils

    def analysis(*args, **kwargs):
        calls.append(kwargs)
        return _Stub()

    fake = lambda *a, **k: _Stub()
    globals_for_spec = {
        "__file__": str(SPEC_FILE),
        "__name__": "meapet_spec",
        "SPECPATH": str(spec_dir),
        "Analysis": analysis,
        "PYZ": fake,
        "EXE": fake,
        "COLLECT": fake,
    }
    def fake_find_spec(name):
        if name == "fidus":
            return object() if fidus_present else None
        return _REAL_FIND_SPEC(name)

    with mock.patch.dict(
        sys.modules,
        {
            "PyInstaller": pkgroot,
            "PyInstaller.utils": utils,
            "PyInstaller.utils.hooks": hooks,
        },
    ), mock.patch.object(importlib.util, "find_spec", fake_find_spec):
        exec(compile(src, str(SPEC_FILE), "exec"), globals_for_spec)
    assert calls, "spec 没有调用 Analysis()"
    return calls[0]


def _bundle_sources(binaries) -> list[Path]:
    """`Analysis.binaries` 的 (源, 目标目录) → 源路径列表（两种 PyInstaller 形态都吃）。"""
    out = []
    for item in binaries:
        src = item[0] if isinstance(item, (tuple, list)) else item
        out.append(Path(src))
    return out


# ---------- spec 侧 ----------

def test_spec_bundles_layer_shim_when_present():
    from meapet.desktop import wayland_layer

    if not (REPO_ROOT / wayland_layer.SHIM_NAME).is_file():
        pytest.skip("仓库根没有产物：跑 build_layer_shell.sh 后这条才有判决力")
    calls: list = []
    kwargs = _run_spec(REPO_ROOT, calls)
    srcs = _bundle_sources(kwargs["binaries"])
    assert REPO_ROOT / wayland_layer.SHIM_NAME in srcs, f"spec 没收桥接层：{srcs}"


def test_spec_collects_every_pyopengl_platform_module():
    """PyOpenGL 的平台模块**整族**进 hiddenimports，不按构建平台猜一个。

    量到的事故（2026-10-19 本机第一份 Linux onedir，#128 六项读数的第 2 条当场红）：
    contrib 的 `hook-OpenGL.py` 在 Linux 分支只补 `OpenGL.platform.glx`，而
    Wayland 会话里 `OpenGL/platform/__init__.py:35-37` 把 guess 定成 "wayland"、
    选中的是 **egl** ⇒ 冻结版在 `platform/__init__.py:52` 执行 `plugin_class()`
    时那个类是 `None`（`PlatformPlugin.load()` 吞掉 ModuleNotFoundError 返回 None），
    报出来是看不懂的 `TypeError: 'NoneType' object is not callable`，Live2D 静默回退
    PNG。源码态同一条 import 是好的（`OpenGL.platform.PLATFORM` 实测为 EGLPlatform
    实例）⇒ **这条只有冻结产物会红**，和上面桥接层那格是同一类盲区。
    """
    from meapet.desktop import wayland_layer

    if not (REPO_ROOT / wayland_layer.SHIM_NAME).is_file():
        pytest.skip("仓库根没有桥接层产物：spec 会在收件前 SystemExit，这条无从判")
    requests: list = []
    calls: list = []
    kwargs = _run_spec(REPO_ROOT, calls, submodule_requests=requests)
    assert "OpenGL.platform" in requests, f"spec 没按包名收 PyOpenGL 平台模块：{requests}"
    assert "OpenGL.platform.__probe__" in kwargs["hiddenimports"], (
        "collect_submodules 的结果没接进 Analysis(hiddenimports)：收了等于没收"
    )


def test_spec_fails_loud_on_linux_when_shim_missing(tmp_path):
    """Linux 上缺产物 ⇒ 响亮报错，而不是静默打出一个没有穿透模式的包。"""
    calls: list = []
    with mock.patch.object(sys, "platform", "linux"):
        with pytest.raises(SystemExit) as exc:
            _run_spec(tmp_path, calls)
    assert "build_layer_shell.sh" in str(exc.value)


def test_spec_tolerates_missing_shim_off_linux(tmp_path):
    """非 Linux 打包面本就没有这个产物，不该拦构建。"""
    calls: list = []
    with mock.patch.object(sys, "platform", "win32"):
        kwargs = _run_spec(tmp_path, calls)
    assert _bundle_sources(kwargs["binaries"]) == []


def test_spec_fails_loud_on_linux_when_fidus_absent(tmp_path):
    """fidus 随包分发是 standing 裁决：Linux 上没装＝打出一个没有定位能力的包。

    拿仓库根当 SPECPATH 是错的：产物不入 VCS 也不在 CI 构建 ⇒ 仓库根没有 `.so` 时，spec
    先在桥接层那道闸出口，而它在 fidus 检查的上游，断言就落到别人家的文案上。本地有产物，
    所以本地看不见这件事。这里放一个同名空占位把上游那道闸打开——spec 对产物只问
    `is_file()`，不读内容——本条才真的判到 fidus 那一道。
    """
    from meapet.desktop import wayland_layer

    (tmp_path / wayland_layer.SHIM_NAME).write_bytes(b"")
    calls: list = []
    with mock.patch.object(sys, "platform", "linux"):
        with pytest.raises(SystemExit) as exc:
            _run_spec(tmp_path, calls, fidus_present=False)
    assert "fidus" in str(exc.value)


def test_spec_only_warns_off_linux_when_fidus_absent(tmp_path, capsys):
    """非 Linux 缺 fidus 不拦构建（那条路本来就不带它）⇒ 只提示，照常走到 Analysis。"""
    calls: list = []
    with mock.patch.object(sys, "platform", "win32"):
        _run_spec(REPO_ROOT, calls, fidus_present=False)
    err = capsys.readouterr().err
    assert "fidus" in err
    assert calls, "只是提示，spec 该照常走到 Analysis"


def test_spec_excludes_fidus_off_linux_even_when_present(tmp_path):
    """"Windows 打包不带 fidus"是**裁决**，不是"这台构建机恰好没装"的副产品。

    判法：在 win32 上把 fidus 造成本机**在场**，要求它仍然进不了包 —— 即 `fidus`
    出现在 `Analysis(excludes=...)` 里。将来撤销那个裁决 = 删 spec 里 `fidus_excludes`
    那一行，这条测试跟着红；这正是它要钉住的东西。
    """
    calls: list = []
    with mock.patch.object(sys, "platform", "win32"):
        kwargs = _run_spec(tmp_path, calls, fidus_present=True)
    assert "fidus" in kwargs["excludes"], f"非 Linux 竟会把 fidus 收进包：{kwargs['excludes']}"


def test_spec_does_not_exclude_fidus_on_linux():
    """Linux 那一档反过来：不能把裁决写反成 excludes（在场性检查与包内容必须同向）。"""
    from meapet.desktop import wayland_layer

    if not (REPO_ROOT / wayland_layer.SHIM_NAME).is_file():
        pytest.skip("仓库根没有产物：Linux 档走不到 Analysis（先跑 build_layer_shell.sh）")
    calls: list = []
    with mock.patch.object(sys, "platform", "linux"):
        kwargs = _run_spec(REPO_ROOT, calls, fidus_present=True)
    assert "fidus" not in kwargs["excludes"]
    assert REPO_ROOT / wayland_layer.SHIM_NAME in _bundle_sources(kwargs["binaries"])


# ---------- 加载面 ----------

def test_candidates_source_mode_leads_with_repo_root(monkeypatch):
    from meapet import paths as paths_mod
    from meapet.desktop import wayland_layer

    monkeypatch.setattr(paths_mod, "is_frozen", lambda: False)
    cands = wayland_layer.shim_candidates()
    assert cands[0] == str(REPO_ROOT / wayland_layer.SHIM_NAME)
    assert all(Path(c).name == wayland_layer.SHIM_NAME for c in cands)


def test_candidates_frozen_mode_leads_with_bundle_dir(monkeypatch, tmp_path):
    """冻结态第一条必须是打包目录，且不能把仓库根那条摆在前头。"""
    from meapet import paths as paths_mod
    from meapet.desktop import wayland_layer

    bundle = tmp_path / "_internal"
    bundle.mkdir()
    monkeypatch.setattr(paths_mod, "is_frozen", lambda: True)
    monkeypatch.setattr(paths_mod.sys, "_MEIPASS", str(bundle), raising=False)
    cands = wayland_layer.shim_candidates()
    assert cands[0] == str(bundle / wayland_layer.SHIM_NAME)
    assert len(cands) >= 2


def test_open_shim_tries_in_order_and_stops_at_first_hit(monkeypatch):
    from meapet.desktop import wayland_layer

    cands = [f"/nope/{i}" for i in range(3)]
    sentinel = object()
    seen = []

    def fake_cdll(path):
        seen.append(path)
        if path == cands[1]:
            return sentinel
        raise OSError(f"no {path}")

    monkeypatch.setattr(wayland_layer, "shim_candidates", lambda: cands)
    monkeypatch.setattr(wayland_layer.ctypes, "CDLL", fake_cdll)
    assert wayland_layer.open_shim() is sentinel
    assert seen == cands[:2]


def test_open_shim_error_lists_every_candidate(monkeypatch):
    from meapet.desktop import wayland_layer

    cands = ["/nope/a", "/nope/b"]
    monkeypatch.setattr(wayland_layer, "shim_candidates", lambda: cands)
    monkeypatch.setattr(
        wayland_layer.ctypes, "CDLL",
        lambda path: (_ for _ in ()).throw(OSError(f"cannot open {path}")),
    )
    with pytest.raises(RuntimeError) as exc:
        wayland_layer.open_shim()
    msg = str(exc.value)
    for cand in cands:
        assert cand in msg
    assert wayland_layer.SHIM_NAME in msg


def test_backend_load_uses_open_shim(monkeypatch):
    """门面那一发必须走同一张表：绕过它就等于又开一条只认仓库根的加载路径。"""
    from meapet.desktop import wayland_layer

    marker = _Stub()
    monkeypatch.setattr(wayland_layer, "open_shim", lambda: marker)
    backend = wayland_layer.WaylandLayerBackend()
    assert backend._load() is marker
