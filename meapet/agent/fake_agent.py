"""**假 Agent 端**：一台只在回路上跑的脚本化 Agent Link v1 服务器。

产品里那条「回合外的 `chat.tool_status` 排空到气泡」的链（`agent_link.py:649-655` →
`control_bridge.py:276-291`）过去只有代码、没有读数：要拿读数就得有人真发那一发帧。
这一格把「有人发」变成仓库里的一份剧本——假端按剧本发帧，取证面报「到了几发、回执了什么码」。
它验的是**客户端这端的搬运**，不验任何真网关会不会发这些帧（那一档是 `--probe-agent` 的判据）。

三条自己做死的规矩：
1. **只听回路**。`host` 默认 `127.0.0.1`，非回路直接拒——这台服务器由取证面就地起，
   把它开到一个对外地址上，等于借「诊断」的名义新开一条外联通路。
2. **剧本不能改地址**。外部剧本里出现 `host`/`port`/`url`/`base_url`/`session_id`
   任一键直接 `FakeAgentScriptError`：一份文件不该能把这一趟从回路里拽出去，也不该能冒充
   别的会话。会话 ID 一律从**收到的** `control.hello` 里取。
3. **不读凭据**。握手只记帧的 `type` 与 token 的**长度**（一个整数），值不落任何地方。
   这第三条不是洁癖：`factory._resolve_secret` 把 `MEA_PET_AGENT_LINK_TOKEN` /
   `MEA_PET_AGENT_TOKEN` 排在**任何** config 字面量之前，所以真拿产品工厂指过来，
   客户端会把它那枚真 token 递进这个监听器——长度那一格就是用来当场证明它是 0 的。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meapet.agent.link_protocol import AgentLinkFrame, make_agent_link_frame

#: 假端的监听路径——与出厂 `DEFAULT_AGENT_LINK_WS_URL` 同一条，读数看着眼熟。
FAKE_AGENT_PATH = "/agent-link"

#: 剧本里**永不许出现**的键（规矩 2）：地址与会话由本模块定，不由一份文件定。
_ADDRESS_KEYS = ("host", "port", "url", "base_url", "session_id")
_TOP_KEYS = ("steps",)
_STEP_KEYS = ("frames", "wait_for", "repeat")
_FRAME_KEYS = ("type", "payload", "id", "reply_to")


@dataclass(frozen=True)
class ScriptStep:
    """剧本的一步：先（可选）等客户端发出 `wait_for` 那一型，再把 `frames` 依次发出去。"""

    frames: tuple[dict[str, Any], ...]
    wait_for: str | None = None
    repeat: int = 1


@dataclass(frozen=True)
class FakeAgentResult:
    """一趟跑完的**服务器侧**事实。客户端侧那一格（落队几发）由调用方自己 `take` 出来。"""

    sent: int
    received_types: tuple[str, ...]
    error_codes: tuple[str, ...]
    snapshot_seen: int
    barrier_acked: bool
    replays: int
    queueable_sent: int
    hello_token_lengths: tuple[int, ...]


class FakeAgentScriptError(ValueError):
    """剧本本身不合法。**在校验阶段就拒**，不等它把一趟跑歪到读数里才发现。"""


def _is_queueable_tool_status(frame: Mapping[str, Any]) -> bool:
    """这一步发出的帧，客户端**应当**收进 `_idle_statuses` 吗。

    判据抄 `agent_link._tool_status_from_payload`：`state` 与 `safe_text` 都得是非空串。
    分母从这里算，所以「落队 0/4」与「落队 0/0」是两句不同的话——后者是剧本根本没发到。
    """
    if str(frame.get("type") or "") != "chat.tool_status":
        return False
    payload = frame.get("payload")
    if not isinstance(payload, Mapping):
        return False
    return all(
        isinstance(payload.get(key), str) and bool(str(payload.get(key)).strip())
        for key in ("state", "safe_text")
    )


def _ready_payload() -> dict[str, Any]:
    """`control.ready`：capabilities 里 chat.submit + tools.dynamic/call 是握手硬条件
    （`agent_link.py:388-398` 少一项就直接协议错），照出厂那套报。"""
    return {
        "version": "1.0",
        "authenticated": True,
        "agent_name": "假 Agent（取证）",
        "server_version": "fake-agent",
        "capabilities": {
            "chat": {"submit": True, "streaming": True, "cancel": True},
            "tools": {"dynamic": True, "call": True, "cancel": True},
        },
        "required_extensions": [],
    }


def _default_script() -> tuple[ScriptStep, ...]:
    """四步。**可解析**的那一支只入队、不发回执（`agent_link.py:649-655` 直接 `return`），
    **解析不出且 `required`** 的那一支才回 `NO_ACTIVE_TURN`（656-672）——两支都发，
    读数才分得开「搬运对了」与「根本没到」。最后一发 `control.ping` 是**派发屏障**：
    入站在 `_supervise` 里是串行 `await _dispatch_frame`，pong 到了才说前三步都跑完了。

    队列型帧刻意压在 4 发（`_MAX_IDLE_TOOL_STATUSES = 16`）——贴着淘汰线会把「落队 N」
    揉成「N 封顶」，那一格就读不出东西了。
    """

    def status(index: int) -> dict[str, Any]:
        return make_agent_link_frame(
            "chat.tool_status",
            {"state": "running", "safe_text": f"假过程帧 #{index}"},
            message_id=f"fake-status-{index}",
        )

    return (
        ScriptStep(frames=tuple(status(index) for index in range(1, 5))),
        ScriptStep(frames=(
            make_agent_link_frame(
                "chat.tool_status",
                {"state": "running", "safe_text": "", "required": True},
                message_id="fake-unparseable",
            ),
        ),),
        ScriptStep(frames=(
            make_agent_link_frame("tools.refresh", {}, message_id="fake-refresh"),
        ),),
        ScriptStep(frames=(
            make_agent_link_frame("control.ping", {}, message_id="fake-ping"),
        ),),
    )


DEFAULT_SCRIPT: tuple[ScriptStep, ...] = _default_script()


def load_script(path: object) -> tuple[ScriptStep, ...]:
    """把一份外部 JSON 剧本读成 `ScriptStep` 序列，并**在当场**拒掉越权写法。

    写法：`{"steps": [{"frames": [{"type": "chat.tool_status", "payload": {...},
    "id": "...", "reply_to": "..."}], "wait_for": "tools.snapshot", "repeat": 1}]}`。
    帧在这一刻就过 `make_agent_link_frame` 自校验、`id` 也在此刻定死；`session_id`
    由跑的时候按收到的 hello 盖。
    """
    try:
        document = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FakeAgentScriptError(f"剧本不是合法 JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise FakeAgentScriptError("剧本顶层必须是对象，且只有 steps 这一个键")
    _reject(document, _ADDRESS_KEYS, "顶层")
    unknown = [str(key) for key in document if str(key) not in _TOP_KEYS]
    if unknown:
        raise FakeAgentScriptError(f"顶层不认这些键: {unknown}（只认 steps）")
    steps_raw = document.get("steps")
    if isinstance(steps_raw, (str, bytes)) or not isinstance(steps_raw, Sequence):
        raise FakeAgentScriptError("steps 必须是数组")
    if not steps_raw:
        raise FakeAgentScriptError("steps 是空的：空剧本这一趟跑不出任何一句话")

    steps: list[ScriptStep] = []
    for index, entry in enumerate(steps_raw, start=1):
        if not isinstance(entry, Mapping):
            raise FakeAgentScriptError(f"第 {index} 步: 必须是对象")
        _reject(entry, _ADDRESS_KEYS, f"第 {index} 步")
        unknown = [str(key) for key in entry if str(key) not in _STEP_KEYS]
        if unknown:
            raise FakeAgentScriptError(
                f"第 {index} 步不认这些键: {unknown}（只认 frames/wait_for/repeat）"
            )
        frames_raw = entry.get("frames")
        if (
            isinstance(frames_raw, (str, bytes))
            or not isinstance(frames_raw, Sequence)
            or not frames_raw
        ):
            raise FakeAgentScriptError(f"第 {index} 步: frames 必须是非空数组")
        repeat = entry.get("repeat", 1)
        if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
            raise FakeAgentScriptError(f"第 {index} 步: repeat 要是 ≥1 的整数，给的是 {repeat!r}")
        wait_for = entry.get("wait_for")
        if wait_for is not None and not str(wait_for).strip():
            raise FakeAgentScriptError(f"第 {index} 步: wait_for 给了空串，要么给型名要么整个不给")

        built: list[dict[str, Any]] = []
        for frame_index, frame_raw in enumerate(frames_raw, start=1):
            where = f"第 {index} 步 第 {frame_index} 帧"
            if not isinstance(frame_raw, Mapping):
                raise FakeAgentScriptError(f"{where}: 必须是对象")
            _reject(frame_raw, _ADDRESS_KEYS, where)
            unknown = [str(key) for key in frame_raw if str(key) not in _FRAME_KEYS]
            if unknown:
                raise FakeAgentScriptError(
                    f"{where} 不认这些键: {unknown}（只认 type/payload/id/reply_to；"
                    "version 与 extensions 由本模块定）"
                )
            frame_type = str(frame_raw.get("type") or "").strip()
            if not frame_type:
                raise FakeAgentScriptError(f"{where}: 没有 type")
            payload = frame_raw.get("payload") or {}
            if not isinstance(payload, Mapping):
                raise FakeAgentScriptError(f"{where}: payload 必须是对象")
            built.append(
                make_agent_link_frame(
                    frame_type,
                    dict(payload),
                    message_id=str(frame_raw.get("id") or "").strip(),
                    reply_to=str(frame_raw.get("reply_to") or "").strip(),
                )
            )
        steps.append(
            ScriptStep(
                frames=tuple(built),
                wait_for=str(wait_for).strip() if wait_for is not None else None,
                repeat=repeat,
            )
        )
    return tuple(steps)


def _reject(entry: Mapping[str, Any], forbidden: Sequence[str], where: str) -> None:
    hit = [key for key in forbidden if key in entry]
    if hit:
        raise FakeAgentScriptError(
            f"{where} 里出现了 {hit}——地址与会话不由剧本定（这一趟只在回路上）"
        )


class _Run:
    """一趟连接的可变观测面。只在事件循环线程里动，所以不加锁。"""

    def __init__(self, steps: Sequence[ScriptStep]) -> None:
        self.steps = tuple(steps)
        self.session_id = ""
        self.received: list[str] = []
        self.error_codes: list[str] = []
        self.token_lengths: list[int] = []
        self.snapshot_seen = 0
        self.sent = 0
        self.replays = 0
        self.queueable_sent = 0
        self.barrier_acked = False
        self.expects_barrier = any(
            str(frame.get("type") or "") == "control.ping"
            for step in self.steps
            for frame in step.frames
        )
        self.sender_done = asyncio.Event()
        self.peer_gone = asyncio.Event()
        self._type_events: dict[str, asyncio.Event] = {}

    def event_for(self, frame_type: str) -> asyncio.Event:
        event = self._type_events.get(frame_type)
        if event is None:
            event = asyncio.Event()
            self._type_events[frame_type] = event
        return event

    def note_inbound(self, frame: Mapping[str, Any]) -> None:
        frame_type = str(frame.get("type") or "")
        self.received.append(frame_type)
        self.event_for(frame_type).set()
        if frame_type == "tools.snapshot":
            self.snapshot_seen += 1
        if frame_type == "control.pong" and self.expects_barrier:
            self.barrier_acked = True
        if frame_type == "control.error":
            payload = frame.get("payload")
            code = payload.get("code") if isinstance(payload, Mapping) else None
            if code:
                self.error_codes.append(str(code))

    def result(self) -> FakeAgentResult:
        return FakeAgentResult(
            sent=self.sent,
            received_types=tuple(self.received),
            error_codes=tuple(self.error_codes),
            snapshot_seen=self.snapshot_seen,
            barrier_acked=self.barrier_acked,
            replays=self.replays,
            queueable_sent=self.queueable_sent,
            hello_token_lengths=tuple(self.token_lengths),
        )


class FakeAgentServer:
    """`scripted_agent_server` 交出去的那把柄：`url` 连它，`wait_result` 收读数。"""

    def __init__(self, url: str, run: _Run) -> None:
        self.url = url
        self._run = run

    async def wait_result(self, timeout: float) -> FakeAgentResult:
        """等剧本发完 + 屏障到位；**超时不抛**——跑歪的这一趟也要有读数可印。

        屏障没到就把已知的交回去，`barrier_acked=False` 由调用方印成「未到齐」，
        不猜一个数填进那一格。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout))
        for event in (
            self._run.sender_done,
            *(
                (self._run.event_for("control.pong"),)
                if self._run.expects_barrier
                else ()
            ),
        ):
            remaining = deadline - loop.time()
            if event.is_set() or remaining <= 0:
                continue
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=remaining)
        return self._run.result()


