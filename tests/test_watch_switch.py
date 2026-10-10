"""屏幕观察拨杆：状态要说真话，样式是「文字 + 拨杆」平铺一行。

出处是人工 2026-10-06 的现场报障：「识图与观察」下的「开启屏幕观察」无法用于
关闭观察。真因不在点击分支，而在两处：

1. ``normalize_config`` 在 ``vision.mode == "disabled"`` 时把 ``watcher.enabled``
   打回 False（观察依赖识图），而 ``vision.mode`` 只有向导写得住——菜单那对识图
   后端只改 ``vision.backend``。默认识图关闭 ⇒ 这个开关永远开不起来。
2. ``_toggle_watcher_enabled`` 拿**保存前**那个字典判断成败，而 ``_save_config``
   会把 ``self.config`` 整个换成规范化后的新字典 ⇒ 每次点都吐「已开启」气泡，
   菜单文字却永远是「开启屏幕观察」。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from meapet.config.store import normalize_config
from meapet.desktop import status_language
from meapet.desktop.config_bridge import PetConfigBridgeMixin
from meapet.desktop.watch_ctrl import PetWatcherMixin


# 默认识图关闭 —— 与仓库自带 config.json 同一形状，就是报障那一档
DISABLED_VISION = {
    "vision": {
        "mode": "disabled",
        "backend": "",
        "model": "qwen3.5:4b",
        "api_base": "https://api.openai.com/v1",
    },
    "llm": {"mode": "direct", "backend": "custom",
            "api_base": "https://api.openai.com/v1"},
    "watcher": {"enabled": False},
}

CLOUD_VISION = {
    "vision": {"mode": "relay", "backend": "mimo", "model": "mimo",
               "api_base": "https://api.xiaomimimo.com/v1"},
    "llm": {"mode": "direct", "backend": "custom",
            "api_base": "https://api.openai.com/v1"},
    "watcher": {"enabled": False, "allow_cloud": False},
}

# 识图关着、但后端已经选了云端：这一档下开观察必须先问到授权
CLOUD_DISABLED_VISION = {
    "vision": {"mode": "disabled", "backend": "mimo", "model": "mimo",
               "api_base": "https://api.xiaomimimo.com/v1"},
    "llm": {"mode": "direct", "backend": "custom",
            "api_base": "https://api.openai.com/v1"},
    "watcher": {"enabled": False, "allow_cloud": False},
}


class WatchSwitchHost(PetWatcherMixin, PetConfigBridgeMixin):
    """真 _toggle_watcher_enabled + 真 _save_config，只把窗口副作用记成读数。"""

    def __init__(self, config: dict, path):
        self.config = normalize_config(config)
        self._config_path = str(path)
        self.bubbles: list[str] = []
        self.timer_actions: list[str] = []
        self.watcher_reinits = 0
        self._watcher_timer = self

    def _show_bubble(self, text, _duration_ms=None):
        self.bubbles.append(text)

    def _start_watcher_timer(self):
        self.timer_actions.append("start")

    def stop(self):
        self.timer_actions.append("stop")

    def _disconnect_watcher_signals(self):
        pass

    def _init_watcher(self):
        self.watcher_reinits += 1


class ToggleTruthTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.mkdtemp(prefix="watch_switch_")
        self.path = Path(self.tmp) / "config.json"

    def _host(self, config=DISABLED_VISION):
        return WatchSwitchHost(config, self.path)

    def _disk(self):
        import json

        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_round_trip_from_default_disabled_vision(self) -> None:
        """默认识图关闭时，第一击必须真的开起来，第二击必须真的关掉。"""
        host = self._host()
        host._toggle_watcher_enabled()
        self.assertTrue(host.config["watcher"]["enabled"], "开——内存")
        self.assertTrue(self._disk()["watcher"]["enabled"], "开——落盘")
        self.assertEqual(self._disk()["vision"]["mode"], "relay",
                         "观察依赖识图：这一击顺手把识图打开到菜单那对后端已经表示的 relay 档")
        self.assertEqual(host.bubbles, [status_language.watcher_enabled_local()])
        self.assertEqual(host.timer_actions, ["start"])
        self.assertEqual(host.watcher_reinits, 1, "改了 vision 配置就得重建识图 worker")

        host.bubbles.clear()
        host.timer_actions.clear()
        host._toggle_watcher_enabled()
        self.assertFalse(host.config["watcher"]["enabled"], "关——内存")
        self.assertFalse(self._disk()["watcher"]["enabled"], "关——落盘")
        self.assertEqual(host.bubbles, [status_language.watcher_disabled()])
        self.assertEqual(host.timer_actions, ["stop"])

    def test_local_vision_keeps_its_mode_when_switching_off(self) -> None:
        host = self._host({**DISABLED_VISION,
                           "vision": {"mode": "relay", "backend": "ollama",
                                      "model": "qwen3.5:4b",
                                      "host": "http://127.0.0.1:11434"}})
        host._toggle_watcher_enabled()
        host._toggle_watcher_enabled()
        self.assertEqual(self._disk()["vision"]["mode"], "relay",
                         "关掉观察不该顺手把用户的识图一起关了")

    def test_bubble_matches_persisted_state_on_every_click(self) -> None:
        """谎报成功是报障本体：气泡说开启、配置却是关——逐击核对两边一致。"""
        host = self._host()
        for _ in range(5):
            host.bubbles.clear()
            host._toggle_watcher_enabled()
            said_on = bool(host.bubbles) and "已开启" in host.bubbles[0]
            self.assertEqual(
                said_on,
                bool(host.config["watcher"]["enabled"]),
                f"点击后的气泡与落盘状态不符：{host.bubbles}",
            )

    def test_cloud_consent_refused_changes_nothing(self) -> None:
        """识图本来关着、后端却是云端：不授权就什么都不动。"""
        host = self._host(CLOUD_DISABLED_VISION)
        with patch(
            "meapet.desktop.watch_ctrl.confirm_cloud_vision", return_value=False
        ) as confirm:
            host._toggle_watcher_enabled()
        self.assertEqual(confirm.call_args.kwargs["title"], "开启云端屏幕观察？")
        self.assertFalse(host.config["watcher"]["enabled"])
        self.assertEqual(host.config["vision"]["mode"], "disabled",
                         "没授权就不该把识图打开")
        self.assertFalse(self.path.exists(), "被拒的授权压根不该落盘")
        self.assertEqual(host.bubbles, [status_language.watcher_not_enabled()])
        self.assertEqual(host.timer_actions, [])

    def test_cloud_consent_granted_asks_and_records_allow_cloud(self) -> None:
        host = self._host(CLOUD_VISION)
        with patch(
            "meapet.desktop.watch_ctrl.confirm_cloud_vision", return_value=True
        ) as confirm:
            host._toggle_watcher_enabled()
            self.assertTrue(host.config["watcher"]["allow_cloud"])
            self.assertTrue(host.config["watcher"]["enabled"])
            self.assertEqual(confirm.call_args.kwargs["title"], "开启云端屏幕观察？")
            self.assertEqual(host.bubbles, [status_language.watcher_enabled_cloud()])

            # 已授权过：再开仍要确认，但话术换成「继续开启」
            host.bubbles.clear()
            host._toggle_watcher_enabled()
            host.bubbles.clear()
            host._toggle_watcher_enabled()
            self.assertEqual(confirm.call_args.kwargs["title"], "开启屏幕观察？")
            self.assertEqual(host.bubbles, [status_language.watcher_enabled_cloud()])


class SwitchPresentationTests(unittest.TestCase):
    """菜单项本身：文字恒定，状态交给拨杆。"""

    def _menu_host(self, watcher_enabled: bool):
        from PyQt5.QtWidgets import QWidget

        from meapet.desktop.dev_options import PetDevOptionsMixin
        from meapet.desktop.window_chrome import PetWindowChromeMixin

        class MenuHost(QWidget, PetWindowChromeMixin, PetDevOptionsMixin):
            def __init__(self):
                super().__init__()
                self.config = normalize_config({
                    "vision": {"mode": "relay", "backend": "ollama",
                               "model": "qwen3.5:4b"},
                    "llm": {"mode": "direct", "backend": "ollama"},
                    "watcher": {"enabled": watcher_enabled},
                })
                self._standby = False
                self._use_live2d = False

            def _safe_set_mood(self, _mood):
                pass

            def _set_vision_backend(self, _backend):
                pass

            def _set_vision_model(self, _model):
                pass

            def _toggle_watcher_enabled(self):
                pass

            def _toggle_standby(self):
                pass

            def _toggle_render_mode(self):
                pass

            def _toggle_fidus_enabled(self):
                pass

            def _toggle_click_feedback_source(self, _name):
                pass

            def _set_click_feedback_poetry_source(self, _source, _enabled):
                pass

            def _open_click_feedback_keywords(self, _name):
                pass

            def _open_click_feedback_weights(self):
                pass

            def _toggle_voice_input(self):
                pass

            def _open_size_dialog(self):
                pass

            def _show_volume_dialog(self):
                pass

            def _do_screen_watch(self, force=False):
                pass

            def _show_status_panel(self):
                pass

            def _show_timeline(self):
                pass

            def _reset_memory(self):
                pass

            def _reopen_setup_wizard(self):
                pass

            def _show_about(self):
                pass

            def _quit(self):
                pass

        return MenuHost()

    @staticmethod
    def _find_menu(menu, title):
        for action in menu.actions():
            child = action.menu()
            if child is None:
                continue
            if child.title() == title:
                return child
            found = SwitchPresentationTests._find_menu(child, title)
            if found is not None:
                return found
        return None

    def _watch_action(self, host):
        menu = host._build_context_menu()
        submenu = self._find_menu(menu, "识图与观察")
        self.assertIsNotNone(submenu)
        action = next(
            a for a in submenu.actions()
            if a.text() == status_language.menu_watch_toggle()
        )
        return menu, action

    def test_watch_item_text_is_state_free_and_checkable(self) -> None:
        from meapet.desktop.menu_window import SWITCH_ACTION_NAME

        self.assertEqual(status_language.menu_watch_toggle(), "是否开启屏幕观察")
        for enabled in (True, False):
            host = self._menu_host(enabled)
            self.addCleanup(host.deleteLater)
            _menu, action = self._watch_action(host)
            self.assertEqual(action.objectName(), SWITCH_ACTION_NAME)
            self.assertTrue(action.isCheckable())
            self.assertEqual(action.isChecked(), enabled,
                             "拨杆的位置就是唯一的状态读数，文字不再随状态换动词")

    def test_watch_item_keeps_the_capture_warning_in_tooltip(self) -> None:
        """换掉「（可能截屏）」这个后缀之后，截屏警告得活在 tooltip 里。"""
        host = self._menu_host(False)
        self.addCleanup(host.deleteLater)
        _menu, action = self._watch_action(host)
        self.assertIn("截取屏幕", action.toolTip())

    def test_menu_window_renders_one_switch_row_and_plain_rows_for_others(self) -> None:
        from PyQt5.QtWidgets import QLabel

        from meapet.desktop.menu_window import (
            PetMenuWindow,
            SWITCH_ACTION_NAME,
            SwitchControl,
        )

        host = self._menu_host(True)
        self.addCleanup(host.deleteLater)
        submenu = self._find_menu(host._build_context_menu(), "识图与观察")
        self.assertIsNotNone(submenu)
        window = PetMenuWindow(submenu, None)
        self.addCleanup(window.deleteLater)

        watch_button = next(
            button
            for button, action in window._action_buttons
            if action.objectName() == SWITCH_ACTION_NAME
        )
        pills = watch_button.findChildren(SwitchControl)
        self.assertEqual(len(pills), 1, "这一行要有拨杆")
        self.assertTrue(pills[0].isVisibleTo(watch_button))
        self.assertEqual(watch_button.text(), "", "文字交给行内 label，才铺得平")
        labels = [label.text() for label in watch_button.findChildren(QLabel)]
        self.assertEqual(labels, [status_language.menu_watch_toggle()])
        self.assertIn("已开启", watch_button.accessibleName())

        ordinary = next(
            button
            for button, action in window._action_buttons
            if action.objectName() != SWITCH_ACTION_NAME
        )
        self.assertEqual(ordinary.findChildren(SwitchControl), [],
                         "别的叶子项照旧是纯文字按钮")


if __name__ == "__main__":
    unittest.main()
