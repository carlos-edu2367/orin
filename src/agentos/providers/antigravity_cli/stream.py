"""JSONL stream translator for Google Antigravity CLI (`agy --input-format stream-json --output-format stream-json`)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agentos.providers.antigravity_cli.media_gate import MediaReadPolicy
from agentos.providers.cli_adapter import (
    CliEvent,
    CliEventKind,
    CliInit,
    CliResult,
    CliUsage,
    RateLimitState,
)

FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {
        "ask_custom_permission",
        "ask_permission",
        "ask_question",
        "browser_click_element",
        "browser_drag_pixel_to_pixel",
        "browser_get_dom",
        "browser_get_network_request",
        "browser_input",
        "browser_list_network_requests",
        "browser_mouse_down",
        "browser_mouse_up",
        "browser_move_mouse",
        "browser_press_key",
        "browser_refresh_page",
        "browser_resize_window",
        "browser_scroll",
        "browser_scroll_dom",
        "browser_select_option",
        "browser_subagent",
        "call_mcp_tool",
        "capture_browser_console_logs",
        "capture_browser_screenshot",
        "click_browser_pixel",
        "command_status",
        "define_subagent",
        "delete_knowledge",
        "execute_browser_javascript",
        "find_by_name",
        "finish",
        "generate_image",
        "grep_search",
        "invoke_subagent",
        "list_browser_pages",
        "list_dir",
        "list_permissions",
        "list_plugin_accounts",
        "list_resources",
        "manage_inbox",
        "manage_subagents",
        "manage_task",
        "multi_replace_file_content",
        "notebook_edit",
        "notebook_execution",
        "open_browser_url",
        "read_browser_page",
        "read_file",
        "read_resource",
        "read_url_content",
        "replace_file_content",
        "run_command",
        "run_workflow",
        "schedule",
        "search_marketplace",
        "search_web",
        "sed_file",
        "send_command_input",
        "send_message",
        "view_file",
        "wait",
        "wait_5_seconds",
        "write_to_file",
        "forbidden_builtin_tool",
    }
)

# agy's own tools that still stop the process when the model calls one (Orin's tools are the only ones a
# turn may use), but that neither change anything nor reach outside it, so the turn resumes from Orin's
# history with a note instead of failing. The process is killed as soon as the call is announced, so
# whatever the tool returned never reaches Orin's history nor a next model call. One by one:
#   manage_task, command_status: list, read or end the tasks/commands agy itself started with run_command;
#       run_command stops the process before it can start one, so there is never a task to act on.
#   list_dir, find_by_name, grep_search, view_file: read only, in agy's own empty per-process folder
#       (`allowNonWorkspaceAccess: false`, `read_file(*)` denied in its settings).
#   list_resources, read_resource: read the resources of agy's MCP servers, which are only Orin's
#       (`inheritMcp: false`).
#   list_permissions, list_plugin_accounts: read agy's own settings and plugin account list.
#   wait, wait_5_seconds, finish: wait or end agy's task; no effect.
#   ask_question, ask_permission, ask_custom_permission: ask through agy's UI, which a headless run has
#       none of; a grant "NEVER grants file, command, network, or tool access" (agy's own description).
#   generate_image: the process is killed when the call is announced, so nothing is generated; the resumed
#       turn is directed to Orin's generate_image tool instead.
#   read_file: only reaches here when it is NOT an allowed media read (a Gemini turn's audio/video/image
#       file inside the workspace passes untouched); read only, and the media gate hook denies it anyway.
# Everything else stays a security abort, as before: shell and command input (run_command,
# send_command_input), file writes (write_to_file, replace_file_content, multi_replace_file_content,
# sed_file, delete_knowledge), the browser (browser_*, open_browser_url, read_browser_page,
# capture_browser_*, click_browser_pixel, execute_browser_javascript, list_browser_pages), the network
# (read_url_content, search_web, search_marketplace), agy's own subagents (invoke_subagent,
# define_subagent, manage_subagents, browser_subagent), notebooks (notebook_edit, notebook_execution),
# other MCP calls (call_mcp_tool), workflows and messages (run_workflow, schedule, send_message,
# manage_inbox) and any tool not named in the list (forbidden_builtin_tool).
RECOVERABLE_ANTIGRAVITY_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {
        "manage_task",
        "command_status",
        "list_dir",
        "find_by_name",
        "grep_search",
        "view_file",
        "list_resources",
        "read_resource",
        "list_permissions",
        "list_plugin_accounts",
        "wait",
        "wait_5_seconds",
        "finish",
        "ask_question",
        "ask_permission",
        "ask_custom_permission",
        "generate_image",
        "read_file",
    }
)

# Which Orin tools (internal names) do what a recoverable agy tool was called for; the resume note names them.
ANTIGRAVITY_BUILTIN_TOOL_ALTERNATIVES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("manage_task", ("read_process_output", "stop_process", "task_list")),
    ("command_status", ("read_process_output",)),
    ("list_dir", ("list_files",)),
    ("find_by_name", ("glob_files", "list_files")),
    ("grep_search", ("search_files",)),
    ("view_file", ("read_file", "view_file")),
    ("list_resources", ("list_mcp_resources",)),
    ("read_resource", ("read_mcp_resource",)),
    ("wait", ("read_process_output", "check_subagent")),
    ("wait_5_seconds", ("read_process_output", "check_subagent")),
    ("ask_question", ("ask_user",)),
    ("ask_permission", ("ask_user",)),
    ("ask_custom_permission", ("ask_user",)),
    ("generate_image", ("generate_image",)),
    ("read_file", ("read_file", "view_file")),
)

_ORIN_TOOL_PREFIXES: tuple[str, ...] = (
    "mcp_orin_",
    "orin/",
    "mcp__orin__",
)


def native_read_file_tolerated(
    tool_name: str,
    tool_info: Any,
    state: str,
    policy: MediaReadPolicy | None,
) -> bool:
    """True when a native `read_file` step is not a reason to stop the agy process.

    With the media gate on (`policy`), the PreToolUse hook decides before anything is read: a call it
    denies still shows up as a step, first ACTIVE, then ERROR with the hook's message, and the model must
    get that message to correct itself. So only a step that COMPLETED (`DONE`) on a path outside the
    policy is a breach, and that still stops the process (defense in depth). Without a policy (the gate is
    off for this model) every `read_file` stays forbidden, as before.
    """
    if tool_name != "read_file" or policy is None:
        return False
    if state and state != "DONE":
        return True
    if not isinstance(tool_info, Mapping):
        return False
    raw_params = tool_info.get("parameters")
    if not isinstance(raw_params, Mapping):
        raw_params = tool_info.get("args")
    if not isinstance(raw_params, Mapping):
        return False
    raw_path = (
        raw_params.get("Path")
        if "Path" in raw_params
        else raw_params.get("AbsolutePath")
    )
    allowed, _ = policy.check(raw_path)
    return allowed


def _non_neg_int(mapping: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
    return 0


def _strip_orin_prefix(tool_name: str) -> str | None:
    for prefix in _ORIN_TOOL_PREFIXES:
        if tool_name.startswith(prefix):
            stripped = tool_name[len(prefix) :].strip()
            return stripped if stripped else None
    return None


def _build_cli_usage(
    raw_in: int,
    out_tok: int,
    cache_read: int,
    think_tok: int,
    *,
    context_window_used_tokens: int | None = None,
) -> CliUsage:
    return CliUsage(
        input_tokens=max(0, raw_in - cache_read),
        output_tokens=max(0, out_tok),
        cache_read_input_tokens=max(0, cache_read),
        cache_creation_input_tokens=0,
        reasoning_tokens=think_tok if think_tok > 0 else None,
        context_window_used_tokens=(
            max(0, context_window_used_tokens)
            if context_window_used_tokens is not None
            else max(0, raw_in)
        ),
    )


class AntigravityStreamTranslator:
    """Line-by-line NDJSON stream translator for `agy --output-format stream-json`."""

    def __init__(
        self,
        *,
        model: str = "",
        allowed_media_root: Path | str | None = None,
        media_policy: MediaReadPolicy | None = None,
    ) -> None:
        self._model: str = model.strip() if isinstance(model, str) else ""
        self.allowed_media_root: Path | None = (
            Path(allowed_media_root) if allowed_media_root is not None else None
        )
        self.media_policy: MediaReadPolicy | None = media_policy
        self.init: CliInit | None = None
        self.result: CliResult | None = None
        self.rate_limit: RateLimitState | None = None
        self.stop_reason: str | None = None
        self._latest_usage: CliUsage = CliUsage()
        self._turn_text_parts: list[str] = []
        self._turn_step_usages: dict[int, dict[str, int]] = {}
        self._prev_result_raw_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cache_read_tokens": 0,
        }
        self._num_turns: int = 0

    def latest_usage(self) -> CliUsage:
        if self.result is not None:
            return self.result.usage
        return self._latest_usage

    def _clear_turn_items(self) -> None:
        self._turn_text_parts.clear()
        self._turn_step_usages.clear()

    def reset_result(self) -> None:
        self.result = None
        self.stop_reason = None
        self._clear_turn_items()

    def flush_pending(self) -> list[CliEvent]:
        return []

    def _session_id(self, packet: Mapping[str, Any] | None = None) -> str:
        if self.init is not None and self.init.session_id:
            return self.init.session_id
        if isinstance(packet, Mapping):
            cid = packet.get("conversation_id")
            if isinstance(cid, str) and cid:
                return cid
        return ""

    def _compute_turn_usage(
        self, result_usage_obj: Mapping[str, Any] | None
    ) -> CliUsage:
        if self._turn_step_usages:
            raw_in = sum(
                u.get("input_tokens", 0) for u in self._turn_step_usages.values()
            )
            out_tok = sum(
                u.get("output_tokens", 0)
                for u in self._turn_step_usages.values()
            )
            cache_read = sum(
                u.get("cache_read_tokens", 0)
                for u in self._turn_step_usages.values()
            )
            think_tok = sum(
                u.get("thinking_tokens", 0)
                for u in self._turn_step_usages.values()
            )
            latest_step_idx = max(self._turn_step_usages)
            latest_step_in = self._turn_step_usages[latest_step_idx].get(
                "input_tokens", 0
            )
            if isinstance(result_usage_obj, Mapping):
                self._prev_result_raw_usage = {
                    "input_tokens": _non_neg_int(
                        result_usage_obj, "input_tokens"
                    ),
                    "output_tokens": _non_neg_int(
                        result_usage_obj, "output_tokens"
                    ),
                    "thinking_tokens": _non_neg_int(
                        result_usage_obj, "thinking_tokens"
                    ),
                    "cache_read_tokens": _non_neg_int(
                        result_usage_obj, "cache_read_tokens"
                    ),
                }
            return _build_cli_usage(
                raw_in,
                out_tok,
                cache_read,
                think_tok,
                context_window_used_tokens=latest_step_in,
            )

        if isinstance(result_usage_obj, Mapping):
            cum_in = _non_neg_int(result_usage_obj, "input_tokens")
            cum_out = _non_neg_int(result_usage_obj, "output_tokens")
            cum_think = _non_neg_int(result_usage_obj, "thinking_tokens")
            cum_cache = _non_neg_int(result_usage_obj, "cache_read_tokens")

            prev = self._prev_result_raw_usage
            raw_in = (
                cum_in - prev["input_tokens"]
                if cum_in >= prev["input_tokens"]
                else cum_in
            )
            out_tok = (
                cum_out - prev["output_tokens"]
                if cum_out >= prev["output_tokens"]
                else cum_out
            )
            think_tok = (
                cum_think - prev["thinking_tokens"]
                if cum_think >= prev["thinking_tokens"]
                else cum_think
            )
            cache_read = (
                cum_cache - prev["cache_read_tokens"]
                if cum_cache >= prev["cache_read_tokens"]
                else cum_cache
            )
            if cum_in > 0 or cum_out > 0:
                self._prev_result_raw_usage = {
                    "input_tokens": cum_in,
                    "output_tokens": cum_out,
                    "thinking_tokens": cum_think,
                    "cache_read_tokens": cum_cache,
                }
            return _build_cli_usage(raw_in, out_tok, cache_read, think_tok)

        return CliUsage()

    def _effective_media_policy(self) -> MediaReadPolicy | None:
        if self.media_policy is not None:
            return self.media_policy
        if self.allowed_media_root is not None:
            return MediaReadPolicy(roots=(str(self.allowed_media_root),))
        return None

    def _is_allowed_native_media_read(
        self,
        raw_tool_name: str,
        tool_info: Mapping[str, Any],
        state: str = "DONE",
    ) -> bool:
        return native_read_file_tolerated(
            raw_tool_name, tool_info, state, self._effective_media_policy()
        )

    def feed_line(self, line: str) -> list[CliEvent]:
        if not isinstance(line, str):
            return []
        stripped = line.strip()
        if not stripped:
            return []
        try:
            packet = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if not isinstance(packet, Mapping):
            return []

        event_type = str(
            packet.get("event") or packet.get("type") or ""
        ).strip()

        if event_type == "init":
            conv_id = str(packet.get("conversation_id") or "").strip()
            init_obj = (
                packet["init"]
                if isinstance(packet.get("init"), Mapping)
                else {}
            )
            raw_model = str(init_obj.get("model") or "").strip()
            resolved_model = raw_model or self._model or "antigravity"

            raw_tools = init_obj.get("tools")
            normalized_tools: list[str] = []
            if isinstance(raw_tools, list):
                for item in raw_tools:
                    if not isinstance(item, str) or not item.strip():
                        continue
                    t_str = item.strip()
                    orin_canonical = _strip_orin_prefix(t_str)
                    if orin_canonical is not None:
                        normalized_tools.append(f"orin/{orin_canonical}")
                    elif (
                        t_str in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS
                        and t_str != "forbidden_builtin_tool"
                    ):
                        # Static list of built-in tools emitted by `agy` in `init.tools`
                        continue
                    else:
                        normalized_tools.append(t_str)

            self.init = CliInit(
                session_id=conv_id,
                model=resolved_model,
                api_key_source="none",
                mcp_servers={"orin": "connected"},
                tools=tuple(normalized_tools),
            )
            return [
                CliEvent(
                    kind=CliEventKind.INIT,
                    init=self.init,
                    raw=packet,
                )
            ]

        if event_type == "step_update":
            su = packet.get("step_update")
            if not isinstance(su, Mapping):
                return []
            step_type_raw = str(su.get("step_type") or "").strip()
            step_type = step_type_raw.lower()
            state = str(su.get("state") or "").strip().upper()
            conv_id = (
                str(su.get("conversation_id") or "").strip()
                or self._session_id(packet)
            )
            raw_idx = (
                su.get("step_index")
                if su.get("step_index") is not None
                else su.get("step_idx")
            )
            if isinstance(raw_idx, int) and not isinstance(raw_idx, bool):
                step_idx = raw_idx
            else:
                step_idx = max(self._turn_step_usages, default=0)

            if step_type == "user_input":
                if state == "DONE":
                    self._num_turns += 1
                return []

            if step_type in ("agent_response", "thought", "thinking", "reasoning"):
                prev_step_before = self._turn_step_usages.get(step_idx)
                prev_think_tokens = prev_step_before.get("thinking_tokens", 0) if prev_step_before else 0
                new_think_tokens = 0
                usage_obj = su.get("usage")
                if isinstance(usage_obj, Mapping):
                    new_usage = {
                        "input_tokens": _non_neg_int(
                            usage_obj, "input_tokens"
                        ),
                        "output_tokens": _non_neg_int(
                            usage_obj, "output_tokens"
                        ),
                        "thinking_tokens": _non_neg_int(
                            usage_obj, "thinking_tokens"
                        ),
                        "cache_read_tokens": _non_neg_int(
                            usage_obj, "cache_read_tokens"
                        ),
                    }
                    prev_step = self._turn_step_usages.get(step_idx)
                    new_think_tokens = new_usage["thinking_tokens"]
                    if prev_step is not None or any(v > 0 for v in new_usage.values()):
                        merged_step = {
                            k: max(prev_step.get(k, 0) if prev_step else 0, v)
                            for k, v in new_usage.items()
                        }
                        self._turn_step_usages[step_idx] = merged_step
                        latest_step_idx = max(self._turn_step_usages)
                        latest_step_in = self._turn_step_usages[latest_step_idx].get(
                            "input_tokens", 0
                        )
                        step_usage = _build_cli_usage(
                            merged_step["input_tokens"],
                            merged_step["output_tokens"],
                            merged_step["cache_read_tokens"],
                            merged_step["thinking_tokens"],
                            context_window_used_tokens=latest_step_in,
                        )
                        self._latest_usage = step_usage

                events: list[CliEvent] = []
                text_delta = su.get("text_delta")
                thinking_text = ""
                for th_key in ("thinking_delta", "thought_delta", "thinking", "thought", "reasoning"):
                    th_val = su.get(th_key)
                    if isinstance(th_val, str) and th_val != "":
                        thinking_text = th_val
                        break
                if thinking_text != "":
                    events.append(
                        CliEvent(
                            kind=CliEventKind.THINKING_DELTA,
                            text=thinking_text,
                            raw=packet,
                        )
                    )
                elif (
                    step_type in ("thought", "thinking", "reasoning")
                    or (new_think_tokens > prev_think_tokens and not (isinstance(text_delta, str) and text_delta != ""))
                ):
                    events.append(
                        CliEvent(
                            kind=CliEventKind.THINKING_DELTA,
                            text="",
                            raw=packet,
                        )
                    )
                if isinstance(text_delta, str) and text_delta != "":
                    self._turn_text_parts.append(text_delta)
                    events.append(
                        CliEvent(
                            kind=CliEventKind.TEXT_DELTA,
                            text=text_delta,
                            raw=packet,
                        )
                    )
                if step_type == "agent_response" and state == "DONE" and self._turn_text_parts:
                    events.append(
                        CliEvent(
                            kind=CliEventKind.ASSISTANT_MESSAGE,
                            text="".join(self._turn_text_parts),
                            raw=packet,
                        )
                    )
                return events

            if (
                step_type == "tool"
                or su.get("tool_name") is not None
                or isinstance(su.get("tool_info"), Mapping)
                or step_type_raw in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS
            ):
                tool_info = (
                    su["tool_info"]
                    if isinstance(su.get("tool_info"), Mapping)
                    else {}
                )
                raw_tool_name = str(
                    su.get("tool_name")
                    or tool_info.get("name")
                    or (step_type_raw if step_type != "tool" else "")
                    or ""
                ).strip()
                tool_use_id = f"{conv_id}:{step_idx}"
                canonical_name = _strip_orin_prefix(raw_tool_name)
                if canonical_name is None:
                    if self._is_allowed_native_media_read(
                        raw_tool_name, tool_info, state
                    ):
                        return []
                    bad_name = (
                        raw_tool_name
                        if raw_tool_name in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS
                        else "forbidden_builtin_tool"
                    )
                    return [
                        CliEvent(
                            kind=CliEventKind.TOOL_USE_START,
                            tool_use_id=tool_use_id,
                            tool_name=bad_name,
                            raw=packet,
                        )
                    ]

                if state != "DONE":
                    return []

                raw_params = tool_info.get("parameters")
                params: dict[str, Any] = (
                    dict(raw_params)
                    if isinstance(raw_params, Mapping)
                    else {}
                )
                output = str(tool_info.get("output") or "")
                return [
                    CliEvent(
                        kind=CliEventKind.TOOL_USE_START,
                        tool_use_id=tool_use_id,
                        tool_name=canonical_name,
                        tool_input=params,
                        raw=packet,
                    ),
                    CliEvent(
                        kind=CliEventKind.TOOL_USE_END,
                        tool_use_id=tool_use_id,
                        tool_name=canonical_name,
                        tool_input=params,
                        raw=packet,
                    ),
                    CliEvent(
                        kind=CliEventKind.TOOL_RESULT,
                        tool_use_id=tool_use_id,
                        tool_name=canonical_name,
                        text=output,
                        raw=packet,
                    ),
                ]

            return []

        if event_type == "result":
            res_obj = packet.get("result")
            if not isinstance(res_obj, Mapping):
                return []
            conv_id = (
                str(res_obj.get("conversation_id") or "").strip()
                or self._session_id(packet)
            )
            status = str(res_obj.get("status") or "").strip().upper()
            finished_reason = str(res_obj.get("finished_reason") or "").strip()

            is_success = status == "SUCCESS" and finished_reason == ""
            is_error = not is_success

            raw_usage_obj = (
                res_obj["usage"]
                if isinstance(res_obj.get("usage"), Mapping)
                else None
            )
            usage = self._compute_turn_usage(raw_usage_obj)
            self._latest_usage = usage

            dur_sec = res_obj.get("duration_seconds")
            duration_ms = (
                int(float(dur_sec) * 1000)
                if isinstance(dur_sec, (int, float))
                and not isinstance(dur_sec, bool)
                and dur_sec >= 0
                else 0
            )
            num_turns_raw = res_obj.get("num_turns")
            num_turns = (
                int(num_turns_raw)
                if isinstance(num_turns_raw, int)
                and not isinstance(num_turns_raw, bool)
                and num_turns_raw > 0
                else max(1, self._num_turns)
            )

            if is_error:
                raw_err = str(res_obj.get("error") or "").strip()
                if not raw_err:
                    if finished_reason:
                        raw_err = f"Antigravity CLI finished with status={status or 'UNKNOWN'} finished_reason={finished_reason}"
                    else:
                        raw_err = f"Antigravity CLI finished with status={status or 'UNKNOWN'}"
                elif (
                    finished_reason
                    and finished_reason.lower() not in raw_err.lower()
                ):
                    raw_err = f"{raw_err} (finished_reason={finished_reason})"

                self.stop_reason = finished_reason or status.lower() or "error"
                self.result = CliResult(
                    subtype="error",
                    is_error=True,
                    result_text=raw_err,
                    stop_reason=self.stop_reason,
                    num_turns=num_turns,
                    duration_ms=duration_ms,
                    session_id=conv_id,
                    api_error_status=None,
                    usage=usage,
                    model_usage={},
                )
                self._clear_turn_items()
                result_event = CliEvent(
                    kind=CliEventKind.RESULT,
                    text=raw_err,
                    result=self.result,
                    usage=usage,
                    raw=packet,
                )
                if self.init is None:
                    return [result_event]
                return [
                    CliEvent(
                        kind=CliEventKind.USAGE,
                        usage=usage,
                        raw=packet,
                    ),
                    result_event,
                ]

            resp_text = "".join(self._turn_text_parts) or str(
                res_obj.get("response") or ""
            )
            self.stop_reason = "end_turn"
            model_id = (
                self.init.model
                if self.init is not None and self.init.model
                else (self._model or "antigravity")
            )
            self.result = CliResult(
                subtype="success",
                is_error=False,
                result_text=resp_text,
                stop_reason="end_turn",
                num_turns=num_turns,
                duration_ms=duration_ms,
                session_id=conv_id,
                api_error_status=None,
                usage=usage,
                model_usage={model_id: usage},
            )
            self._clear_turn_items()
            return [
                CliEvent(
                    kind=CliEventKind.USAGE,
                    usage=usage,
                    raw=packet,
                ),
                CliEvent(
                    kind=CliEventKind.RESULT,
                    text=resp_text,
                    result=self.result,
                    usage=usage,
                    raw=packet,
                ),
            ]

        return []


__all__ = [
    "ANTIGRAVITY_BUILTIN_TOOL_ALTERNATIVES",
    "AntigravityStreamTranslator",
    "FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS",
    "RECOVERABLE_ANTIGRAVITY_BUILTIN_TOOLS",
    "native_read_file_tolerated",
]