@contextlib.asynccontextmanager
async def scripted_agent_server(
    *,
    script: Sequence[ScriptStep] | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    path: str = FAKE_AGENT_PATH,
):
    """起一台脚本化假 Agent 端（回路 only），`yield` 出 `FakeAgentServer`。

    `port=0` 让内核挑端口，读回来的 `url` 才是这台监听器**真的**在听的那一条。
    """
    from meapet.agent.ws_transport import is_loopback_host

    if not is_loopback_host(host):
        raise FakeAgentScriptError(
            f"假 Agent 端只在回路上起，host={host!r} 不是回路（新开外联须人工明确点头）"
        )
    steps = DEFAULT_SCRIPT if script is None else tuple(script)
    if not steps:
        raise FakeAgentScriptError("剧本是空的：空剧本这一趟跑不出任何一句话")
    run = _Run(steps)

    from websockets.asyncio.server import serve

    async def handler(websocket: Any) -> None:
        try:
            await _serve_connection(websocket, run)
        except Exception:  # noqa: BLE001 - 假端自己出格不该把 traceback 递给取证面
            pass
        finally:
            run.peer_gone.set()
            run.sender_done.set()

    async with serve(handler, host, port) as server:
        bound_port = server.sockets[0].getsockname()[1]
        yield FakeAgentServer(f"ws://{host}:{bound_port}{path}", run)


