"""配置中心本轮交互修复的回归契约（OpenAI 兼容版）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QMessageBox,
)


class WizardConfigurationExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._widgets = []

    def tearDown(self) -> None:
        for widget in reversed(self._widgets):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QApplication.processEvents()

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    @staticmethod
    def _stop_startup_work(wizard) -> None:
        wizard.env_page._check_timer.stop()
        wizard._load_timer.stop()
        for timer in wizard.tts_page._startup_timers:
            timer.stop()

    # ------------------------------------------------------------------
    # 所有下拉框忽略滚轮
    # ------------------------------------------------------------------
    def test_all_wizard_combo_boxes_ignore_wheel_changes(self) -> None:
        from wizard.app import SetupWizard
        from wizard.widgets import WheelSafeComboBox

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)

        combos = wizard.findChildren(QComboBox)
        # 移除了一个后端选择组合框（不再有 backend 下拉）
        self.assertGreaterEqual(len(combos), 7)
        self.assertTrue(all(isinstance(combo, WheelSafeComboBox) for combo in combos))

        combo = wizard.tts_page.backend_combo
        combo.setCurrentIndex(0)
        event = Mock()
        combo.wheelEvent(event)
        self.assertEqual(combo.currentIndex(), 0)
        event.ignore.assert_called_once_with()

    # ------------------------------------------------------------------
    # 模型选择器可编辑并记住文本
    # ------------------------------------------------------------------
    def test_model_selector_is_editable_and_remembers_text(self) -> None:
        from wizard.page_llm import LLMPage

        page = self._track(LLMPage())
        self.assertTrue(page.model_combo.isEditable())
        page.model_combo.setEditText("custom-model-v1")
        self.assertEqual(page.model_combo.currentText(), "custom-model-v1")

    # ------------------------------------------------------------------
    # 模型限额有说明且默认为 4096
    # ------------------------------------------------------------------
    def test_model_limits_are_explained_and_default_to_4096(self) -> None:
        from meapet.config.store import normalize_config
        from wizard.page_llm import LLMPage

        page = self._track(LLMPage())
        self.assertEqual(page.max_tokens_input.value(), 4096)
        copy = " ".join(label.text() for label in page.findChildren(QLabel))
        self.assertIn("随机性", copy)
        self.assertIn("最大回复长度", copy)

    # ------------------------------------------------------------------
    # Agent 配置内提供跟随类型切换的接入步骤窗口
    # ------------------------------------------------------------------
    def test_agent_setup_help_is_discoverable_modeless_and_tracks_kind(
        self,
    ) -> None:
        from meapet.ui_theme import MIN_TARGET_SIZE
        from wizard.page_backend import BackendPage
        from wizard.styles import set_status

        page = self._track(BackendPage())
        button = page.agent_setup_help_btn

        self.assertIn("Hermes", button.text())
        self.assertGreaterEqual(button.minimumHeight(), MIN_TARGET_SIZE)
        self.assertTrue(button.accessibleName())
        self.assertTrue(button.accessibleDescription())
        self.assertTrue(button.property("doesNotModifyConfig"))

        button.click()
        QApplication.processEvents()
        dialog = self._track(page._agent_help_dialog)
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.isModal())
        self.assertEqual(dialog.windowModality(), Qt.NonModal)
        self.assertTrue(dialog.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(dialog.agent_kind, "hermes")
        self.assertIn("hermes serve", dialog.body.toPlainText())
        self.assertIn(
            "ws://127.0.0.1:9119/api/ws",
            dialog.body.toPlainText(),
        )
        help_html = dialog.body.toHtml().lower()
        self.assertNotIn('bgcolor="#100c18"', help_html)
        self.assertNotIn("background-color:#100c18", help_html)
        self.assertGreaterEqual(dialog.width(), 640)
        self.assertIn("websockets", dialog.dependency_detail.text().lower())
        self.assertEqual(
            dialog.dependency_status.property("status"),
            "success",
        )
        self.assertEqual(
            dialog.connection_status.text(),
            page.agent_connection_status.text(),
        )
        self.assertNotEqual(
            dialog.button(QMessageBox.Close).objectName(),
            "MessagePrimaryButton",
        )
        for action in (
            dialog.dependency_refresh_button,
            dialog.connection_test_button,
            dialog.button(QMessageBox.Close),
        ):
            self.assertEqual(action.height(), MIN_TARGET_SIZE)
        self.assertGreaterEqual(dialog.body.height(), 140)

        triggered = []
        page.test_agent_connection_btn.clicked.connect(
            lambda _checked=False: triggered.append(True)
        )
        dialog.connection_test_button.click()
        self.assertTrue(triggered)

        set_status(
            page.agent_connection_status,
            "error",
            "连接失败：测试端口未监听。",
        )
        dialog._sync_connection_status()
        self.assertEqual(
            dialog.connection_status.text(),
            "连接失败：测试端口未监听。",
        )
        self.assertEqual(
            dialog.connection_status.property("status"),
            "error",
        )

        page.set_agent_kind("openclaw")
        QApplication.processEvents()
        self.assertIn("OpenClaw", button.text())
        self.assertEqual(dialog.agent_kind, "openclaw")
        self.assertIn("openclaw devices approve", dialog.body.toPlainText())
        self.assertIn(
            "ws://127.0.0.1:18789",
            dialog.body.toPlainText(),
        )
        self.assertIn("cryptography", dialog.dependency_detail.text().lower())

        QApplication.processEvents()
        self.assertLess(
            dialog.body.geometry().bottom(),
            dialog.button(QMessageBox.Close).geometry().top(),
        )

        QTest.keyClick(dialog, Qt.Key_Escape)
        QApplication.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(page._agent_help_dialog)

    def test_agent_dependency_diagnostic_reports_missing_and_versions(
        self,
    ) -> None:
        from wizard.agent_setup_help import inspect_agent_dependencies

        missing = inspect_agent_dependencies(
            "hermes",
            find_spec=lambda _name: None,
            get_version=lambda _name: (_ for _ in ()).throw(
                LookupError("missing")
            ),
            executable=Path("C:/MeaPet/.venv/Scripts/python.exe"),
        )
        self.assertFalse(missing.ready)
        self.assertIn("websockets", missing.summary)
        self.assertIn("pip install", missing.install_command)
        self.assertIn("websockets>=13,<16", missing.install_command)

        versions = {
            "websockets": "15.0.1",
            "cryptography": "49.0.0",
        }
        ready = inspect_agent_dependencies(
            "openclaw",
            find_spec=lambda _name: object(),
            get_version=versions.__getitem__,
            executable=Path("C:/MeaPet/.venv/Scripts/python.exe"),
        )
        self.assertTrue(ready.ready)
        self.assertIn("websockets 15.0.1", ready.detail)
        self.assertIn("cryptography 49.0.0", ready.detail)

    def test_agent_help_shows_async_connection_progress_and_failure(
        self,
    ) -> None:
        from wizard.app import SetupWizard
        from wizard.connection_test import ConnectionResult

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)
        wizard.backend_page.agent_radio.setChecked(True)
        wizard.backend_page.agent_setup_help_btn.click()
        QApplication.processEvents()
        dialog = wizard.backend_page._agent_help_dialog
        future = Future()

        def submit(coro):
            coro.close()
            return future

        with patch("meapet.async_runtime.submit", side_effect=submit):
            dialog.connection_test_button.click()

        dialog._sync_connection_status()
        self.assertFalse(dialog.connection_test_button.isEnabled())
        self.assertIn("正在测试", dialog.connection_status.text())
        self.assertEqual(
            dialog.connection_status.property("status"),
            "warning",
        )

        future.set_result(
            ConnectionResult(False, "连接失败：目标 WebSocket 端口未监听。")
        )
        wizard._poll_connection_test("agent")
        dialog._sync_connection_status()

        self.assertTrue(dialog.connection_test_button.isEnabled())
        self.assertEqual(
            dialog.connection_status.text(),
            "连接失败：目标 WebSocket 端口未监听。",
        )
        self.assertEqual(
            dialog.connection_status.property("status"),
            "error",
        )

    def test_agent_help_keeps_footer_clear_at_max_font_scale(self) -> None:
        from meapet.ui_theme import (
            get_ui_font_scale,
            set_ui_font_scale,
        )
        from wizard.page_backend import BackendPage

        previous_scale = get_ui_font_scale()
        self.addCleanup(set_ui_font_scale, previous_scale)
        set_ui_font_scale(1.5)

        page = self._track(BackendPage())
        page.agent_radio.setChecked(True)
        page.agent_setup_help_btn.click()
        QApplication.processEvents()
        dialog = page._agent_help_dialog
        close_button = dialog.button(QMessageBox.Close)

        from wizard.styles import set_status

        set_status(
            page.agent_connection_status,
            "error",
            "连接失败：" + "目标 WebSocket 端口未监听，请检查服务和令牌。" * 6,
        )
        dialog._sync_connection_status()
        QApplication.processEvents()

        self.assertLess(
            dialog.body.geometry().bottom(),
            close_button.geometry().top(),
        )
        self.assertEqual(close_button.height(), 44)
        self.assertEqual(dialog.connection_test_button.height(), 44)
        self.assertEqual(
            dialog.body.horizontalScrollBarPolicy(),
            Qt.ScrollBarAlwaysOff,
        )

    # ------------------------------------------------------------------
    # 现有配置在构造函数返回前已加载
    # ------------------------------------------------------------------
    def test_existing_config_is_loaded_before_constructor_returns(self) -> None:
        from wizard.app import SetupWizard

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.json"
            path.write_text(
                json.dumps(
                    {
                        "display": {"font_scale": 1.3},
                        "llm": {
                            "mode": "direct",
                            "direct": {
                                "provider": "custom",
                                "protocol": "openai_chat",
                                "api_base": "https://example.test/v1",
                                "model": "saved-model",
                            },
                        },
                        "tts": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            wizard = self._track(SetupWizard(config_path=path))
            self._stop_startup_work(wizard)

            self.assertFalse(wizard._load_timer.isActive())
            self.assertEqual(wizard.font_scale_slider.value(), 130)
            self.assertEqual(wizard.llm_page.model_combo.currentText(), "saved-model")
            self.assertEqual(
                wizard._existing_config["display"]["font_scale"],
                1.3,
            )

            wizard.font_scale_slider.setValue(125)
            with patch.object(
                wizard,
                "_configuration_issues",
                return_value={index: [] for index in range(4)},
            ), patch(
                "wizard.app.styled_message_box",
                return_value=QMessageBox.Ok,
            ):
                wizard._save()

            reopened = self._track(SetupWizard(config_path=path))
            self._stop_startup_work(reopened)
            self.assertEqual(reopened.font_scale_slider.value(), 125)

    def test_saved_font_scale_is_painted_on_first_show(self) -> None:
        """恢复值必须在首次绘制生效，不能依赖再次移动滑块。"""
        from meapet.ui_theme import get_ui_font_scale, set_ui_font_scale
        from wizard.app import SetupWizard

        previous_scale = get_ui_font_scale()
        self.addCleanup(set_ui_font_scale, previous_scale)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.json"
            path.write_text(
                json.dumps(
                    {
                        "display": {"font_scale": 1.3},
                        "tts": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            wizard = self._track(SetupWizard(config_path=path))
            self._stop_startup_work(wizard)
            wizard._fade_timer.stop()
            wizard.setWindowOpacity(1.0)
            wizard.show()
            QApplication.processEvents()

            title = wizard.display_page.findChild(QLabel, "PageTitle")
            self.assertIsNotNone(title)
            self.assertEqual(wizard.font_scale_slider.value(), 130)
            self.assertEqual(title.font().pixelSize(), 29)
            self.assertEqual(wizard.font_scale_value.font().pixelSize(), 18)
            self.assertEqual(wizard.save_btn.font().pixelSize(), 20)

    # ------------------------------------------------------------------
    # 环境检测在 UI 线程外派发
    # ------------------------------------------------------------------
    def test_environment_startup_checks_are_dispatched_off_ui_thread(self) -> None:
        from wizard.page_env import EnvCheckPage

        env = self._track(EnvCheckPage())
        env._check_timer.stop()
        with patch.object(env, "_run_checks_impl") as checks, patch(
            "wizard.page_env.threading.Thread"
        ) as thread:
            env._run_checks()
        checks.assert_not_called()
        thread.assert_called_once()
        thread.return_value.start.assert_called_once_with()

    # ------------------------------------------------------------------
    # Python 3.13 是有效运行时
    # ------------------------------------------------------------------
    def test_python_313_is_a_valid_core_runtime_with_a_local_vits_advisory(self) -> None:
        from wizard.platform_info import (
            PYTHON_CHECK_NAME,
            platform_checklist,
            python_runtime_compatibility,
        )

        ok, status = python_runtime_compatibility(
            SimpleNamespace(major=3, minor=13, micro=3)
        )

        self.assertTrue(ok)
        self.assertIn("3.13.3", status)
        self.assertIn("VITS", status)
        self.assertEqual(PYTHON_CHECK_NAME, "Python 3.10+")
        names = [name for name, _hint, _required in platform_checklist()]
        self.assertIn(PYTHON_CHECK_NAME, names)
        self.assertNotIn("Python 3.10–3.12", names)

        too_old, old_status = python_runtime_compatibility(
            SimpleNamespace(major=3, minor=9, micro=19)
        )
        self.assertFalse(too_old)
        self.assertIn("3.10+", old_status)

    # ------------------------------------------------------------------
    # SpinBox 使用暗色主题和可访问高度
    # ------------------------------------------------------------------
    def test_wizard_spin_boxes_use_the_dark_theme_and_accessible_height(self) -> None:
        from meapet.ui_theme import MIN_TARGET_SIZE
        from wizard.app import SetupWizard
        from wizard.styles import WIZARD_STYLESHEET

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)
        spin_boxes = wizard.findChildren(QAbstractSpinBox)

        self.assertGreaterEqual(len(spin_boxes), 5)
        self.assertTrue(
            all(widget.minimumHeight() >= MIN_TARGET_SIZE for widget in spin_boxes)
        )
        for selector in (
            "QSpinBox,",
            "QDoubleSpinBox",
            "QSpinBox::up-button",
            "QSpinBox::down-button",
            "QDoubleSpinBox::up-button",
            "QDoubleSpinBox::down-button",
            "QSpinBox::up-arrow",
            "QSpinBox::down-arrow",
            "QComboBox::down-arrow",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, WIZARD_STYLESHEET)

    # ------------------------------------------------------------------
    # GSV 探测不在页面打开时调度
    # ------------------------------------------------------------------
    def test_slow_gsv_probe_is_not_scheduled_when_page_opens(self) -> None:
        from wizard.page_tts import TTSPage

        with patch.object(TTSPage, "_check_gsv") as check:
            page = self._track(TTSPage())
        check.assert_not_called()
        self.assertEqual(page._startup_timers, [])

    # ------------------------------------------------------------------
    # Vision 页无虚假高级开关或持久化范围表单
    # ------------------------------------------------------------------
    def test_vision_page_has_no_fake_advanced_toggle_or_persistent_scope_form(self) -> None:
        from wizard.page_vision import VisionPage

        page = self._track(VisionPage())
        self.assertFalse(hasattr(page, "advanced_toggle"))
        self.assertFalse(hasattr(page, "capture_scope_combo"))

        page.mode_combo.setCurrentIndex(page.mode_combo.findData("inherit"))
        self.assertFalse(page.advanced_frame.isHidden())
        page.mode_combo.setCurrentIndex(page.mode_combo.findData("disabled"))
        self.assertTrue(page.advanced_frame.isHidden())

    # ------------------------------------------------------------------
    # 每个模型请求区都暴露连接测试
    # ------------------------------------------------------------------
    def test_every_model_request_area_exposes_a_connection_test(self) -> None:
        from wizard.app import SetupWizard

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)

        controls = (
            (wizard.llm_page.test_connection_btn, wizard.llm_page.connection_status),
            (
                wizard.backend_page.test_agent_connection_btn,
                wizard.backend_page.agent_connection_status,
            ),
            (wizard.tts_page.test_connection_btn, wizard.tts_page.connection_status),
            (
                wizard.vision_page.test_connection_btn,
                wizard.vision_page.connection_status,
            ),
        )
        for button, status in controls:
            with self.subTest(button=button.accessibleName()):
                self.assertTrue(button.text())
                self.assertTrue(button.accessibleName())
                self.assertTrue(status.accessibleName())

    # ------------------------------------------------------------------
    # 连接测试报告进度和结果且不阻塞 UI
    # ------------------------------------------------------------------
    def test_connection_test_reports_progress_and_result_without_blocking_ui(self) -> None:
        from wizard.app import SetupWizard
        from wizard.connection_test import ConnectionResult

        wizard = self._track(SetupWizard())
        self._stop_startup_work(wizard)
        future = Future()
        button = wizard.llm_page.test_connection_btn
        status = wizard.llm_page.connection_status

        def submit(coro):
            coro.close()
            return future

        with patch("meapet.async_runtime.submit", side_effect=submit):
            wizard._start_connection_test("direct", button, status)

        self.assertFalse(button.isEnabled())
        self.assertIn("正在测试", status.text())
        self.assertTrue(wizard._connection_test_jobs["direct"][1].isActive())

        future.set_result(ConnectionResult(True, "回复模型连接正常。"))
        wizard._poll_connection_test("direct")
        self.assertTrue(button.isEnabled())
        self.assertEqual(status.text(), "回复模型连接正常。")
        self.assertEqual(status.property("status"), "success")


class ConnectionProbeTests(unittest.IsolatedAsyncioTestCase):
    """连接探测测试（OpenAI 兼容）。"""

    async def test_direct_probe_uses_real_protocol_shape_with_a_small_reply(self) -> None:
        from meapet.direct.types import TextDelta
        from wizard.connection_test import probe_connection

        captured = {}

        class Client:
            async def stream(self, request):
                captured["request"] = request
                yield TextDelta("OK")

            async def close(self):
                captured["closed"] = True

        config = {
            "llm": {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                    "api_base": "https://models.example.test/v1",
                    "model": "reply-model",
                    "api_key": "secret",
                    "temperature": 0.7,
                    "max_tokens": 99999,
                },
            }
        }
        with patch(
            "meapet.direct.client.DirectProtocolClient",
            return_value=Client(),
        ):
            result = await probe_connection("direct", config)

        self.assertTrue(result.ok, result.message)
        self.assertLessEqual(captured["request"].max_tokens, 32)
        self.assertTrue(captured["closed"])

    async def test_vision_probe_uses_a_synthetic_image_not_a_screenshot(self) -> None:
        from meapet.direct.types import TextDelta
        from wizard.connection_test import probe_connection

        captured = {}

        class Client:
            async def stream(self, request):
                captured["request"] = request
                yield TextDelta("OK")

            async def close(self):
                pass

        config = {
            "llm": {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                    "api_base": "https://api.example.test/v1",
                    "model": "reply-model",
                    "api_key": "secret",
                },
            },
            "vision": {
                "mode": "inherit",
                "main_model_supports_images": True,
            },
        }
        with patch(
            "meapet.direct.client.DirectProtocolClient",
            return_value=Client(),
        ):
            result = await probe_connection("vision", config)

        self.assertTrue(result.ok, result.message)
        content = captured["request"].messages[-1]["content"]
        image = next(part for part in content if part["type"] == "image")
        self.assertEqual(image["media_type"], "image/png")
        self.assertGreater(len(image["data"]), 20)

    async def test_agent_probe_uses_native_websocket_capability_handshake(self) -> None:
        """Agent 探测调用原生 Gateway 的握手能力检查。"""
        from wizard.connection_test import probe_connection

        adapter = Mock()
        adapter.probe = AsyncMock(return_value=object())
        adapter.close = AsyncMock()

        config = {
            "llm": {
                "mode": "agent",
                "agent": {
                    "kind": "openclaw",
                    "base_url": "ws://127.0.0.1:18789",
                    "auth_token": "secret",
                    "session_key": "agent:main:meapet:test",
                    "timeout_seconds": 30,
                },
            }
        }
        with patch(
            "meapet.agent.factory.create_agent_adapter_from_config",
            return_value=adapter,
        ):
            result = await probe_connection("agent", config)

        self.assertTrue(result.ok)
        adapter.probe.assert_awaited_once_with()
        adapter.close.assert_awaited_once_with()

    async def test_tts_probe_synthesizes_a_short_sample(self) -> None:
        from wizard.connection_test import probe_connection

        tts = Mock()
        tts.enabled = True
        tts.speak_async = AsyncMock(
            return_value=("/tmp/connection-test.wav", "jp")
        )
        # 故意不带 llm.mode：回归 store.normalize_config 在缺 mode 时的 NameError
        config = {"tts": {"enabled": True, "engine": "mimo", "voice_lang": "jp"}}
        with patch("meapet.tts.service.MeaTTS", return_value=tts) as ctor:
            result = await probe_connection("tts", config)

        self.assertTrue(result.ok, result.message)
        ctor.assert_called_once()
        tts.speak_async.assert_awaited_once_with(
            "接続テスト",
            mood="neutral",
            language="jp",
        )


if __name__ == "__main__":
    unittest.main()
