"""live2d-py 的符号面闸门：只认 1.0 这一代，别的代响亮拒绝，不发门票。

为什么只认一代：live2d-py 到 v0.8.0.9-alpha（2026-09-24）才加上 Linux 制品，其发布正文写着
「Support manylinux workflows and linux pypi binary builds」「Python cross-version compatibility
(python>=3.11)」；v1.0.0（2026-09-30）正文又写着「Removed the live2d.v3 and live2d.v2cpp
modules」「v2 and v3 models are now loaded uniformly using the live2d.Model class (some
interface names have changed)」。旧名不是废弃而是**根本不存在**（包里没有别名层），所以支持两代
等于替上游背一条长期兼容债。人工 2026-10-17 裁决：不背，安装面钉 `live2d-py>=1.0.0`，代码只走
1.0 那一代的名字。

那这道闸为什么还留着：它不为兼容服务，为**门票诚实**服务。旧判据只读 `import live2d` +
`__version__`，实测是假阳性——装了符号面不对的包时这两句照样成立，渲染层随后拿不到名字，安静退回
PNG，日志还说「live2d package not installed」（这句在包装着时是假话）。钉版本挡不住盘上已有的旧
wheel，也挡不住镜像源给了别的版本，所以这里逐格 getattr。

读数与证据：`~/.Athena/projects/meapet/reference/live2d-py-1.0.0-20261016/readouts.md`
（1.0.0 与 0.7.0.4 两臂的符号面、画布读数、反证七发都在里面；0.7 那一臂现在的正确结局是被这里拒掉）。
"""
from __future__ import annotations

import sys

#: 产品要求的 live2d-py 下限。安装面（`启动桌宠.bat`、向导「一键安装」、README）钉的是同一个数，
#: 别处不要再抄第二个数。
LIVE2D_PY_MIN = "1.0.0"

#: live2d-py 自己要的解释器下限（那颗轮子是 `cp311-abi3`）。`启动桌宠.bat` 兜底那条
#: 系统 Python 选路钉的也是这个数——那里写不了 PEP 508 marker，只能靠候选表自己守住。
LIVE2D_PY_PYTHON_MIN = (3, 11)

#: 渲染层无条件触碰的模块级名字。`Model` 只查在不在、**绝不构造**：1.0.0 的 `Model()` 在没有 GL
#: 上下文时直接段错误（本机实测 rc=139），探测路把进程打死比名字对不上严重得多。
MODULE_SYMBOLS = ("init", "glInit", "clearBuffer", "dispose", "MotionPriority", "Model")

#: `live2d.Model` 上无条件触碰的方法（构造与每帧鼠标跟随都要用）。
MODEL_SYMBOLS = ("LoadModelJson", "SetParamById", "SetAutoBlink", "SetAutoBreath")

try:
    import live2d
except Exception as exc:  # 没装、坏 wheel、原生扩展加载失败都落到这里
    live2d = None  # type: ignore[assignment]
    LIVE2D_IMPORT_ERROR: Exception | None = exc
else:
    LIVE2D_IMPORT_ERROR = None


def _missing_symbols():
    """返回缺的名字列表；全包 import 不上时返回 `None`（那是另一种话）。

    两档都要求**可调用**，一条规则：1.0.0 上这十个名字实测全为 callable
    （`~/.cache/live2d100-probe` 那份 1.0.0 wheel，Python 3.12 直读）。`getattr` 命中一个字符串
    常量不算能用。
    """
    if live2d is None:
        return None
    missing = [f"live2d.{name}" for name in MODULE_SYMBOLS
               if not callable(getattr(live2d, name, None))]
    model_class = getattr(live2d, "Model", None)
    if callable(model_class):
        missing += [f"live2d.Model.{name}" for name in MODEL_SYMBOLS
                    if not callable(getattr(model_class, name, None))]
    return missing


def _distribution_version() -> str | None:
    """发行包 `live2d` 的 `__version__`（挂在顶层包上）。读不到就说读不到，不猜；包 import 不上时是 None。"""
    return str(getattr(live2d, "__version__", "未知")) if live2d is not None else None


def _unavailable_reason(missing, import_error) -> str:
    """把闸门那次失败翻译成人话——「没装」与「装了但对不上」是两种话，各归各位。"""
    if missing is None:
        return (
            f"live2d 包 import 不上（产品要 live2d-py>={LIVE2D_PY_MIN}）："
            f"{type(import_error).__name__}: {import_error}"
        )
    python_note = ""
    if sys.version_info < LIVE2D_PY_PYTHON_MIN:
        floor = f"{LIVE2D_PY_PYTHON_MIN[0]}.{LIVE2D_PY_PYTHON_MIN[1]}"
        python_note = (
            f"；live2d-py 从 0.8 起要求 Python {floor}+，本机是 "
            f"{sys.version_info.major}.{sys.version_info.minor}"
        )
    return (
        f"live2d-py 装了（version={_distribution_version()}），但缺渲染层要用的 "
        f"{missing}——产品只认 {LIVE2D_PY_MIN} 那一代的名字"
        f"（1.0.0 删了 `live2d.v3`/`live2d.v2cpp`，旧名不是废弃而是不存在）{python_note}"
    )


LIVE2D_MISSING = _missing_symbols()
#: 门票是"渲染层要用的名字凑齐了"，不是"模块 import 得上"——后者正是旧闸门的谎。
LIVE2D_AVAILABLE = LIVE2D_MISSING is not None and not LIVE2D_MISSING
LIVE2D_VERSION = _distribution_version()
LIVE2D_UNAVAILABLE_REASON = "" if LIVE2D_AVAILABLE else _unavailable_reason(
    LIVE2D_MISSING, LIVE2D_IMPORT_ERROR
)
