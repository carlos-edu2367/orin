"""Bus subscriber that runs the hooks matching an event and merges their decisions."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agentos.agentic.lifecycle import LifecycleDecision, LifecycleEvent

from .config import HookSpec
from .matching import matcher_applies
from .protocol import HookRun, decision_from_command, decision_from_verdict

logger = logging.getLogger(__name__)


class HookDispatcher:
    def __init__(
        self,
        specs: tuple[HookSpec, ...],
        *,
        command_runner=None,
        http_runner=None,
        prompt_runner=None,
        agent_runner=None,
        recorder: Callable[[Mapping[str, Any]], None] | None = None,
        max_workers: int = 8,
    ) -> None:
        self.specs = tuple(specs)
        self.runners = {"command": command_runner, "http": http_runner, "prompt": prompt_runner, "agent": agent_runner}
        self.recorder = recorder
        self.max_workers = max(1, int(max_workers))
        self._background = ThreadPoolExecutor(max_workers=4, thread_name_prefix="orin-hook-async")

    def close(self) -> None:
        """Release the pools this dispatcher owns. Safe to call more than once."""
        self._background.shutdown(wait=False, cancel_futures=True)
        for runner in self.runners.values():
            closer = getattr(runner, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - closing never breaks the turn
                    logger.debug("hook runner did not close cleanly", exc_info=True)

    def _record(self, spec: HookSpec, status: str, exit_code: int | None, decision: LifecycleDecision, started: float) -> None:
        if self.recorder is None:
            return
        try:
            self.recorder({
                "hook_event": spec.event.value, "source": spec.source, "type": spec.type, "label": spec.label(),
                "status": status, "exit_code": exit_code, "blocked": bool(decision.blocked or decision.permission_decision == "deny"),
                "reason": (decision.reason or "")[:300], "duration_ms": int((time.monotonic() - started) * 1000),
            })
        except Exception:  # noqa: BLE001 - recording never breaks a hook
            logger.debug("hook recorder failed", exc_info=True)

    def _run_one(self, spec: HookSpec, payload: Mapping[str, Any]) -> LifecycleDecision:
        started = time.monotonic()
        runner = self.runners.get(spec.type)
        exit_code: int | None = None
        try:
            if runner is None:
                raise RuntimeError(f"no runner for {spec.type} hooks")
            if spec.type in {"command", "http"}:
                run = runner.run(spec, payload)
                exit_code = run.exit_code
                decision = decision_from_command(spec.event, run, on_failure=spec.on_failure)
                if run.timed_out:
                    status = "timeout"
                elif run.error or (run.exit_code not in (0, 2)):
                    status = "failed"
                else:
                    status = "ok"
            else:
                verdict = runner.run(spec, payload)
                decision = decision_from_verdict(spec.event, verdict, on_failure=spec.on_failure)
                status = "failed" if verdict is None else "ok"
        except Exception as error:  # noqa: BLE001 - a hook never breaks the turn
            logger.warning("hook %s failed: %s", spec.label(), error)
            decision = decision_from_command(spec.event, HookRun(None, "", "", error=str(error)[:300]), on_failure=spec.on_failure)
            status = "failed"
        if status == "ok" and (decision.blocked or decision.permission_decision == "deny"):
            status = "blocked"
        self._record(spec, status, exit_code, decision, started)
        return decision

    def __call__(self, event: LifecycleEvent) -> LifecycleDecision | None:
        payload = dict(event.payload)
        selected: list[HookSpec] = []
        seen: set[tuple[Any, ...]] = set()
        for spec in self.specs:
            if spec.event is not event.name or not matcher_applies(spec.event, spec.matcher, payload):
                continue
            key = spec.identity()
            if key in seen:
                continue
            seen.add(key)
            selected.append(spec)
        if not selected:
            return None
        for spec in selected:
            if spec.run_async:
                try:
                    self._background.submit(self._run_one, spec, payload)
                except RuntimeError:
                    pass
        blocking = [spec for spec in selected if not spec.run_async]
        if not blocking:
            return LifecycleDecision()
        if len(blocking) == 1:
            decisions = [self._run_one(blocking[0], payload)]
        else:
            with ThreadPoolExecutor(max_workers=min(len(blocking), self.max_workers), thread_name_prefix="orin-hook") as pool:
                decisions = list(pool.map(lambda spec: self._run_one(spec, payload), blocking))
        merged = LifecycleDecision()
        for decision in decisions:
            merged.merge(decision)
        return merged
