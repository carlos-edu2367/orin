"""The control protocol between ``orin code`` and its SDK host (spec §9.3).

Host to harness: ``initialize`` (the host's hook callbacks and agents), ``interrupt``, ``set_permission_mode``,
``set_model``, ``set_max_thinking_tokens``, ``rewind_files`` and ``mcp_status``. Harness to host: ``can_use_tool``
when a turn asks for a permission, and ``hook_callback`` when a turn runs one of the host's hooks. Every answer the
host gives goes back to the worker through the local API: a permission decision, or a host-request reply.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agentos.agentic.tools.public_names import public_name

from .args import PERMISSION_MODES
from .errors import CliRuntimeError
from .selection import resolve_model

if TYPE_CHECKING:  # pragma: no cover
    from .headless import HeadlessRun

MAX_SDK_HOOKS = 100


def sdk_hooks_layer(hooks: object) -> dict[str, list[dict[str, Any]]]:
    """``initialize.hooks`` (``{event: [{matcher, hookCallbackIds, timeout}]}``) as the cli layer's ``hooks`` setting."""
    layer: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(hooks, dict):
        return layer
    count = 0
    for event, matchers in hooks.items():
        if not isinstance(event, str) or not isinstance(matchers, list):
            continue
        groups: list[dict[str, Any]] = []
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            entries = []
            for callback_id in matcher.get("hookCallbackIds") or []:
                if isinstance(callback_id, str) and callback_id and count < MAX_SDK_HOOKS:
                    entry: dict[str, Any] = {"type": "callback", "callback_id": callback_id}
                    if isinstance(matcher.get("timeout"), (int, float)) and not isinstance(matcher.get("timeout"), bool):
                        entry["timeout"] = matcher["timeout"]
                    entries.append(entry)
                    count += 1
            if entries:
                groups.append({"matcher": str(matcher.get("matcher") or ""), "hooks": entries})
        if groups:
            layer[event] = groups
    return layer


