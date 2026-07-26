"""供应商预设注册表与向导预设选择器的测试。"""
from __future__ import annotations

import unittest

from meapet.config.providers import (
    CUSTOM_ID,
    PROTO_ANTHROPIC,
    PROTO_OLLAMA,
    PROTO_OPENAI,
    all_presets,
    detect_preset_by_url,
    preset_by_id,
)
from meapet.config.store import (
    ENV_LLM_KEY_BY_FAMILY,
    PROTOCOL_BY_ENDPOINT_FAMILY,
    detect_endpoint_family,
    infer_direct_protocol,
)


class ProviderPresetRegistryTests(unittest.TestCase):
    def test_registry_is_wellformed(self):
        presets = all_presets()
        self.assertGreater(len(presets), 15)
        ids = [p.id for p in presets]
        self.assertEqual(len(ids), len(set(ids)), "预设 id 必须唯一")
        names = [p.name for p in presets]
        self.assertEqual(len(names), len(set(names)), "预设展示名必须唯一")
        for p in presets:
            with self.subTest(preset=p.id):
                self.assertTrue(p.id and p.name)
                self.assertIn(
                    p.protocol, (PROTO_OPENAI, PROTO_OLLAMA, PROTO_ANTHROPIC)
                )
                if p.api_base:
                    self.assertTrue(
                        p.api_base.startswith(("http://", "https://")),
                        f"{p.id} 的 api_base 必须是完整 URL",
                    )
                # 需要密钥的云端预设必须给出环境变量名，便于 $VAR 占位
                if p.requires_key and p.api_base:
                    self.assertTrue(p.env_keys, f"{p.id} 缺少 env_keys")
                # 中立兜底变量始终存在
                if p.env_keys:
                    self.assertIn("MEAPET_API_KEY", p.family_env_keys)

    def test_preset_lookup(self):
        self.assertIsNone(preset_by_id(CUSTOM_ID))
        self.assertIsNone(preset_by_id("nope"))
        self.assertEqual(preset_by_id("deepseek").name, "DeepSeek 深度求索")

    def test_major_providers_have_default_model(self):
        # 有固定模型清单的主流厂商，切换预设时应能给出默认模型
        for pid, want in (
            ("openai", "gpt-4o-mini"),
            ("deepseek", "deepseek-chat"),
            ("anthropic", "claude-3-5-sonnet-latest"),
            ("qwen", "qwen-plus"),
        ):
            with self.subTest(provider=pid):
                p = preset_by_id(pid)
                self.assertEqual(p.default_model, want)
                self.assertEqual(p.models[0], want)
        # 聚合/本地供应商可以没有固定清单
        self.assertEqual(preset_by_id("openrouter").default_model, "")

    def test_detect_preset_by_url(self):
        cases = {
            "https://api.moonshot.cn/v1": "moonshot",
            "https://open.bigmodel.cn/api/paas/v4": "zhipu",
            "https://dashscope.aliyuncs.com/compatible-mode/v1": "qwen",
            "https://api.siliconflow.cn/v1": "siliconflow",
            "https://openrouter.ai/api/v1": "openrouter",
            "https://api.x.ai/v1": "xai",
            "http://127.0.0.1:11434": "ollama",
        }
        for url, want in cases.items():
            with self.subTest(url=url):
                hit = detect_preset_by_url(url)
                self.assertIsNotNone(hit)
                self.assertEqual(hit.id, want)
        self.assertIsNone(detect_preset_by_url("https://example.invalid/v1"))
        self.assertIsNone(detect_preset_by_url(""))


