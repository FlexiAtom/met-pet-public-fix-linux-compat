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

from meapet.config.normalizers import normalize_input_device_index
from meapet.log import get_color_logger
from meapet.paths import (
    VOICE_ASR_MODEL_FILES,
    find_voice_asr_model_dir,
    normalize_voice_asr_precision,
)

log = get_color_logger("voice")

# 识别器与采集共用的采样率：设备的可用性按它探测，向导文案也按它标注，别两处各写一个数。
VOICE_INPUT_SAMPLE_RATE = 16000


def _device_supports_input(p_audio, index: int, sample_rate: int, channels: int) -> bool:
    """这台设备能否按 (sample_rate, channels, paInt16) 开输入流。

    pyaudio 用 ``ValueError`` 表示"不支持"，实测与真开流的失败一致（hw 设备固定 48k 时
    16k 直接 Errno -9997）。探针本身出别的错（老版本、缺方法）时**按可用处理**——
    探测失败不该把设备判死，真开流时自会出声。
    """
    try:
        import pyaudio

        p_audio.is_format_supported(
            sample_rate,
            input_device=index,
            input_channels=channels,
            input_format=pyaudio.paInt16,
        )
    except ValueError:
        return False
    except Exception:
        return True
    return True


def list_input_devices(
    sample_rate: int = VOICE_INPUT_SAMPLE_RATE, channels: int = 1
) -> list[tuple[int, str, int, bool]]:
    """枚举有输入通道的设备 ``[(index, name, channels, 该采样率可用)]``。

    pyaudio 缺失或枚举异常时返回空表，不抛。
    """
    try:
        import pyaudio
    except ImportError:
        return []

    devices: list[tuple[int, str, int, bool]] = []
    audio = None
    try:
        audio = pyaudio.PyAudio()
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            try:
                input_channels = int(info.get("maxInputChannels", 0) or 0)
            except (TypeError, ValueError):
                input_channels = 0
            if input_channels > 0:
                devices.append(
                    (
                        index,
                        str(info.get("name") or f"设备 {index}"),
                        input_channels,
                        _device_supports_input(audio, index, sample_rate, channels),
                    )
                )
    except Exception as exc:
        log.warning(f"[voice] 设备枚举失败: {type(exc).__name__}: {exc}")
    finally:
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass
    return devices


class VoiceEngine(QThread):
    """后台录音 + 语音识别线程。"""

    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal()
    result_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        language: str = "zh",
        sample_rate: int = VOICE_INPUT_SAMPLE_RATE,
        channels: int = 1,
        chunk_ms: int = 30,
        precision: object = None,
        input_device_index: object = None,
    ):
        super().__init__()
        self._language = str(language or "zh").strip() or "zh"
        self._sample_rate = int(sample_rate or VOICE_INPUT_SAMPLE_RATE)
        self._channels = int(channels or 1)
        self._chunk_size = max(256, self._sample_rate * int(chunk_ms or 30) // 1000)
        self._precision = normalize_voice_asr_precision(precision)
        self._input_device_index = normalize_input_device_index(input_device_index)

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
            mic_idx = self._pick_mic_index(p)
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
            log.trace(lambda: traceback.format_exc())
            self.error.emit(f"语音识别异常: {exc}")

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    def _pick_mic_index(self, p_audio) -> int:
        """用户指定的设备优先；那台不可用时回落自动选路（出声，不闷掉）。

        "不可用"有两种，实测都撞到过，日志须分开点名：
        设备压根枚举不到/没有输入通道，或设备在但开不了 ``sample_rate``（hw 固定 48k
        的内置麦就是这一类，真开流报 Errno -9997）。
        """
        if self._input_device_index is None:
            return self._find_mic_device(p_audio)

        info = None
        try:
            info = p_audio.get_device_info_by_index(self._input_device_index)
        except Exception:
            info = None
        reason = ""
        if not info or int(info.get("maxInputChannels", 0) or 0) <= 0:
            reason = "枚举不到或没有输入通道"
        elif not _device_supports_input(
            p_audio, self._input_device_index, self._sample_rate, self._channels
        ):
            reason = f"不支持 {self._sample_rate} Hz 输入"

        if not reason:
            return self._input_device_index

        log.info(
            f"[voice] 指定设备 index={self._input_device_index} {reason}，回落自动选路"
        )
        return self._find_mic_device(p_audio)

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

            model_dir = find_voice_asr_model_dir(self._precision)
            if model_dir is None:
                self.error.emit(
                    f"语音识别模型缺失（{self._precision} 档），请在配置页下载"
                )
                return False
            files = VOICE_ASR_MODEL_FILES[self._precision]
            log.info(
                f"[voice] loading sherpa-onnx zipformer zh-en model ({self._precision})..."
            )
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                encoder=os.path.join(model_dir, files["encoder"]),
                decoder=os.path.join(model_dir, files["decoder"]),
                joiner=os.path.join(model_dir, files["joiner"]),
                tokens=os.path.join(model_dir, files["tokens"]),
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
            log.trace(lambda: traceback.format_exc())
            self.error.emit(f"转文字失败: {exc}")
            return ""


def create_voice_engine(config: Optional[dict] = None) -> Optional[VoiceEngine]:
    cfg = config or {}
    if not cfg.get("enabled", False):
        return None
    return VoiceEngine(
        language=cfg.get("language", "zh"),
        precision=cfg.get("precision"),
        input_device_index=cfg.get("input_device_index"),
    )
