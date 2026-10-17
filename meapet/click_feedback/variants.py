"""点击反馈的文本变体处理：繁简归一 ＋ 命中校验。

两件事分开做，是因为实测把它们各自钉死了：

* **繁简归一**——Wikisource 存的是繁體（``牀前看月光``），而用户可见那一档已由人工裁为
  **仅简体**（``2026-10-10T11:03:34``「可以仅简体，繁体在底层留支持」）：查询侧要能把简体
  改写成繁體再打过去，返回侧要能归一回简体。
* **命中校验**——同一接口 ``srsearch=床前明月光`` 实测命中的是**不相干的《四季歌》**
  （``snippet=打得鴛鴦各一方``）。那不只是繁简：今版第二字是「明」，古籍本作「看」，属**版本**
  差异，**任何字符映射都救不了它**。所以"查得到"不等于"查对了"，得由命中校验把关，
  不过就换源/回退——总之不把《四季歌》当《静夜思》念出来。

``opencc`` 只挂在 ``vits`` 那组 extras 上，产品主链不依赖它：读不到就按恒等处理，
命中校验照常生效（真正拦住"念错诗"的是它，不是映射）。转换器**首次用到时才载**——
两个新源默认关，不该为一条没人走的链付一次导入。
"""
from __future__ import annotations

import re

_MIN_RUN = 3
_STRIP = re.compile(r"[^0-9A-Za-z\u3400-\u9FFF\uF900-\uFAFF]+")

_converters: dict = {}
_converters_loaded = False


def _converter(direction: str):
    global _converters_loaded
    if not _converters_loaded:
        _converters_loaded = True
        try:
            import opencc

            _converters["t2s"] = opencc.OpenCC("t2s")
            _converters["s2t"] = opencc.OpenCC("s2t")
        except Exception:
            _converters.clear()
    return _converters.get(direction)


def _convert(text: str, direction: str) -> str:
    converter = _converter(direction)
    if converter is None:
        return text
    try:
        return str(converter.convert(text))
    except Exception:
        return text


def to_simplified(text: object) -> str:
    """归一成简体；转换器缺席时原样返回（不谎报"已归一"）。"""
    raw = str(text or "")
    return _convert(raw, "t2s") if raw else raw


def to_traditional(text: object) -> str:
    """把简体查询改写成繁體变体，供检索型古籍库使用。"""
    raw = str(text or "")
    return _convert(raw, "s2t") if raw else raw


def compact(text: object) -> str:
    """比对用的形状：去过标点空白，并过一遍简体归一。"""
    return _STRIP.sub("", to_simplified(text))


def is_hit(query: object, title: object, snippet: object, *, min_run: int = _MIN_RUN) -> bool:
    """检索回来的东西是否真是查询要的那一首／那一个词条。

    判据取**连续片段**而非字符集合重合：古诗里月、山、人这类高频字会让集合判据大量误判，
    而实测那条不相干的《四季歌》正是"没有任何连得上的片段"才被拦下的。所需长度取
    ``min(min_run, len(查询))``——**不往更短降级**：一个单字重合就把《四季歌》放行，等于没这道闸。

    代价要说在前面：查询是一**句诗**而非诗题时，古籍本与今版相差一字（「明月光」对「看月光」）
    就连不出三字片段，于是**正确的命中也会被拒**并回退到随机那一档。宁可少给一句，不可念错一首。
    """
    needle = compact(query)
    if not needle:
        return False
    haystacks = [hay for hay in (compact(title), compact(snippet)) if hay]
    if not haystacks:
        return False
    size = min(len(needle), max(1, int(min_run)))
    return any(
        needle[start : start + size] in hay for start in range(0, len(needle) - size + 1) for hay in haystacks
    )


def query_variants(query: object) -> list[str]:
    """检索型古籍库该按什么顺序打：原样在前，繁體改写在后（去重）。

    没有转换器时只剩原样那一条——那是"降级但如实"，不是"改写失败静默当成成功"。
    """
    raw = str(query or "").strip()
    if not raw:
        return []
    variants = [raw]
    converted = to_traditional(raw)
    if converted and converted not in variants:
        variants.append(converted)
    return variants
