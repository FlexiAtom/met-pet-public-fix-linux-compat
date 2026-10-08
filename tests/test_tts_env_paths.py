"""VITS/GSV 外部环境的解释器与 site-packages 路径必须按平台拼。

出处：Athena 件 meapet-vits-posix-env-path-bug——向导里写死 ``Scripts/python.exe``
与 ``Lib/site-packages``，POSIX 上这两条路径不存在，于是 venv 建成了却被判"创建失败"。
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 只有 meapet/tts/common.py 允许拼平台形状；这几处出现字面量就说明 POSIX 分支漏了。
# 不含 meapet/tools/vits_infer.py：那两处 Lib/lib 是给 Windows embeddable 兜底用的，
# venv/conda 自带的 site-packages 由解释器自己配好，不需要这里补。
_CALLER_SOURCES = (
    "wizard/page_tts.py",
    "wizard/page_tts_vits.py",
    "wizard/page_tts_gsv.py",
    "meapet/tts/service.py",
)
_BANNED = ("Scripts", "site-packages")


def test_venv_and_prefix_python_return_the_native_interpreter_shapes(tmp_path):
    from meapet.tts.common import prefix_python, venv_python

    env_dir = tmp_path / "vits_env"
    (env_dir / "Scripts").mkdir(parents=True)
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (env_dir / "bin" / "python").write_text("", encoding="utf-8")

    assert venv_python(str(env_dir), windows=True) == str(env_dir / "Scripts" / "python.exe")
    assert venv_python(str(env_dir), windows=False) == str(env_dir / "bin" / "python")
    # conda env 与 GPT-SoVITS 整合包的 runtime/ 是扁平前缀：Windows 上 exe 就在目录本身
    assert prefix_python(str(env_dir), windows=True) == str(env_dir / "python.exe")
    assert prefix_python(str(env_dir), windows=False) == str(env_dir / "bin" / "python")
    # 默认按当前机器判定，且 venv 那条必须真的存在
    assert Path(venv_python(str(env_dir))).is_file()


def test_env_site_packages_keeps_the_python_version_segment(tmp_path):
    from meapet.tts.common import env_site_packages

    env_dir = tmp_path / "vits_env"
    (env_dir / "Lib" / "site-packages").mkdir(parents=True)
    (env_dir / "lib" / "python3.12" / "site-packages").mkdir(parents=True)

    assert env_site_packages(str(env_dir), windows=True) == str(
        env_dir / "Lib" / "site-packages"
    )
    # POSIX 的纯库目录带版本段：写死 lib/site-packages 在两平台都命中不了
    assert env_site_packages(str(env_dir), windows=False) == str(
        env_dir / "lib" / "python3.12" / "site-packages"
    )
    assert Path(env_site_packages(str(env_dir))).is_dir()


def test_env_site_packages_does_not_invent_a_missing_dir(tmp_path):
    from meapet.tts.common import env_site_packages

    empty = tmp_path / "broken_env"
    empty.mkdir()
    assert not Path(env_site_packages(str(empty), windows=False)).exists()


def test_gsv_python_candidates_cover_the_native_layout(tmp_path):
    """GSV 发现层按平台各出自己的形状：POSIX 是 ``bin/python``，Windows 是 ``python.exe``。

    从前这条把 POSIX 那侧写死成无条件断言，Windows 上必红——同一条 assert 在两个
    平台上问的是两件不同的事。改法不是放宽成"任一即可"，而是正向钉本平台那侧、
    反向钉对面那侧（两台机器的形状都铺在磁盘上，才看得出有没有串味）。
    """
    from meapet.tts.service import gsv_python_choices

    home = tmp_path / "home"
    runtime = home / "GPT-SoVITS" / "runtime"
    (runtime / "bin").mkdir(parents=True)
    posix = runtime / "bin" / "python"
    windows = runtime / "python.exe"
    posix.write_text("", encoding="utf-8")
    windows.write_text("", encoding="utf-8")

    candidates = [
        path for path, _root in gsv_python_choices(
            home=home,
            environ={},
            executable=tmp_path / "python",
            frozen=True,
        )
    ]

    native, foreign = (
        (windows, posix) if os.name == "nt" else (posix, windows)
    )
    assert str(native) in candidates, "每台机器只出自己的形状：本平台那侧必须在"
    assert str(foreign) not in candidates, "另一平台的形状不该出现在候选里"


def test_gsv_python_candidates_include_the_extracted_layer(tmp_path):
    """整合包解出来通常带一层版本/日期目录，候选表必须认这一档。

    出处：Athena 件 meapet/tts-speech-false-missing-deps。从前只写到
    ``~/GPT-SoVITS-v2pro/runtime``，而实际解压形状是
    ``~/GPT-SoVITS-v2pro/GPT-SoVITS-v2pro-20250604/runtime``。上面那格落空后，
    候选表往后走正好命中 ``~/miniconda3`` 那种没装依赖的解释器——闸门报
    「缺少 14 个依赖」，而带 runtime 的那台机器其实能合成。
    """
    from meapet.tts.common import prefix_python
    from meapet.tts.service import gsv_python_choices

    home = tmp_path / "home"
    bundled = home / "GPT-SoVITS-v2pro" / "GPT-SoVITS-v2pro-bundle" / "runtime"
    (bundled / "bin").mkdir(parents=True)
    (bundled / "bin" / "python").write_text("", encoding="utf-8")
    conda_base = home / "miniconda3"
    (conda_base / "bin").mkdir(parents=True)
    (conda_base / "bin" / "python").write_text("", encoding="utf-8")

    choices = list(
        gsv_python_choices(
            home=home, environ={}, executable=tmp_path / "python", frozen=True
        )
    )
    candidates = [path for path, _root in choices]

    # 名字里带什么日期不重要——所以判据是"多一层也能认"，不是把某个包名枚举进代码。
    nested = str(Path(prefix_python(bundled)))
    assert nested in candidates, "解压带一层版本/日期目录时必须进候选"
    conda = str(Path(prefix_python(conda_base)))
    assert nested in candidates[: candidates.index(conda)], (
        "真装了依赖的那个 runtime 得排在裸 conda base 前面"
    )
    # 布局根必须跟着这一格出来，且是含 runtime 的那一层——选路就靠它排序
    assert dict(choices)[nested] == str(bundled.parent)


def test_selection_prefers_an_interpreter_that_names_its_layout(tmp_path):
    """`.venv` 排在表头，但它旁边没有整合包 ⇒ 子进程报 GSV root not found。

    出处：2026-10-08 08:36 现场——依赖被装进产品自己的 venv 后闸门放行，
    选路只看"解释器在不在"，于是选了说不出布局根的那一格，子进程 0.1s 失败。
    """
    from meapet.tts.service import select_gsv_python

    venv = str(tmp_path / ".venv" / "bin" / "python")
    runtime = str(tmp_path / "GPT-SoVITS-v2pro" / "bundle" / "runtime" / "bin" / "python")
    choices = [(venv, ""), (runtime, str(tmp_path / "GPT-SoVITS-v2pro" / "bundle"))]

    assert select_gsv_python(choices, exists=lambda _p: True) == (runtime, choices[1][1])


def test_selection_falls_back_when_no_candidate_names_a_layout():
    """没有整合包布局时**不改变**既有行为：仍选第一个存在的（conda/venv 装法照旧）。"""
    from meapet.tts.service import select_gsv_python

    conda = "/home/x/miniconda3/envs/GPTSoVits/bin/python"
    other = "/home/x/miniconda3/bin/python"
    picked = select_gsv_python([(conda, ""), (other, "")], exists=lambda _p: True)
    assert picked == (conda, "")

    assert select_gsv_python([], exists=lambda _p: True) == ("", "")
    assert select_gsv_python([(conda, "")], exists=lambda _p: False) == ("", "")


def test_windows_shapes_are_only_built_by_the_helper():
    """改一处就够：向导/服务里再出现 Windows 形状的字面量，POSIX 分支必然漏掉。"""
    offenders = []
    for rel in _CALLER_SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(bad in line for bad in _BANNED):
                offenders.append(f"{rel}:{line_no}: {line.strip()}")

    assert not offenders, "写死的 venv 形状（应经 meapet.tts.common 的助手）:\n" + "\n".join(
        offenders
    )
