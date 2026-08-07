"""
语音输入引擎 — 录音 + sherpa-onnx 离线转文字。

点击切换模式：第一次点击开始录音（后台加载模型），第二次点击停止 → 转文字 → 发射结果。
默认关闭，需在 config 中 voice_input.enabled=true 才初始化。

模型来源：ModelScope pkufool/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20
"""
from __future__ import annotations

import os
import traceback
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from meapet.log import get_color_logger
from meapet.paths import find_voice_asr_model_dir

log = get_color_logger("voice")


class VoiceEngine(QThread):
    """后台录音 + 语音识别线程。"""

    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal()
    result_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        language: str = "zh",
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 30,
    ):
        super().__init__()
        self._language = str(language or "zh").strip() or "zh"
        self._sample_rate = int(sample_rate or 16000)
        self._channels = int(channels or 1)
        self._chunk_size = max(256, self._sample_rate * int(chunk_ms or 30) // 1000)

        self._stop = False
        self._toggle = False
        self._recognizer = None
        self._audio_frames: list[bytes] = []

    def toggle(self) -> bool:
        if self.isRunning():
            self._toggle = True
            return True
        self._toggle = False
        self._stop = False
        self.start()
        return True

    def stop(self, timeout_ms: int = 0) -> bool:
        self._stop = True
        self._toggle = True
        if not self.isRunning():
            return True
        try:
            to = max(0, int(timeout_ms or 0))
        except (TypeError, ValueError):
            to = 0
        if to <= 0:
            return False
        return bool(self.wait(to))

    # ------------------------------------------------------------------
    # 线程主循环（录音 + 识别）
    # ------------------------------------------------------------------

    def run(self):
        try:
            if not self._load_model():
                return

            import pyaudio

            self._audio_frames = []
            p = pyaudio.PyAudio()
            mic_idx = self._find_mic_device(p)
            log.info(f"[voice] using mic device index={mic_idx}")

            stream = p.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._sample_rate,
                input=True,
                input_device_index=mic_idx,
                frames_per_buffer=self._chunk_size,
            )
            self.recording_started.emit()
            log.info("[voice] recording started")

            while not self._stop:
                try:
                    data = stream.read(self._chunk_size, exception_on_overflow=False)
                except Exception:
                    continue
                self._audio_frames.append(data)
                if self._toggle:
                    break

            stream.stop_stream()
            stream.close()
            p.terminate()
            self.recording_stopped.emit()
            log.info(f"[voice] recording stopped, frames={len(self._audio_frames)}")

            if self._stop:
                return

            text = self._transcribe()
            if text:
                self.result_ready.emit(text)

        except ImportError:
            self.error.emit("pyaudio 未安装，请在配置页下载依赖")
        except Exception as exc:
            log.error(f"[voice] error: {type(exc).__name__}: {exc}")
            log.track(lambda: traceback.format_exc())
            self.error.emit(f"语音识别异常: {exc}")

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    def _find_mic_device(self, p_audio) -> int:
        """找真正的麦克风，跳过立体声混音/扬声器回录等设备。"""
        exclude = {"立体声混音", "stereo mix", "扬声器", "speaker",
                    "声音映射器", "mapper", "主声音捕获", "主声音"}
        default = p_audio.get_default_input_device_info()
        default_name = (default.get("name") or "").lower()
        excluded = any(kw in default_name for kw in exclude)
        if not excluded:
            return default.get("index")

        # 默认设备是立体声混音等，遍历找真正的麦克风
        for i in range(p_audio.get_device_count()):
            info = p_audio.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) <= 0:
                continue
            name = (info.get("name") or "").lower()
            if not any(kw in name for kw in exclude):
                return i

        return default.get("index")  # fallback

    def _load_model(self) -> bool:
        if self._recognizer is not None:
            return True

        try:
            import sherpa_onnx

            model_dir = find_voice_asr_model_dir()
            if model_dir is None:
                self.error.emit("语音识别模型缺失，请在配置页下载")
                return False
            log.info("[voice] loading sherpa-onnx zipformer zh-en model...")
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                encoder=os.path.join(model_dir, "encoder-epoch-99-avg-1.int8.onnx"),
                decoder=os.path.join(model_dir, "decoder-epoch-99-avg-1.int8.onnx"),
                joiner=os.path.join(model_dir, "joiner-epoch-99-avg-1.int8.onnx"),
                tokens=os.path.join(model_dir, "tokens.txt"),
                num_threads=4,
                provider="cpu",
                sample_rate=self._sample_rate,
                feature_dim=80,
                decoding_method="greedy_search",
            )
            log.info("[voice] model loaded successfully")
            return True

        except ImportError:
            self.error.emit("sherpa-onnx 未安装，请在配置页下载依赖")
            return False
        except Exception as exc:
            self.error.emit(f"模型加载失败: {exc}")
            log.error(f"[voice] failed to load model: {exc}")
            return False

    # ------------------------------------------------------------------
    # 转文字
    # ------------------------------------------------------------------

    def _transcribe(self) -> str:
        if not self._audio_frames or self._recognizer is None:
            return ""

        audio_data = b"".join(self._audio_frames)
        total_samples = len(audio_data) // 2
        if total_samples < self._sample_rate // 4:
            log.warning("[voice] audio too short, skipping")
            return ""

        try:
            import numpy as np

            samples = (
                np.frombuffer(audio_data, dtype=np.int16)
                .astype(np.float32)
                / 32768.0
            )

            # 流式模型：全量喂入 + input_finished，不补额外静音（避免复读）
            stream = self._recognizer.create_stream()
            stream.accept_waveform(self._sample_rate, samples)
            stream.input_finished()
            while self._recognizer.is_ready(stream):
                self._recognizer.decode_stream(stream)

            result = self._recognizer.get_result(stream).strip()
            # 清理 SIL token（\b 对中文无效，直接用简单替换）
            import re
            result = re.sub(r'\s*SIL\s*', '', result).strip()

            log.info(
                f"[voice] transcribed: "
                f"'{result[:80]}{'...' if len(result) > 80 else ''}'"
            )
            return result

        except Exception as exc:
            log.error(f"[voice] transcription error: {exc}")
            log.track(lambda: traceback.format_exc())
            self.error.emit(f"转文字失败: {exc}")
            return ""


def create_voice_engine(config: Optional[dict] = None) -> Optional[VoiceEngine]:
    cfg = config or {}
    if not cfg.get("enabled", False):
        return None
    return VoiceEngine(
        language=cfg.get("language", "zh"),
    )
