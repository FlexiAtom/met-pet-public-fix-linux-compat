"""TTS engine mixin (extracted from tts.py)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Optional

from meapet.config.normalizers import normalize_gsv_ref_language
from meapet.paths import project_root
from meapet.log import get_color_logger
from meapet.tts.common import (
    LANG_TTS,
    MOOD_TO_REF,
    hidden_subprocess_kwargs,
    resolve_external_python,
)

log = get_color_logger("tts")

# 人工 2026-10-12T14:07:28「要禁止并发」。推理子进程吃满 CPU/内存，两个一起跑会互相拖慢到
# 双双撞上 gsv_timeout（实测 14 字句在受压窗口 241.35 s / 240.38 s 被砍成静默）。VITS 那一路
# 早有 vits_runtime.py:37 的锁，GSV 这路此前一把都没有——所以排队，不是并行。
_GSV_INFER_LOCK = threading.Lock()

# 上游 ``TTS_infer_pack/TTS.py:1020`` 的 ``seed`` 默认 ``-1``，而 ``set_seed()``（:194-196）
# 把 -1 当场换成 ``random.randint(0, 2**32-1)`` ⇒ 同句连 6 发拿到 4 种时长
# （4.62/4.70/4.74/4.82）、12 个 wav md5 两两不同。人工同刻裁决「seed要改」：钉成定值。
# 不做成配置键——这一档要的就是"不再飘"，可调等于把飘留在配置里；
# 与 gsv_infer.py 里 text_split_method/batch_size 那批脚本内定值同档。
GSV_INFER_SEED = 0

_GSV_LANGUAGE_LABELS = {
    "jp": LANG_TTS,
    "zh": "中文",
    "en": "英文",
}


def _gsv_language_label(value: object) -> str:
    return _GSV_LANGUAGE_LABELS[normalize_gsv_ref_language(value)]


def _gsv_language_tag(value: object) -> str:
    return normalize_gsv_ref_language(value)


class TtsGsvMixin:
    _gsv_language_label = staticmethod(_gsv_language_label)
    _gsv_language_tag = staticmethod(_gsv_language_tag)

    def _speak_gsv(self, tts_text: str, output_wav: str, mood: str,
                    ref_wav: str, ref_text: str, ref_lang: str,
                    text_lang: str | None = None) -> Optional[tuple[str, str]]:
        """GPT-SoVITS backend inference.

        Requires a real external Python runtime. Never launches MeaPet.exe.
        """
        python_exe = resolve_external_python(getattr(self, "python_exe", None))
        if not python_exe:
            log.warning(
                "No real Python interpreter for GSV inference. "
                "Skipping local GSV; use in-process VITS or MiMo cloud TTS."
            )
            return None, ""
        self.python_exe = python_exe
        reference_language = _gsv_language_label(ref_lang)
        synthesis_language = _gsv_language_label(text_lang or ref_lang)
        # 获取参考音频
        payload = {
            "ref_wav": ref_wav,
            "prompt_text": ref_text,
            "prompt_language": reference_language,
            "text": tts_text,
            "text_language": synthesis_language,
            "gpt_path": self.gpt_path,
            "sovits_path": self.sovits_path,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "speed": self.speed,
            "sample_steps": self.sample_steps,
            "output_wav": output_wav,
            # 钉采样种子：不带这行上游就按 -1 每次随机（模块级 GSV_INFER_SEED 的注释给了实测散布）。
            "seed": GSV_INFER_SEED,
            # 根不靠解释器路径反推：conda env 在树外时，只有这一路能把根传进子进程。
            "gsv_root": self.gsv_root,
        }
        payload_json = json.dumps(payload, ensure_ascii=False)

        log.info(
            f"ref={os.path.basename(ref_wav)} "
            f"prompt_lang={reference_language} text_lang={synthesis_language}"
        )
        log.info(f"text_len={len(tts_text)} chars payload_size={len(payload_json)} bytes")

        try:
            cmd = [self.python_exe, self.infer_script]
            log.info("启动推理子进程…")

            # 禁止并发（人工 2026-10-12T14:07:28）：排队上限取同一条 gsv_timeout——排队本身
            # 也吃预算，等满了就放弃这一次并留理由，不把调用方（朗读也有 UI 线程上的入口）无限期挂住。
            queued_at = time.time()
            if not _GSV_INFER_LOCK.acquire(timeout=float(self.gsv_timeout)):
                log.warning(
                    f"GSV 推理已在进行，等满 {self.gsv_timeout:.0f}s 仍未排到 → "
                    "本次放弃（禁止并发）"
                )
                return None, ""
            try:
                t1 = time.time()
                proc = subprocess.run(
                    cmd,
                    input=payload_json.encode("utf-8"),
                    capture_output=True,
                    timeout=self.gsv_timeout,
                    cwd=project_root(),
                    **hidden_subprocess_kwargs(),
                )
            finally:
                _GSV_INFER_LOCK.release()
            waited = t1 - queued_at
            if waited > 1.0:
                log.info(f"排队等前一句 {waited:.1f}s")
            elapsed = time.time() - t1
            # 用 replace 忽略无法解码的字节（GPT-SoVITS 可能会输出 GBK 编码的中文日志）
            stdout_text = proc.stdout.decode('utf-8', errors='replace')
            stderr_text = proc.stderr.decode('utf-8', errors='replace')

            log.info(f"子进程返回 (rc={proc.returncode}, {elapsed:.1f}s)")
            if stderr_text.strip():
                log.warning(f"stderr chars={len(stderr_text.strip())}")
                log.trace(lambda: f"stderr [debug]: {stderr_text.strip()[-200:]}")

            if proc.returncode != 0:
                log.error(f"TTS subprocess failed: rc={proc.returncode}")
                if stderr_text.strip():
                    log.trace(lambda: f"stderr [debug]: {stderr_text[:300]}")
                return None, ""

            # 取最后一行非空 JSON
            lines = [l.strip() for l in stdout_text.split('\n') if l.strip()]
            if not lines:
                log.warning("TTS: 子进程无输出")
                return None, ""
            last_line = lines[-1]
            result = json.loads(last_line)

            if not result.get("ok"):
                err = result.get('error', 'unknown')
                # 原因要进 error 正文：只报长度时，用户看到的是"配好了却永远不出声"。
                log.error(f"TTS subprocess failed: {str(err)[:200]}")
                log.trace(lambda: f"TTS subprocess error [debug]: {err}")
                if result.get("captured"):
                    captured = str(result["captured"])
                    log.warning(f"captured chars={len(captured)}")
                    log.trace(lambda: f"captured [debug]: {captured[:300]}")
                return None, ""

            duration = result.get("duration", 0)
            result_lang = _gsv_language_tag(synthesis_language)
            log.info(f"SoVITS output: {os.path.basename(output_wav)} ({duration}s) lang={result_lang}")
            return output_wav, result_lang

        except subprocess.TimeoutExpired:
            # 超时的形状是"子进程还在干活"，不是"它坏了"：进度停在 audio_cache/_gsv_infer.log，
            # 那句话是这次量到的最远一步，别把它跟"启动即失败"混成同一条日志。
            log.error(
                f"TTS timeout ({self.gsv_timeout}s)——推理子进程仍在运行被截停，"
                f"走到哪一步看 audio_cache/_gsv_infer.log；句子更长或机器更慢就调 tts.timeout"
            )
            return None, ""
        except json.JSONDecodeError as e:
            log.error(f"TTS JSON parse error: {type(e).__name__}")
            if 'last_line' in locals():
                log.trace(lambda: f"  last_line [debug]: {last_line[:200]}")
            return None, ""
        except Exception as e:
            log.error(f"TTS subprocess error: {type(e).__name__}")
            import traceback
            log.trace(lambda: traceback.format_exc())
            return None, ""

    def _get_ref_paths(
        self,
        mood: str,
        voice_language: str = "",
    ) -> tuple:
        """
        根据情绪获取 (参考音频路径, 参考文本, 语言)
        返回 (wav_path, ref_text, lang) 或 (None, None, None)
        根据 voice_lang 选择对应语言的参考文件（zh_* / jp_*）
        """
        target_language = normalize_gsv_ref_language(
            voice_language or getattr(self, "voice_lang", "jp")
        )
        references = getattr(self, "reference_audios", {})
        mapped_reference = (
            references.get(target_language)
            if isinstance(references, dict)
            else None
        )
        mapped_text = ""
        reference_language = target_language
        if isinstance(mapped_reference, dict):
            explicit_ref = str(mapped_reference.get("path") or "").strip()
            mapped_text = str(mapped_reference.get("text") or "").strip()
        else:
            explicit_ref = str(mapped_reference or "").strip()

        # 旧单条配置仅在未指定本轮语言，或其语言与本轮一致时继续生效。
        if not explicit_ref:
            legacy_language = normalize_gsv_ref_language(
                getattr(self, "gsv_ref_lang", "jp")
            )
            if not voice_language or legacy_language == target_language:
                explicit_ref = str(
                    getattr(self, "gsv_ref_wav", "") or ""
                ).strip()
                reference_language = legacy_language

        if explicit_ref:
            if os.path.isfile(explicit_ref):
                sidecar_text = os.path.splitext(explicit_ref)[0] + ".txt"
                ref_text = mapped_text
                if not ref_text:
                    try:
                        with open(sidecar_text, "r", encoding="utf-8") as f:
                            ref_text = f.read().strip()
                    except FileNotFoundError:
                        log.warning(
                            "指定参考音频没有同名 .txt，将使用空参考文本: "
                            f"{os.path.basename(explicit_ref)}"
                        )
                    except OSError as exc:
                        log.warning(
                            "读取指定参考文本失败，将使用空参考文本: "
                            f"{type(exc).__name__}"
                        )
                return (
                    explicit_ref,
                    ref_text,
                    _gsv_language_label(reference_language),
                )
            log.warning(
                "指定参考音频不存在，回退按情绪自动选择: "
                f"{explicit_ref}"
            )

        ref_type = MOOD_TO_REF.get(mood, "normal")
        ref_folder = os.path.join(self.ref_dir, ref_type)

        # 与配置 voice_lang 对齐；缺省日语（本地 GSV 传统路径）
        vlang = target_language
        if vlang in ("zh", "cn", "zh-cn", "zh_cn", "chinese", "中文", "汉语"):
            prefixes = ("zh_", "cn_")
            lang_label = "中文"
        elif vlang in ("en", "eng", "english", "英文", "英语"):
            prefixes = ("en_",)
            lang_label = "英文"
        else:
            prefixes = ("jp_", "ja_")
            lang_label = LANG_TTS  # 日文

        def _scan(prefs: tuple[str, ...]):
            wav_file = None
            txt_file = None
            try:
                for f in os.listdir(ref_folder):
                    fl = f.lower()
                    if "~" in f:
                        continue
                    for p in prefs:
                        if fl.startswith(p) and fl.endswith(".wav"):
                            wav_file = os.path.join(ref_folder, f)
                        elif fl.startswith(p) and fl.endswith(".txt"):
                            txt_file = os.path.join(ref_folder, f)
            except FileNotFoundError:
                return None, None
            return wav_file, txt_file

        wav_file, txt_file = _scan(prefixes)

        # 只有旧调用（未显式指定本轮语言）允许跨语言回退。
        # 新对话协议会传 voice_language，此时宁可跳过语音，
        # 也不能把中文原文与日语参考音频错配。
        if (not wav_file or not txt_file) and not voice_language:
            if prefixes[0].startswith("zh") or prefixes[0].startswith("en"):
                fallback = ("jp_", "ja_")
                fallback_label = "日文"
            else:
                fallback = ("zh_", "cn_")
                fallback_label = "中文"
            alt_wav, alt_txt = _scan(fallback)
            if alt_wav and alt_txt:
                log.warning(
                    f"无 {prefixes[0]}* 参考，回退 {os.path.basename(alt_wav)}"
                )
                wav_file, txt_file = alt_wav, alt_txt
                lang_label = fallback_label

        # 与 VITS 那一档对齐（engines/vits.py:37 判到中文才 [ZH]，其余一律 [JA]）：
        # 本轮语言不是中文、而池里又没有这门语言的参考时，落 jp 档而不是把这句掐成静默——
        # 参考只决定音色，念什么由 payload 里另发的 text_language 决定（:57-65），
        # 跨语言克隆正是这一路的原生能力。随包池只有 zh_/jp_ 两档，英文句因此有声音。
        # 中文不进这一档：上面那条前人裁决仍然有效（宁可跳过，也不给中文原文配日文参考）。
        if (
            (not wav_file or not txt_file)
            and voice_language
            and target_language != "zh"
            and not prefixes[0].startswith("jp")
        ):
            alt_wav, alt_txt = _scan(("jp_", "ja_"))
            if alt_wav and alt_txt:
                log.warning(
                    f"无 {prefixes[0]}* 参考，整句非中文 → 落 jp 档 "
                    f"{os.path.basename(alt_wav)}"
                )
                wav_file, txt_file = alt_wav, alt_txt
                lang_label = LANG_TTS

        if not wav_file or not txt_file:
            if not os.path.isdir(ref_folder):
                log.warning(f"Ref folder not found: {ref_folder}")
            else:
                log.warning(f"No ref audio for mood={mood} type={ref_type}")
            return None, None, None

        with open(txt_file, "r", encoding="utf-8") as f:
            ref_text = f.read().strip()

        return wav_file, ref_text, lang_label

    def _clean_jp(self, text: str) -> str:
        """后处理日语文本，替换模型训练数据中不常见的粗俗/敏感词"""
        import re
        for bad, good in self.JP_CLEAN_MAP.items():
            text = text.replace(bad, good)
        # 压缩连续重复的 にゃ/ニャ（娇喘数据会导致模型哼唧而非清晰发音）
        text = re.sub(r'(にゃ){2,}', 'にゃ', text)
        text = re.sub(r'(ニャ){2,}', 'ニャ', text)
        return text
