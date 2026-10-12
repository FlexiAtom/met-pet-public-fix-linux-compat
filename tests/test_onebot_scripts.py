"""运维脚本里那些**纯判断函数**的测试。

这些脚本平时不进 CI 的覆盖范围，但它们会碰真实环境（真实桌宠、真实 NapCat、
真实 `config.json`），所以里面「放行/拒绝」的判断值得像产品代码一样钉住。
导入是安全的：这些脚本的副作用都在 `if __name__ == "__main__"` 里。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.onebot_bridge_e2e import pet_reply_mode_blocks_via_pet  # noqa: E402
from scripts.onebot_postinstall_check import (  # noqa: E402
    detect_active_profile,
    first_session_verdict,
)


class ViaPetGuardTests(unittest.TestCase):
    """`--via-pet` 会**用真实 config.json 启动真实桌宠**，所以必须先问一句。

    `reply_mode` 含 `pet` 时，注入的合成消息会被桌宠**真的回复**，而回复会经桌宠
    自己的上游（生产配置里就是真实 NapCat）发到注入时用的 `user_id` —— 那是一个
    真实 QQ 号。这条判断就是那道闸门，写松了就会真的发消息出去。
    """

    def test_blocks_everything_that_is_not_hub(self) -> None:
        """**fail-closed**：只有明确是 `hub` 才放行。"""
        for mode in ("pet", "both", "PET", " Both ", "both\n", "unknown-mode", "hubbish"):
            self.assertTrue(
                pet_reply_mode_blocks_via_pet(mode),
                f"reply_mode={mode!r} 必须被拒（非 hub 一律不放行）",
            )

    def test_allows_hub_including_casing_and_whitespace(self) -> None:
        for mode in ("hub", "HUB", " hub ", "Hub"):
            self.assertFalse(
                pet_reply_mode_blocks_via_pet(mode),
                f"reply_mode={mode!r} 应当放行",
            )

    def test_missing_value_is_treated_as_unset(self) -> None:
        # 规范里 reply_mode 一定存在（normalize 会补齐），这里只是把"没有值"
        # 和 "pet" 区分开，避免把 None 当成含 pet 而误伤。
        self.assertFalse(pet_reply_mode_blocks_via_pet(None))
        self.assertFalse(pet_reply_mode_blocks_via_pet(""))


class FirstSessionVerdictTests(unittest.TestCase):
    """重启后自检的"到底跑通过一轮没有"判据。

    钉住两件事：① 读数→结论的翻译要对；② **"还差你发一条消息"绝不能被算成故障** ——
    否则操作者会以为桥坏了，而实际上只是还没人给它发消息。
    """

    def test_disconnected_is_user_action_not_failure(self) -> None:
        lines, needs_action = first_session_verdict(
            {"client": {"connected": False}, "conversations": 0},
            state_file_exists=False,
        )
        self.assertTrue(needs_action)
        self.assertIn("桌宠", " ".join(lines))

    def test_connected_but_no_conversation_points_at_the_next_step(self) -> None:
        lines, needs_action = first_session_verdict(
            {
                "client": {"connected": True},
                "conversations": 0,
                "statePath": "C:\\home\\onebot-relay\\state.json",
            },
            state_file_exists=False,
        )
        self.assertTrue(needs_action)
        text = " ".join(lines)
        self.assertIn("还没有任何会话", text)
        self.assertIn("@", text)  # 提醒群聊要先 @（requireAt=true）

    def test_finished_round_reports_pass(self) -> None:
        lines, needs_action = first_session_verdict(
            {
                "client": {"connected": True},
                "conversations": 1,
                "pending": [],
                "statePath": "C:\\home\\onebot-relay\\state.json",
            },
            state_file_exists=True,
        )
        self.assertFalse(needs_action)
        text = " ".join(lines)
        self.assertIn("PASS", text)
        self.assertIn("1 个", text)
        self.assertIn("没有在途轮次", text)
        self.assertIn("已落盘", text)

    def test_pending_round_and_missing_state_file_are_reported(self) -> None:
        lines, needs_action = first_session_verdict(
            {
                "client": {"connected": True},
                "conversations": 3,
                "pending": [{"turn": 1}, {"turn": 2}],
                "statePath": "C:\\home\\onebot-relay\\state.json",
            },
            state_file_exists=False,
        )
        self.assertFalse(needs_action)
        text = " ".join(lines)
        self.assertIn("2 个在途轮次", text)
        self.assertIn("WARN", text)

    def test_missing_payload_yields_nothing(self) -> None:
        self.assertEqual(first_session_verdict(None, state_file_exists=False), ([], False))


class ActiveProfileDetectionTests(unittest.TestCase):
    """404 的常见成因之一：插件装是装了，但装进了**错的 profile**。

    实测踩过一次：插件在 `web`，harness 跑的却是 `desktop`，症状就是永远 404，
    而且看配置"哪都对"。判定本身要钉住，否则这条诊断会反过来误导人。
    """

    def setUp(self) -> None:
        import shutil

        self.root = ROOT / ".tmp" / "active-profile-test"
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / "profiles").mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def _make_profile(self, name: str, *, composed_at: float, plugin: bool) -> None:
        import os

        directory = self.root / "profiles" / name
        directory.mkdir(parents=True, exist_ok=True)
        composed = directory / "cordis.yml"
        composed.write_text("[]\n", encoding="utf-8")
        os.utime(composed, (composed_at, composed_at))
        if plugin:
            (directory / "node_modules" / "dsh-onebot-relay").mkdir(parents=True, exist_ok=True)

    def test_picks_the_most_recently_composed_profile(self) -> None:
        self._make_profile("web", composed_at=1_000.0, plugin=True)
        self._make_profile("desktop", composed_at=2_000.0, plugin=True)
        info = detect_active_profile(self.root, "dsh-onebot-relay")
        self.assertEqual(info["active"], "desktop")
        self.assertEqual(sorted(info["installed_in"]), ["desktop", "web"])
        self.assertTrue(info["installed"])

    def test_reports_the_mismatch_that_causes_a_404(self) -> None:
        # 今天的真实形状：插件在 web，活跃的是 desktop
        self._make_profile("web", composed_at=2_000.0, plugin=True)
        self._make_profile("desktop", composed_at=3_000.0, plugin=False)
        info = detect_active_profile(self.root, "dsh-onebot-relay")
        self.assertEqual(info["active"], "desktop")
        self.assertEqual(info["installed_in"], ["web"])
        self.assertFalse(info["installed"], "插件不在活跃 profile 里，installed 必须是 False")

    def test_reports_nothing_installed(self) -> None:
        self._make_profile("desktop", composed_at=1.0, plugin=False)
        info = detect_active_profile(self.root, "dsh-onebot-relay")
        self.assertEqual(info["active"], "desktop")
        self.assertEqual(info["installed_in"], [])
        self.assertFalse(info["installed"])

    def test_missing_profiles_dir_is_not_fatal(self) -> None:
        info = detect_active_profile(ROOT / ".tmp" / "no-such-dsh-home", "dsh-onebot-relay")
        self.assertEqual(info, {"active": "", "mtime": 0.0, "installed": False, "installed_in": []})


if __name__ == "__main__":
    unittest.main()