class StoreIntegrationTests(unittest.TestCase):
    def test_new_families_resolve_protocol_and_env(self):
        for pid in ("moonshot", "zhipu", "qwen", "siliconflow", "groq"):
            with self.subTest(family=pid):
                self.assertEqual(
                    PROTOCOL_BY_ENDPOINT_FAMILY.get(pid), PROTO_OPENAI
                )
                env = ENV_LLM_KEY_BY_FAMILY.get(pid)
                self.assertTrue(env and "MEAPET_API_KEY" in env)

    def test_builtin_families_are_not_overridden(self):
        # 注册表合并用 setdefault，既有内置 family 行为必须原样保留
        self.assertEqual(PROTOCOL_BY_ENDPOINT_FAMILY["ollama"], PROTO_OLLAMA)
        self.assertEqual(PROTOCOL_BY_ENDPOINT_FAMILY["anthropic"], PROTO_ANTHROPIC)
        self.assertEqual(PROTOCOL_BY_ENDPOINT_FAMILY["custom"], PROTO_OPENAI)
        self.assertEqual(
            ENV_LLM_KEY_BY_FAMILY["deepseek"],
            ("DEEPSEEK_API_KEY", "MEAPET_API_KEY"),
        )

    def test_typed_urls_detect_new_providers(self):
        self.assertEqual(detect_endpoint_family("https://api.moonshot.cn/v1"), "moonshot")
        self.assertEqual(
            infer_direct_protocol("custom", api_base="https://api.anthropic.com/v1"),
            PROTO_ANTHROPIC,
        )

    def test_lm_studio_not_swallowed_by_localhost_ollama_rule(self):
        """LM Studio 走本地 OpenAI 兼容协议，不能因 127.0.0.1 被判成 ollama。"""
        self.assertEqual(detect_endpoint_family("http://127.0.0.1:1234/v1"), "lmstudio")
        self.assertEqual(
            infer_direct_protocol("custom", api_base="http://127.0.0.1:1234/v1"),
            PROTO_OPENAI,
        )
        # 真正的 Ollama 端口仍判为 ollama
        self.assertEqual(detect_endpoint_family("http://127.0.0.1:11434"), "ollama")
        self.assertEqual(
            infer_direct_protocol("custom", api_base="http://127.0.0.1:11434"),
            PROTO_OLLAMA,
        )