async def _serve_connection(websocket: Any, run: _Run) -> None:
    """握手 → 并发跑「按剧本发」与「把入站记账」两条；任一条收尾就收整条。"""
    hello = _parse_hello(await websocket.recv())
    run.note_inbound(hello.raw)
    run.token_lengths.append(hello.token_length)
    run.session_id = hello.session_id
    await websocket.send(
        json.dumps(
            make_agent_link_frame(
                "control.ready",
                _ready_payload(),
                session_id=hello.session_id,
                reply_to=hello.message_id,
            ),
            ensure_ascii=False,
        )
    )
    sender = asyncio.create_task(_send_script(websocket, run))
    receiver = asyncio.create_task(_note_inbound_loop(websocket, run))
    try:
        await asyncio.gather(sender, receiver, return_exceptions=True)
    finally:
        for task in (sender, receiver):
            if not task.done():
                task.cancel()


@dataclass(frozen=True)
class _Hello:
    """握手那一帧里本案**只**需要的三样。

    故意不整帧留档：`auth.token` 在场（`agent_link.py:294-297`），把 payload 抄进结果里
    等于给那枚真 token 一个可能被印出来的去处。`raw` 只有 `type` 进记账，其余不外递。
    """

    message_id: str
    session_id: str
    token_length: int
    raw: dict[str, Any]

    @classmethod
    def from_frame(cls, frame: AgentLinkFrame) -> "_Hello":
        auth = frame.payload.get("auth")
        token = auth.get("token") if isinstance(auth, Mapping) else None
        return cls(
            message_id=frame.id,
            session_id=frame.session_id,
            token_length=len(str(token)) if isinstance(token, str) else 0,
            raw=frame.to_dict(),
        )


