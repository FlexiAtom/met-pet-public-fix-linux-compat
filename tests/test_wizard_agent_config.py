"""配置中心的 direct/Agent 互斥配置与截图范围。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QSignalSpy


class TestWizardConversationConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from wizard.app import SetupWizard

        self.wizard = SetupWizard()
        self.wizard._load_timer.stop()
        self.wizard.llm_page._status_timer.stop()
        self.wizard.env_page._check_timer.stop()
        for timer in self.wizard.tts_page._startup_timers:
            timer.stop()

    def tearDown(self):
        self.wizard.close()
        self.wizard.deleteLater()
        QApplication.processEvents()

    def test_direct_mode_saves_actual_protocol_endpoint_model_and_limits(self):
        page = self.wizard.llm_page
        self.wizard.backend_page.direct_radio.setChecked(True)
        page.set_backend("deepseek")
        page.set_protocol("openai_responses")
        page.endpoint_input.setText("https://models.example.test/v1")
        page.model_input.setText("custom-reply-model")
        page.temperature_input.setValue(0.35)
        page.max_tokens_input.setValue(2048)
        self.wizard.key_page_ds.key_input.setText("$CUSTOM_MODEL_KEY")

        config = self.wizard.collect_config()

        self.assertEqual(config["llm"]["mode"], "direct")
        self.assertEqual(
            config["llm"]["direct"],
            {
                "provider": "deepseek",
                "protocol": "openai_responses",
                "api_base": "https://models.example.test/v1",
                "host": "",
                "model": "custom-reply-model",
                "api_key": "$CUSTOM_MODEL_KEY",
                "temperature": 0.35,
                "max_tokens": 2048,
            },
        )
        self.assertEqual(config["llm"]["api_base"], "https://models.example.test/v1")
        self.assertEqual(config["llm"]["model"], "custom-reply-model")

    def test_agent_mode_preserves_direct_profile_and_collects_control_listener(self):
        self.wizard._existing_config = {
            "llm": {
                "mode": "direct",
                "direct": {
                    "provider": "mimo",
                    "protocol": "openai_chat",
                    "api_base": "https://saved.example/v1",
                    "host": "",
                    "model": "saved-model",
                    "api_key": "$SAVED_KEY",
                    "temperature": 0.2,
                    "max_tokens": 900,
                },
            }
        }
        page = self.wizard.backend_page
        page.agent_radio.setChecked(True)
        page.set_agent_kind("hermes")
        page.agent_base_url.setText("http://192.168.50.20:8642")
        page.agent_auth_token.setText("$HERMES_API_SERVER_KEY")
        page.agent_session_id.setText("session-a")
        page.agent_session_key.setText("memory-a")
        page.agent_history_turns.setValue(5)
        page.timeline_turns.setValue(9)
        page.control_enabled.setChecked(True)
        page.control_listen_host.setText("192.168.50.10")
        page.control_allowed_ip.setText("192.168.50.20")
        page.control_port.setValue(8765)
        page.control_auth_token.setText("$MEAPET_CONTROL_TOKEN")
        page.control_allow_http.setChecked(True)

        config = self.wizard.collect_config()

        self.assertEqual(config["llm"]["mode"], "agent")
        self.assertEqual(config["llm"]["agent"]["kind"], "hermes")
        self.assertEqual(
            config["llm"]["agent"]["base_url"],
            "http://192.168.50.20:8642",
        )
        self.assertEqual(config["llm"]["agent"]["history_turns"], 5)
        self.assertEqual(config["ui"]["timeline_turns"], 9)
        self.assertEqual(config["llm"]["direct"]["model"], "saved-model")
        self.assertEqual(
            config["agent_control"],
            {
                "enabled": True,
                "listen_host": "192.168.50.10",
                "port": 8765,
                "allowed_agent_ip": "192.168.50.20",
                "auth_token": "$MEAPET_CONTROL_TOKEN",
                "allow_insecure_http": True,
                "cert_file": "",
                "key_file": "",
                "ca_file": "",
            },
        )
        self.assertTrue(page.insecure_http_warning.isVisibleTo(page))

    def test_loading_agent_config_restores_mode_without_overwriting_inactive_direct(self):
        config = {
            "llm": {
                "mode": "agent",
                "backend": "hermes",
                "direct": {
                    "provider": "ollama",
                    "protocol": "ollama_chat",
                    "api_base": "",
                    "host": "http://10.0.0.2:11434",
                    "model": "local-model",
                    "api_key": "",
                    "temperature": 0.6,
                    "max_tokens": 700,
                },
                "agent": {
                    "kind": "hermes",
                    "base_url": "https://agent.example.test",
                    "auth_token": "$HERMES_API_SERVER_KEY",
                    "session_id": "resume-me",
                    "session_key": "memory-me",
                    "history_turns": 7,
                    "tls": {"verify": True, "ca_file": "agent-ca.pem"},
                },
            },
            "agent_control": {
                "enabled": False,
                "listen_host": "127.0.0.1",
                "port": 9000,
                "allowed_agent_ip": "127.0.0.1",
                "auth_token": "saved-control-token-value-long-enough",
                "allow_insecure_http": False,
                "cert_file": "server.pem",
                "key_file": "server-key.pem",
                "ca_file": "client-ca.pem",
            },
            "ui": {"timeline_turns": 7},
        }

        self.wizard.apply_conversation_config(config)
        collected = self.wizard.collect_config()

        self.assertTrue(self.wizard.backend_page.agent_radio.isChecked())
        self.assertEqual(collected["llm"]["agent"]["session_id"], "resume-me")
        self.assertEqual(collected["llm"]["direct"]["model"], "local-model")
        self.assertEqual(collected["agent_control"]["port"], 9000)
        self.assertEqual(self.wizard.backend_page.timeline_turns.value(), 7)
        self.assertEqual(collected["ui"]["timeline_turns"], 7)

    def test_openclaw_remote_plaintext_ws_requires_explicit_visible_opt_in(self):
        page = self.wizard.backend_page
        page.agent_radio.setChecked(True)
        page.set_agent_kind("openclaw")
        page.agent_base_url.setText("ws://192.168.50.20:18789")
        page.agent_auth_token.setText("$OPENCLAW_GATEWAY_TOKEN")

        self.assertFalse(page.agent_allow_insecure_ws.isChecked())
        self.assertFalse(page.insecure_ws_warning.isVisibleTo(page))

        page.agent_allow_insecure_ws.setChecked(True)
        config = self.wizard.collect_config()

        self.assertTrue(config["llm"]["agent"]["allow_insecure_ws"])
        self.assertTrue(page.insecure_ws_warning.isVisibleTo(page))

        self.wizard.apply_conversation_config(config)
        self.assertTrue(page.agent_allow_insecure_ws.isChecked())

    def test_control_token_can_be_revealed_copied_and_regenerated_before_save(self):
        from PyQt5.QtWidgets import QLineEdit

        page = self.wizard.backend_page
        page.control_auth_token.setText("old-control-token-value-long-enough")

        page._toggle_control_token_visibility()
        self.assertEqual(page.control_auth_token.echoMode(), QLineEdit.Normal)
        page._copy_control_token()
        self.assertEqual(
            QApplication.clipboard().text(),
            "old-control-token-value-long-enough",
        )
        page._regenerate_control_token()

        regenerated = page.control_auth_token.text()
        self.assertNotEqual(regenerated, "old-control-token-value-long-enough")
        self.assertGreaterEqual(len(regenerated), 43)

    def test_saving_emits_normalized_config_for_the_running_desktop(self):
        spy = QSignalSpy(self.wizard.config_saved)
        payload = {
            "llm": {
                "mode": "agent",
                "agent": {
                    "kind": "hermes",
                    "base_url": "http://127.0.0.1:8642",
                },
            }
        }

        with (
            unittest.mock.patch.object(
                self.wizard,
                "collect_config",
                return_value=payload,
            ),
            unittest.mock.patch("wizard.app.os.path.isfile", return_value=False),
            unittest.mock.patch("meapet.config.store.save_config"),
            unittest.mock.patch("wizard.app.QMessageBox.information"),
        ):
            self.wizard._save()

        self.assertEqual(len(spy), 1)
        emitted = spy[0][0]
        self.assertEqual(emitted["llm"]["mode"], "agent")
        self.assertIn("direct", emitted["llm"])


class TestWizardCaptureScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_screen_observer_collects_application_and_region_scope(self):
        from wizard.page_vision import VisionPage

        page = VisionPage()
        self.addCleanup(page.deleteLater)
        page.capture_scope_combo.setCurrentIndex(
            page.capture_scope_combo.findData("application")
        )
        page.capture_application_input.setText("Visual Studio Code")

        application = page.collect("ollama", {})["watcher"]["capture"]
        self.assertEqual(
            application,
            {
                "scope": "application",
                "region": None,
                "application": "Visual Studio Code",
            },
        )

        page.capture_scope_combo.setCurrentIndex(
            page.capture_scope_combo.findData("region")
        )
        page.capture_x.setValue(-120)
        page.capture_y.setValue(40)
        page.capture_width.setValue(1280)
        page.capture_height.setValue(720)
        region = page.collect("ollama", {})["watcher"]["capture"]
        self.assertEqual(
            region,
            {
                "scope": "region",
                "region": {
                    "x": -120,
                    "y": 40,
                    "width": 1280,
                    "height": 720,
                },
                "application": "",
            },
        )

    def test_vision_modes_are_explicit_and_inherit_never_saves_relay_backend(self):
        from wizard.page_vision import VisionPage

        page = VisionPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(
            [page.mode_combo.itemData(i) for i in range(page.mode_combo.count())],
            ["disabled", "inherit", "relay"],
        )

        page.mode_combo.setCurrentIndex(page.mode_combo.findData("inherit"))
        page.main_model_vision_cb.setChecked(True)
        inherited = page.collect(
            "custom",
            {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                },
            },
        )

        self.assertEqual(inherited["vision"]["mode"], "inherit")
        self.assertTrue(inherited["vision"]["main_model_supports_images"])
        self.assertEqual(inherited["vision"]["backend"], "")

        page.mode_combo.setCurrentIndex(page.mode_combo.findData("relay"))
        page.backend_combo.setCurrentIndex(page.backend_combo.findData("ollama"))
        relayed = page.collect("custom", {"mode": "direct"})
        self.assertEqual(relayed["vision"]["mode"], "relay")
        self.assertEqual(relayed["vision"]["backend"], "ollama")

    def test_apply_config_restores_inherit_capability_without_enabling_relay_fields(self):
        from wizard.page_vision import VisionPage

        page = VisionPage()
        self.addCleanup(page.deleteLater)
        page.advanced_toggle.setChecked(True)
        page.apply_config(
            {
                "mode": "inherit",
                "main_model_supports_images": True,
                "backend": "mimo",
            },
            {"enabled": True},
        )

        self.assertEqual(page.mode_combo.currentData(), "inherit")
        self.assertTrue(page.main_model_vision_cb.isChecked())
        self.assertTrue(page.backend_combo.isHidden())


if __name__ == "__main__":
    unittest.main()