class WizardPresetSelectorTests(unittest.TestCase):
    """向导 LLM 页的预设选择器行为（离屏 Qt）。"""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from wizard.page_llm import LLMPage
        from PyQt5.QtWidgets import QApplication
        self.page = LLMPage()
        # 显式管理生命周期，避免整套件同进程运行时裸 widget 被中途 GC
        # 触发 PyQt5 的原生 teardown 崩溃。
        self.addCleanup(QApplication.processEvents)
        self.addCleanup(self.page.deleteLater)

    def _select(self, preset_id: str):
        idx = next(
            i for i in range(self.page.provider_combo.count())
            if self.page.provider_combo.itemData(i) == preset_id
        )
        self.page.provider_combo.setCurrentIndex(idx)

    def test_selecting_preset_fills_endpoint_and_model(self):
        self._select("anthropic")
        self.assertEqual(
            self.page.endpoint_input.text(), "https://api.anthropic.com/v1"
        )
        # 关键：模型不再残留 OpenAI 的 gpt-4o-mini，而是 Anthropic 默认模型
        self.assertEqual(
            self.page.model_combo.currentText(), "claude-3-5-sonnet-latest"
        )
        profile = self.page.collect_direct_profile()
        self.assertEqual(profile["provider"], "custom")
        self.assertEqual(profile["protocol"], "anthropic_messages")
        self.assertEqual(profile["model"], "claude-3-5-sonnet-latest")

    def test_switching_between_presets_updates_model(self):
        for pid, want_url, want_model in (
            ("deepseek", "https://api.deepseek.com/v1", "deepseek-chat"),
            ("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
            ("openai", "https://api.openai.com/v1", "gpt-4o-mini"),
        ):
            self._select(pid)
            self.assertEqual(self.page.endpoint_input.text(), want_url)
            self.assertEqual(self.page.model_combo.currentText(), want_model)

    def test_restore_saved_profile_keeps_saved_model(self):
        """apply_direct_profile 回选下拉时不得覆写用户已存的模型。"""
        self.page.apply_direct_profile({
            "api_base": "https://api.deepseek.com/v1",
            "model": "deepseek-reasoner",
        })
        self.assertEqual(
            self.page.endpoint_input.text(), "https://api.deepseek.com/v1"
        )
        self.assertEqual(self.page.model_combo.currentText(), "deepseek-reasoner")
        self.assertEqual(self.page.provider_combo.currentData(), "deepseek")


class ModelFetchRequestTests(unittest.TestCase):
    """「获取模型列表」的请求构造（不发真实网络请求，只断言 headers/URL）。"""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from wizard.page_llm import LLMPage
        from PyQt5.QtWidgets import QApplication
        self.page = LLMPage()
        self.addCleanup(QApplication.processEvents)
        self.addCleanup(self.page.deleteLater)

    def _capture(self, base_url, api_key, protocol, extra=None):
        """跑一次 worker，截获它构造的 urllib Request。"""
        import io
        import json as _json
        from unittest import mock
        seen = {}

        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _Resp(_json.dumps({"data": [{"id": "m1"}]}).encode())

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            self.page._fetch_models_worker(base_url, api_key, protocol, extra or {})
        return seen

    def test_always_sends_user_agent(self):
        """无 UA 的请求会被 Cloudflare 等网关 403（error code 1010）拦掉。"""
        seen = self._capture("https://api.example.com/v1", "sk-x", "openai_chat")
        self.assertIn("user-agent", seen["headers"])
        self.assertTrue(seen["headers"]["user-agent"])

    def test_openai_uses_bearer(self):
        seen = self._capture("https://api.example.com/v1", "sk-x", "openai_chat")
        self.assertEqual(seen["headers"].get("authorization"), "Bearer sk-x")
        self.assertNotIn("x-api-key", seen["headers"])
        self.assertTrue(seen["url"].endswith("/models"))

    def test_anthropic_uses_x_api_key(self):
        """Anthropic 用 x-api-key + anthropic-version，不是 Bearer。"""
        seen = self._capture(
            "https://api.anthropic.com/v1", "sk-ant", "anthropic_messages"
        )
        self.assertEqual(seen["headers"].get("x-api-key"), "sk-ant")
        self.assertEqual(seen["headers"].get("anthropic-version"), "2023-06-01")
        self.assertNotIn("authorization", seen["headers"])

    def test_ollama_uses_native_tags_endpoint(self):
        seen = self._capture("http://127.0.0.1:11434", "", "ollama_chat")
        self.assertTrue(seen["url"].endswith("/api/tags"), seen["url"])

    def test_env_placeholder_key_is_not_sent_as_literal(self):
        seen = self._capture("https://api.example.com/v1", "$MY_KEY", "openai_chat")
        self.assertNotIn("authorization", seen["headers"])

    def test_custom_headers_cannot_override_auth(self):
        seen = self._capture(
            "https://api.example.com/v1", "sk-x", "openai_chat",
            {"X-Title": "MeaPet", "Authorization": "Bearer attacker"},
        )
        self.assertEqual(seen["headers"].get("authorization"), "Bearer sk-x")
        self.assertEqual(seen["headers"].get("x-title"), "MeaPet")


class AdvancedSectionTests(unittest.TestCase):
    """向导「高级配置」折叠区：超时 / 代理 / 自定义头 / Anthropic 扩展思考。"""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from wizard.page_llm import LLMPage
        from PyQt5.QtWidgets import QApplication
        self.page = LLMPage()
        self.page.show()
        self.addCleanup(QApplication.processEvents)
        self.addCleanup(self.page.deleteLater)

    def _select(self, preset_id: str):
        idx = next(
            i for i in range(self.page.provider_combo.count())
            if self.page.provider_combo.itemData(i) == preset_id
        )
        self.page.provider_combo.setCurrentIndex(idx)

    def test_collapsed_by_default_and_toggles(self):
        self.assertFalse(self.page.advanced_box.isVisible())
        self.page.advanced_toggle.setChecked(True)
        self.assertTrue(self.page.advanced_box.isVisible())
        self.page.advanced_toggle.setChecked(False)
        self.assertFalse(self.page.advanced_box.isVisible())

    def test_thinking_group_only_for_anthropic(self):
        self.page.advanced_toggle.setChecked(True)
        self._select("anthropic")
        self.assertTrue(self.page.thinking_group.isVisible())
        self._select("openai")
        self.assertFalse(self.page.thinking_group.isVisible())

    def test_adaptive_thinking_payload(self):
        self._select("anthropic")
        self.page.thinking_mode.setCurrentIndex(
            self.page.thinking_mode.findData("adaptive")
        )
        self.page.thinking_effort.setCurrentIndex(
            self.page.thinking_effort.findData("high")
        )
        profile = self.page.collect_direct_profile()
        self.assertEqual(profile["thinking"], {"type": "adaptive", "effort": "high"})

    def test_thinking_not_saved_for_non_anthropic(self):
        self._select("anthropic")
        self.page.thinking_mode.setCurrentIndex(
            self.page.thinking_mode.findData("adaptive")
        )
        self._select("openai")
        self.assertNotIn("thinking", self.page.collect_direct_profile())

    def test_timeout_proxy_headers_roundtrip(self):
        self.page.headers_input.setText("X-Title: MeaPet; HTTP-Referer: https://x.dev")
        self.page.timeout_input.setValue(90)
        self.page.proxy_input.setText("http://127.0.0.1:7890")
        profile = self.page.collect_direct_profile()
        self.assertEqual(
            profile["headers"],
            {"X-Title": "MeaPet", "HTTP-Referer": "https://x.dev"},
        )
        self.assertEqual(profile["timeout_seconds"], 90.0)
        self.assertEqual(profile["proxy"], "http://127.0.0.1:7890")

        from wizard.page_llm import LLMPage
        from PyQt5.QtWidgets import QApplication
        restored = LLMPage()
        self.addCleanup(QApplication.processEvents)
        self.addCleanup(restored.deleteLater)
        restored.show()
        restored.apply_direct_profile(profile)
        # 有高级配置时应自动展开，避免用户以为配置丢了
        self.assertTrue(restored.advanced_box.isVisible())
        self.assertEqual(restored.timeout_input.value(), 90)
        self.assertEqual(restored.proxy_input.text(), "http://127.0.0.1:7890")
        self.assertEqual(restored.collect_direct_profile()["headers"], profile["headers"])

    def test_zero_timeout_means_auto_and_is_not_saved(self):
        self.page.timeout_input.setValue(0)
        self.assertNotIn("timeout_seconds", self.page.collect_direct_profile())


class AnthropicThinkingPayloadTests(unittest.TestCase):
    """thinking 配置 → Anthropic 请求体的翻译规则。"""

    def _body(self, thinking):
        from meapet.direct.client import (
            DirectProtocolConfig,
            _SPEC_BUILDERS,
        )
        from meapet.direct.types import CanonicalChatRequest
        cfg = DirectProtocolConfig(
            protocol="anthropic_messages",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant",
            thinking=thinking,
        )
        req = CanonicalChatRequest(
            model="claude-3-5-sonnet-latest",
            messages=({"role": "user", "content": "hi"},),
        )
        return _SPEC_BUILDERS[cfg.protocol](cfg, req).body

    def test_adaptive_with_effort(self):
        body = self._body({"type": "adaptive", "effort": "high"})
        self.assertEqual(body["thinking"], {"type": "adaptive", "effort": "high"})
        # 开启思考时不得发送 temperature（接口要求）
        self.assertNotIn("temperature", body)

    def test_manual_budget_requires_minimum(self):
        self.assertEqual(
            self._body({"type": "enabled", "budget": 2048})["thinking"],
            {"type": "enabled", "budget_tokens": 2048},
        )
        # 低于 1024 视为无效，不发送 thinking
        self.assertNotIn("thinking", self._body({"type": "enabled", "budget": 512}))

    def test_disabled_by_default(self):
        body = self._body({})
        self.assertNotIn("thinking", body)
        self.assertIn("temperature", body)

    def test_invalid_effort_is_dropped(self):
        body = self._body({"type": "adaptive", "effort": "bogus"})
        self.assertEqual(body["thinking"], {"type": "adaptive"})


class ExtraHeaderMergeTests(unittest.TestCase):
    """direct 客户端的自定义请求头合并规则。"""

    def _spec(self, extra):
        from meapet.direct.client import (
            DirectProtocolConfig,
            _SPEC_BUILDERS,
            _with_extra_headers,
        )
        from meapet.direct.types import CanonicalChatRequest
        cfg = DirectProtocolConfig(
            protocol="openai_chat",
            base_url="https://api.example.com/v1",
            api_key="sk-real",
            extra_headers=extra,
        )
        req = CanonicalChatRequest(
            model="m", messages=({"role": "user", "content": "hi"},)
        )
        return _with_extra_headers(_SPEC_BUILDERS[cfg.protocol](cfg, req), extra)

    def test_extra_headers_are_added(self):
        spec = self._spec({"HTTP-Referer": "https://example.com", "X-Title": "MeaPet"})
        self.assertEqual(spec.headers["X-Title"], "MeaPet")
        self.assertEqual(spec.headers["HTTP-Referer"], "https://example.com")

    def test_auth_headers_are_protected(self):
        """自定义头不得覆盖鉴权/协议头，否则一个配置错误就会把密钥发错地方。"""
        spec = self._spec({
            "Authorization": "Bearer attacker",
            "Content-Type": "text/plain",
            "x-api-key": "leak",
        })
        self.assertEqual(spec.headers["Authorization"], "Bearer sk-real")
        self.assertEqual(spec.headers["Content-Type"], "application/json")
        self.assertNotIn("x-api-key", spec.headers)


if __name__ == "__main__":
    unittest.main()