def _parse_hello(raw: object) -> _Hello:
    """把线上那一条帧读成 `_Hello`。

    入站是 **str/bytes**（`websocket.recv()` 给的东西），不是 dict：直接递给
    `AgentLinkFrame.parse` 会被判"消息必须是 JSON 对象"，那一格错在握手第一步就把整趟掐了。
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="strict")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FakeAgentScriptError(f"第一帧不是 JSON: {exc}") from exc
    frame = AgentLinkFrame.parse(raw)
    if frame.type != "control.hello":
        raise FakeAgentScriptError(f"第一帧要的是 control.hello，收到的是 {frame.type}")
    return _Hello.from_frame(frame)


async def _send_script(websocket: Any, run: _Run) -> None:
    try:
        for step in run.steps:
            if step.wait_for is not None and not await _wait_type(run, step.wait_for):
                return
            for _ in range(step.repeat):
                for frame in step.frames:
                    stamped = dict(frame)
                    stamped["session_id"] = run.session_id
                    await websocket.send(json.dumps(stamped, ensure_ascii=False))
                    run.sent += 1
                    if _is_queueable_tool_status(frame):
                        run.queueable_sent += 1
                    if step.repeat > 1:
                        run.replays += 1
    except Exception:  # noqa: BLE001 - 连接断了就收尾，那格读数由 barrier 说
        return
    finally:
        run.sender_done.set()


async def _wait_type(run: _Run, frame_type: str) -> bool:
    """等到客户端发出那一型返回 True；**对端先走**返回 False（那一步不发，别把循环卡死）。"""
    wanted = run.event_for(frame_type)
    waiter = asyncio.create_task(wanted.wait())
    until_gone = asyncio.create_task(run.peer_gone.wait())
    try:
        await asyncio.wait({waiter, until_gone}, return_when=asyncio.FIRST_COMPLETED)
        return wanted.is_set()
    finally:
        for task in (waiter, until_gone):
            if not task.done():
                task.cancel()


async def _note_inbound_loop(websocket: Any, run: _Run) -> None:
    async for raw in websocket:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, Mapping):
            run.note_inbound(frame)
