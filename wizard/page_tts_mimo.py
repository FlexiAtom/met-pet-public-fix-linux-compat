"""TTS 配置页 mixin"""
from __future__ import annotations

import json
import os

from wizard.platform_info import CONFIG_PATH
from wizard.styles import set_status, styled_open_file

class TtsPageMimoMixin:
    @staticmethod
    def _mask_key(key: str) -> str:
        key = (key or "").strip()
        if not key:
            return ""
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}…{key[-4:]}"

    def _find_existing_mimo_credentials(self) -> dict:
        """
        按优先级查找已有 MiMo Key / Base。
        返回: {key, base, source}  source 为人话来源说明
        """
        candidates = []  # (priority, source, key, base)

        # 0) 本页已填写
        if hasattr(self, "mimo_api_key_input"):
            cur = self.mimo_api_key_input.text().strip()
            cur_base = ""
            if hasattr(self, "mimo_api_base_input"):
                cur_base = self.mimo_api_base_input.text().strip()
            if cur:
                candidates.append((0, "语音页已填写", cur, cur_base))

        # 1) 对话页是直连凭据的唯一编辑源。
        try:
            wiz = self.window()
            if (
                hasattr(wiz, "llm_page")
                and wiz.llm_page.get_backend() == "mimo"
            ):
                key = wiz.llm_page.direct_api_key_input.text().strip()
                base = wiz.llm_page.endpoint_input.text().strip()
                if key:
                    candidates.append((1, "对话配置页（MiMo）", key, base))
        except Exception:
            pass

        # 2) 当前向导实际使用的配置；不再固定读取项目根 config.json。
        try:
            wiz = self.window()
            cfg = getattr(wiz, "_existing_config", None)
            config_path = getattr(wiz, "config_path", CONFIG_PATH)
            if not isinstance(cfg, dict) and os.path.isfile(config_path):
                with open(config_path, "r", encoding="utf-8") as file:
                    cfg = json.load(file)
            if isinstance(cfg, dict):
                llm = cfg.get("llm", {}) or {}
                direct = llm.get("direct", {}) or {}
                tts = cfg.get("tts", {}) or {}
                if tts.get("api_key"):
                    candidates.append((
                        2,
                        "config.json → tts.api_key",
                        str(tts.get("api_key", "")).strip(),
                        str(tts.get("api_base") or "").strip(),
                    ))
                if (
                    direct.get("provider") == "mimo"
                    and direct.get("api_key")
                ):
                    candidates.append((
                        2,
                        "当前配置 → llm.direct.api_key（MiMo）",
                        str(direct.get("api_key", "")).strip(),
                        str(direct.get("api_base") or "").strip(),
                    ))
                elif (
                    llm.get("api_key")
                    and "mimo" in str(llm.get("api_base", "")).lower()
                ):
                    candidates.append((
                        3,
                        "当前配置 → llm.api_key（MiMo 地址）",
                        str(llm.get("api_key", "")).strip(),
                        str(llm.get("api_base") or "").strip(),
                    ))
        except Exception:
            pass

        # 3) 环境变量
        env_key = (
            os.environ.get("MIMO_API_KEY", "")
            or os.environ.get("XIAOMIMIMO_API_KEY", "")
            or ""
        ).strip()
        env_base = (
            os.environ.get("MIMO_API_BASE", "")
            or os.environ.get("XIAOMIMIMO_API_BASE", "")
            or ""
        ).strip()
        if env_key:
            candidates.append((4, "环境变量 MIMO_API_KEY", env_key, env_base))

        if not candidates:
            return {"key": "", "base": "", "source": ""}

        candidates.sort(key=lambda x: x[0])
        _prio, source, key, base = candidates[0]
        if not base:
            base = "https://api.xiaomimimo.com/v1"
        return {"key": key, "base": base, "source": source}

    def _refresh_mimo_key_status(self):
        """根据检测结果刷新状态文案。"""
        if not hasattr(self, "mimo_key_status"):
            return
        found = self._find_existing_mimo_credentials()
        typed = ""
        if hasattr(self, "mimo_api_key_input"):
            typed = self.mimo_api_key_input.text().strip()

        if typed:
            # 若当前输入来自检测结果，显示来源；否则显示「手动填写」
            if found.get("key") and typed == found.get("key"):
                set_status(
                    self.mimo_key_status,
                    "success",
                    f"已使用已有 Key（来源：{found.get('source')}，"
                    f"{self._mask_key(typed)}）",
                )
            else:
                set_status(
                    self.mimo_key_status,
                    "success",
                    f"Key 已填写（{self._mask_key(typed)}）",
                )
            return

        if found.get("key"):
            set_status(
                self.mimo_key_status,
                "success",
                f"检测到已有 MiMo Key（来源：{found.get('source')}，"
                f"{self._mask_key(found['key'])}），可点下方按钮填入",
            )
        else:
            set_status(
                self.mimo_key_status,
                "warning",
                "未检测到已有 MiMo Key（对话页 / config.json / 环境变量均无）。"
                "请手动填写。",
            )

    def _detect_and_fill_mimo_key(self, force: bool = False):
        """
        检测已有 MiMo Key 并填入输入框。
        force=False：仅当输入框为空时自动填入
        force=True：覆盖填入（用户点「重新检测」）
        """
        if not hasattr(self, "mimo_api_key_input"):
            return

        found = self._find_existing_mimo_credentials()
        key = found.get("key") or ""
        base = found.get("base") or "https://api.xiaomimimo.com/v1"
        source = found.get("source") or ""

        current = self.mimo_api_key_input.text().strip()
        if key and (force or not current):
            # force 时跳过「来源=语音页已填写」避免无意义重写
            if force and source == "语音页已填写":
                # 重新从外部源找（排除本页）
                # 临时清空再查
                old = self.mimo_api_key_input.text()
                self.mimo_api_key_input.blockSignals(True)
                self.mimo_api_key_input.setText("")
                self.mimo_api_key_input.blockSignals(False)
                found2 = self._find_existing_mimo_credentials()
                if found2.get("key"):
                    key, base, source = found2["key"], found2.get("base") or base, found2.get("source") or source
                    self.mimo_api_key_input.setText(key)
                else:
                    self.mimo_api_key_input.setText(old)
            else:
                self.mimo_api_key_input.setText(key)

            if base and (
                force
                or not self.mimo_api_base_input.text().strip()
                or self.mimo_api_base_input.text().strip() == "https://api.xiaomimimo.com/v1"
            ):
                self.mimo_api_base_input.setText(base)

            if source and source != "语音页已填写":
                self.log(f"已自动填入 MiMo Key（来源：{source}）")
        elif not key and force:
            self.log("未检测到对话页 / config.json / 环境变量中的 MiMo Key")

        self._refresh_mimo_key_status()

    # 兼容旧方法名
    def _fill_mimo_key_from_chat(self, silent: bool = False):
        self._detect_and_fill_mimo_key(force=not silent)

    def _browse_clone_ref(self):
        path = styled_open_file(
            self,
            "选择 voice-clone 参考音频",
            os.path.join(os.path.dirname(CONFIG_PATH), "voice_cache"),
            "Audio (*.wav *.mp3);;All (*.*)",
        )
        if path and hasattr(self, "mimo_clone_ref_input"):
            self.mimo_clone_ref_input.setText(path)
            if hasattr(self, "mimo_voiceclone_cb"):
                self.mimo_voiceclone_cb.setChecked(True)
