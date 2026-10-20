"""Orin's activity events as Claude Code's headless messages (spec §9.3).

``Translator`` turns the conversation's activity events for one turn into the messages an SDK host reads:
``assistant`` (one per content block, sharing the model call's message id), ``user`` (tool results),
``stream_event`` (Anthropic's streaming events, whatever the provider), ``system`` (init, compact boundaries, hooks)
and the closing ``result``. Only the main agent's events become messages; a subagent's work reaches the host as the
result of its ``Agent`` tool call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentos.agentic.tools.public_names import arguments_out, public_name

RESULT_SUBTYPES: dict[str, str] = {"MAX_TURNS": "error_max_turns", "MAX_BUDGET_USD": "error_max_budget_usd"}
_MAIN_AGENT_SUFFIX = ":main"


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _new_tool_use_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"


@dataclass
class TurnUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def as_usage(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "server_tool_use": {"web_search_requests": 0}, "service_tier": "standard",
        }


@dataclass
class _Block:
    kind: str  # "text" | "thinking"
    index: int
    parts: list[str] = field(default_factory=list)


class Translator:
    """Stateful, one per run: feed it a turn's events in order, then ``result(...)``."""

    def __init__(self, *, session_id: str, model: str, partial: bool = False, hook_events: bool = False,
                 new_uuid: Callable[[], str] = lambda: str(uuid.uuid4())) -> None:
        self.session_id = session_id
        self.model = model
        self.partial = partial
        self.hook_events = hook_events
        self._uuid = new_uuid
        self._message_id: str | None = None
        self._block: _Block | None = None
        self._next_index = 0
        # invocation id -> [(tool_use id, public name, input, internal name)]
        self._open_tools: dict[str, list[tuple[str, str, dict[str, Any], str]]] = {}
        self._text_since_tools: list[str] = []
        self.model_calls = 0
        self.denials: list[dict[str, Any]] = []

    # -- envelopes ------------------------------------------------------------------------------------------------

    def _base(self, kind: str) -> dict[str, Any]:
        return {"type": kind, "session_id": self.session_id, "uuid": self._uuid()}

    def init_message(self, *, cwd: str, tools: list[str], permission_mode: str, mcp_servers: list[dict[str, str]],
                     slash_commands: list[str], version: str, output_style: str = "default",
                     agents: list[str] | None = None) -> dict[str, Any]:
        return {
            **self._base("system"), "subtype": "init", "cwd": cwd, "tools": tools, "mcp_servers": mcp_servers,
            "model": self.model, "permissionMode": permission_mode, "slash_commands": slash_commands,
            "apiKeySource": "none", "claude_code_version": version, "output_style": output_style,
            "agents": agents or [], "skills": [], "plugins": [],
        }

    def _stream(self, event: dict[str, Any]) -> dict[str, Any]:
        return {**self._base("stream_event"), "event": event, "parent_tool_use_id": None}

    def _assistant(self, block: dict[str, Any], stop_reason: str | None = None) -> dict[str, Any]:
        return {
            **self._base("assistant"),
            "message": {
                "id": self._message_id, "type": "message", "role": "assistant", "model": self.model,
                "content": [block], "stop_reason": stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
            "parent_tool_use_id": None,
        }

    # -- message and block boundaries -----------------------------------------------------------------------------

    def _start_message(self, out: list[dict[str, Any]]) -> None:
        if self._message_id is not None:
            return
        self._message_id = _new_message_id()
        self._next_index = 0
        self.model_calls += 1
        if self.partial:
            out.append(self._stream({"type": "message_start", "message": {
                "id": self._message_id, "type": "message", "role": "assistant", "model": self.model, "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0},
            }}))

    def _close_block(self, out: list[dict[str, Any]]) -> None:
        block = self._block
        if block is None:
            return
        self._block = None
        text = "".join(block.parts)
        if self.partial:
            out.append(self._stream({"type": "content_block_stop", "index": block.index}))
        if block.kind == "text":
            out.append(self._assistant({"type": "text", "text": text}))
        else:
            out.append(self._assistant({"type": "thinking", "thinking": text, "signature": ""}))

    def _end_message(self, out: list[dict[str, Any]], stop_reason: str) -> None:
        self._close_block(out)
        if self._message_id is None:
            return
        if self.partial:
            out.append(self._stream({"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                     "usage": {"output_tokens": 0}}))
            out.append(self._stream({"type": "message_stop"}))
        self._message_id = None

    def _delta(self, out: list[dict[str, Any]], kind: str, text: str) -> None:
        if not text:
            return
        if self._open_tools:  # text after tool calls belongs to the next model call
            self._end_message(out, "tool_use")
        self._start_message(out)
        if self._block is not None and self._block.kind != kind:
            self._close_block(out)
        if self._block is None:
            self._block = _Block(kind, self._next_index)
            self._next_index += 1
            if self.partial:
                start = {"type": "text", "text": ""} if kind == "text" else {"type": "thinking", "thinking": "", "signature": ""}
                out.append(self._stream({"type": "content_block_start", "index": self._block.index, "content_block": start}))
        self._block.parts.append(text)
        if kind == "text":
            self._text_since_tools.append(text)
        if self.partial:
            delta = {"type": "text_delta", "text": text} if kind == "text" else {"type": "thinking_delta", "thinking": text}
            out.append(self._stream({"type": "content_block_delta", "index": self._block.index, "delta": delta}))

    # -- events ---------------------------------------------------------------------------------------------------

    @staticmethod
    def is_main_agent(event: Mapping[str, Any]) -> bool:
        agent = str(event.get("agent_id") or "")
        return not event.get("parent_agent_id") and (not agent or agent.endswith(_MAIN_AGENT_SUFFIX))

    def feed(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        kind = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if kind == "context.compacted":
            out.append({**self._base("system"), "subtype": "compact_boundary", "compact_metadata": {
                "trigger": "manual" if payload.get("trigger") == "manual" else "auto",
                "pre_tokens": int(payload.get("pre_tokens") or payload.get("used_tokens") or 0),
            }})
            return out
        if kind == "plugin.hook_executed" and self.hook_events:
            out.append({**self._base("system"), "subtype": "hook_response",
                        "hook_name": str(payload.get("hook_name") or payload.get("name") or ""),
                        "hook_event": str(payload.get("hook_event") or payload.get("event") or ""),
                        "output": str(payload.get("output") or ""), "exit_code": payload.get("exit_code")})
            return out
        if not self.is_main_agent(event):
            return out
        if kind == "assistant.delta":
            self._delta(out, "text", str(payload.get("content") or ""))
        elif kind == "agent.thought_delta":
            self._delta(out, "thinking", str(payload.get("delta") or payload.get("content") or ""))
        elif kind == "tool.started":
            self._tool_use(out, payload)
        elif kind == "tool.finished":
            self._tool_result(out, payload)
        return out

    def _tool_use(self, out: list[dict[str, Any]], payload: Mapping[str, Any]) -> None:
        internal = str(payload.get("tool_name") or "tool")
        invocation = str(payload.get("invocation_id") or "")
        self._start_message(out)
        self._close_block(out)
        tool_use_id = _new_tool_use_id()
        name = public_name(internal)
        raw_input = payload.get("tool_input")
        tool_input = arguments_out(internal, dict(raw_input)) if isinstance(raw_input, Mapping) else {}
        block = {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}
        if self.partial:
            index = self._next_index
            self._next_index += 1
            out.append(self._stream({"type": "content_block_start", "index": index,
                                     "content_block": {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}}))
            out.append(self._stream({"type": "content_block_delta", "index": index,
                                     "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input, ensure_ascii=False)}}))
            out.append(self._stream({"type": "content_block_stop", "index": index}))
        out.append(self._assistant(block))
        self._open_tools.setdefault(invocation, []).append((tool_use_id, name, tool_input, internal))
        self._text_since_tools = []

    def _tool_result(self, out: list[dict[str, Any]], payload: Mapping[str, Any]) -> None:
        invocation = str(payload.get("invocation_id") or "")
        pending = self._open_tools.get(invocation)
        if not pending:
            return  # a result with no call the host saw (Orin's own bookkeeping, e.g. todo settling)
        tool_use_id, name, tool_input, _ = pending.pop(0)
        if not pending:
            del self._open_tools[invocation]
        status = str(payload.get("status") or "succeeded")
        is_error = status != "succeeded"
        content = str(payload.get("tool_result") if payload.get("tool_result") is not None else payload.get("summary") or "")
        if self._message_id is not None and not self._open_tools:
            self._end_message(out, "tool_use")
        out.append({
            **self._base("user"),
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error},
            ]},
            "parent_tool_use_id": None,
            "tool_use_result": content,
        })
        if is_error and str(payload.get("error_code") or "") == "CODE_PERMISSION_DENIED":
            self.denials.append({"tool_name": name, "tool_use_id": tool_use_id, "tool_input": tool_input})

    def open_tool(self, internal: str) -> tuple[str, str, dict[str, Any]] | None:
        """The latest call of this tool still waiting for its result: ``(tool_use id, public name, input)``."""
        for calls in reversed(list(self._open_tools.values())):
            for tool_use_id, name, tool_input, called in reversed(calls):
                if called == internal:
                    return tool_use_id, name, tool_input
        return None

    def tool_use_id_for(self, invocation: str) -> str | None:
        calls = self._open_tools.get(invocation)
        return calls[0][0] if calls else None

    # -- end of the turn ------------------------------------------------------------------------------------------

    @property
    def result_text(self) -> str:
        return "".join(self._text_since_tools).strip()

    def result(self, *, state: str, error_code: str | None, usage: TurnUsage, duration_ms: int, duration_api_ms: int,
               structured_output: Any = None, errors: list[str] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        self._end_message(out, "end_turn")
        if state == "completed":
            subtype = "success"
        else:
            subtype = RESULT_SUBTYPES.get(str(error_code or ""), "error_during_execution")
        message: dict[str, Any] = {
            **self._base("result"), "subtype": subtype, "is_error": subtype != "success",
            "duration_ms": int(duration_ms), "duration_api_ms": int(duration_api_ms), "num_turns": max(1, self.model_calls),
            "result": self.result_text, "stop_reason": "end_turn" if subtype == "success" else None,
            "total_cost_usd": float(usage.cost_usd), "usage": usage.as_usage(),
            "modelUsage": {self.model: {
                "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens, "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": float(usage.cost_usd),
            }},
            "permission_denials": list(self.denials),
        }
        if structured_output is not None:
            message["structured_output"] = structured_output
        if subtype != "success":
            message["errors"] = list(errors or ([str(error_code)] if error_code else ["the turn did not complete"]))
        out.append(message)
        self.model_calls = 0
        self.denials = []
        self._text_since_tools = []
        self._open_tools = {}
        return out


__all__ = ["RESULT_SUBTYPES", "Translator", "TurnUsage"]
