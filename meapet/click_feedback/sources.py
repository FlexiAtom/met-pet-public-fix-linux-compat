"""点击反馈的内容源：把「一次点击要多长时间、失败说些什么」钉在同一个地方。

三条链各自的形状来自实测，不是设计出来的：

* **Tavily**（爬虫）——免 key 档 ``X-Tavily-Access-Mode: keyless`` 开箱可用，key 非空时发
  Bearer；**无效 key 直接算失败**，不悄悄退回 keyless（那是把用户填错当成没填）。
* **hitokoto 一言**（古诗词，免 key）——只给随机一句，不接受关键词。
* **Wikisource MediaWiki**（古诗词，免 key，可检索）——库里存的是繁體且与今版有用字差异，
  所以检索前改写在繁、回来后用命中校验验「是不是那一首」，两格都在 ``variants.py``。

超时必须分两层：httpx 的 ``timeout=`` 只管**每次操作**，实测一个每秒滴一口的响应能在
7.25 s 之后照样报 200，所以「点一下等多久」得另有一条端到端上限；而端到端上限**跨档、跨换源共用**
——逐地址回退三档 × 换源两档若各给 8 s，一次点击能拖到半分钟，那就不是回退而是卡顿。

地址族不能写死一族：本机 IPv6 出口不稳，同一域名 v4 通 / v6 挂与 v4 挂 / v6 通**按轮翻转**
（案文 2026-10-10 那两轮读数）。所以取 **auto → 钉 ipv4 → 钉 ipv6** 的逐档回退，全挂再换源。
"""
from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

import httpx

from meapet import USER_AGENT
from meapet.click_feedback.variants import is_hit, query_variants, to_simplified
from meapet.http_async import get_client

#: 每一档（auto / ipv4 / ipv6）的**每次操作**上限。
REQUEST_TIMEOUT_SECONDS = 3.0
#: 一次取文的**端到端**硬上限，跨档、跨换源共用同一条线。
TOTAL_BUDGET_SECONDS = 8.0
#: httpcore 的逃生口：``0.0.0.0`` 把 socket 建成 AF_INET，``::`` 建成 AF_INET6。
FAMILY_BIND = {"ipv4": "0.0.0.0", "ipv6": "::"}
#: 逐地址回退的顺序，``auto`` 走产品那个共享客户端（由系统双栈挑）。
FAMILY_LADDER = ("auto", "ipv4", "ipv6")

#: 交给下游的最大条数：一次点击用一条，多留两条是给「这条不合口味想换一条」的余地，
#: 不是把整页搜索结果搬进气泡。
MAX_ITEMS = 3
#: 单条文本截断长度。正文页动辄几千字，气泡放不下，LLM 一次性生成也不必读整页。
ITEM_CHAR_LIMIT = 240

#: 端点全部来自案文实测，不是猜的（隐私口径：不猜 URL）。
TAVILY_URL = "https://api.tavily.com/search"
HITOKOTO_URL = "https://v1.hitokoto.cn/?c=i&encode=json"
WIKISOURCE_URL = "https://zh.wikisource.org/w/api.php"

_HITOKOTO_CATEGORY = "i"  # 案文实测：c=i 即诗词类目，返回本就是简体
_WIKISOURCE_SEARCH_CHARS = 120


