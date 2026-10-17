"""向导语音页在 Linux 上的说实话契约（甲：只让页面说实话，不加安装功能）。

这组测试盯的是同一件事的两头：**页面说的话必须与交付码做的同一回事**。
2026-10-11 本机实测的两条谎——

* GSV：``tts.python_exe`` 留空、交付码从候选表找到
  ``~/GPT-SoVITS-v2pro/GPT-SoVITS-v2pro-20250604/runtime/bin/python`` 并正常出声，
  而页面因为只在点「浏览」时才检测、且只认输入框那一格，报「尚未配置语音」。
* VITS：``_check_vits`` 只看随包的 ``vits_models/G_latest.pth``（本机 134 B 的 LFS
  pointer），无视配置里指向 158 MB 真身的 ``tts.vits_model``，报红「权重仍是 pointer」。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402

from wizard.page_tts import TTSPage  # noqa: E402
from wizard.page_tts_gsv import (  # noqa: E402
    find_pack_python,
    gsv_dir_field,
    resolve_gsv_python,
)
from meapet.tts.common import prefix_python  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    widget = TTSPage()
    yield widget
    widget.close()
    QApplication.processEvents()


def _empty_table():
    """把候选表掐空：被测的是"这一格输入怎么走"，不是本机装了什么。"""
    return patch("meapet.tts.service.gsv_python_choices", return_value=())


# ─── GSV：什么时候才探测 ───────────────────────────────────────────────


def test_show_event_runs_the_gsv_probe_once(page):
    """第一次露脸补一次检测；反复切页不该反复起子进程。"""
    page.set_engine("gpt_sovits")  # 没露脸时切引擎不探测（构造那一路）
    with patch.object(TTSPage, "_check_gsv") as check:
        page.show()
        QApplication.processEvents()
        assert check.call_count == 1, "开页必须自己补一次检测，别等用户点浏览"
        page.hide()
        page.show()
        QApplication.processEvents()
        assert check.call_count == 1, "露脸只量一次，反复切页不该反复起子进程"


def test_show_event_leaves_a_non_gsv_engine_alone(page):
    """选了 MiMo 的人不该为一格看不见的 GSV 状态付一次子进程。"""
    page.set_engine("mimo")
    with patch.object(TTSPage, "_check_gsv") as check:
        page.show()
        QApplication.processEvents()
    check.assert_not_called()


def test_engine_switch_probes_after_the_page_has_been_shown(page):
    page.engine_details_toggle.setChecked(True)
    with patch.object(TTSPage, "_check_gsv") as check:
        # 没露过脸：切引擎不探测（构造期与启动那一路都走这里）
        page._env_probe_allowed = False
        page._gsv_probe_done = True
        page.set_engine("gpt_sovits")
        check.assert_not_called()
        # 露过脸之后：用户亲手换引擎就该拿到反馈。setCurrentIndex 与 set_engine
        # 末尾那一次显式调用各触发一遍 _toggle_backend，是既有形状，这里只问"有没有"。
        page._env_probe_allowed = True
        page.set_engine("mimo")
        check.reset_mock()
        page.set_engine("gpt_sovits")
        assert check.call_count >= 1, "亲手换到 GPT-SoVITS 还不开口，页面就是在装死"


# ─── GSV：判据与交付码同一套 ────────────────────────────────────────────


def test_resolver_uses_the_products_own_candidate_table(tmp_path):
    """向导不许自己再排一份候选表——复制的那份永远比原件窄。"""
    pack_root = tmp_path / "GPT-SoVITS-v2pro"
    pack_py = pack_root / "runtime" / "bin" / "python"
    pack_py.parent.mkdir(parents=True)
    pack_py.write_text("", encoding="utf-8")
    conda_py = str(tmp_path / "miniconda3" / "envs" / "GPTSoVits" / "bin" / "python")
    table = [(conda_py, ""), (str(pack_py), str(pack_root))]
    seen = []

    def fake_choices(**_kw):
        seen.append(True)
        return tuple(table)

    with patch("meapet.tts.service.gsv_python_choices", side_effect=fake_choices), patch(
        "wizard.page_tts_gsv.gsv_root_for", return_value=str(pack_root)
    ):
        got = resolve_gsv_python("", environ={})

    assert seen == [True], "空输入必须回落到交付码那张候选表"
    # conda 那格在盘上不存在，且说不出布局根；整合包那格两样都有 —— 排序归交付码
    assert got[0] == str(pack_py), "排序也归 select_gsv_python：整合包布局那格优先"
    assert got[1] == "自动检测"
    assert got[2] == str(pack_root)


def test_field_beats_env_beats_table():
    """三级顺序照 MeaTTS.__init__：输入框 → MEA_PET_GSV_PYTHON → 候选表。"""
    field_py = str(ROOT / "meapet" / "__init__.py")  # 任意一个在盘上的文件
    with patch("meapet.tts.service.gsv_python_choices", return_value=()):
        got = resolve_gsv_python(field_py, environ={"MEA_PET_GSV_PYTHON": field_py})
        assert got[1] == "填的解释器"
        got = resolve_gsv_python("", environ={"MEA_PET_GSV_PYTHON": field_py})
        assert got[1] == "环境变量 MEA_PET_GSV_PYTHON"


def test_unusable_folder_input_is_named_not_swallowed(page, tmp_path):
    """填了个没有 runtime 的文件夹：那句"你填的没被采纳"必须说出来。

    交付码在这一格解析不出时会静默改用候选表，页面要是只跟着候选表那头的绿灯走，
    填错的人永远看不见自己填的那条被丢了（2026-10-11 复盘）。这里候选表掐空，
    两头都没戏 → 黄灯点名，而不是"尚未配置语音"那句把人推向"我没配"。
    """
    with _empty_table(), patch.dict(os.environ, {}, clear=True):
        page.gsv_dir_input.setText(str(tmp_path))
        page._check_gsv()
    assert page.gsv_status.property("status") == "warning"
    text = page.gsv_status.text()
    assert "没有 runtime 解释器" in text and str(tmp_path) in text
    assert "程序自己也没检测到" in text

    with _empty_table(), patch.dict(os.environ, {}, clear=True):
        page.gsv_dir_input.setText(str(tmp_path / "ghost" / "python"))
        page._check_gsv()
    assert page.gsv_status.property("status") == "warning"
    assert "不是可用解释器" in page.gsv_status.text()

    # 什么都没填、候选表也空：这才是"尚未配置"那一格，不该拿黄色吓人
    with _empty_table(), patch.dict(os.environ, {}, clear=True):
        page.gsv_dir_input.setText("")
        page._check_gsv()
    assert page.gsv_status.property("status") == "muted"
    assert "尚未配置语音" in page.gsv_status.text()


def test_dropped_input_trails_a_green_light(page, tmp_path):
    """候选表救回来了、但用户填的那格被丢：绿灯可以亮，那句尾巴不能省。"""
    good = tmp_path / "elsewhere" / "runtime" / "bin" / "python"
    good.parent.mkdir(parents=True)
    good.write_text("", encoding="utf-8")
    with patch(
        "meapet.tts.service.gsv_python_choices", return_value=((str(good), ""),)
    ), patch("wizard.page_tts_gsv._gsv_probe", return_value=True), patch(
        "wizard.page_tts_gsv.gsv_root_for", return_value=str(tmp_path)
    ), patch.dict(
        os.environ, {}, clear=True
    ):
        page.gsv_dir_input.setText(str(tmp_path / "typo"))
        page._check_gsv()
    text = page.gsv_status.text()
    assert page.gsv_status.property("status") == "success"
    assert "不是可用解释器" in text
    assert "配置里留空也会走它" not in text, "框里明明写着东西，不能说它留空"


def test_interpreter_without_a_tree_says_which_gate_failed(page, tmp_path):
    """解释器在、旁边没整合包树：这是 2026-10-08 那次的形态，得点出 tts.gsv_root。"""
    py = tmp_path / "bin" / "python"
    py.parent.mkdir()
    py.write_text("not executable", encoding="utf-8")
    with _empty_table(), patch.dict(os.environ, {}, clear=True), patch(
        "wizard.page_tts_gsv.gsv_root_for", return_value=""
    ):
        page.gsv_dir_input.setText(str(py))
        page._check_gsv()
    text = page.gsv_status.text()
    assert page.gsv_status.property("status") == "warning"
    assert "GPT_SoVITS/TTS_infer_pack" in text
    assert "tts.gsv_root" in text


def test_interpreter_with_a_tree_but_no_deps_is_the_other_message(page, tmp_path):
    py = tmp_path / "bin" / "python"
    py.parent.mkdir()
    py.write_text("not executable", encoding="utf-8")
    with _empty_table(), patch.dict(os.environ, {}, clear=True), patch(
        "wizard.page_tts_gsv.gsv_root_for", return_value=str(tmp_path)
    ):
        page.gsv_dir_input.setText(str(py))
        page._check_gsv()
    text = page.gsv_status.text()
    assert "import GPT_SoVITS 失败" in text
    assert "没装依赖" in text, "别把缺依赖说成路径写错——两种病吃法不同"


def test_auto_detected_success_says_the_config_can_stay_empty(page):
    py = str(ROOT / "meapet" / "__init__.py")
    with patch("meapet.tts.service.gsv_python_choices", return_value=((py, ""),)), patch(
        "wizard.page_tts_gsv._gsv_probe", return_value=True
    ), patch("wizard.page_tts_gsv.gsv_root_for", return_value=str(ROOT)), patch.dict(
        os.environ, {}, clear=True
    ):
        page.gsv_dir_input.setText("")
        page._check_gsv()
    assert page.gsv_status.property("status") == "success"
    assert "自动检测" in page.gsv_status.text()
    assert "配置里留空也会走它" in page.gsv_status.text()


# ─── GSV：措辞与回填按平台分叉 ──────────────────────────────────────────


def test_folder_search_accepts_both_runtime_layouts(tmp_path):
    """整合包那一格：平铺一层要命中，解压多带一层日期目录那档也要命中。

    解释器名字按平台给（Windows ``runtime\\python.exe``，POSIX ``runtime/bin/python``），
    判据取自交付码同一个 ``prefix_python``，测试里不写第二套路径拼法。
    """
    flat = tmp_path / "flat" / "runtime"
    flat.mkdir(parents=True)
    flat_py = prefix_python(flat)
    Path(flat_py).parent.mkdir(parents=True, exist_ok=True)
    Path(flat_py).write_text("", encoding="utf-8")
    assert find_pack_python(str(tmp_path / "flat")) == flat_py

    nested = tmp_path / "nested" / "GPT-SoVITS-v2pro-20250604" / "runtime"
    nested.mkdir(parents=True)
    nested_py = prefix_python(nested)
    Path(nested_py).parent.mkdir(parents=True, exist_ok=True)
    Path(nested_py).write_text("", encoding="utf-8")
    assert find_pack_python(str(tmp_path / "nested")) == nested_py

    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_pack_python(str(empty)) is None


def test_posix_interpreter_backfills_to_the_pack_folder(tmp_path):
    pack = tmp_path / "GPT-SoVITS-v2pro" / "GPT-SoVITS-v2pro-20250604"
    posix = pack / "runtime" / "bin" / "python"
    posix.parent.mkdir(parents=True)
    posix.write_text("", encoding="utf-8")
    # 反推出来的那一格必须喂得回 find_pack_python——不然回填成另一种写法就是新的谎
    assert gsv_dir_field(str(posix)) == str(pack)
    assert find_pack_python(gsv_dir_field(str(posix))) == str(posix)

    win = pack / "runtime" / "python.exe"
    win.write_text("", encoding="utf-8")
    assert gsv_dir_field(str(win)) == str(pack)

    conda = tmp_path / "miniconda3" / "envs" / "GPTSoVits" / "bin" / "python"
    conda.parent.mkdir(parents=True)
    conda.write_text("", encoding="utf-8")
    assert gsv_dir_field(str(conda)) == str(conda), "装在源码树外的别反推成 envs/bin"

    assert gsv_dir_field(str(tmp_path)) == str(tmp_path), "文件夹原样回填"


def test_apply_config_backfills_posix_python_exe_to_a_folder(page, tmp_path):
    pack = tmp_path / "pack"
    interpreter = pack / "runtime" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    page.apply_config({"engine": "gpt_sovits", "python_exe": str(interpreter)})
    assert page.gsv_dir_input.text() == str(pack)


@pytest.mark.skipif(os.name != "posix", reason="POSIX 那一支的措辞")
def test_label_and_guide_speak_posix_paths(page):
    labels = [label.text() for label in page.findChildren(QLabel)]
    assert any("runtime/bin/python" in text for text in labels)
    steps = page._guide_steps()
    footer = page._guide_footer()
    assert "runtime/bin/python" in steps
    assert "README" in steps, "Linux 没有整合包，手动配置的去处要指出来"
    assert "MeaPet.exe" not in footer
    assert "不能当 Python 用" in footer


def test_guide_on_windows_keeps_the_exe_wording():
    import wizard.page_tts_gsv as gsv

    stub = SimpleNamespace()
    with patch.object(gsv, "PLATFORM", {"is_windows": True}):
        steps = gsv.TtsPageGsvMixin._guide_steps(stub)
        footer = gsv.TtsPageGsvMixin._guide_footer(stub)
    assert r"runtime\python.exe" in steps
    assert "MeaPet.exe" in footer
    assert "README" not in steps


# ─── VITS：认配置里的权重路径 ───────────────────────────────────────────


def _write_model(path: Path, size: int = 5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_check_vits_honors_the_configured_model_path(page, tmp_path):
    model = _write_model(tmp_path / "elsewhere" / "G_latest.pth")
    conf = tmp_path / "finetune_speaker.json"
    conf.write_text("{}", encoding="utf-8")
    page._vits_model_cfg = str(model)
    page._vits_config_cfg = str(conf)
    page._check_vits()
    text = page.vits_status.text()
    assert page.vits_status.property("status") == "success"
    assert "tts.vits_model" in text
    assert str(model) not in text or "tts.vits_model" in text


def test_bundled_lfs_pointer_does_not_shadow_a_real_configured_model(page, tmp_path):
    """随包那格还是 pointer、配置指向真身：页面不该再报 pointer 缺失。"""
    pointer = tmp_path / "bundled" / "G_latest.pth"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "size 158910108\n",
        encoding="utf-8",
    )
    model = _write_model(tmp_path / "weights" / "G_latest.pth")
    conf = tmp_path / "finetune_speaker.json"
    conf.write_text("{}", encoding="utf-8")
    import meapet.tts.common as common

    with patch.object(common, "vits_model_path", return_value=str(pointer)), patch.object(
        common, "vits_config_path", return_value=str(conf)
    ):
        page._vits_model_cfg = ""
        page._check_vits()
        assert page.vits_status.property("status") == "error"
        assert "LFS pointer" in page.vits_status.text()

        page._vits_model_cfg = str(model)
        page._check_vits()
        assert page.vits_status.property("status") == "success"
        assert "来自 tts.vits_model" in page.vits_status.text()


def test_apply_config_captures_the_model_before_the_engine_switch(page, tmp_path):
    """set_engine 一路会触发 _check_vits：路径必须先落袋，否则第一次读数还是旧的。"""
    model = _write_model(tmp_path / "weights" / "G_latest.pth")
    conf = tmp_path / "finetune_speaker.json"
    conf.write_text("{}", encoding="utf-8")
    page.apply_config(
        {
            "engine": "vits",
            "enabled": True,
            "vits_model": str(model),
            "vits_config": str(conf),
        }
    )
    assert page._vits_model_cfg == str(model)
    assert page._vits_config_cfg == str(conf)


def test_missing_configured_model_says_where_it_looked(page, tmp_path):
    ghost = tmp_path / "weights" / "G_latest.pth"
    page._vits_model_cfg = str(ghost)
    page._check_vits()
    text = page.vits_status.text()
    assert page.vits_status.property("status") == "error"
    assert "不存在" in text and str(ghost) in text


def test_vits_placeholder_does_not_demand_cuda():
    """CPU torch 也能跑 VITS（本机实测），占位句写"需含 PyTorch CUDA"就是劝退。"""
    src = (ROOT / "wizard" / "page_tts.py").read_text(encoding="utf-8")
    assert "CPU 版也行" in src
    assert "需含 PyTorch CUDA" not in src


def test_wizard_does_not_keep_a_second_gsv_candidate_table():
    """结构性闸门：向导不许自带候选表——只能向交付码要。

    查的是代码里的字符串常量；文档字符串与注释允许举真实路径当证据（AST 里本来
    就没有注释，文档字符串按值排除）。
    """
    import ast

    path = ROOT / "wizard" / "page_tts_gsv.py"
    src = path.read_text(encoding="utf-8")
    assert "from meapet.tts.service import gsv_python_choices, select_gsv_python" in src

    tree = ast.parse(src)
    doc_values = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            text = ast.get_docstring(node)
            if text:
                doc_values.add(text)

    banned = ("GPT-SoVITS-v2pro", "miniconda3", "anaconda3", "CONDA_PREFIX", "VIRTUAL_ENV")
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in doc_values
        and any(word in node.value for word in banned)
    ]
    assert not offenders, "候选表在交付码里，向导里再写一遍目录名就是第二份真值的开始"
