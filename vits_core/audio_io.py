"""torchaudio 读音频的后端选择。

单独成文件（而不是并进 data_utils）的理由：data_utils 顶层 import torch，
主环境里没装 torch 就测不到这一层；本模块不含 torch，能直接跑回归测试。
"""


def load_audio(module, path, **kwargs):
    """优先 soundfile；返回 (waveform, sample_rate)。

    torchaudio 的 sox 扩展按上游 sox 14.4.2 的 ABI 编译，而 Arch 等发行版的
    ``libsox`` 实为 sox_ng —— 走到它是 SIGSEGV，不是可捕获的异常。
    """
    try:
        return module.load(path, backend="soundfile", **kwargs)
    except TypeError:  # 老 torchaudio 没有 backend 参数
        return module.load(path, **kwargs)
    except ValueError:  # 这个环境里没有 soundfile，只能回原路
        return module.load(path, **kwargs)
