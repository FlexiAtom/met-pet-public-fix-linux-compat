"""live2d-py 两代 API 的名字解析层（依赖闸门与渲染层共用同一份真值）。

为什么要这一层：live2d-py 0.7.x 与社区维护版 1.0.0 之间是**破坏性改名**，旧名不是废弃而是
根本不存在（包里没留别名层）。实测的失效形状是——装了 1.0.0 后 `live2d_widget` 的
`import live2d.v3` 抛 `ModuleNotFoundError`，被那句 `except Exception` 吞掉，安静退回 PNG，
日志还说「live2d package not installed」（这句在 1.0.0 装着时是假话）。

读数与证据：`~/.Athena/projects/meapet/reference/live2d-py-1.0.0-20261016/readouts.md`
（2026-10-16 两臂实测：0.7.0.4 本机那份 + 1.0.0 的 cp311-abi3 wheel 跑在 Python 3.12 上）。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass

#: 每格按候选名顺序解析，装哪一代都能跑。两代的名字互不重叠，顺序即优先级。
LIVE2D_API_NAMES = {
    "namespace": ("live2d.v3", "live2d"),
    "model": ("LAppModel", "Model"),
    "param": ("SetParameterValue", "SetParamById"),
    "blink": ("SetAutoBlinkEnable", "SetAutoBlink"),
    "breath": ("SetAutoBreathEnable", "SetAutoBreath"),
}


@dataclass(frozen=True)
class Live2DApi:
    #: 四格存的都是**名字**而非对象：模型类要 GL 上下文里才许构造，见 `_detect`。
    namespace: str
    model_class: str
    param_setter: str
    blink_setter: str
    breath_setter: str


def _import_namespace():
    """返回 (模块, 命名空间名, 最后一个异常)。

    顺序不能换成 `hasattr(live2d, "v3")` 试探：0.7 上没 import 过子模块时那个属性根本不存在
    （本机实测），一试就把好装的版本判成不可用。
    """
    last_error: Exception | None = None
    for name in LIVE2D_API_NAMES["namespace"]:
        try:
            return importlib.import_module(name), name, None
        except Exception as exc:  # 缺包、坏 wheel、原生扩展加载失败都按"这一代没装"处理
            last_error = exc
    return None, None, last_error


def _resolve(obj, key: str):
    """返回 `obj` 上 `LIVE2D_API_NAMES[key]` 里第一个可调用的名字，全都没有就返回 None。"""
    for name in LIVE2D_API_NAMES[key]:
        if callable(getattr(obj, name, None)):
            return name
    return None


def _detect(module, namespace):
    """解析本机那份 live2d-py ⇒ `(api|None, 对不上的那一格)`。

    只对模块与**类**做 getattr，绝不构造实例：1.0.0 的 `Model` 在没有 GL 上下文时构造直接
    段错误（本机实测 rc=139；0.7 的 `v3.Model()` 不段，这条坑是新版本带进来的）。探测路把
    进程打死，比名字没解析出来严重得多。
    """
    if module is None:
        return None, "package"
    model_class = _resolve(module, "model")
    if model_class is None:
        return None, "model"
    model_object = getattr(module, model_class)
    setters = {}
    for key in ("param", "blink", "breath"):
        name = _resolve(model_object, key)
        if name is None:
            return None, key
        setters[f"{key}_setter"] = name
    return Live2DApi(namespace, model_class, **setters), None


def _distribution_version():
    """发行包 `live2d` 的 `__version__`。0.7 的版本号挂在顶层包上，命名空间 `live2d.v3` 自己没有。"""
    try:
        return getattr(importlib.import_module("live2d"), "__version__", "未知")
    except Exception:
        return "未知"


def _unavailable_reason(missing_key, import_error, version) -> str:
    """把 `_detect` 回的那一格失败翻译成人话（模块导入时那行用的就是它）。

    分开写是为了让「装了但认不出来」和「没装」两种话各归各位：旧闸门只看到 import 得上就发
    门票，import 不上才说 "live2d package not installed"，于是 1.0.0 装着时它报的就是假话。
    """
    if missing_key is None:
        return ""
    if missing_key == "package":
        return (
            f"live2d 包 import 不上（试过 {list(LIVE2D_API_NAMES['namespace'])}）：{import_error}"
        )
    return (
        f"live2d-py 装了（version={version}），但它的符号面缺 "
        f"{LIVE2D_API_NAMES[missing_key]} 里的全部名字——两代 API 都不认识这份"
    )


live2d, _NAMESPACE, LIVE2D_IMPORT_ERROR = _import_namespace()
LIVE2D_API, LIVE2D_MISSING_KEY = _detect(live2d, _NAMESPACE)
#: 门票是"渲染层真正要用的四格符号凑齐了"，不是"模块 import 得上"——后者正是旧闸门的谎。
LIVE2D_AVAILABLE = LIVE2D_API is not None
LIVE2D_VERSION = _distribution_version() if live2d is not None else None
LIVE2D_UNAVAILABLE_REASON = _unavailable_reason(
    LIVE2D_MISSING_KEY, LIVE2D_IMPORT_ERROR, LIVE2D_VERSION
)
