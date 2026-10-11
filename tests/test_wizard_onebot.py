"""OneBot（QQ 通道）配置页的回归契约。"""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
)

from meapet.onebot.config import (  # noqa: E402
    DEFAULT_ONEBOT,
    normalize_onebot_config,
)
from wizard.page_onebot import OneBotPage, format_int_list, parse_int_list  # noqa: E402


def fully_populated() -> dict:
    config = copy.deepcopy(DEFAULT_ONEBOT)
    config.update(
        {
            "enabled": True,
            "self_ids": [123456789, 987654321],
            "reply_mode": "both",
        }
    )
    config["forward_ws"].update(
        {
            "enabled": True,
            "url": "wss://napcat.example.test/onebot",
            "access_token": "$ONEBOT_FORWARD_TOKEN",
            "reconnect": False,
            "reconnect_initial_ms": 2500,
            "reconnect_max_ms": 40000,
        }
    )
    config["reverse_ws"].update(
        {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 6701,
            "path": "/onebot/v11/ws",
            "access_token": "reverse-token-value",
            "allow_insecure_remote": True,
        }
    )
    config["http_api"].update(
        {
            "enabled": True,
            "api_base": "https://napcat.example.test:3000",
            "access_token": "http-api-token-value",
            "timeout_s": 7.5,
        }
    )
    config["http_post"].update(
        {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 6721,
            "path": "/onebot/v11/http",
            "access_token": "http-post-token-value",
            "allow_insecure_remote": True,
        }
    )
    config["hub"].update(
        {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 6702,
            "ws_path": "/onebot/v11/hub",
            "http_path": "/onebot/v11/hub/http",
            "access_token": "hub-token-value",
            "publish_events": False,
            "allow_send": False,
            "allow_insecure_remote": True,
            "push_urls": ["http://127.0.0.1:9999/onebot/v11/http",
                          "https://consumer.example.test/hook"],
            "push_token": "push-token-value",
            "push_timeout_s": 7.5,
        }
    )
    config["allow"].update(
        {
            "private": False,
            "groups": True,
            "require_at": False,
            "users": [10001, 10002],
            "group_ids": [20001],
        }
    )
    config["queue"].update({"max_pending": 32, "turn_timeout_s": 95.5})
    return config


class OneBotPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = OneBotPage()
        self.addCleanup(self.page.deleteLater)

    # ------------------------------------------------------------------
    # 完全填满的配置往返等价
    # ------------------------------------------------------------------
    def test_round_trip_of_a_fully_populated_config_is_lossless(self) -> None:
        original = normalize_onebot_config(fully_populated())

        self.page.apply_config(fully_populated())
        collected = self.page.collect()

        self.assertEqual(
            normalize_onebot_config(collected),
            original,
        )
        self.assertIsInstance(collected["enabled"], bool)
        self.assertIsInstance(collected["self_ids"], list)
        self.assertTrue(all(isinstance(v, int) for v in collected["self_ids"]))
        self.assertIsInstance(collected["http_api"]["timeout_s"], float)
        self.assertIsInstance(collected["queue"]["turn_timeout_s"], float)
        self.assertIsInstance(collected["queue"]["max_pending"], int)

    # ------------------------------------------------------------------
    # None / {} / 错误类型都不能炸
    # ------------------------------------------------------------------
    def test_apply_config_tolerates_none_empty_and_wrong_types(self) -> None:
        for payload in (None, {}, [], "junk", 42):
            with self.subTest(payload=payload):
                self.page.apply_config(payload)  # type: ignore[arg-type]
                collected = self.page.collect()
                self.assertEqual(
                    normalize_onebot_config(collected),
                    normalize_onebot_config(DEFAULT_ONEBOT),
                )

        broken = {
            "enabled": "yes",
            "self_ids": "1, 2, junk",
            "reply_mode": "NOPE",
            "forward_ws": {
                "enabled": "true",
                "url": None,
                "access_token": 12,
                "reconnect": "off",
                "reconnect_initial_ms": "abc",
                "reconnect_max_ms": 10 ** 9,
            },
            "reverse_ws": "not-a-dict",
            "http_api": {"timeout_s": "NaN"},
            "http_post": {"port": "x"},
            "hub": {"publish_events": 0, "allow_send": None},
            "allow": {"private": [], "users": {"a": 1}, "group_ids": [3, "4"]},
            "queue": None,
        }
        self.page.apply_config(broken)
        collected = self.page.collect()
        self.assertTrue(collected["enabled"])
        # 字符串形式的 self_ids 与 normalize_onebot_config 一致地退化为空
        self.assertEqual(collected["self_ids"], [])
        self.assertEqual(collected["reply_mode"], "pet")
        self.assertEqual(collected["allow"]["group_ids"], [3, 4])
        self.assertIn("hub", collected)
        self.assertGreaterEqual(collected["queue"]["max_pending"], 1)

    # ------------------------------------------------------------------
    # 逗号分隔整数：忽略垃圾、去重、保序
    # ------------------------------------------------------------------
    def test_integer_lists_ignore_junk_deduplicate_and_keep_order(self) -> None:
        self.assertEqual(
            parse_int_list("300, 100, 300, abc, -5, 100, 200"),
            [300, 100, 200],
        )
        self.assertEqual(parse_int_list("１００，２００；３００"), [100, 200, 300])
        self.assertEqual(parse_int_list(""), [])
        self.assertEqual(parse_int_list(None), [])
        self.assertEqual(format_int_list([5, "5", "x", -1, 7]), "5, 7")
        self.assertEqual(format_int_list("5,7"), "")

        self.page.apply_config({"allow": {"users": [7, 7, 9, "oops", -3]}})
        self.assertEqual(self.page.collect()["allow"]["users"], [7, 9])
        self.page.apply_config({"self_ids": [1, 1, 2]})
        self.assertEqual(self.page.collect()["self_ids"], [1, 2])
        self.assertEqual(self.page.self_ids_input.text(), "1, 2")

        # 界面里手打的文本同样忽略垃圾、去重、保序
        self.page.allow_group_ids_input.setText("20001、20001; x 20002")
        self.assertEqual(
            self.page.collect()["allow"]["group_ids"], [20001, 20002]
        )

    # ------------------------------------------------------------------
    # 关闭开关只置灰，不丢值
    # ------------------------------------------------------------------
    def test_disabled_groups_grey_out_but_still_report_their_values(self) -> None:
        self.page.apply_config(fully_populated())

        self.assertTrue(self.page.forward_enabled.isChecked())
        self.assertTrue(self.page.forward_url.isEnabled())
        self.assertTrue(self.page.reverse_port.isEnabled())

        self.page.forward_enabled.setChecked(False)
        self.assertTrue(self.page.forward_url.isEnabled() is False)
        self.assertFalse(self.page.forward_token.isEnabled())
        # 其他组不受影响
        self.assertTrue(self.page.reverse_port.isEnabled())

        collected = self.page.collect()
        self.assertFalse(collected["forward_ws"]["enabled"])
        self.assertEqual(
            collected["forward_ws"]["url"],
            "wss://napcat.example.test/onebot",
        )

        # 总开关关闭时全部置灰，但取值仍然保留
        self.page.reverse_enabled.setChecked(False)
        self.page.enabled_cb.setChecked(False)
        self.assertFalse(self.page.reverse_port.isEnabled())
        self.assertFalse(self.page.allow_users_input.isEnabled())
        self.assertEqual(
            self.page.collect()["reverse_ws"]["host"],
            "0.0.0.0",
        )

    # ------------------------------------------------------------------
    # 令牌默认不回显明文，且能显示已配置/未配置
    # ------------------------------------------------------------------
    def test_tokens_are_masked_by_default_and_report_configuration_state(self) -> None:
        self.page.apply_config(fully_populated())

        for line, status in (
            (self.page.forward_token, self.page.forward_token_status),
            (self.page.reverse_token, self.page.reverse_token_status),
            (self.page.http_api_token, self.page.http_api_token_status),
            (self.page.http_post_token, self.page.http_post_token_status),
            (self.page.hub_token, self.page.hub_token_status),
        ):
            with self.subTest(line=line.accessibleName()):
                self.assertEqual(line.echoMode(), QLineEdit.Password)
                self.assertTrue(line.accessibleName())
                self.assertEqual(status.text(), "已配置")
                self.assertEqual(status.property("status"), "success")

        self.page.hub_token.clear()
        self.assertEqual(self.page.hub_token_status.text(), "未配置")

        self.page.forward_token_visibility.click()
        self.assertEqual(self.page.forward_token.echoMode(), QLineEdit.Normal)
        self.page.forward_token_visibility.click()
        self.assertEqual(self.page.forward_token.echoMode(), QLineEdit.Password)

    # ------------------------------------------------------------------
    # 所有下拉框都是 WheelSafeComboBox，且可访问名齐全
    # ------------------------------------------------------------------
    def test_page_uses_wheel_safe_combos_and_named_controls(self) -> None:
        from wizard.widgets import WheelSafeComboBox

        combos = self.page.findChildren(QComboBox)
        self.assertGreaterEqual(len(combos), 1)
        self.assertTrue(all(isinstance(c, WheelSafeComboBox) for c in combos))
        self.assertEqual(self.page.reply_mode_combo.count(), 3)
        self.assertEqual(
            [
                self.page.reply_mode_combo.itemData(i)
                for i in range(self.page.reply_mode_combo.count())
            ],
            ["pet", "hub", "both"],
        )

        missing = [
            widget.accessibleName()
            for widget in self.page.findChildren(QLineEdit)
            if not widget.accessibleName()
            and not isinstance(widget.parent(), QAbstractSpinBox)
        ]
        self.assertEqual(missing, [])
        for checkbox in self.page.findChildren(type(self.page.enabled_cb)):
            self.assertTrue(checkbox.accessibleName())

    # ------------------------------------------------------------------
    # collect 的形状与 schema 完全一致，且摘要不泄露令牌
    # ------------------------------------------------------------------
    def test_collect_matches_the_schema_shape(self) -> None:
        collected = self.page.collect()

        self.assertEqual(
            set(collected),
            {
                "enabled",
                "self_ids",
                "reply_mode",
                "forward_ws",
                "reverse_ws",
                "http_api",
                "http_post",
                "hub",
                "allow",
                "queue",
            },
        )
        self.assertEqual(
            set(collected["forward_ws"]),
            {
                "enabled",
                "url",
                "access_token",
                "reconnect",
                "reconnect_initial_ms",
                "reconnect_max_ms",
            },
        )
        self.assertEqual(
            set(collected["hub"]),
            {
                "enabled",
                "host",
                "port",
                "ws_path",
                "http_path",
                "access_token",
                "publish_events",
                "allow_send",
                "allow_insecure_remote",
                "push_urls",
                "push_token",
                "push_timeout_s",
            },
        )
        self.assertEqual(
            set(collected["allow"]),
            {"private", "groups", "require_at", "users", "group_ids"},
        )
        self.assertEqual(set(collected["queue"]), {"max_pending", "turn_timeout_s"})

        self.page.apply_config(fully_populated())
        summary = self.page.summary()
        self.assertIn("正向 WS", summary)
        for secret in (
            "$ONEBOT_FORWARD_TOKEN",
            "reverse-token-value",
            "http-api-token-value",
            "http-post-token-value",
            "hub-token-value",
        ):
            self.assertNotIn(secret, summary)


class OneBotWizardWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from wizard.app import SetupWizard

        self.wizard = SetupWizard()
        self.addCleanup(self._dispose)
        self.wizard._load_timer.stop()
        self.wizard.env_page._check_timer.stop()
        for timer in self.wizard.tts_page._startup_timers:
            timer.stop()
        # 用例必须与开发机本地 config.json 无关（本地可能已经启用了 OneBot 通道），
        # 否则断言会随个人的运行配置漂移。需要既有配置的用例自己显式设置。
        self.wizard._existing_config = {}
        self.wizard.onebot_page.apply_config({})

    def _dispose(self) -> None:
        self.wizard.close()
        self.wizard.deleteLater()
        QApplication.processEvents()

    # ------------------------------------------------------------------
    # 向导打开不报 KeyError，OneBot 标签就位
    # ------------------------------------------------------------------
    def test_wizard_opens_with_the_onebot_tab_registered(self) -> None:
        labels = [
            self.wizard.tabs.tabText(index)
            for index in range(self.wizard.tabs.count())
        ]
        # 不写死序号：上游会继续往中间插分类页（这个 PR 期间就插了 Live2D 与语音输入）
        self.assertEqual(self.wizard.TAB_ONEBOT, labels.index("OneBot"))
        self.assertEqual(labels.count("OneBot"), 1)
        self.assertEqual(self.wizard.tabs.tabText(self.wizard.TAB_ONEBOT), "OneBot")

        issues = self.wizard._configuration_issues()
        self.assertIn(self.wizard.TAB_ONEBOT, issues)
        # 默认关闭，不应产生阻塞保存的问题
        self.assertEqual(issues[self.wizard.TAB_ONEBOT], [])
        self.wizard._refresh_required_tabs()
        self.assertTrue(
            self.wizard.tabs.tabIcon(self.wizard.TAB_ONEBOT).isNull()
        )

    # ------------------------------------------------------------------
    # collect_config 深合并 onebot 段并保留扩展字段
    # ------------------------------------------------------------------
    def test_collect_config_deep_merges_the_onebot_section(self) -> None:
        self.wizard._existing_config = {
            "onebot": {
                "enabled": False,
                "custom_onebot_key": "keep-onebot",
                "queue": {"custom_queue_key": "keep-queue"},
            }
        }
        self.wizard.onebot_page.apply_config(fully_populated())

        config = self.wizard.collect_config()

        self.assertTrue(config["onebot"]["enabled"])
        self.assertEqual(config["onebot"]["reply_mode"], "both")
        self.assertEqual(config["onebot"]["custom_onebot_key"], "keep-onebot")
        self.assertEqual(
            config["onebot"]["queue"]["custom_queue_key"],
            "keep-queue",
        )
        self.assertEqual(
            normalize_onebot_config(config["onebot"]),
            normalize_onebot_config(fully_populated()),
        )

    # ------------------------------------------------------------------
    # 启用但没有任何通信方式时，OneBot 标签出现红点提示
    # ------------------------------------------------------------------
    def test_enabled_onebot_without_a_mode_marks_the_tab(self) -> None:
        self.wizard.onebot_page.enabled_cb.setChecked(True)
        self.wizard._refresh_required_tabs()

        issues = self.wizard._configuration_issues()
        self.assertTrue(issues[self.wizard.TAB_ONEBOT])
        self.assertFalse(
            self.wizard.tabs.tabIcon(self.wizard.TAB_ONEBOT).isNull()
        )

        self.wizard.onebot_page.forward_enabled.setChecked(True)
        self.wizard._refresh_required_tabs()
        self.assertEqual(
            self.wizard._configuration_issues()[self.wizard.TAB_ONEBOT],
            [],
        )

    # ------------------------------------------------------------------
    # 加载已有配置会回填 OneBot 页
    # ------------------------------------------------------------------
    def test_load_existing_config_restores_the_onebot_page(self) -> None:
        from unittest.mock import patch

        with (
            patch("wizard.app.os.path.isfile", return_value=True),
            patch.object(
                type(self.wizard),
                "_read_config_file",
                staticmethod(lambda path: {"onebot": fully_populated()}),
            ),
        ):
            self.wizard._load_existing_config()

        self.assertTrue(self.wizard.onebot_page.enabled_cb.isChecked())
        self.assertEqual(
            self.wizard.onebot_page.forward_url.text(),
            "wss://napcat.example.test/onebot",
        )
        self.assertEqual(
            self.wizard.onebot_page.collect()["self_ids"],
            [123456789, 987654321],
        )

    # ------------------------------------------------------------------
    # 保存会写出规范化后的 onebot 段
    # ------------------------------------------------------------------
    def test_save_writes_a_normalized_onebot_section(self) -> None:
        from unittest.mock import patch

        self.wizard.onebot_page.apply_config(fully_populated())
        with (
            patch.object(
                self.wizard,
                "_configuration_issues",
                return_value={index: [] for index in range(5)},
            ),
            patch("wizard.app.os.path.isfile", return_value=False),
            patch("meapet.config.store.save_config") as save,
            patch("wizard.app.styled_message_box"),
        ):
            self.wizard._save()

        self.assertTrue(save.called)
        saved = save.call_args.args[0]
        self.assertEqual(
            saved["onebot"],
            normalize_onebot_config(fully_populated()),
        )

    # ------------------------------------------------------------------
    # 保存之后**真的落盘、再读回来**
    # ------------------------------------------------------------------
    def test_save_persists_to_disk_and_reloads(self) -> None:
        """`_save` 的链是 `collect_config → normalize_config → save_config`。

        既有那条测试把 `save_config` **mock 掉了**，所以「UI 收集到的值**能不能真的
        落到磁盘再读回来**」从没被验过。实测两种坏法都能被这条测试抓住（都验过）：
        UI 收集到不可 JSON 化的值（元组 / set / Qt 枚举）时，MeaPet 的规范化函数会
        把它**静默清成默认值**（不是抛错）——也就是用户保存后**值悄悄没了**。

        这里不 mock `save_config`：先放一个真实文件当磁盘底稿（这样 `_read_config_file`
        走真实分支，不需要去 patch 全局的 `os.path.isfile`），保存后**逐键读回来核对**，
        重点看后加的字段（`hub.push_*` / `allow.*`）有没有在链路上被丢掉。
        """
        import json
        import shutil
        from unittest.mock import patch

        from meapet.config.store import load_config

        self.wizard.onebot_page.apply_config(fully_populated())
        scratch = ROOT / ".tmp" / "wizard-save-roundtrip"
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        path = scratch / "config.json"
        path.write_text("{}\n", encoding="utf-8")
        try:
            self.wizard.config_path = str(path)
            with (
                patch.object(
                    self.wizard,
                    "_configuration_issues",
                    return_value={index: [] for index in range(5)},
                ),
                patch("wizard.app.styled_message_box"),
            ):
                self.wizard._save()

            # 1) 真的写出了文件，而且是合法 JSON（这一步就会暴露不可序列化的值）
            self.assertTrue(path.exists(), "保存应当留下 config.json")
            raw = json.loads(path.read_text(encoding="utf-8"))
            expected = normalize_onebot_config(fully_populated())
            self.assertEqual(raw["onebot"], expected)

            # 2) 再走真实加载路径读回来，值必须一模一样
            loaded = load_config(str(path))
            self.assertEqual(loaded["onebot"], expected)

            # 3) 后加的字段逐项点名（防止"整体相等"掩盖某个键被丢）
            hub = loaded["onebot"]["hub"]
            self.assertEqual(hub["push_token"], expected["hub"]["push_token"])
            self.assertEqual(hub["push_urls"], expected["hub"]["push_urls"])
            self.assertEqual(hub["push_timeout_s"], expected["hub"]["push_timeout_s"])
            self.assertEqual(loaded["onebot"]["allow"], expected["allow"])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class OneBotTemplateSyncTests(unittest.TestCase):
    """`config.example.json` 是本仓库的**唯一模板**（见 AGENTS.md）。

    用户在向导里起步、或手抄一份配置时，看到的 OneBot 键就来自这里（向导本身也会
    用它当底稿）。新加一个配置项却忘了补模板，症状**不是报错**，而是"这个功能在模板
    和文档里都不存在"——正是插件侧 `cordis.patch.yml` 落后 schema 4 个键那个坑的同型问题
    （见 `docs/onebot-architecture.md` §13.2）。这条测试把它钉住。
    """

    @staticmethod
    def _dotted_keys(node: dict, prefix: str = "") -> set[str]:
        out: set[str] = set()
        for key, value in node.items():
            path = f"{prefix}{key}"
            out.add(path)
            if isinstance(value, dict):
                out |= OneBotTemplateSyncTests._dotted_keys(value, f"{path}.")
        return out

    def test_example_template_lists_every_onebot_key(self) -> None:
        import json

        template = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        section = template.get("onebot")
        self.assertIsNotNone(section, "config.example.json 应当有 onebot 段")

        template_keys = self._dotted_keys(section)
        schema_keys = self._dotted_keys(DEFAULT_ONEBOT)

        missing = sorted(schema_keys - template_keys)
        stale = sorted(template_keys - schema_keys)
        self.assertEqual(missing, [], f"模板缺少这些 OneBot 配置项：{missing}")
        self.assertEqual(stale, [], f"模板里这些 OneBot 项 schema 里没有（陈旧或拼错）：{stale}")