def _broker_input(internal: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The input the host is shown when Orin cannot find the call: the broker's own command or path, or None."""
    command = payload.get("command")
    if internal in {"run_command", "powershell"} and isinstance(command, str) and command:
        return {"command": command}
    path = payload.get("path")
    if isinstance(path, str) and path:
        return {"file_path": path}
    return None


class HostBridge:
    def __init__(self, run: "HeadlessRun") -> None:
        self.run = run
        self._waiting: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.sdk_hooks: dict[str, list[dict[str, Any]]] = {}
        self.sdk_agents: dict[str, Any] = {}

    # -- harness to host ------------------------------------------------------------------------------------------

    def ask(self, request: dict[str, Any], on_answer: Callable[[dict[str, Any]], None]) -> None:
        if self.run.options.input_format != "stream-json":
            # W6-R11: only an SDK host on stream-json answers. Without one the request would be written into the output and
            # the turn would wait the full timeout for an answer that cannot come, so it is refused at once.
            on_answer({"subtype": "error", "error": "no SDK host on this run"})
            return
        request_id = f"orin_{uuid.uuid4().hex[:16]}"
        self._waiting[request_id] = on_answer
        self.run.out.message({"type": "control_request", "request_id": request_id, "request": request})

    def answered(self, response: dict[str, Any]) -> None:
        callback = self._waiting.pop(str(response.get("request_id") or ""), None)
        if callback is not None:
            callback(response)

    def on_turn_event(self, event: dict[str, Any], turn_id: str) -> None:
        kind = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind == "code_mode.decision_required" and payload.get("permission_request_id"):
            self._permission(payload, turn_id)
        elif kind == "host.request" and payload.get("request_id"):
            self._host_request(payload)

    def _permission(self, payload: dict[str, Any], turn_id: str) -> None:
        run = self.run
        request_id = str(payload["permission_request_id"])
        internal = str(payload.get("tool_name") or "")
        invocation = str(payload.get("invocation_id") or "")
        path = f"/v1/conversations/{run.conversation_id}/turns/{turn_id}/tool-permission"

        def decide(decision: str, reason: str | None) -> None:
            try:
                run.client.post(path, {"request_id": request_id, "decision": decision, **({"reason": reason[:500]} if reason else {})})
            except CliRuntimeError as error:
                run._debug(f"permission {request_id} could not be answered: {error}")

        if run.options.permission_prompt_tool != "stdio":
            decide("deny", "nobody can answer a permission prompt in this run")
            return

        # The host is shown the call the broker asked about (W6-R1). A call that is not open here (a subagent's, which
        # the translator never sees) is shown from the broker's own fields, and a request with neither is refused.
        found = run.translator.open_call(invocation, internal) if run.translator is not None and invocation else None
        if found is not None:
            tool_use_id, name, tool_input = found
        else:
            tool_use_id, name = None, public_name(internal)
            broker_input = _broker_input(internal, payload)
            if broker_input is None:
                decide("deny", "orin code cannot show this call to the SDK host")
                return
            tool_input = broker_input

        def answer(response: dict[str, Any]) -> None:
            body = response.get("response") if isinstance(response.get("response"), dict) else {}
            if response.get("subtype") != "success":
                decide("deny", str(response.get("error") or "the host could not decide"))
                return
            if body.get("behavior") == "allow":
                updated = body.get("updatedInput")
                if isinstance(updated, dict) and updated != tool_input:
                    # Orin runs the call it was asked about; it cannot swap in other input (fail closed).
                    decide("deny", "orin code cannot run a tool with input the host changed")
                    return
                decide("allow_once", None)
                return
            decide("deny", str(body.get("message") or "denied by the host"))
            if body.get("interrupt") is True:
                self.interrupt()

        self.ask({
            "subtype": "can_use_tool", "tool_name": name, "input": tool_input, "permission_suggestions": [],
            "tool_use_id": tool_use_id, "blocked_path": payload.get("path") or None,
            "decision_reason": str(payload.get("suggested_rule") or "") or None,
        }, answer)

    def _host_request(self, payload: dict[str, Any]) -> None:
        run = self.run
        request_id = str(payload["request_id"])
        try:
            entry = run.client.get(f"/v1/conversations/{run.conversation_id}/transcript/{payload.get('transcript_id')}")
        except CliRuntimeError as error:
            run._debug(f"host request {request_id} could not be read: {error}")
            return
        body = (entry or {}).get("host_request") if isinstance(entry, dict) else None
        if not isinstance(body, dict):
            return
        kind, inner = body.get("kind"), body.get("request") if isinstance(body.get("request"), dict) else {}

        def reply(response: dict[str, Any]) -> None:
            try:
                run.client.post(f"/v1/conversations/{run.conversation_id}/host-requests/{request_id}", {"response": response})
            except CliRuntimeError as error:
                run._debug(f"host request {request_id} could not be answered: {error}")

        if kind == "hook_callback":
            hook_input = dict(inner.get("input") or {})
            hook_input["session_id"] = run._session_id
            invocation = str(inner.get("tool_use_id") or hook_input.get("tool_use_id") or "")
            mapped = run.translator.tool_use_id_for(invocation) if run.translator is not None and invocation else None
            if mapped:
                hook_input["tool_use_id"] = mapped

            def hook_answer(response: dict[str, Any]) -> None:
                if response.get("subtype") == "success" and isinstance(response.get("response"), dict):
                    reply(response["response"])
                else:
                    reply({"_error": str(response.get("error") or "the host's hook failed")[:500]})

            self.ask({"subtype": "hook_callback", "callback_id": str(inner.get("callback_id") or ""), "input": hook_input,
                      "tool_use_id": mapped or invocation or None}, hook_answer)
        elif kind == "mcp_message":
            def mcp_answer(response: dict[str, Any]) -> None:
                body = response.get("response") if isinstance(response.get("response"), dict) else {}
                if response.get("subtype") == "success" and isinstance(body.get("mcp_response"), dict):
                    reply({"mcp_response": body["mcp_response"]})
                else:
                    reply({"_error": str(response.get("error") or "the host's MCP server did not answer")[:500]})

            self.ask({"subtype": "mcp_message", "server_name": str(inner.get("server_name") or ""),
                      "message": inner.get("message") if isinstance(inner.get("message"), dict) else {}}, mcp_answer)
        else:
            reply({"_error": f"orin code does not relay {kind!r} requests"})

    # -- host to harness ------------------------------------------------------------------------------------------

    def _respond(self, request_id: str, response: dict[str, Any] | None = None, error: str | None = None) -> None:
        if error is not None:
            body: dict[str, Any] = {"subtype": "error", "request_id": request_id, "error": error}
        else:
            body = {"subtype": "success", "request_id": request_id, "response": response or {}}
        self.run.out.message({"type": "control_response", "response": body})

    def interrupt(self) -> None:
        run = self.run
        if run.conversation_id is not None and run.current_turn_id is not None:
            run.client.post(f"/v1/conversations/{run.conversation_id}/cancel", {})

    def handle(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("request_id") or "")
        body = message.get("request") if isinstance(message.get("request"), dict) else {}
        subtype = str(body.get("subtype") or "")
        handler = getattr(self, f"_on_{subtype}", None)
        if handler is None:
            self._respond(request_id, error=f"orin code does not handle {subtype or 'this'} requests")
            return
        try:
            self._respond(request_id, handler(body))
        except (CliRuntimeError, ValueError) as error:
            self._respond(request_id, error=str(error))

    def _on_initialize(self, body: dict[str, Any]) -> dict[str, Any]:
        self.sdk_hooks = sdk_hooks_layer(body.get("hooks"))
        agents = body.get("agents")
        self.sdk_agents = dict(agents) if isinstance(agents, dict) else {}
        return {"commands": [], "agents": sorted(self.sdk_agents), "output_style": "default",
                "available_output_styles": ["default"], "models": [], "account": {}, "pid": os.getpid()}

    def _on_interrupt(self, body: dict[str, Any]) -> dict[str, Any]:
        self.interrupt()
        return {}

    def _on_set_permission_mode(self, body: dict[str, Any]) -> dict[str, Any]:
        mode = str(body.get("mode") or "")
        if mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode {mode!r}")
        if mode == "bypassPermissions":  # W6-R4: refused, not ignored, unless the run may bypass and policy allows it
            if self.run.bypass_policy:
                raise ValueError("bypass mode is disabled by policy (permissions.disableBypassPermissionsMode)")
            if not self.run.bypass_allowed:
                raise ValueError("bypass mode was not allowed for this run: start orin code with --allow-dangerously-skip-permissions")
        self.run.options.permission_mode = mode
        self.run.options.dangerously_skip_permissions = False
        return {}

    def _on_set_model(self, body: dict[str, Any]) -> dict[str, Any]:
        run = self.run
        model = body.get("model")
        if model is None:
            run.selection = run.initial_selection
        else:
            run.selection = resolve_model(run.catalog, str(model), run.options.provider)
        if run.translator is not None:
            run.translator.model = run.selection.model_id
        return {}

    def _on_set_max_thinking_tokens(self, body: dict[str, Any]) -> dict[str, Any]:
        value = body.get("max_thinking_tokens")
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("max_thinking_tokens must be a whole number")
        self.run.options.max_thinking_tokens = value
        self.run.options.effort = None
        self.run.options.thinking = "disabled" if value == 0 else None
        return {}

    def _on_mcp_status(self, body: dict[str, Any]) -> dict[str, Any]:
        listed = self.run.client.get("/v1/mcp/servers")
        items = listed.get("items") if isinstance(listed, dict) else listed
        servers = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                state = str(item.get("state") or "")
                servers.append({"name": str(item.get("display_name") or item.get("slug") or ""),
                                "status": "connected" if state == "active" else (state or "unknown")})
        return {"mcpServers": servers}

    def _on_rewind_files(self, body: dict[str, Any]) -> dict[str, Any]:
        run = self.run
        turn_id = run.prompt_turns.get(str(body.get("user_message_id") or ""))
        if turn_id is None or run.conversation_id is None:
            raise ValueError("that user message is not one of this run's prompts")
        conversation = run.client.get(f"/v1/conversations/{run.conversation_id}")
        messages = conversation.get("messages") if isinstance(conversation, dict) else []
        message_id = next((str(item.get("message_id")) for item in messages or []
                           if isinstance(item, dict) and item.get("role") == "user" and item.get("turn_id") == turn_id), None)
        if message_id is None:
            raise ValueError("the prompt's message was not found")
        run.client.post(f"/v1/conversations/{run.conversation_id}/messages/{message_id}/rewind", {"mode": "code"})
        return {}


__all__ = ["HostBridge", "MAX_SDK_HOOKS", "sdk_hooks_layer"]
