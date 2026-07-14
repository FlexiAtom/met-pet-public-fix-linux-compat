"""把直连模型的 Canonical 流转换为 MeaPet 统一分段事件。"""

from __future__ import annotations

import sys  # 新增：用于 print flush
from typing import AsyncIterator

from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.prompts import (
    MAX_REPAIR_INPUT_CHARS,
    OUTPUT_INSTRUCTION,
    REPAIR_INSTRUCTION,
    frontend_context_json,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
)
from meapet.direct.client import DirectProtocolError
from meapet.direct.types import (
    CanonicalChatRequest,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)

from meapet.ollama import build_ollama_messages


class DirectConversationAdapter:
    """协调 MeaPet 本地角色/历史与供应商无关的流式协议客户端。"""

    def __init__(self, engine, protocol_client) -> None:
        self.engine = engine
        self.protocol_client = protocol_client
        self._cancelled_turns: set[str] = set()
        print(f"[DEBUG conversation] __init__: engine.backend={getattr(engine, 'backend', None)}, protocol_client={type(protocol_client).__name__}", file=sys.stderr, flush=True)

    def _canonical_request(
        self,
        messages,
        *,
        max_tokens: int | None = None,
    ) -> CanonicalChatRequest:
        req = CanonicalChatRequest(
            model=self.engine.model,
            messages=tuple(messages),
            temperature=self.engine.temperature,
            max_tokens=max_tokens or self.engine.max_tokens,
            stream=True,
        )
        print(f"[DEBUG conversation] _canonical_request: model={req.model}, messages_count={len(req.messages)}, max_tokens={req.max_tokens}", file=sys.stderr, flush=True)
        return req

    async def cancel(self, turn_id: str) -> None:
        tid = str(turn_id or "").strip()
        print(f"[DEBUG conversation] cancel called: turn_id={tid!r}", file=sys.stderr, flush=True)
        self._cancelled_turns.add(tid)
        print(f"[DEBUG conversation] cancel added to _cancelled_turns: size={len(self._cancelled_turns)}", file=sys.stderr, flush=True)

    async def close(self) -> None:
        print(f"[DEBUG conversation] close called", file=sys.stderr, flush=True)
        await self.protocol_client.close()
        print(f"[DEBUG conversation] close finished", file=sys.stderr, flush=True)

    async def _repair_result(
        self,
        *,
        request: AgentTurnRequest,
        malformed_output: str,
    ):
        print(f"[DEBUG conversation] _repair_result entered: turn_id={request.turn_id}, malformed_length={len(malformed_output)}", file=sys.stderr, flush=True)
        repair_request = self._canonical_request(
            (
                {"role": "system", "content": REPAIR_INSTRUCTION},
                {
                    "role": "user",
                    "content": malformed_output[:MAX_REPAIR_INPUT_CHARS],
                },
            )
        )
        parser = MeaPetOutputStreamParser()
        try:
            async for event in self.protocol_client.stream(repair_request):
                if request.turn_id in self._cancelled_turns:
                    print(f"[DEBUG conversation] _repair_result cancelled during stream: turn_id={request.turn_id}", file=sys.stderr, flush=True)
                    return None
                if isinstance(event, TextDelta):
                    print(f"[DEBUG conversation] _repair_result feed delta: len={len(event.delta)}, preview={event.delta[:80]!r}", file=sys.stderr, flush=True)
                    parser.feed(event.delta)
                elif isinstance(event, StreamDone):
                    print(f"[DEBUG conversation] _repair_result received StreamDone", file=sys.stderr, flush=True)
                    break
        except DirectProtocolError as e:
            print(f"[DEBUG conversation] _repair_result DirectProtocolError: category={e.category}, msg={e.safe_message}", file=sys.stderr, flush=True)
            return None
        result = parser.close(tts_enabled=request.tts_enabled)
        print(f"[DEBUG conversation] _repair_result close result: segments={len(result.segments)}, issues={len(result.issues)}, done={result.done}", file=sys.stderr, flush=True)
        if result.requires_repair(tts_enabled=request.tts_enabled):
            print(f"[DEBUG conversation] _repair_result still requires repair, returning None", file=sys.stderr, flush=True)
            return None
        print(f"[DEBUG conversation] _repair_result success, returning result", file=sys.stderr, flush=True)
        return result

    async def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[object]:
        print(f"[DEBUG conversation] stream_turn entered: turn_id={request.turn_id}, user_text_len={len(request.user_text)}, attachments={len(request.attachments) if request.attachments else 0}", file=sys.stderr, flush=True)
        print(f"[DEBUG conversation] stream_turn: backend={self.engine.backend}, available={self.engine.available}", file=sys.stderr, flush=True)

        if request.turn_id in self._cancelled_turns:
            print(f"[DEBUG conversation] turn already cancelled before processing: {request.turn_id}", file=sys.stderr, flush=True)
            self._cancelled_turns.discard(request.turn_id)
            yield TurnCancelled(request.turn_id)
            print(f"[DEBUG conversation] yielded TurnCancelled early", file=sys.stderr, flush=True)
            return
        if not self.engine.available:
            print(f"[DEBUG conversation] engine not available, yielding TurnFailed", file=sys.stderr, flush=True)
            yield TurnFailed(
                request.turn_id,
                "backend_unavailable",
                "模型服务尚未就绪，请检查配置和运行状态。",
                True,
            )
            return

        prepared = False
        try:
            # --- Ollama 专用路径 ---
            if self.engine.backend == "ollama":
                print(f"[DEBUG conversation] using Ollama path", file=sys.stderr, flush=True)
                from meapet.ollama import build_ollama_messages
                # 手动更新 engine.history（模拟 _prepare_direct_turn 的行为）
                with self.engine._history_lock:
                    print(f"[DEBUG conversation] Ollama: locked history, current length={len(self.engine.history)}", file=sys.stderr, flush=True)
                    # 先保存当前历史（不含即将添加的用户消息）
                    past_history = tuple(self.engine.history[1:])  # 排除旧的 system
                    print(f"[DEBUG conversation] Ollama: past_history count={len(past_history)}", file=sys.stderr, flush=True)
                    # 然后添加用户消息
                    self.engine.history.append({"role": "user", "content": request.user_text})
                    print(f"[DEBUG conversation] Ollama: appended user message, history now {len(self.engine.history)} items", file=sys.stderr, flush=True)
                    if len(self.engine.history) > 32:  # 与 engine.py 中的截断策略一致
                        saved_system = self.engine.history[0]
                        self.engine.history = [saved_system] + self.engine.history[-30:]
                        print(f"[DEBUG conversation] Ollama: truncated history to {len(self.engine.history)} items", file=sys.stderr, flush=True)
                    messages = build_ollama_messages(
                        user_text=request.user_text,
                        history=past_history,  # 排除 system
                        frontend_context=request.frontend_context,
                        attachments=request.attachments,
                    )
                    print(f"[DEBUG conversation] Ollama: built messages count={len(messages)}", file=sys.stderr, flush=True)
                    for i, msg in enumerate(messages):
                        print(f"[DEBUG conversation] Ollama: msg[{i}] role={msg['role']}, content_len={len(str(msg['content']))}", file=sys.stderr, flush=True)
                    # 更新 engine.history 中的 system prompt
                    self.engine.history[0] = {"role": "system", "content": messages[0]["content"]}
                    print(f"[DEBUG conversation] Ollama: updated history[0] system prompt", file=sys.stderr, flush=True)

                # 标记为已准备，以便 rollback 时能正确处理
                prepared = True
                print(f"[DEBUG conversation] Ollama: prepared=True", file=sys.stderr, flush=True)
            else:
                # 原有逻辑
                print(f"[DEBUG conversation] using non-Ollama path", file=sys.stderr, flush=True)
                messages = self.engine._prepare_direct_turn(request.user_text)
                prepared = True
                print(f"[DEBUG conversation] non-Ollama: _prepare_direct_turn returned {len(messages)} messages", file=sys.stderr, flush=True)
                if request.attachments:
                    print(f"[DEBUG conversation] non-Ollama: adding attachments to last user message", file=sys.stderr, flush=True)
                    messages[-1] = {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": request.user_text},
                            *(attachment.canonical_part() for attachment in request.attachments),
                        ],
                    }
                system = str(messages[0].get("content") or "")
                messages[0] = {
                    "role": "system",
                    "content": (
                        f"{system}\n\n{OUTPUT_INSTRUCTION}\n"
                        f"前端只读摘要：{frontend_context_json(request)}"
                    ),
                }
                print(f"[DEBUG conversation] non-Ollama: final messages[0] system length={len(messages[0]['content'])}", file=sys.stderr, flush=True)

            canonical = self._canonical_request(messages)
            parser = MeaPetOutputStreamParser()
            raw_chunks: list[str] = []
            completed_indices: set[int] = set()
            protocol_completed_emitted = False
            stream_done = False

            print(f"[DEBUG conversation] starting protocol_client.stream", file=sys.stderr, flush=True)
            async for event in self.protocol_client.stream(canonical):
                if request.turn_id in self._cancelled_turns:
                    print(f"[DEBUG conversation] turn cancelled during stream: {request.turn_id}", file=sys.stderr, flush=True)
                    self._cancelled_turns.discard(request.turn_id)
                    self.engine._rollback_direct_turn(request.user_text)
                    print(f"[DEBUG conversation] rolled back after cancellation", file=sys.stderr, flush=True)
                    yield TurnCancelled(request.turn_id)
                    print(f"[DEBUG conversation] yielded TurnCancelled during stream", file=sys.stderr, flush=True)
                    return
                if isinstance(event, TextDelta):
                    print(f"[DEBUG conversation] received TextDelta: len={len(event.delta)}, preview={event.delta[:60]!r}", file=sys.stderr, flush=True)
                    raw_chunks.append(event.delta)
                    for parsed_event in parser.feed(event.delta):
                        if isinstance(parsed_event, SegmentCompleted):
                            print(f"[DEBUG conversation] parsed SegmentCompleted: index={parsed_event.segment.index}, missing={parsed_event.segment.missing_required_fields}", file=sys.stderr, flush=True)
                            if parsed_event.segment.missing_required_fields:
                                print(f"[DEBUG conversation] skipping segment due to missing fields", file=sys.stderr, flush=True)
                                continue
                            completed_indices.add(parsed_event.segment.index)
                        elif isinstance(parsed_event, ProtocolCompleted):
                            protocol_completed_emitted = True
                            print(f"[DEBUG conversation] parsed ProtocolCompleted", file=sys.stderr, flush=True)
                        yield parsed_event
                elif isinstance(event, ReasoningDelta):
                    print(f"[DEBUG conversation] received ReasoningDelta (skipped)", file=sys.stderr, flush=True)
                    continue
                elif isinstance(event, StreamDone):
                    stream_done = True
                    print(f"[DEBUG conversation] received StreamDone", file=sys.stderr, flush=True)
                    break
            if not stream_done:
                print(f"[DEBUG conversation] stream ended without StreamDone, raising DirectProtocolError", file=sys.stderr, flush=True)
                raise DirectProtocolError("protocol", "模型流未正常结束。")

            result = parser.close(tts_enabled=request.tts_enabled, backend=self.engine.backend)
            print(f"[DEBUG conversation] parser.close result: segments={len(result.segments)}, issues={len(result.issues)}, done={result.done}", file=sys.stderr, flush=True)
            if result.requires_repair(tts_enabled=request.tts_enabled):
                print(f"[DEBUG conversation] requires repair, emitting FormatRepairRequired", file=sys.stderr, flush=True)
                yield FormatRepairRequired(result)
                repaired = await self._repair_result(
                    request=request,
                    malformed_output="".join(raw_chunks),
                )
                if request.turn_id in self._cancelled_turns:
                    print(f"[DEBUG conversation] turn cancelled after repair attempt", file=sys.stderr, flush=True)
                    self._cancelled_turns.discard(request.turn_id)
                    self.engine._rollback_direct_turn(request.user_text)
                    yield TurnCancelled(request.turn_id)
                    return
                if repaired is not None:
                    result = repaired
                    print(f"[DEBUG conversation] repair succeeded, result updated", file=sys.stderr, flush=True)
                else:
                    print(f"[DEBUG conversation] repair failed, keeping original result", file=sys.stderr, flush=True)

            if not any(segment.display_text.strip() for segment in result.segments):
                print(f"[DEBUG conversation] no displayable segments, rolling back and yielding TurnFailed", file=sys.stderr, flush=True)
                self.engine._rollback_direct_turn(request.user_text)
                yield TurnFailed(
                    request.turn_id,
                    "protocol",
                    "模型没有返回可展示的回复。",
                )
                return

            for segment in result.segments:
                if segment.index not in completed_indices:
                    print(f"[DEBUG conversation] yielding pending SegmentCompleted: index={segment.index}", file=sys.stderr, flush=True)
                    yield SegmentCompleted(segment)
                    completed_indices.add(segment.index)
            if result.done and not protocol_completed_emitted:
                print(f"[DEBUG conversation] yielding ProtocolCompleted (not yet emitted)", file=sys.stderr, flush=True)
                yield ProtocolCompleted()
            self.engine._commit_direct_turn(result)
            print(f"[DEBUG conversation] committed turn, yielding TurnCompleted", file=sys.stderr, flush=True)
            yield TurnCompleted(request.turn_id, result)
        except DirectProtocolError as exc:
            print(f"[DEBUG conversation] caught DirectProtocolError: category={exc.category}, safe_message={exc.safe_message}, retryable={exc.retryable}", file=sys.stderr, flush=True)
            if prepared:
                print(f"[DEBUG conversation] rolling back because prepared=True", file=sys.stderr, flush=True)
                self.engine._rollback_direct_turn(request.user_text)
            yield TurnFailed(
                request.turn_id,
                exc.category,
                exc.safe_message,
                exc.retryable,
            )
            print(f"[DEBUG conversation] yielded TurnFailed from DirectProtocolError", file=sys.stderr, flush=True)
        except (ValueError, TypeError) as exc:
            print(f"[DEBUG conversation] caught ValueError/TypeError: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if prepared:
                print(f"[DEBUG conversation] rolling back because prepared=True", file=sys.stderr, flush=True)
                self.engine._rollback_direct_turn(request.user_text)
            yield TurnFailed(
                request.turn_id,
                "configuration",
                "模型配置不完整，请检查协议、地址和模型。",
            )
            print(f"[DEBUG conversation] yielded TurnFailed from configuration error", file=sys.stderr, flush=True)
        except Exception as exc:
            # 兜底：捕获所有未预料的异常
            print(f"[DEBUG conversation] caught unexpected exception: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
            if prepared:
                self.engine._rollback_direct_turn(request.user_text)
            yield TurnFailed(
                request.turn_id,
                "internal",
                f"内部错误: {exc}",
                False,
            )