class SourceUnavailable(RuntimeError):
    """这一条源在这一取回里没给到可用的文本。

    ``detail`` 是给日志看的那一句：带上了逐档的失败形状，因为「连不上」「超时」「内容是空」
    「命中校验没过」在案文里是四种不同的证据，合成一句「失败」就没法事后归因。
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Retrieved:
    """一条取回的文本，已经过用户可见那一档的简体归一。"""

    text: str
    source: str
    attribution: str = ""


class Deadline:
    """一次点击的共用预算。第一次使用时才绑到当前运行中的 loop 上。"""

    def __init__(self, seconds: float = TOTAL_BUDGET_SECONDS):
        self._seconds = max(0.0, float(seconds))
        self._end: Optional[float] = None

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def remaining(self) -> float:
        if self._end is None:
            self._end = self._now() + self._seconds
        return self._end - self._now()

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def attempt_timeout(self) -> float:
        return max(0.0, min(REQUEST_TIMEOUT_SECONDS, self.remaining()))


def _visible(text: object) -> str:
    """用户可见那一档只做简体（人工 2026-10-10T11:03:34 已裁）。"""
    return " ".join(str(to_simplified(text) or "").split())


def _trimmed(text: str) -> str:
    return text if len(text) <= ITEM_CHAR_LIMIT else text[:ITEM_CHAR_LIMIT].rstrip() + "…"


def _pinned_client(family: str, timeout: float) -> httpx.AsyncClient:
    """钉死地址族的那一发：TLS 上下文与 UA 照共享客户端那一套，只多一枚 ``local_address``。

    ``verify`` 必须传给 **transport**（传给 client 会被 transport 参数整体吞掉），否则这条回退
    档退回 certifi——共享客户端用 OS 证书 store 正是为了绕开打包态 certifi 缺失。
    与取证探针不同，这里**不关** ``trust_env``：产品这一路的目的是「把内容取回来」而不是
    「判定源站走哪一族」，环境里有代理时应当照用；代价是那一档钉住的是**到代理那一段**的族别。
    """
    transport = httpx.AsyncHTTPTransport(
        local_address=FAMILY_BIND[family],
        verify=ssl.create_default_context(),
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


async def _send(
    family: str,
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float,
) -> httpx.Response:
    if family == "auto":
        client = await get_client()
    else:
        client = _pinned_client(family, timeout)
    try:
        if method.upper() == "GET":
            return await client.get(url, params=params, headers=headers or {}, timeout=timeout)
        return await client.post(url, params=params, json=json_body, headers=headers or {}, timeout=timeout)
    finally:
        if family != "auto":
            await client.aclose()


async def _request_ladder(
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    deadline: Optional[Deadline] = None,
    accept: Callable[[httpx.Response], bool] = lambda response: response.status_code == 200,
    label: str = "源",
) -> httpx.Response:
    """逐地址回退：auto → ipv4 → ipv6，共用同一条端到端预算，全挂才报失败。"""
    notes: list[str] = []
    for family in FAMILY_LADDER:
        timeout = deadline.attempt_timeout() if deadline is not None else REQUEST_TIMEOUT_SECONDS
        if timeout <= 0.0:
            notes.append(f"{family}:端到端预算用完")
            break
        try:
            response = await asyncio.wait_for(
                _send(
                    family,
                    method,
                    url,
                    params=params,
                    json_body=json_body,
                    headers=headers,
                    timeout=timeout,
                ),
                timeout,
            )
        except asyncio.TimeoutError:
            notes.append(f"{family}:{timeout:.1f}s 内未完成")
            continue
        except httpx.HTTPError as exc:
            notes.append(f"{family}:{type(exc).__name__}")
            continue
        if accept(response):
            return response
        notes.append(f"{family}:HTTP {response.status_code}")
        # 状态码这一类失败换地址族没有意义（同一端点会再给一次同样的答复），直接收口。
        raise SourceUnavailable(f"{label} · " + " | ".join(notes))
    raise SourceUnavailable(f"{label} · 三档未通 · " + " | ".join(notes))


def _json_body(response: httpx.Response, label: str) -> dict:
    try:
        parsed = response.json()
    except Exception as exc:  # noqa: BLE001 - 解析不出是「这源没给到可用内容」，不是 traceback
        raise SourceUnavailable(f"{label} · 响应不是可解析 JSON（{type(exc).__name__}）") from exc
    return parsed if isinstance(parsed, dict) else {}


async def fetch_crawler_material(
    keywords: Sequence[str],
    api_key: str = "",
    *,
    deadline: Optional[Deadline] = None,
) -> list[Retrieved]:
    """Tavily 检索：返回若干条「可供 AI 改写的资料片段」，不是可直接念的句子。

    关键词是这条链的前提——「点一下给点用户感兴趣的东西」里，感兴趣的内容由那串关键词定义。
    """
    words = [str(word).strip() for word in (keywords or ()) if str(word).strip()]
    if not words:
        raise SourceUnavailable("爬虫 · 未配置关键词")
    query = " ".join(words[:4])
    key = str(api_key or "").strip()
    headers = {
        "Content-Type": "application/json",
        # key 为空才走免 key 档；非空时它是用户自己填的凭据，填错就如实失败（禁忌：不悄悄降级）。
        "X-Tavily-Access-Mode": "keyless" if not key else "standard",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"query": query, "max_results": MAX_ITEMS, "search_depth": "basic"}
    response = await _request_ladder(
        "POST",
        TAVILY_URL,
        json_body=body,
        headers=headers,
        deadline=deadline,
        accept=lambda r: r.status_code == 200,
        label=f"爬虫（Tavily，关键词「{query}」）",
    )
    data = _json_body(response, "爬虫（Tavily）")
    items: list[Retrieved] = []
    for row in data.get("results") or ():
        if not isinstance(row, dict):
            continue
        title = _visible(row.get("title"))
        content = _visible(row.get("content"))
        text = f"{title}｜{content}" if title and content else (content or title)
        if text:
            items.append(Retrieved(text=_trimmed(text), source="crawler", attribution=str(row.get("url") or "")))
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise SourceUnavailable("爬虫（Tavily）· 响应里没有可用文本")
    return items


async def fetch_hitokoto_line(*, deadline: Optional[Deadline] = None) -> Retrieved:
    """一言的诗词类目：免 key、返回一句随机诗（实测本就是简体）。"""
    response = await _request_ladder(
        "GET",
        HITOKOTO_URL,
        deadline=deadline,
        label="古诗词（hitokoto）",
    )
    data = _json_body(response, "古诗词（hitokoto）")
    line = _visible(data.get("hitokoto"))
    if not line:
        raise SourceUnavailable("古诗词（hitokoto）· 响应里没有 hitokoto 字段")
    author = _visible(data.get("from_who"))
    origin = _visible(data.get("from"))
    attribution = "·".join(part for part in (author or origin,) if part)
    return Retrieved(text=line, source="hitokoto", attribution=attribution)


def _clean_snippet(raw: object) -> str:
    """MediaWiki 的 snippet 带 ``<span class="searchmatch">`` 高亮标签，取纯文本。"""
    text = str(raw or "")
    for token in ("<span class=\"searchmatch\">", "</span>"):
        text = text.replace(token, "")
    return _visible(text)


async def fetch_wikisource_line(
    keywords: Sequence[str],
    *,
    deadline: Optional[Deadline] = None,
) -> Retrieved:
    """Wikisource 按关键词取一阕：检索前改繁體变体，回来后用命中校验把关。

    这道校验不是装饰：实测 ``srsearch=床前明月光`` 命中的是不相干的《四季歌》，不做掉它就真会把
    它当《静夜思》念出来（今版「明月光」与古籍本「看月光」属**版本**差异，字符映射救不了）。
    """
    words = [str(word).strip() for word in (keywords or ()) if str(word).strip()]
    if not words:
        raise SourceUnavailable("古诗词（Wikisource）· 未配置关键词，随机档由 hitokoto 承担")
    misses: list[str] = []
    for word in words[:4]:
        for variant in query_variants(word):
            if deadline is not None and deadline.expired():
                break
            response = await _request_ladder(
                "GET",
                WIKISOURCE_URL,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": variant,
                    "srnamespace": 0,
                    "srlimit": MAX_ITEMS,
                    "format": "json",
                },
                deadline=deadline,
                label=f"古诗词（Wikisource，查询「{variant}」）",
            )
            data = _json_body(response, "古诗词（Wikisource）")
            rows = ((data.get("query") or {}).get("search")) or ()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = _visible(row.get("title"))
                snippet = _clean_snippet(row.get("snippet"))
                if not is_hit(variant, title, snippet):
                    misses.append(f"「{variant}」→「{title[:20]}」未通过命中校验")
                    continue
                body = snippet or title
                if len(body) > _WIKISOURCE_SEARCH_CHARS:
                    body = body[:_WIKISOURCE_SEARCH_CHARS].rstrip() + "…"
                return Retrieved(text=body, source="wikisource", attribution=title)
    detail = " · ".join(misses[:3]) if misses else "没有可用命中"
    raise SourceUnavailable(f"古诗词（Wikisource）· {detail}")


async def fetch_poetry_line(
    keywords: Sequence[str],
    sources: Iterable[str],
    *,
    deadline: Optional[Deadline] = None,
) -> Retrieved:
    """按启用的源依次尝试，第一条能过的就返回；全挂才失败（失败理由留给日志）。"""
    order = [str(name).strip() for name in (sources or ()) if str(name).strip()]
    if not order:
        raise SourceUnavailable("古诗词 · 未启用任何源")
    words = [str(word).strip() for word in (keywords or ()) if str(word).strip()]
    notes: list[str] = []
    for name in order:
        if deadline is not None and deadline.expired():
            notes.append(f"{name}:端到端预算用完")
            break
        try:
            if name == "hitokoto":
                return await fetch_hitokoto_line(deadline=deadline)
            if name == "wikisource":
                if not words:
                    notes.append("wikisource:未配置关键词")
                    continue
                return await fetch_wikisource_line(words, deadline=deadline)
            notes.append(f"{name}:未知的源")
        except SourceUnavailable as exc:
            notes.append(str(exc))
        except asyncio.TimeoutError:
            notes.append(f"{name}:端到端预算用完")
    raise SourceUnavailable("古诗词 · 换源皆未通 · " + " | ".join(notes))