class OneBotProgressiveDisclosureTests(unittest.TestCase):
    """界面的**渐进披露**：未启用的通信方式整段折叠，"启用"入口必须始终可见。

    设计依据：`design-system/pages/wizard.md` → Progressive disclosure
    （TTS 页"引擎详细设置默认折叠"是同一条原则）。实测同一份配置下整页
    高度 **3546px → 1981px（−44%）**。

    这里钉住的都是"改错了用户会直接卡住"的点：
    1. 未启用 ⇒ 分区折叠，但**勾选框本身仍可见**（否则没法启用它）；
    2. 关掉总开关后再勾选某方式 ⇒ 分区必须出现（可见但置灰），不能"勾了没反应"；
    3. `reply_mode=hub` 时准入规则整段不生效（§6.1）⇒ 折叠它并留一句说明。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = OneBotPage()
        self.page.show()  # isVisible() 语义需要控件真的显示过
        self.addCleanup(self.page.deleteLater)

    def _visible(self) -> dict:
        return {
            name: frame.isVisible()
            for name, _checkbox, frame in self.page._mode_groups
        }

    def test_disabled_modes_are_folded_but_their_switches_stay_visible(self) -> None:
        self.page.apply_config({"enabled": True, "reply_mode": "hub"})
        self.app.processEvents()

        self.assertEqual(
            self._visible(),
            {name: False for name, _cb, _frame in self.page._mode_groups},
        )
        for name, checkbox, _frame in self.page._mode_groups:
            with self.subTest(mode=name):
                self.assertTrue(
                    checkbox.isVisible(),
                    f"{name} 的启用勾选框必须可见，否则用户无法启用它",
                )
                self.assertFalse(checkbox.isChecked())
                self.assertTrue(
                    checkbox.toolTip(),
                    "分区折叠后，说明文字要靠勾选框的 tooltip 保留",
                )

    def test_enabling_a_mode_reveals_only_that_section(self) -> None:
        self.page.apply_config(
            {"enabled": True, "reply_mode": "hub", "hub": {"enabled": True}}
        )
        self.app.processEvents()

        visible = self._visible()
        self.assertEqual([name for name, shown in visible.items() if shown], ["hub"])
        self.assertTrue(self.page.hub_port.isVisible())
        self.assertFalse(self.page.forward_url.isVisible())

    def test_switching_a_mode_on_while_master_is_off_still_shows_the_section(self) -> None:
        """关掉总开关后勾选某方式：分区要出现（置灰），不能"勾了什么都不显示"。"""
        self.page.apply_config({"enabled": False, "reply_mode": "hub"})
        self.page.hub_enabled.setChecked(True)
        self.app.processEvents()

        hub_frame = dict(
            (name, frame) for name, _cb, frame in self.page._mode_groups
        )["hub"]
        self.assertTrue(hub_frame.isVisible(), "分区应当出现")
        self.assertFalse(hub_frame.isEnabled(), "总开关关着 ⇒ 置灰（不生效）")
        self.assertFalse(self.page.hub_port.isEnabled())

    def test_allowlist_is_folded_exactly_when_it_cannot_apply(self) -> None:
        self.page.apply_config({"enabled": True, "reply_mode": "pet"})
        self.app.processEvents()
        self.assertTrue(self.page._allow_frame.isVisible())
        self.assertFalse(self.page.hub_allow_note.isVisible())

        self.page.reply_mode_combo.setCurrentIndex(
            self.page.reply_mode_combo.findData("hub")
        )
        self.app.processEvents()
        self.assertFalse(
            self.page._allow_frame.isVisible(),
            "hub 模式下桌宠不回复，准入规则一行都不生效，应折叠",
        )
        self.assertTrue(
            self.page.hub_allow_note.isVisible(),
            "折叠时必须留一句说明，否则用户会以为功能没了",
        )

    def test_folding_is_purely_visual_and_never_changes_values(self) -> None:
        self.page.apply_config(fully_populated())
        before = normalize_onebot_config(self.page.collect())

        for _name, checkbox, _frame in self.page._mode_groups:
            checkbox.setChecked(False)
        self.app.processEvents()

        after = normalize_onebot_config(self.page.collect())
        self.assertEqual(after["forward_ws"]["url"], before["forward_ws"]["url"])
        self.assertEqual(after["reverse_ws"]["port"], before["reverse_ws"]["port"])
        self.assertEqual(after["hub"]["push_urls"], before["hub"]["push_urls"])
        self.assertEqual(after["allow"], before["allow"])


if __name__ == "__main__":
    unittest.main()
