"""日语词典必须同时写两个环境变量名：pyopenjtalk 只读裸名。

出处：Athena 件 openjtalk-dict-env-name-not-read-by-pyopenjtalk——产品写的是
``MEA_PET_OPEN_JTALK_DICT_DIR``，而整个 site-packages 里没有这个名字的读取方（grep 实测为空）；
上游读裸名 ``OPEN_JTALK_DICT_DIR``（``pyopenjtalk/__init__.py:38``，**导入时**求值一次）。
后果是随包的 ``dic/`` 从未被接上，「✓ 使用项目内置日语词典」那句是产品自证的，
而第一次真日语调用会去 GitHub 拉约 100 MB（离线机直接失败）。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIC_NAME = "open_jtalk_dic_utf_8-1.11"
PREFIXED = "MEA_PET_OPEN_JTALK_DICT_DIR"
BARE = "OPEN_JTALK_DICT_DIR"


def _load(script_path: Path):
    spec = importlib.util.spec_from_file_location("vits_infer_under_test", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_tree(tmp_path: Path) -> Path:
    # _bootstrap() 按 __file__ 的三级父目录认 base，所以整棵假树得真造出来
    script = tmp_path / "meapet" / "tools" / "vits_infer.py"
    script.parent.mkdir(parents=True)
    shutil.copy(ROOT / "meapet" / "tools" / "vits_infer.py", script)
    return script


@pytest.fixture
def no_dict_env(monkeypatch):
    for name in (PREFIXED, BARE, "MEA_PET_APPDATA"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_bundled_dict_writes_both_names(no_dict_env, tmp_path):
    script = _fake_tree(tmp_path)
    dic = tmp_path / "dic" / DIC_NAME
    dic.mkdir(parents=True)

    _load(script)._bootstrap()

    assert os.environ[BARE] == str(dic)
    assert os.environ[BARE] == os.environ[PREFIXED]


def test_cached_dict_writes_both_names(no_dict_env, tmp_path):
    script = _fake_tree(tmp_path)
    appdata = tmp_path / "appdata"
    cached = appdata / ".pyopenjtalk" / DIC_NAME
    cached.mkdir(parents=True)
    os.environ["MEA_PET_APPDATA"] = str(appdata)

    _load(script)._bootstrap()

    assert os.environ[BARE] == str(cached) == os.environ[PREFIXED]


def test_missing_dict_writes_neither(no_dict_env, tmp_path, capsys):
    """没词典那一支必须仍然两个名字都不写——否则"接上了"是假的。"""
    script = _fake_tree(tmp_path)

    _load(script)._bootstrap()

    assert BARE not in os.environ
    assert PREFIXED not in os.environ
    assert "未找到日语词典" in capsys.readouterr().err


def test_runtime_helper_writes_both_names(no_dict_env, tmp_path, monkeypatch):
    from meapet.tts.engines import vits_runtime

    dic = tmp_path / "dic" / DIC_NAME
    dic.mkdir(parents=True)
    # 真 project_path 返回 str（meapet/paths.py:25），替身按同一契约
    monkeypatch.setattr(
        vits_runtime,
        "project_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )

    assert vits_runtime._ensure_openjtalk_dict() == str(dic)
    assert os.environ[BARE] == str(dic) == os.environ[PREFIXED]


def test_subprocess_script_has_no_prefixed_only_write_left():
    """防漂移：子进程那份脚本里前缀名只允许出现在"两个名字一起写"的那一处。"""
    source = (ROOT / "meapet" / "tools" / "vits_infer.py").read_text(encoding="utf-8")

    assert source.count(f'os.environ["{PREFIXED}"]') == 1
    assert f'os.environ["{BARE}"]' in source
