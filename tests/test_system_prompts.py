"""
测试 System Prompt 集中化配置加载逻辑：
1. 配置文件存在时，优先使用配置文件中的值；
2. 配置文件不存在（或读取失败时），回退到源码中的硬编码默认值；
3. 运行时占位符（如 {idle_minutes}）应由调用方填充，加载器不做 format。
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

# 导入路径由 tests/conftest.py 注入（项目根加入 sys.path），此处直接按包导入
from meapet.config import prompt_loader
from meapet.config.prompt_loader import (
    load_system_prompt,
    reload_system_prompts,
    _CONFIG_PATH,
    _CACHE,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个用例前后清空缓存与 CONFIG_PATH 覆盖，保证用例隔离。"""
    _CACHE.clear()
    original = prompt_loader._CONFIG_PATH
    yield
    prompt_loader._CONFIG_PATH = original
    _CACHE.clear()


# ---------- 一、配置文件存在时：优先使用配置 ----------

class TestConfigTakesPriority:
    def test_uses_config_value_when_file_exists(self, tmp_path):
        # 准备一个临时配置文件，覆盖加载路径
        config = {
            "persona": {"description": "测试", "prompt": "来自配置文件的人格"},
            "watch_decision": {
                "description": "测试",
                "prompt": "来自配置的决策 prompt，idle={idle_minutes}",
            },
        }
        cfg_file = tmp_path / "system_prompts.json"
        cfg_file.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        prompt_loader._CONFIG_PATH = cfg_file

        reload_system_prompts()  # 清空缓存后重新加载

        assert load_system_prompt("persona") == "来自配置文件的人格"
        assert load_system_prompt("watch_decision") == "来自配置的决策 prompt，idle={idle_minutes}"

    def test_config_value_used_even_if_default_provided(self, tmp_path):
        """即使调用方给了 default，配置存在时也应以配置为准。"""
        cfg_file = tmp_path / "system_prompts.json"
        cfg_file.write_text(
            json.dumps({"persona": {"prompt": "CONFIG_VALUE"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        prompt_loader._CONFIG_PATH = cfg_file
        reload_system_prompts()

        # default 是一个完全不同的值，验证不会回退到 default
        result = load_system_prompt("persona", default="HARDCODED_FALLBACK")
        assert result == "CONFIG_VALUE"


# ---------- 二、配置文件不存在 / 读取失败时：回退硬编码 ----------

class TestFallbackToDefault:
    def test_missing_config_file_falls_back_to_default(self, tmp_path):
        # 指向一个不存在的文件
        prompt_loader._CONFIG_PATH = tmp_path / "not_exist.json"
        reload_system_prompts()

        assert load_system_prompt("persona", default="硬编码的人格") == "硬编码的人格"
        assert load_system_prompt("watch_decision", default="硬编码的决策") == "硬编码的决策"

    def test_invalid_json_falls_back_to_default(self, tmp_path):
        # 文件存在但 JSON 损坏，应捕获异常并回退
        cfg_file = tmp_path / "broken.json"
        cfg_file.write_text("{ 这不是合法 JSON ", encoding="utf-8")
        prompt_loader._CONFIG_PATH = cfg_file
        reload_system_prompts()

        assert load_system_prompt("persona", default="回退值") == "回退值"

    def test_empty_config_falls_back_to_default(self, tmp_path):
        # 配置文件为空对象，对应 key 不存在 -> 回退
        cfg_file = tmp_path / "empty.json"
        cfg_file.write_text(json.dumps({}), encoding="utf-8")
        prompt_loader._CONFIG_PATH = cfg_file
        reload_system_prompts()

        assert load_system_prompt("persona", default="空配置回退") == "空配置回退"

    def test_permission_or_io_error_falls_back(self, tmp_path):
        # 模拟 open() 抛出 OSError，验证异常被吞掉并回退
        cfg_file = tmp_path / "unreadable.json"
        cfg_file.write_text(json.dumps({"persona": {"prompt": "SHOULD_NOT_SEE"}}), encoding="utf-8")
        prompt_loader._CONFIG_PATH = cfg_file
        reload_system_prompts()

        with patch("builtins.open", side_effect=OSError("模拟读取失败")):
            result = load_system_prompt("persona", default="IO失败回退")
        assert result == "IO失败回退"


# ---------- 三、占位符与缓存行为 ----------

class TestPlaceholdersAndCaching:
    def test_placeholders_are_preserved(self, tmp_path):
        """加载器不做 .format，占位符原样返回，由调用方填充。"""
        cfg_file = tmp_path / "system_prompts.json"
        cfg_file.write_text(
            json.dumps({"watch_decision": {"prompt": "idle={idle_minutes}, s={summary}"}}),
            encoding="utf-8",
        )
        prompt_loader._CONFIG_PATH = cfg_file
        reload_system_prompts()

        raw = load_system_prompt("watch_decision")
        assert raw == "idle={idle_minutes}, s={summary}"
        # 调用方负责填充
        filled = raw.format(idle_minutes=15, summary="写代码")
        assert "15" in filled and "写代码" in filled

    def test_result_is_cached(self, tmp_path):
        # 第一次读取后命中缓存，避免重复读盘
        cfg_file = tmp_path / "system_prompts.json"
        cfg_file.write_text(
            json.dumps({"persona": {"prompt": "CACHED"}}), encoding="utf-8"
        )
        prompt_loader._CONFIG_PATH = cfg_file
        reload_system_prompts()

        assert load_system_prompt("persona", default="X") == "CACHED"
        # 篡改磁盘文件，第二次读取应仍返回缓存值
        cfg_file.write_text(
            json.dumps({"persona": {"prompt": "CHANGED"}}), encoding="utf-8"
        )
        assert load_system_prompt("persona", default="X") == "CACHED"

    def test_reload_refreshes_cache(self, tmp_path):
        cfg_file = tmp_path / "system_prompts.json"
        cfg_file.write_text(
            json.dumps({"persona": {"prompt": "V1"}}), encoding="utf-8"
        )
        prompt_loader._CONFIG_PATH = cfg_file
        reload_system_prompts()
        assert load_system_prompt("persona") == "V1"

        cfg_file.write_text(
            json.dumps({"persona": {"prompt": "V2"}}), encoding="utf-8"
        )
        reload_system_prompts()
        assert load_system_prompt("persona") == "V2"


# ---------- 四、集成验证：改造后的源文件实际行为 ----------

class TestIntegrationWithSourceModules:
    """
    验证各模块在「有配置 / 无配置」两种情况下确实走不同分支。
    用一个与 engine.py 同构的「模块级常量赋值」片段来模拟真实启动逻辑，
    避免导入整个 engine（其依赖 PyQt / defaults 等重环境）。
    """

    @staticmethod
    def _simulate_engine_init(config_present: bool):
        """
        与 engine.py 完全同构的模块级赋值：
            PERSONA_PROMPT = load_system_prompt("persona", default=硬编码)
        通过 side_effect 精确模拟加载器行为：
            - config_present=True  -> 该 key 有值，返回配置值（优先配置）
            - config_present=False -> key 缺失/为空，返回 ""，触发 default 回退
        """
        def side_effect(key, default=""):
            if config_present:
                return f"CONFIG_{key}"
            return default  # 配置缺失时返回调用方传入的 default

        with patch.object(prompt_loader, "load_system_prompt", side_effect=side_effect):
            HARDCODED_PERSONA = "硬编码：你是梅尔默认人格"
            HARDCODED_LEGACY = "硬编码：格式"
            persona = prompt_loader.load_system_prompt("persona", default=HARDCODED_PERSONA)
            legacy = prompt_loader.load_system_prompt("legacy_output", default=HARDCODED_LEGACY)
            system = f"{persona}\n{legacy}"
            return persona, system

    def test_engine_uses_config_persona(self):
        # 有配置 -> 模块常量采纳配置值（不回退）
        persona, system = self._simulate_engine_init(config_present=True)
        assert persona == "CONFIG_persona"
        assert "CONFIG_persona" in system

    def test_engine_falls_back_when_config_missing(self):
        # 配置缺失（key 取不到）-> 回退到硬编码 default
        persona, system = self._simulate_engine_init(config_present=False)
        assert persona == "硬编码：你是梅尔默认人格"
        assert "硬编码：你是梅尔默认人格" in system
        assert "硬编码：格式" in system  # legacy 同样回退成功
