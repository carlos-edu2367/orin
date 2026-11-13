"""Discord Bridge Service and SessionWatchCoordinator for Orin Friend orchestration."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from agentos.agentic.workspace import ConversationWorkspace, resolve_workspace
from agentos.code_mode.models import public_permission_mode
from agentos.code_mode.permissions import ToolPermissionBroker
from agentos.installation import orin_paths
from agentos.persistence.postgres.schema import (
    conversation_activity_events,
    conversation_turns,
    conversations,
    discord_interaction_bindings,
    projects,
    provider_api_keys,
    provider_configurations,
    provider_model_catalog,
    provider_model_favorites,
)
from agentos.provider_catalog.models import (
    PROVIDERS_WITH_OPTIONAL_KEY,
    SUBSCRIPTION_CLI_PROVIDERS,
)
from agentos.providers.cli_registry import cli_adapter_for
from agentos.uploads.media import MAX_UPLOAD_BYTES, UploadRejected
from agentos.uploads.promotion import promote_uploads
from agentos.uploads.staging import UploadStaging
import mimetypes

from .formatter import (
    build_decision_card,
    build_live_progress_embed,
    build_progress_components,
    build_resolved_decision_components,
    compute_progress_digest_hash,
    split_discord_markdown,
    strip_residual_speak_tags,
)
from .gateway_client import DEFAULT_GATEWAY_URL, DiscordGatewayClient, DiscordRuntimeStatus
from .rest_client import DiscordAttachmentError, DiscordFileAttachment, DiscordRestClient
from .store import (
    DiscordBotConfig,
    DiscordInteractionBinding,
    FriendSessionWatch,
    PostgresDiscordStore,
)

_LOGGER = logging.getLogger("agentos.discord.bridge")

DEFAULT_ATTACHMENT_ONLY_PROMPT = "Analise o(s) anexo(s) enviado(s)."
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_TOTAL_ATTACHMENT_BYTES_PER_MESSAGE = MAX_UPLOAD_BYTES * 2
_WORKSPACE_URI_RE = re.compile(r"workspace://([^\s)\]>\"'`]+)")
_ACTIVE_TURN_STATES = frozenset({"queued", "starting", "running", "cancelling"})
_TERMINAL_TURN_STATES = frozenset({"completed", "completed_with_caveats", "failed", "cancelled"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=UTC)


def _strip_custom_id_prefix(raw_id: str) -> str:
    cleaned = str(raw_id or "").strip()
    if cleaned.startswith("orin:v1:"):
        return cleaned[len("orin:v1:") :].strip()
    if cleaned.startswith("orin:"):
        parts = cleaned.split(":", 2)
        return parts[-1].strip()
    return cleaned


@dataclass
class _UserDiscordRunner:
    user_id: str
    config: DiscordBotConfig
    rest_client: DiscordRestClient
    gateway_client: DiscordGatewayClient
    status: DiscordRuntimeStatus = field(default_factory=lambda: DiscordRuntimeStatus(status="connecting"))
    active_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    stop_bindings_by_watch: dict[str, str] = field(default_factory=dict)


class SessionWatchCoordinator:
    """Polls `friend_session_watches` and `conversation_activity_events` to update Live Progress Cards,
    publish interactive Decision Cards on `waiting_user`, and trigger compact Orin Friend wakeups.
    """

    def __init__(
        self,
        bridge: DiscordBridgeService,
        *,
        poll_interval_seconds: float = 2.0,
        debounce_seconds: float = 5.0,
        purge_every_n_ticks: int = 100,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._bridge = bridge
        self._poll_interval = max(0.05, float(poll_interval_seconds))
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._purge_every_n_ticks = max(1, int(purge_every_n_ticks))
        self._clock = clock
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._tick_lock = asyncio.Lock()
        self._tick_count = 0
        self._activity_seq_cursors: dict[str, int] = {}

    async def wait_background_tasks(self, *, user_id: str | None = None, timeout: float = 10.0) -> None:
        await self._bridge.wait_background_tasks(user_id=user_id, timeout=timeout)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="discord-session-watch-coordinator")
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("SessionWatchCoordinator tick failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except TimeoutError:
                pass

    async def tick(self, *, user_id: str | None = None) -> int:
        """Run one full inspection pass over open session watches. Returns number of watches processed."""
        before_tasks = set(self._bridge._background_tasks)
        async with self._tick_lock:
            self._tick_count += 1
            if self._tick_count % self._purge_every_n_ticks == 0:
                try:
                    await asyncio.to_thread(
                        self._bridge.discord_store.purge_expired_bindings,
                        self._clock(),
                    )
                except Exception:
                    _LOGGER.debug("Could not purge expired Discord bindings", exc_info=True)

            watches = await asyncio.to_thread(
                self._bridge.discord_store.list_open_watches,
                user_id=user_id,
            )
            processed = 0
            for watch in watches:
                if self._stop_event.is_set():
                    break
                try:
                    await self._process_watch(watch)
                    processed += 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception("Failed processing session watch %s", watch.watch_id)

        # Outside _tick_lock: give newly spawned background wakeup tasks a brief slice (up to 750ms)
        # so fast in-memory test turns complete deterministically while slow LLM turns never block tick().
        new_tasks = [t for t in (self._bridge._background_tasks - before_tasks) if not t.done()]
        if new_tasks:
            await asyncio.wait(new_tasks, timeout=0.75)
        return processed

    async def _process_watch(self, watch: FriendSessionWatch) -> None:
        inspection = await asyncio.to_thread(self._bridge._inspect_watched_session, watch)
        if inspection is None:
            return

        now = _as_utc(self._clock()) or _utc_now()
        snapshot = inspection["snapshot"]
        max_seq = int(inspection["max_activity_seq"])
        raw_turn_state = str(inspection["turn_state"] or "running").strip().lower()
        effective_state = str(snapshot.get("state") or raw_turn_state).strip().lower()
        pending_block = snapshot.get("pending_block")
        current_turn_id = str(inspection.get("current_turn_id") or watch.target_turn_id)

        config = await asyncio.to_thread(self._bridge.discord_store.get_config, watch.user_id)
        channel_id = watch.discord_channel_id or (config.default_notify_channel_id if config else None)
        rest_client = self._bridge._get_rest_client(watch.user_id)

        is_terminal = raw_turn_state in _TERMINAL_TURN_STATES and pending_block is None
        terminal_status = (
            "completed"
            if raw_turn_state in {"completed", "completed_with_caveats"}
            else ("cancelled" if raw_turn_state == "cancelled" else "failed")
        )
        if is_terminal:
            snapshot = {**snapshot, "state": terminal_status, "pending_block": None}
            effective_state = terminal_status

        digest_hash = compute_progress_digest_hash(snapshot)
        prev_seq = max(watch.last_activity_seq, self._activity_seq_cursors.get(watch.watch_id, 0))
        effective_max_seq = max(max_seq, prev_seq)
        if effective_max_seq > 0:
            self._activity_seq_cursors[watch.watch_id] = effective_max_seq

        watch_updates: dict[str, Any] = {}
        if channel_id and not watch.discord_channel_id:
            watch_updates["discord_channel_id"] = channel_id

        progress_msg_id = watch.discord_progress_message_id

        # 1. Live Progress Card creation or debounced PATCH
        if rest_client is not None and channel_id:
            embed = build_live_progress_embed(snapshot)
            if is_terminal:
                if progress_msg_id:
                    try:
                        await rest_client.edit_message(
                            channel_id,
                            progress_msg_id,
                            embeds=[embed],
                            components=build_progress_components(None, active=False),
                        )
                        watch_updates["last_digest_hash"] = digest_hash
                        watch_updates["last_patched_at"] = now
                    except Exception:
                        _LOGGER.warning(
                            "Could not patch terminal progress card %s for watch %s",
                            progress_msg_id,
                            watch.watch_id,
                            exc_info=True,
                        )
                else:
                    try:
                        sent = await rest_client.send_message(
                            channel_id,
                            embeds=[embed],
                            components=[],
                        )
                        if sent.get("id"):
                            progress_msg_id = str(sent["id"])
                            watch_updates["discord_progress_message_id"] = progress_msg_id
                            watch_updates["last_digest_hash"] = digest_hash
                            watch_updates["last_patched_at"] = now
                    except Exception:
                        _LOGGER.warning("Could not send terminal progress card for watch %s", watch.watch_id, exc_info=True)
            else:
                stop_binding_id = await self._bridge._ensure_stop_binding(
                    watch=watch,
                    current_turn_id=current_turn_id,
                    channel_id=channel_id,
                )
                components = build_progress_components(stop_binding_id, active=True)
                if not progress_msg_id:
                    try:
                        sent = await rest_client.send_message(
                            channel_id,
                            embeds=[embed],
                            components=components,
                        )
                        if sent.get("id"):
                            progress_msg_id = str(sent["id"])
                            watch_updates["discord_progress_message_id"] = progress_msg_id
                            watch_updates["last_digest_hash"] = digest_hash
                            watch_updates["last_patched_at"] = now
                            if stop_binding_id:
                                await asyncio.to_thread(
                                    self._bridge.discord_store.attach_binding_message,
                                    [stop_binding_id],
                                    discord_channel_id=channel_id,
                                    discord_message_id=progress_msg_id,
                                )
                    except Exception:
                        _LOGGER.warning("Could not create progress card for watch %s", watch.watch_id, exc_info=True)
                else:
                    last_patched = _as_utc(watch.last_patched_at)
                    elapsed = (
                        (now - last_patched).total_seconds()
                        if last_patched is not None
                        else self._debounce_seconds + 1.0
                    )
                    entering_waiting = effective_state == "waiting_user" and watch.status != "waiting_user"
                    if digest_hash != watch.last_digest_hash and (
                        elapsed >= self._debounce_seconds or entering_waiting
                    ):
                        try:
                            await rest_client.edit_message(
                                channel_id,
                                progress_msg_id,
                                embeds=[embed],
                                components=components,
                            )
                            watch_updates["last_digest_hash"] = digest_hash
                            watch_updates["last_patched_at"] = now
                        except Exception:
                            _LOGGER.warning(
                                "Could not edit progress card %s for watch %s",
                                progress_msg_id,
                                watch.watch_id,
                                exc_info=True,
                            )

        # 2. Blocking Decision Card (`waiting_user` / `tool_permission` / `plan_approval` / `ask_user`)
        if not is_terminal and isinstance(pending_block, Mapping):
            fingerprint_payload = json.dumps(
                {"turn_id": current_turn_id, "block": dict(pending_block)},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            prompt_fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
            if watch.status != "waiting_user":
                watch_updates["status"] = "waiting_user"

            if prompt_fingerprint != watch.last_prompt_fingerprint and rest_client is not None and channel_id:
                prompt_msg_id = await self._bridge._publish_decision_card(
                    user_id=watch.user_id,
                    target_conversation_id=watch.target_conversation_id,
                    target_turn_id=current_turn_id,
                    session_title=str(snapshot.get("title") or "Sessão"),
                    pending_block=pending_block,
                    channel_id=channel_id,
                    rest_client=rest_client,
                )
                if prompt_msg_id:
                    watch_updates["discord_prompt_message_id"] = prompt_msg_id
                    watch_updates["last_prompt_fingerprint"] = prompt_fingerprint
            elif prompt_fingerprint != watch.last_prompt_fingerprint and (rest_client is None or not channel_id):
                # Record status transition without marking the prompt sent so it can be sent if channel connects
                pass
        elif not is_terminal and watch.status == "waiting_user" and pending_block is None:
            watch_updates["status"] = "watching"

        # 3. Terminal State & Idempotent Orin Friend Wakeup
        if is_terminal and not watch.notified_terminal:
            watch_updates["status"] = terminal_status
            watch_updates["notified_terminal"] = True
            if effective_max_seq > watch.last_activity_seq:
                watch_updates["last_activity_seq"] = effective_max_seq
            self._activity_seq_cursors.pop(watch.watch_id, None)
            runner = self._bridge._runners.get(watch.user_id)
            if runner is not None:
                runner.stop_bindings_by_watch.pop(watch.watch_id, None)
            stopped_by_discord = watch.watch_id in self._bridge._user_stopped_watches
            self._bridge._user_stopped_watches.discard(watch.watch_id)

            updated_watch = await asyncio.to_thread(
                self._bridge.discord_store.update_watch,
                watch.watch_id,
                **watch_updates,
            )
            watch_updates.clear()
            effective_watch = updated_watch or watch
            if not stopped_by_discord:
                self._bridge._spawn_user_task(
                    watch.user_id,
                    self._bridge._trigger_friend_wakeup(
                        watch=effective_watch,
                        snapshot=snapshot,
                        terminal_status=terminal_status,
                        channel_id=channel_id,
                    ),
                )

        if watch_updates:
            if effective_max_seq > watch.last_activity_seq:
                watch_updates["last_activity_seq"] = effective_max_seq
            await asyncio.to_thread(
                self._bridge.discord_store.update_watch,
                watch.watch_id,
                **watch_updates,
            )


class DiscordBridgeService:
    """Supervises per-user Discord Gateway connections, routes DMs/attachments to Orin Friend,
    processes interactive button/select callbacks, and coordinates Live Progress Cards.
    """

    def __init__(
        self,
        *,
        discord_store: PostgresDiscordStore,
        chat_application: Any,
        uploads: UploadStaging | None = None,
        workspace_root: Path | str | None = None,
        local_workspaces: Any | None = None,
        project_store: Any | None = None,
        provider_catalog: Any | None = None,
        rest_client_factory: Callable[[str], DiscordRestClient] | None = None,
        gateway_client_factory: Callable[..., DiscordGatewayClient] | None = None,
        turn_executor: Callable[[str, str], Awaitable[None] | None] | None = None,
        watch_poll_interval_seconds: float = 2.0,
        typing_interval_seconds: float = 8.0,
        progress_debounce_seconds: float = 5.0,
        turn_poll_interval_seconds: float = 0.15,
        turn_wait_timeout_seconds: float = 600.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.discord_store = discord_store
        self.chat_application = chat_application
        self.chat_store = getattr(chat_application, "store", chat_application)
        self.uploads = uploads or UploadStaging(orin_paths().data / "uploads" / "staging")
        self._workspace_root = Path(workspace_root) if workspace_root is not None else None
        self.local_workspaces = local_workspaces
        self.project_store = project_store
        self.provider_catalog = provider_catalog
        self._rest_client_factory = rest_client_factory or (lambda token: DiscordRestClient(token))
        self._gateway_client_factory = gateway_client_factory or DiscordGatewayClient
        self._turn_executor = turn_executor
        self._typing_interval = max(0.05, float(typing_interval_seconds))
        self._turn_poll_interval = max(0.02, float(turn_poll_interval_seconds))
        self._turn_wait_timeout = max(1.0, float(turn_wait_timeout_seconds))
        self._clock = clock

        self._runners: dict[str, _UserDiscordRunner] = {}
        self._disconnected_statuses: dict[str, DiscordRuntimeStatus] = {}
        self._friend_locks: dict[str, asyncio.Lock] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._user_stopped_watches: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()

        self.watch_coordinator = SessionWatchCoordinator(
            self,
            poll_interval_seconds=watch_poll_interval_seconds,
            debounce_seconds=progress_debounce_seconds,
            clock=clock,
        )

    def _friend_lock_for(self, user_id: str) -> asyncio.Lock:
        lock = self._friend_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._friend_locks[user_id] = lock
        return lock

    def _get_rest_client(self, user_id: str) -> DiscordRestClient | None:
        runner = self._runners.get(user_id)
        return runner.rest_client if runner is not None else None

    def _managed_root_for(self, user_id: str) -> Path:
        if self._workspace_root is not None:
            return self._workspace_root
        return orin_paths().user_workspaces(user_id)

    # ------------------------------------------------------------------
    # Multi-user lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start Gateway connections for all users with `enabled=True` in the database.

        If no user has Discord enabled, returns immediately without opening any
        network connections or starting polling loops.
        """
        self._started = True
        try:
            enabled_configs = await asyncio.to_thread(self.discord_store.list_enabled_configs)
        except Exception:
            _LOGGER.warning("Could not query enabled Discord configs on startup", exc_info=True)
            return

        if not enabled_configs:
            return

        for cfg in enabled_configs:
            try:
                await self.reload_user(cfg.user_id)
            except Exception:
                _LOGGER.exception("Failed to start Discord runner for user %s", cfg.user_id)

        if self._runners:
            self.watch_coordinator.start()

    def start_in_background(self) -> asyncio.Task[None] | None:
        """Schedule `start()` on the running event loop without blocking `/healthz`."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        return loop.create_task(self.start(), name="discord-bridge-startup")

    async def stop(self) -> None:
        """Gracefully stop the watch coordinator and all active user Gateway connections."""
        self._started = False
        await self.watch_coordinator.stop()
        user_ids = list(self._runners.keys())
        for uid in user_ids:
            try:
                await self.stop_user(uid)
            except Exception:
                _LOGGER.warning("Error stopping Discord runner for user %s", uid, exc_info=True)
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def stop_user(self, user_id: str) -> None:
        clean_uid = str(user_id or "").strip()
        if not clean_uid:
            return
        async with self._lifecycle_lock:
            runner = self._runners.pop(clean_uid, None)
        if runner is None:
            return

        for task in list(runner.active_tasks):
            if not task.done():
                task.cancel()
        if runner.active_tasks:
            await asyncio.gather(*runner.active_tasks, return_exceptions=True)
        runner.active_tasks.clear()

        try:
            await runner.gateway_client.stop()
        except Exception:
            _LOGGER.debug("Error stopping gateway client for %s", clean_uid, exc_info=True)
        try:
            await runner.rest_client.aclose()
        except Exception:
            _LOGGER.debug("Error closing rest client for %s", clean_uid, exc_info=True)

        last_snap = runner.gateway_client.status_snapshot
        self._disconnected_statuses[clean_uid] = DiscordRuntimeStatus(
            status="disconnected" if last_snap.status != "error" else "error",
            last_connected_at=last_snap.last_connected_at,
            last_error=last_snap.last_error,
            bot_user_id=last_snap.bot_user_id or runner.config.bot_user_id,
            bot_username=last_snap.bot_username or runner.config.bot_username,
            latency_ms=None,
            bot_application_id=last_snap.bot_application_id or runner.config.bot_application_id,
        )

    async def reload_user(self, user_id: str) -> DiscordRuntimeStatus:
        """Reload configuration from DB and (re)start or stop the user's Discord Gateway runner."""
        clean_uid = str(user_id or "").strip()
        if not clean_uid:
            raise ValueError("user_id must be a non-empty string")

        await self.stop_user(clean_uid)

        config = await asyncio.to_thread(self.discord_store.get_config, clean_uid)
        token = await asyncio.to_thread(self.discord_store.get_decrypted_bot_token, clean_uid)

        if (
            config is None
            or not config.enabled
            or not token
            or not config.allowed_discord_user_ids
        ):
            status = DiscordRuntimeStatus(
                status="disconnected",
                bot_user_id=config.bot_user_id if config else None,
                bot_username=config.bot_username if config else None,
                bot_application_id=config.bot_application_id if config else None,
            )
            self._disconnected_statuses[clean_uid] = status
            return status

        rest_client = self._rest_client_factory(token)
        gateway_url = DEFAULT_GATEWAY_URL

        async def _on_ready(payload: dict[str, Any]) -> None:
            await self._handle_ready(clean_uid, payload)

        async def _on_status(new_status: DiscordRuntimeStatus) -> None:
            runner_ref = self._runners.get(clean_uid)
            if runner_ref is not None:
                runner_ref.status = new_status
            self._disconnected_statuses[clean_uid] = new_status

        async def _on_message(payload: dict[str, Any]) -> None:
            self._spawn_user_task(clean_uid, self.handle_message_create(clean_uid, payload))

        async def _on_interaction(payload: dict[str, Any]) -> None:
            self._spawn_user_task(clean_uid, self.handle_interaction_create(clean_uid, payload))

        try:
            gateway_client = self._gateway_client_factory(
                token,
                on_message=_on_message,
                on_interaction=_on_interaction,
                on_ready=_on_ready,
                on_status_change=_on_status,
                gateway_url=gateway_url,
            )

            runner = _UserDiscordRunner(
                user_id=clean_uid,
                config=config,
                rest_client=rest_client,
                gateway_client=gateway_client,
            )
            async with self._lifecycle_lock:
                self._runners[clean_uid] = runner

            start_result = gateway_client.start()
            if asyncio.iscoroutine(start_result):
                await start_result
        except BaseException:
            async with self._lifecycle_lock:
                self._runners.pop(clean_uid, None)
            try:
                await rest_client.aclose()
            except Exception:
                _LOGGER.debug("Error closing rest client after failed reload for %s", clean_uid, exc_info=True)
            raise

        if self._started and not self.watch_coordinator.is_running:
            self.watch_coordinator.start()

        return gateway_client.status_snapshot

    def _spawn_user_task(self, user_id: str, coro: Awaitable[Any]) -> asyncio.Task[Any] | None:
        runner = self._runners.get(user_id)
        task = asyncio.create_task(self._run_guarded(coro))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        if runner is not None:
            runner.active_tasks.add(task)
            task.add_done_callback(runner.active_tasks.discard)
        return task

    async def wait_background_tasks(self, *, user_id: str | None = None, timeout: float = 10.0) -> None:
        """Wait for spawned background tasks (such as terminal friend wakeups) to complete."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout))
        while True:
            pending: list[asyncio.Task[Any]] = []
            if user_id:
                runner = self._runners.get(str(user_id).strip())
                if runner is not None:
                    pending.extend(t for t in runner.active_tasks if not t.done())
            pending.extend(t for t in self._background_tasks if not t.done() and t not in pending)
            if not pending:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.wait(pending, timeout=remaining)

    @staticmethod
    async def _run_guarded(coro: Awaitable[Any]) -> Any:
        try:
            return await coro
        except asyncio.CancelledError:
            if hasattr(coro, "close"):
                try:
                    coro.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
            raise
        except Exception:
            _LOGGER.exception("Unhandled error in Discord bridge task")
            return None

    def get_user_status(self, user_id: str) -> DiscordRuntimeStatus:
        clean_uid = str(user_id or "").strip()
        runner = self._runners.get(clean_uid)
        if runner is not None:
            snap = runner.gateway_client.status_snapshot
            return DiscordRuntimeStatus(
                status=snap.status,
                last_connected_at=snap.last_connected_at,
                last_error=snap.last_error,
                bot_user_id=snap.bot_user_id or runner.config.bot_user_id,
                bot_username=snap.bot_username or runner.config.bot_username,
                latency_ms=snap.latency_ms,
                bot_application_id=snap.bot_application_id or runner.config.bot_application_id,
            )
        cached = self._disconnected_statuses.get(clean_uid)
        if cached is not None:
            return cached
        try:
            cfg = self.discord_store.get_config(clean_uid)
        except Exception:
            cfg = None
        return DiscordRuntimeStatus(
            status="disconnected",
            bot_user_id=cfg.bot_user_id if cfg else None,
            bot_username=cfg.bot_username if cfg else None,
            bot_application_id=cfg.bot_application_id if cfg else None,
        )

    async def _handle_ready(self, user_id: str, data: Mapping[str, Any]) -> None:
        user_obj = data.get("user") if isinstance(data.get("user"), Mapping) else {}
        app_obj = data.get("application") if isinstance(data.get("application"), Mapping) else {}
        bot_uid = str(user_obj.get("id") or "").strip() or None
        bot_uname = str(user_obj.get("username") or "").strip() or None
        app_id = str(app_obj.get("id") or "").strip() or None
        updated_cfg = await asyncio.to_thread(
            self.discord_store.update_bot_identity,
            user_id,
            application_id=app_id,
            bot_user_id=bot_uid,
            bot_username=bot_uname,
        )
        runner = self._runners.get(user_id)
        if runner is not None and updated_cfg is not None:
            runner.config = updated_cfg

    # ------------------------------------------------------------------
    # Inbound MESSAGE_CREATE -> Orin Friend
    # ------------------------------------------------------------------

    async def handle_message_create(self, user_id: str, data: Mapping[str, Any]) -> dict[str, Any] | None:
        """Process an inbound Discord `MESSAGE_CREATE` event for `user_id`."""
        if not isinstance(data, Mapping):
            return None

        # 1. Ignore bots and webhooks
        author = data.get("author")
        if not isinstance(author, Mapping):
            return None
        if bool(author.get("bot")) or data.get("webhook_id") is not None:
            return None

        config = await asyncio.to_thread(self.discord_store.get_config, user_id)
        if config is None or not config.enabled or not config.allowed_discord_user_ids:
            return None

        author_id = str(author.get("id") or "").strip()
        if not author_id:
            return None
        if config.bot_user_id and author_id == config.bot_user_id:
            return None

        # 2. Strict fail-closed allowlist check
        if author_id not in config.allowed_discord_user_ids:
            _LOGGER.warning(
                "Ignored Discord message from unauthorized user_id=%s for Orin profile=%s",
                author_id,
                user_id,
            )
            return None

        # 3. Validate DM (guild_id is None) or allowed channel
        channel_id = str(data.get("channel_id") or "").strip()
        if not channel_id:
            return None
        guild_id = data.get("guild_id")
        is_dm = guild_id is None or str(guild_id).strip() == ""
        if not is_dm and channel_id not in config.allowed_channel_ids:
            return None

        # 4. Update default_notify_channel_id if changed
        if channel_id != config.default_notify_channel_id:
            try:
                updated_cfg = await asyncio.to_thread(
                    self.discord_store.update_default_notify_channel,
                    user_id,
                    channel_id,
                )
                runner = self._runners.get(user_id)
                if runner is not None and updated_cfg is not None:
                    runner.config = updated_cfg
            except Exception:
                _LOGGER.debug("Could not update default_notify_channel_id for %s", user_id, exc_info=True)

        rest_client = self._get_rest_client(user_id)
        if rest_client is None:
            return None

        message_id = str(data.get("id") or uuid4().hex).strip()
        raw_content = str(data.get("content") or "").strip()
        raw_attachments = data.get("attachments")
        attachments_list = (
            [item for item in raw_attachments if isinstance(item, Mapping)]
            if isinstance(raw_attachments, Sequence) and not isinstance(raw_attachments, (str, bytes))
            else []
        )

        if not raw_content and not attachments_list:
            return None

        # 5. Serialize turns and attachment staging on the Orin Friend conversation with per-user lock
        friend_lock = self._friend_lock_for(user_id)
        async with friend_lock:
            staged_upload_ids: list[str] = []
            if len(attachments_list) > MAX_ATTACHMENTS_PER_MESSAGE:
                try:
                    await rest_client.send_message(
                        channel_id,
                        content=(
                            f"⚠️ A mensagem possui {len(attachments_list)} anexos; "
                            f"apenas os primeiros {MAX_ATTACHMENTS_PER_MESSAGE} serão processados."
                        ),
                        reply_to_message_id=message_id,
                    )
                except Exception:
                    pass
                attachments_list = attachments_list[:MAX_ATTACHMENTS_PER_MESSAGE]

            total_downloaded_bytes = 0
            for att in attachments_list:
                filename = str(att.get("filename") or "anexo").strip() or "anexo"
                att_url = str(att.get("url") or att.get("proxy_url") or "").strip()
                declared_size = att.get("size")
                try:
                    if isinstance(declared_size, int) and declared_size > MAX_UPLOAD_BYTES:
                        raise DiscordAttachmentError(
                            f"O arquivo `{filename}` ({declared_size} bytes) excede o limite de {MAX_UPLOAD_BYTES} bytes."
                        )
                    remaining_budget = min(
                        MAX_UPLOAD_BYTES,
                        MAX_TOTAL_ATTACHMENT_BYTES_PER_MESSAGE - total_downloaded_bytes,
                    )
                    if remaining_budget <= 0 or (
                        isinstance(declared_size, int) and declared_size > remaining_budget
                    ):
                        raise DiscordAttachmentError(
                            f"O arquivo `{filename}` excede o orçamento total de anexos por mensagem ({MAX_TOTAL_ATTACHMENT_BYTES_PER_MESSAGE} bytes)."
                        )
                    file_bytes = await rest_client.download_attachment(att_url, max_bytes=remaining_budget)
                    total_downloaded_bytes += len(file_bytes)
                    staged = await asyncio.to_thread(self.uploads.store, user_id, filename, file_bytes)
                    staged_upload_ids.append(staged.upload_id)
                except (DiscordAttachmentError, UploadRejected, Exception) as exc:
                    _LOGGER.warning("Failed staging Discord attachment %r: %s", filename, exc)
                    try:
                        await rest_client.send_message(
                            channel_id,
                            content=f"⚠️ Não foi possível processar o anexo `{filename}`: {exc}",
                            reply_to_message_id=message_id,
                        )
                    except Exception:
                        pass

            if not raw_content and not staged_upload_ids:
                return None

            prompt_text = raw_content or DEFAULT_ATTACHMENT_ONLY_PROMPT

            friend_conv = await asyncio.to_thread(self._ensure_friend_conversation_with_model, user_id)
            friend_cid = str(friend_conv["conversation_id"])
            provider = str(friend_conv["provider"])
            model_id = str(friend_conv["model_id"])

            # Wait if there is already a turn running/queued on the PC for Orin Friend
            await self._wait_for_active_friend_turns(
                user_id=user_id,
                friend_conversation_id=friend_cid,
                channel_id=channel_id,
                rest_client=rest_client,
            )

            # Promote staged attachments into the Friend conversation workspace
            promoted_attachments: list[dict[str, Any]] = []
            if staged_upload_ids:
                try:
                    friend_ws = resolve_workspace(
                        friend_cid,
                        managed_root=self._managed_root_for(user_id),
                        local_root=None,
                    )
                    promoted_attachments = await asyncio.to_thread(
                        promote_uploads,
                        self.uploads,
                        friend_ws,
                        user_id,
                        staged_upload_ids,
                    )
                except Exception as exc:
                    _LOGGER.warning("Failed promoting uploads for Friend conversation %s: %s", friend_cid, exc)
                    try:
                        await rest_client.send_message(
                            channel_id,
                            content=f"⚠️ Falha ao anexar arquivos na conversa do Orin Friend: {exc}",
                            reply_to_message_id=message_id,
                        )
                    except Exception:
                        pass
                    if not raw_content:
                        return None

            idempotency_key = f"discord:msg:{message_id}"
            receipt = await asyncio.to_thread(
                self._send_turn_to_conversation,
                user_id=user_id,
                conversation_id=friend_cid,
                message=prompt_text,
                idempotency_key=idempotency_key,
                attachments=promoted_attachments,
                provider=provider,
                model_id=model_id,
                code_mode="friend",
                live_mode=False,
            )

            turn_id = str(receipt.turn_id)
            completed_conv = await self._await_turn_with_typing(
                user_id=user_id,
                conversation_id=friend_cid,
                turn_id=turn_id,
                channel_id=channel_id,
                rest_client=rest_client,
            )

            await self._deliver_friend_turn_response(
                user_id=user_id,
                friend_conversation_id=friend_cid,
                turn_id=turn_id,
                conversation_snapshot=completed_conv,
                channel_id=channel_id,
                rest_client=rest_client,
                reply_to_message_id=message_id,
            )
            return {
                "conversation_id": friend_cid,
                "turn_id": turn_id,
                "channel_id": channel_id,
            }

    def _ensure_friend_conversation_with_model(self, user_id: str) -> dict[str, Any]:
        """Ensure the singleton Orin Friend conversation and verify its `(provider, model_id)` is configured."""
        if hasattr(self.chat_application, "ensure_friend_conversation"):
            friend_conv = dict(self.chat_application.ensure_friend_conversation(user_id))
        else:
            friend_conv = dict(self.chat_store.ensure_friend_conversation(user_id))

        current_provider = str(friend_conv.get("provider") or "").strip().lower()
        current_model = str(friend_conv.get("model_id") or "").strip()

        resolved_provider, resolved_model = self._resolve_configured_friend_model(
            user_id,
            preferred_provider=current_provider,
            preferred_model_id=current_model,
        )
        if (
            resolved_provider
            and resolved_model
            and (resolved_provider != current_provider or resolved_model != current_model)
        ):
            engine = getattr(self.chat_store, "_engine", None)
            if engine is not None:
                try:
                    with engine.begin() as conn:
                        conn.execute(
                            update(conversations)
                            .where(
                                conversations.c.conversation_id == friend_conv["conversation_id"],
                                conversations.c.user_id == user_id,
                            )
                            .values(
                                provider=resolved_provider,
                                model_id=resolved_model,
                                updated_at=_utc_now(),
                            )
                        )
                except Exception:
                    pass
            friend_conv["provider"] = resolved_provider
            friend_conv["model_id"] = resolved_model

        return friend_conv

    def _resolve_configured_friend_model(
        self,
        user_id: str,
        *,
        preferred_provider: str = "",
        preferred_model_id: str = "",
    ) -> tuple[str, str]:
        """Check if `(preferred_provider, preferred_model_id)` is enabled for `user_id`;
        if not, fall back to the user's first favorite or first catalog model on an enabled provider.
        """
        engine = getattr(self.chat_store, "_engine", None)
        if engine is None:
            return preferred_provider, preferred_model_id

        try:
            with engine.connect() as conn:
                enabled_rows = (
                    conn.execute(
                        select(
                            provider_configurations.c.provider,
                            provider_configurations.c.model,
                            provider_configurations.c.base_url,
                            provider_configurations.c.secret_ref,
                            provider_configurations.c.risk_acknowledged_at,
                        ).where(
                            provider_configurations.c.user_id == user_id,
                            provider_configurations.c.enabled.is_(True),
                        )
                    )
                    .mappings()
                    .all()
                )
                if not enabled_rows:
                    # No provider_configurations rows (e.g. unit test environment) -> keep current
                    return preferred_provider, preferred_model_id

                active_key_providers = {
                    str(r["provider"]).strip().lower()
                    for r in conn.execute(
                        select(provider_api_keys.c.provider).where(
                            provider_api_keys.c.user_id == user_id,
                            provider_api_keys.c.status == "active",
                            provider_api_keys.c.api_key_ciphertext.is_not(None),
                        )
                    )
                    .mappings()
                    .all()
                    if r.get("provider")
                }
                catalog_providers = {
                    str(r["provider"]).strip().lower()
                    for r in conn.execute(
                        select(provider_model_catalog.c.provider)
                        .where(provider_model_catalog.c.user_id == user_id)
                        .distinct()
                    )
                    .mappings()
                    .all()
                    if r.get("provider")
                }

                enabled_configs: dict[str, dict[str, Any]] = {}
                for row in enabled_rows:
                    prov = str(row.get("provider") or "").strip().lower()
                    if not prov:
                        continue
                    if prov == "google-antigravity" and row.get("risk_acknowledged_at") is None:
                        continue
                    requires_key = (
                        prov not in PROVIDERS_WITH_OPTIONAL_KEY
                        and prov not in SUBSCRIPTION_CLI_PROVIDERS
                    )
                    has_secret = bool(str(row.get("secret_ref") or "").strip()) or (
                        prov in active_key_providers
                    )
                    if requires_key and not has_secret and prov not in catalog_providers:
                        continue
                    enabled_configs[prov] = dict(row)

                enabled_providers = set(enabled_configs.keys())
                if not enabled_providers:
                    return preferred_provider, preferred_model_id

                if preferred_provider in enabled_providers and preferred_model_id:
                    return preferred_provider, preferred_model_id

                # Prefer a favorite model from an enabled provider (including CLI providers without catalog rows)
                fav_row = (
                    conn.execute(
                        select(
                            provider_model_favorites.c.provider,
                            provider_model_favorites.c.model_id,
                        )
                        .where(
                            provider_model_favorites.c.user_id == user_id,
                            provider_model_favorites.c.provider.in_(sorted(enabled_providers)),
                        )
                        .order_by(
                            provider_model_favorites.c.provider.asc(),
                            provider_model_favorites.c.model_id.asc(),
                        )
                    )
                    .mappings()
                    .first()
                )
                if fav_row is not None:
                    return str(fav_row["provider"]), str(fav_row["model_id"])

                # Otherwise pick the first model in provider_model_catalog from an enabled provider
                cat_row = (
                    conn.execute(
                        select(
                            provider_model_catalog.c.provider,
                            provider_model_catalog.c.model_id,
                        )
                        .where(
                            provider_model_catalog.c.user_id == user_id,
                            provider_model_catalog.c.provider.in_(sorted(enabled_providers)),
                        )
                        .order_by(
                            provider_model_catalog.c.display_name.asc(),
                            provider_model_catalog.c.model_id.asc(),
                        )
                    )
                    .mappings()
                    .first()
                )
                if cat_row is not None:
                    return str(cat_row["provider"]), str(cat_row["model_id"])

                # Fallback to CLI adapter models (e.g. google-antigravity, openai-codex, claude)
                for prov in sorted(enabled_providers):
                    cfg = enabled_configs[prov]
                    configured_model = str(cfg.get("model") or "").strip()
                    if configured_model:
                        return prov, configured_model
                    adapter = cli_adapter_for(
                        prov,
                        cfg.get("base_url"),
                        risk_acknowledged_at=cfg.get("risk_acknowledged_at"),
                    )
                    if adapter is None:
                        continue
                    try:
                        cli_models = list(adapter.models())
                    except Exception:
                        cli_models = []
                    if cli_models:
                        first_id = str(getattr(cli_models[0], "id", "") or "").strip()
                        if first_id:
                            return prov, first_id
        except Exception:
            pass

        return preferred_provider, preferred_model_id

    def _send_turn_to_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message: str,
        idempotency_key: str,
        attachments: Sequence[Mapping[str, Any]] = (),
        provider: str = "",
        model_id: str = "",
        code_mode: str = "chat",
        permission_mode: str | None = None,
        live_mode: bool | None = False,
    ) -> Any:
        if hasattr(self.chat_application, "send"):
            return self.chat_application.send(
                user_id,
                conversation_id,
                message,
                idempotency_key,
                attachments=attachments,
                provider=provider,
                model_id=model_id,
                code_mode=code_mode,
                permission_mode=permission_mode,
                live_mode=live_mode,
            )
        return self.chat_store.create(
            user_id=user_id,
            conversation_id=conversation_id,
            message=message,
            provider=provider,
            model_id=model_id,
            idempotency_key=idempotency_key,
            attachments=attachments,
            code_mode=code_mode,
            permission_mode=permission_mode,
            live_mode=live_mode,
        )

    async def _wait_for_active_friend_turns(
        self,
        *,
        user_id: str,
        friend_conversation_id: str,
        channel_id: str | None,
        rest_client: DiscordRestClient | None,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._turn_wait_timeout
        last_typing_at = 0.0

        while loop.time() < deadline:
            has_active = await asyncio.to_thread(
                self._conversation_has_active_turn,
                friend_conversation_id,
                user_id,
            )
            if not has_active:
                return
            now_mono = loop.time()
            if rest_client is not None and channel_id and (now_mono - last_typing_at) >= self._typing_interval:
                last_typing_at = now_mono
                try:
                    await rest_client.trigger_typing(channel_id)
                except Exception:
                    pass
            await asyncio.sleep(self._turn_poll_interval)

    def _conversation_has_active_turn(self, conversation_id: str, user_id: str) -> bool:
        try:
            conv = self.chat_store.get(conversation_id, user_id)
        except Exception:
            return False
        if not isinstance(conv, Mapping):
            return False
        if str(conv.get("state") or "") in _ACTIVE_TURN_STATES:
            return True
        turns = conv.get("turns")
        if isinstance(turns, Sequence):
            return any(
                isinstance(t, Mapping) and str(t.get("state") or "") in _ACTIVE_TURN_STATES
                for t in turns
            )
        return False

    async def _await_turn_with_typing(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        channel_id: str | None,
        rest_client: DiscordRestClient | None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._turn_wait_timeout
        last_typing_at = 0.0

        if rest_client is not None and channel_id:
            last_typing_at = loop.time()
            try:
                await rest_client.trigger_typing(channel_id)
            except Exception:
                pass

        executor_task: asyncio.Task[Any] | None = None
        if callable(self._turn_executor):
            res = self._turn_executor(conversation_id, turn_id)
            if asyncio.iscoroutine(res) or isinstance(res, Awaitable):
                executor_task = asyncio.create_task(res)  # type: ignore[arg-type]

        try:
            while loop.time() < deadline:
                if executor_task is not None and executor_task.done():
                    executor_task.result()
                conv = await asyncio.to_thread(self.chat_store.get, conversation_id, user_id)
                turns = list(conv.get("turns") or ()) if isinstance(conv, Mapping) else []
                target_turn = next(
                    (t for t in reversed(turns) if isinstance(t, Mapping) and str(t.get("turn_id")) == turn_id),
                    None,
                )
                turn_state = str(
                    (target_turn.get("state") if target_turn else conv.get("state")) or ""
                ).strip().lower()
                if turn_state and turn_state not in _ACTIVE_TURN_STATES:
                    return dict(conv) if isinstance(conv, Mapping) else {}

                now_mono = loop.time()
                if rest_client is not None and channel_id and (now_mono - last_typing_at) >= self._typing_interval:
                    last_typing_at = now_mono
                    try:
                        await rest_client.trigger_typing(channel_id)
                    except Exception:
                        pass
                await asyncio.sleep(self._turn_poll_interval)
        finally:
            if executor_task is not None and not executor_task.done():
                try:
                    await asyncio.wait_for(executor_task, timeout=1.0)
                except Exception:
                    pass

        conv = await asyncio.to_thread(self.chat_store.get, conversation_id, user_id)
        return dict(conv) if isinstance(conv, Mapping) else {}

    async def _deliver_friend_turn_response(
        self,
        *,
        user_id: str,
        friend_conversation_id: str,
        turn_id: str,
        conversation_snapshot: Mapping[str, Any],
        channel_id: str,
        rest_client: DiscordRestClient,
        reply_to_message_id: str | None = None,
    ) -> None:
        turns = list(conversation_snapshot.get("turns") or ())
        target_turn = next(
            (t for t in reversed(turns) if isinstance(t, Mapping) and str(t.get("turn_id")) == turn_id),
            turns[-1] if turns and isinstance(turns[-1], Mapping) else {},
        )
        assistant_msg_id = str(target_turn.get("assistant_message_id") or "")
        messages = list(conversation_snapshot.get("messages") or ())
        assistant_msg = next(
            (
                m
                for m in reversed(messages)
                if isinstance(m, Mapping)
                and (
                    (assistant_msg_id and str(m.get("message_id")) == assistant_msg_id)
                    or (str(m.get("turn_id") or "") == turn_id and str(m.get("role") or "") == "assistant")
                )
            ),
            None,
        )
        if assistant_msg is None:
            assistant_msg = next(
                (m for m in reversed(messages) if isinstance(m, Mapping) and str(m.get("role") or "") == "assistant"),
                {},
            )

        raw_text = str((assistant_msg or {}).get("content") or "").strip()
        cleaned_text = strip_residual_speak_tags(raw_text)
        chunks = split_discord_markdown(cleaned_text)

        outgoing_files = await asyncio.to_thread(
            self._collect_outgoing_files,
            user_id=user_id,
            conversation_id=friend_conversation_id,
            assistant_message=assistant_msg or {},
            text=cleaned_text,
        )

        turn_state = str(target_turn.get("state") or conversation_snapshot.get("state") or "").strip().lower()
        if not chunks and not outgoing_files:
            if turn_state in {"failed", "error"}:
                err_code = str(target_turn.get("error") or target_turn.get("error_code") or "falha na execução").strip()
                chunks = [f"⚠️ Não foi possível concluir o turno do Orin Friend ({err_code})."]
            elif turn_state != "waiting_user":
                chunks = ["✅ Concluído."]

        for idx, chunk in enumerate(chunks):
            is_first = idx == 0
            is_last = idx == len(chunks) - 1
            await rest_client.send_message(
                channel_id,
                content=chunk,
                files=outgoing_files if is_last and outgoing_files else None,
                reply_to_message_id=reply_to_message_id if is_first else None,
            )

        if not chunks and outgoing_files:
            await rest_client.send_message(
                channel_id,
                content="📎 Arquivo(s) gerado(s):",
                files=outgoing_files,
                reply_to_message_id=reply_to_message_id,
            )

        # If Orin Friend itself entered waiting_user (AskUserQuestion), publish interactive buttons
        if turn_state == "waiting_user":
            inspection = await asyncio.to_thread(
                self._inspect_conversation_by_id,
                user_id,
                friend_conversation_id,
            )
            pending_block = inspection.get("pending_block") if isinstance(inspection, Mapping) else None
            if isinstance(pending_block, Mapping):
                await self._publish_decision_card(
                    user_id=user_id,
                    target_conversation_id=friend_conversation_id,
                    target_turn_id=turn_id,
                    session_title="Orin Friend",
                    pending_block=pending_block,
                    channel_id=channel_id,
                    rest_client=rest_client,
                )

    def _collect_outgoing_files(
        self,
        *,
        user_id: str,
        conversation_id: str,
        assistant_message: Mapping[str, Any],
        text: str,
    ) -> list[DiscordFileAttachment]:
        rel_paths: list[str] = []
        raw_atts = assistant_message.get("attachments")
        if isinstance(raw_atts, Sequence) and not isinstance(raw_atts, (str, bytes)):
            for item in raw_atts:
                if isinstance(item, Mapping) and item.get("path"):
                    rel_paths.append(str(item["path"]).strip())
        for match in _WORKSPACE_URI_RE.finditer(text or ""):
            uri_path = match.group(1).strip().lstrip("/")
            if uri_path and uri_path not in rel_paths:
                rel_paths.append(uri_path)

        if not rel_paths:
            return []

        try:
            workspace = resolve_workspace(
                conversation_id,
                managed_root=self._managed_root_for(user_id),
                local_root=None,
            )
        except Exception:
            return []

        files: list[DiscordFileAttachment] = []
        total_bytes = 0
        ws_root = workspace.root.resolve()
        for rel in rel_paths[:10]:
            try:
                norm_rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
                if not norm_rel:
                    continue
                parts = Path(norm_rel).parts
                if not parts or any(not p or p in {".", ".."} or p.startswith(".") for p in parts):
                    continue
                raw_candidate = workspace.root.joinpath(*parts)
                # Reject symlinks anywhere along the path before resolving
                cursor = raw_candidate
                has_symlink = False
                while True:
                    if cursor.is_symlink():
                        has_symlink = True
                        break
                    if cursor == workspace.root or cursor.parent == cursor:
                        break
                    cursor = cursor.parent
                if has_symlink:
                    continue

                resolved = workspace.resolve(norm_rel)
                if resolved.is_symlink() or not resolved.is_file():
                    continue
                rel_from_root = resolved.relative_to(ws_root)
                if any(part.startswith(".") for part in rel_from_root.parts):
                    continue
                size = resolved.stat().st_size
                if size <= 0 or size > MAX_UPLOAD_BYTES or (total_bytes + size) > MAX_UPLOAD_BYTES:
                    continue
                data = resolved.read_bytes()
                if len(data) <= 0 or (total_bytes + len(data)) > MAX_UPLOAD_BYTES:
                    continue
                total_bytes += len(data)
                files.append(
                    DiscordFileAttachment(
                        filename=resolved.name,
                        content=data,
                        content_type=mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
                    )
                )
            except Exception:
                continue
        return files

    # ------------------------------------------------------------------
    # INTERACTION_CREATE -> Buttons & StringSelect
    # ------------------------------------------------------------------

    async def handle_interaction_create(self, user_id: str, data: Mapping[str, Any]) -> dict[str, Any] | None:
        """Handle a Discord `INTERACTION_CREATE` event (`component_type` 2 Button or 3 StringSelect)."""
        if not isinstance(data, Mapping):
            return None

        interaction_id = str(data.get("id") or "").strip()
        interaction_token = str(data.get("token") or "").strip()
        if not interaction_id or not interaction_token:
            return None

        rest_client = self._get_rest_client(user_id)
        if rest_client is None:
            return None

        # Extract Discord user ID from DM (`user.id`) or guild (`member.user.id`)
        discord_user_id = ""
        member = data.get("member")
        if isinstance(member, Mapping) and isinstance(member.get("user"), Mapping):
            discord_user_id = str(member["user"].get("id") or "").strip()
        elif isinstance(data.get("user"), Mapping):
            discord_user_id = str(data["user"].get("id") or "").strip()

        config = await asyncio.to_thread(self.discord_store.get_config, user_id)
        if (
            config is None
            or not config.enabled
            or not config.allowed_discord_user_ids
            or not discord_user_id
            or discord_user_id not in config.allowed_discord_user_ids
        ):
            try:
                await rest_client.ack_interaction(
                    interaction_id,
                    interaction_token,
                    response_type=4,
                    data={"content": "Acesso não autorizado.", "flags": 64},
                )
            except Exception:
                _LOGGER.debug("Could not send unauthorized interaction ACK", exc_info=True)
            return {"status": "unauthorized"}

        interaction_data = data.get("data") if isinstance(data.get("data"), Mapping) else {}
        component_type = int(interaction_data.get("component_type") or 2)
        raw_custom_id = str(interaction_data.get("custom_id") or "").strip()
        if not raw_custom_id:
            return None

        selected_value: str | None = None
        if component_type == 3:
            raw_values = interaction_data.get("values")
            if isinstance(raw_values, Sequence) and not isinstance(raw_values, (str, bytes)) and raw_values:
                selected_value = str(raw_values[0] or "").strip()

        binding_id = _strip_custom_id_prefix(raw_custom_id)
        # In StringSelect with 6..25 bindings, each option's value may be the option's own binding_id
        if component_type == 3 and selected_value:
            candidate_val_id = _strip_custom_id_prefix(selected_value)
            if candidate_val_id.startswith("dbnd_"):
                existing_val_binding = await asyncio.to_thread(
                    self.discord_store.get_binding,
                    candidate_val_id,
                )
                if existing_val_binding is not None:
                    binding_id = candidate_val_id
                    selected_value = None

        binding = await asyncio.to_thread(
            self.discord_store.consume_binding,
            binding_id,
            user_id=user_id,
        )
        if binding is None:
            try:
                await rest_client.ack_interaction(
                    interaction_id,
                    interaction_token,
                    response_type=4,
                    data={"content": "Esta ação já foi processada ou expirou.", "flags": 64},
                )
            except Exception:
                _LOGGER.debug("Could not send expired interaction ACK", exc_info=True)
            return {"status": "already_consumed_or_expired"}

        # Consume any sibling bindings attached to the same Discord message so other buttons cannot be clicked
        msg_obj = data.get("message") if isinstance(data.get("message"), Mapping) else {}
        message_id = str(msg_obj.get("id") or binding.discord_message_id or "").strip()
        channel_id = str(data.get("channel_id") or msg_obj.get("channel_id") or binding.discord_channel_id or "").strip()
        if message_id and binding.action_kind != "stop_session":
            await asyncio.to_thread(self._consume_sibling_bindings, user_id, message_id)

        # Immediate ACK (response_type=6: DEFERRED_UPDATE_MESSAGE)
        await rest_client.ack_interaction(
            interaction_id,
            interaction_token,
            response_type=6,
        )

        chosen_label = await self._execute_interaction_binding(
            user_id=user_id,
            binding=binding,
            selected_value=selected_value,
            channel_id=channel_id,
            rest_client=rest_client,
        )

        # Disable components on the original card message and mark `✔ Respondido`
        resolved_components = build_resolved_decision_components(chosen_label)
        existing_embeds = msg_obj.get("embeds") if isinstance(msg_obj.get("embeds"), Sequence) else None
        if binding.action_kind == "stop_session":
            watch_id = str(binding.payload.get("watch_id") or "").strip()
            watch_obj = (
                await asyncio.to_thread(self.discord_store.get_watch, watch_id)
                if watch_id
                else None
            )
            if watch_obj is not None:
                inspection = await asyncio.to_thread(self._inspect_watched_session, watch_obj)
                if inspection is not None and isinstance(inspection.get("snapshot"), Mapping):
                    cancelled_snap = {
                        **inspection["snapshot"],
                        "state": "cancelled",
                        "pending_block": None,
                    }
                    existing_embeds = [build_live_progress_embed(cancelled_snap)]

        edited = False
        if channel_id and message_id:
            try:
                await rest_client.edit_message(
                    channel_id,
                    message_id,
                    embeds=existing_embeds if existing_embeds else None,
                    components=resolved_components,
                )
                edited = True
            except Exception:
                _LOGGER.debug("Could not edit message %s after interaction", message_id, exc_info=True)

        if not edited:
            app_id = str(data.get("application_id") or (config.bot_application_id if config else "") or "").strip()
            if app_id:
                try:
                    await rest_client.edit_interaction_original(
                        app_id,
                        interaction_token,
                        embeds=existing_embeds if existing_embeds else None,
                        components=resolved_components,
                    )
                except Exception:
                    _LOGGER.debug("Could not edit interaction original after interaction", exc_info=True)

        return {
            "status": "executed",
            "binding_id": binding.binding_id,
            "action_kind": binding.action_kind,
            "target_conversation_id": binding.target_conversation_id,
            "label": chosen_label,
        }

    def _consume_sibling_bindings(self, user_id: str, discord_message_id: str) -> None:
        now = _as_utc(self._clock()) or _utc_now()
        try:
            with self.discord_store._engine.begin() as conn:
                conn.execute(
                    update(discord_interaction_bindings)
                    .where(
                        discord_interaction_bindings.c.user_id == user_id,
                        discord_interaction_bindings.c.discord_message_id == discord_message_id,
                        discord_interaction_bindings.c.consumed_at.is_(None),
                    )
                    .values(consumed_at=now)
                )
        except Exception:
            pass

    async def _execute_interaction_binding(
        self,
        *,
        user_id: str,
        binding: DiscordInteractionBinding,
        selected_value: str | None,
        channel_id: str,
        rest_client: DiscordRestClient,
    ) -> str:
        payload = binding.payload
        action_kind = binding.action_kind
        target_cid = binding.target_conversation_id

        if action_kind == "stop_session":
            watch_id = str(payload.get("watch_id") or "").strip()
            if watch_id:
                self._user_stopped_watches.add(watch_id)
            await asyncio.to_thread(self._cancel_conversation, user_id, target_cid)
            if watch_id:
                try:
                    await asyncio.to_thread(
                        self.discord_store.update_watch,
                        watch_id,
                        status="cancelled",
                        notified_terminal=True,
                        last_patched_at=_as_utc(self._clock()) or _utc_now(),
                    )
                except Exception:
                    pass
            return "Sessão interrompida"

        if action_kind == "tool_permission":
            decision = str(payload.get("decision") or "allow_once").strip().lower()
            request_id = str(payload.get("request_id") or "").strip()
            ws_hint = str(payload.get("workspace_root") or "").strip() or None
            await asyncio.to_thread(
                self._resolve_tool_permission,
                user_id,
                target_cid,
                request_id=request_id,
                decision=decision,
                workspace_root_hint=ws_hint,
            )
            label_map = {
                "allow_once": "Permitir 1x",
                "allow_always": "Sempre permitir",
                "deny": "Negado",
            }
            return str(payload.get("label") or label_map.get(decision, f"Respondido ({decision})"))

        if action_kind == "plan_decision":
            decision = str(payload.get("decision") or "approve").strip().lower()
            feedback = str(payload.get("feedback") or "").strip()
            new_turn_id = await asyncio.to_thread(
                self._submit_plan_decision,
                user_id=user_id,
                target_conversation_id=target_cid,
                decision=decision,
                feedback=feedback,
                binding_id=binding.binding_id,
                channel_id=channel_id,
            )
            if callable(self._turn_executor) and new_turn_id:
                self._spawn_user_task(user_id, self._maybe_run_turn_executor(target_cid, new_turn_id))
            label_map = {
                "approve": "Plano aprovado",
                "revise": "Ajustes solicitados",
                "reject": "Plano rejeitado",
            }
            return str(payload.get("label") or label_map.get(decision, "Respondido"))

        # action_kind == "ask_user"
        answer_text = str(
            selected_value
            or payload.get("answer")
            or payload.get("value")
            or payload.get("label")
            or "Confirmado"
        ).strip()
        chosen_display = answer_text
        options = payload.get("options")
        if selected_value and isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
            for opt in options:
                if isinstance(opt, Mapping):
                    opt_val = str(opt.get("value") or opt.get("label") or "").strip()
                    if opt_val == selected_value:
                        chosen_display = str(opt.get("label") or selected_value).strip()
                        break

        conv = await asyncio.to_thread(self.chat_store.get, target_cid, user_id)
        conv_mode = str(conv.get("mode") or "chat").strip().lower() if isinstance(conv, Mapping) else "chat"

        if conv_mode == "friend":
            async def _continue_friend() -> None:
                async with self._friend_lock_for(user_id):
                    receipt = await asyncio.to_thread(
                        self._send_turn_to_conversation,
                        user_id=user_id,
                        conversation_id=target_cid,
                        message=answer_text,
                        idempotency_key=f"discord:bind:{binding.binding_id}",
                        provider=str(conv.get("provider") or ""),
                        model_id=str(conv.get("model_id") or ""),
                        code_mode="friend",
                        live_mode=False,
                    )
                    done_conv = await self._await_turn_with_typing(
                        user_id=user_id,
                        conversation_id=target_cid,
                        turn_id=str(receipt.turn_id),
                        channel_id=channel_id,
                        rest_client=rest_client,
                    )
                    await self._deliver_friend_turn_response(
                        user_id=user_id,
                        friend_conversation_id=target_cid,
                        turn_id=str(receipt.turn_id),
                        conversation_snapshot=done_conv,
                        channel_id=channel_id,
                        rest_client=rest_client,
                    )

            self._spawn_user_task(user_id, _continue_friend())
        else:
            new_turn_id = await asyncio.to_thread(
                self._submit_session_answer,
                user_id=user_id,
                target_conversation_id=target_cid,
                answer_text=answer_text,
                binding_id=binding.binding_id,
                channel_id=channel_id,
            )
            if callable(self._turn_executor) and new_turn_id:
                self._spawn_user_task(user_id, self._maybe_run_turn_executor(target_cid, new_turn_id))

        return f"Respondido: {chosen_display}"

    async def _maybe_run_turn_executor(self, conversation_id: str, turn_id: str) -> None:
        if not callable(self._turn_executor):
            return
        res = self._turn_executor(conversation_id, turn_id)
        if asyncio.iscoroutine(res) or isinstance(res, Awaitable):
            await res  # type: ignore[misc]

    def _cancel_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any]:
        if hasattr(self.chat_application, "cancel"):
            try:
                return dict(self.chat_application.cancel(conversation_id, user_id))
            except Exception:
                pass
        return dict(self.chat_store.request_cancel(conversation_id, user_id))

    def _resolve_tool_permission(
        self,
        user_id: str,
        conversation_id: str,
        *,
        request_id: str,
        decision: str,
        workspace_root_hint: str | None = None,
    ) -> dict[str, Any]:
        eff_decision = decision if decision in {"allow_once", "allow_always", "deny"} else "deny"
        conv = self.chat_store.get(conversation_id, user_id)
        candidate_roots = self._candidate_workspace_roots(user_id, conversation_id, conv, workspace_root_hint)

        # Find the request file across candidate roots
        for root in candidate_roots:
            broker = ToolPermissionBroker(root)
            if request_id:
                req = broker.get_request(request_id)
                if req is not None:
                    return broker.resolve_request(request_id, decision=eff_decision)  # type: ignore[arg-type]
            else:
                pending = self._scan_pending_permissions_in_root(root, conversation_id)
                if pending:
                    target_id = str(pending[-1].get("request_id") or "")
                    if target_id:
                        return broker.resolve_request(target_id, decision=eff_decision)  # type: ignore[arg-type]

        # Fallback: if request_id was given and the first candidate root exists, try resolving directly
        if candidate_roots and request_id:
            broker = ToolPermissionBroker(candidate_roots[0])
            return broker.resolve_request(request_id, decision=eff_decision)  # type: ignore[arg-type]

        raise LookupError(f"Permission request '{request_id}' not found for conversation '{conversation_id}'")

    def _submit_plan_decision(
        self,
        *,
        user_id: str,
        target_conversation_id: str,
        decision: str,
        feedback: str,
        binding_id: str,
        channel_id: str | None,
    ) -> str:
        conv = self.chat_store.get(target_conversation_id, user_id)
        conv_mode = str(conv.get("mode") or "code").strip().lower()
        effective_perm = str(
            conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions")
        )
        if decision == "approve" and effective_perm == "plan_mode":
            if hasattr(self.chat_store, "set_permission_mode"):
                try:
                    updated = self.chat_store.set_permission_mode(
                        target_conversation_id,
                        user_id,
                        "accept_edits",
                    )
                    effective_perm = str(updated.get("permission_mode") or "accept_edits")
                except Exception:
                    effective_perm = "accept_edits"
            else:
                effective_perm = "accept_edits"

        if decision == "approve":
            reply_text = feedback or "Plano aprovado. Pode prosseguir com a implementação."
        elif decision == "revise":
            reply_text = (
                f"Ajuste solicitado no plano: {feedback}"
                if feedback
                else "Por favor, revise o plano antes de prosseguir."
            )
        else:
            reply_text = (
                f"Plano rejeitado: {feedback}"
                if feedback
                else "Plano rejeitado. Aguarde novas instruções."
            )

        receipt = self._send_turn_to_conversation(
            user_id=user_id,
            conversation_id=target_conversation_id,
            message=reply_text,
            idempotency_key=f"discord:plan:{binding_id}",
            provider=str(conv.get("provider") or ""),
            model_id=str(conv.get("model_id") or ""),
            code_mode=conv_mode,
            permission_mode=effective_perm,
            live_mode=False,
        )
        self._advance_watch_to_new_turn(
            user_id=user_id,
            target_conversation_id=target_conversation_id,
            new_turn_id=str(receipt.turn_id),
            channel_id=channel_id,
        )
        return str(receipt.turn_id)

    def _submit_session_answer(
        self,
        *,
        user_id: str,
        target_conversation_id: str,
        answer_text: str,
        binding_id: str,
        channel_id: str | None,
    ) -> str:
        conv = self.chat_store.get(target_conversation_id, user_id)
        conv_mode = str(conv.get("mode") or "chat").strip().lower()
        effective_perm = str(
            conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions")
        )
        receipt = self._send_turn_to_conversation(
            user_id=user_id,
            conversation_id=target_conversation_id,
            message=answer_text,
            idempotency_key=f"discord:ans:{binding_id}",
            provider=str(conv.get("provider") or ""),
            model_id=str(conv.get("model_id") or ""),
            code_mode=conv_mode,
            permission_mode=effective_perm,
            live_mode=False,
        )
        self._advance_watch_to_new_turn(
            user_id=user_id,
            target_conversation_id=target_conversation_id,
            new_turn_id=str(receipt.turn_id),
            channel_id=channel_id,
        )
        return str(receipt.turn_id)

    def _advance_watch_to_new_turn(
        self,
        *,
        user_id: str,
        target_conversation_id: str,
        new_turn_id: str,
        channel_id: str | None,
    ) -> None:
        open_watches = self.discord_store.list_open_watches(user_id=user_id)
        matching = [w for w in open_watches if w.target_conversation_id == target_conversation_id]
        progress_msg_id: str | None = None
        friend_cid: str | None = None
        eff_channel = channel_id
        for old_watch in matching:
            if old_watch.discord_progress_message_id:
                progress_msg_id = old_watch.discord_progress_message_id
            if old_watch.friend_conversation_id:
                friend_cid = old_watch.friend_conversation_id
            if not eff_channel and old_watch.discord_channel_id:
                eff_channel = old_watch.discord_channel_id
            if old_watch.target_turn_id != new_turn_id:
                self._user_stopped_watches.discard(old_watch.watch_id)
                runner = self._runners.get(user_id)
                if runner is not None:
                    runner.stop_bindings_by_watch.pop(old_watch.watch_id, None)
                try:
                    self.discord_store.update_watch(
                        old_watch.watch_id,
                        status="completed",
                        notified_terminal=True,
                    )
                except Exception:
                    pass

        new_watch = self.discord_store.upsert_watch(
            user_id,
            target_conversation_id,
            new_turn_id,
            friend_conversation_id=friend_cid,
            origin="discord",
            discord_channel_id=eff_channel,
        )
        if progress_msg_id and not new_watch.discord_progress_message_id:
            try:
                self.discord_store.update_watch(
                    new_watch.watch_id,
                    discord_progress_message_id=progress_msg_id,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Session Watch inspection, Decision Cards & Wakeup
    # ------------------------------------------------------------------

    def _candidate_workspace_roots(
        self,
        user_id: str,
        conversation_id: str,
        conv: Mapping[str, Any],
        workspace_root_hint: str | None = None,
    ) -> list[Path]:
        candidates: list[Path] = []

        def _add(raw: Path | str | None) -> None:
            if not raw:
                return
            try:
                p = Path(raw)
                if p not in candidates:
                    candidates.append(p)
            except Exception:
                pass

        if workspace_root_hint:
            _add(workspace_root_hint)

        proj_id = str(conv.get("project_id") or "").strip()
        engine = getattr(self.chat_store, "_engine", None)
        if proj_id and self.project_store is not None:
            try:
                proj = self.project_store.get(proj_id, user_id)
                if proj is not None and self.local_workspaces is not None:
                    ws_id = getattr(proj, "workspace_id", "") or f"workspace:{proj_id}"
                    root = self.local_workspaces.root_for(ws_id, user_id)
                    if root:
                        _add(root)
            except Exception:
                pass
        elif proj_id and engine is not None and self.local_workspaces is not None:
            try:
                with engine.connect() as conn:
                    ws_id = conn.execute(
                        select(projects.c.workspace_id).where(
                            projects.c.project_id == proj_id,
                            projects.c.user_id == user_id,
                        )
                    ).scalar_one_or_none()
                if ws_id:
                    root = self.local_workspaces.root_for(str(ws_id), user_id)
                    if root:
                        _add(root)
            except Exception:
                pass

        if self.local_workspaces is not None:
            for key in (f"workspace:{conversation_id}", conversation_id):
                try:
                    root = self.local_workspaces.root_for(key, user_id)
                    if root:
                        _add(root)
                except Exception:
                    pass

        try:
            _add(self._managed_root_for(user_id) / conversation_id)
        except Exception:
            pass
        if self._workspace_root is not None:
            _add(self._workspace_root)

        existing = [p for p in candidates if p.exists() and p.is_dir()]
        return existing or candidates

    @staticmethod
    def _scan_pending_permissions_in_root(root: Path, conversation_id: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        perm_dirs: list[Path] = []
        try:
            perm_dirs.append(ToolPermissionBroker(root).permissions_dir)
        except Exception:
            pass
        perm_dirs.append(root / ".orin" / "permissions")

        seen_ids: set[str] = set()
        for perm_dir in perm_dirs:
            if not perm_dir.is_dir():
                continue
            for json_file in sorted(perm_dir.glob("*.json")):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(data, dict) or data.get("status") != "pending":
                    continue
                req_cid = str(data.get("conversation_id") or "").strip()
                if req_cid and req_cid != conversation_id:
                    continue
                req_id = str(data.get("request_id") or json_file.stem).strip()
                if req_id and req_id not in seen_ids:
                    seen_ids.add(req_id)
                    found.append({**data, "request_id": req_id, "_workspace_root": str(root)})
        return found

    def _inspect_conversation_by_id(self, user_id: str, conversation_id: str) -> dict[str, Any]:
        conv = self.chat_store.get(conversation_id, user_id)
        if not isinstance(conv, Mapping):
            return {}
        watch_stub = FriendSessionWatch(
            watch_id="inspect_tmp",
            user_id=user_id,
            friend_conversation_id=None,
            target_conversation_id=conversation_id,
            target_turn_id="",
            origin="friend",
            discord_channel_id=None,
            discord_progress_message_id=None,
            discord_prompt_message_id=None,
            status="watching",
            last_activity_seq=0,
            last_digest_hash=None,
            last_prompt_fingerprint=None,
            last_patched_at=None,
            notified_terminal=False,
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        res = self._inspect_watched_session(watch_stub)
        return res["snapshot"] if res is not None else {}

    def _inspect_watched_session(self, watch: FriendSessionWatch) -> dict[str, Any] | None:
        try:
            conv = self.chat_store.get(watch.target_conversation_id, watch.user_id)
        except Exception:
            return None
        if not isinstance(conv, Mapping):
            return None

        turns = [t for t in (conv.get("turns") or ()) if isinstance(t, Mapping)]
        target_turn = next(
            (t for t in reversed(turns) if str(t.get("turn_id") or "") == watch.target_turn_id),
            turns[-1] if turns else None,
        )
        current_turn_id = str(target_turn.get("turn_id") if target_turn else watch.target_turn_id)
        turn_state = str(
            (target_turn.get("state") if target_turn else conv.get("state")) or "running"
        ).strip().lower()
        conv_state = str(conv.get("state") or turn_state).strip().lower()

        activities: list[dict[str, Any]] = []
        engine = getattr(self.chat_store, "_engine", None)
        if engine is not None:
            try:
                with engine.connect() as conn:
                    rows = (
                        conn.execute(
                            select(conversation_activity_events)
                            .where(
                                conversation_activity_events.c.conversation_id == watch.target_conversation_id,
                                conversation_activity_events.c.user_id == watch.user_id,
                            )
                            .order_by(conversation_activity_events.c.id.asc())
                            .limit(400)
                        )
                        .mappings()
                        .all()
                    )
                    activities = [dict(r) for r in rows]
            except Exception:
                pass
        if not activities and isinstance(conv.get("activities"), Sequence):
            activities = [dict(a) for a in conv.get("activities") if isinstance(a, Mapping)]

        max_seq = watch.last_activity_seq
        todos: list[dict[str, Any]] = []
        files_modified: list[str] = []
        verifications: list[str] = []
        recent_activities: list[dict[str, Any]] = []
        latest_questions: list[dict[str, Any]] | None = None
        latest_plan_approval: dict[str, Any] | None = None

        for act in activities:
            seq_val = act.get("sequence") or act.get("id") or 0
            try:
                seq_int = int(seq_val)
                if seq_int > max_seq:
                    max_seq = seq_int
            except (TypeError, ValueError):
                pass

            evt_type = str(act.get("event_type") or "")
            summary = str(act.get("summary") or "")
            raw_payload = act.get("payload")
            if isinstance(raw_payload, str):
                try:
                    raw_payload = json.loads(raw_payload)
                except Exception:
                    raw_payload = {}
            payload = raw_payload if isinstance(raw_payload, Mapping) else {}
            tool_name = str(payload.get("tool_name") or payload.get("name") or "")

            raw_todos = payload.get("todos")
            if isinstance(raw_todos, list):
                todos = [dict(t) for t in raw_todos if isinstance(t, Mapping)]
            elif isinstance(payload.get("tool_output"), Mapping) and isinstance(
                payload["tool_output"].get("todos"), list
            ):
                todos = [dict(t) for t in payload["tool_output"]["todos"] if isinstance(t, Mapping)]

            for path_key in ("path", "file_path", "filePath"):
                p_val = payload.get(path_key)
                if not p_val and isinstance(payload.get("tool_output"), Mapping):
                    p_val = payload["tool_output"].get(path_key)
                if isinstance(p_val, str) and p_val.strip():
                    if (
                        tool_name in {"write_file", "edit_file", "Write", "Edit", "notebook_edit", "NotebookEdit"}
                        or payload.get("tool_kind") in {"write", "edit"}
                        or "structured_patch" in payload
                    ):
                        clean_p = p_val.strip()
                        if clean_p not in files_modified:
                            files_modified.append(clean_p)

            if tool_name in {"verify_project", "VerifyProject", "report_verification"} or evt_type == "verification.completed":
                if summary and summary not in verifications:
                    verifications.append(summary[:200])

            act_turn_id = str(act.get("turn_id") or "").strip()
            matches_current_turn = not current_turn_id or not act_turn_id or act_turn_id == current_turn_id

            if matches_current_turn and isinstance(payload.get("questions"), list) and payload.get("questions"):
                latest_questions = [dict(q) for q in payload["questions"] if isinstance(q, Mapping)]

            if matches_current_turn and (
                payload.get("plan_approval") or payload.get("code_approval") or payload.get("plan_ready")
            ):
                latest_plan_approval = {
                    "plan_path": payload.get("plan_path"),
                    "plan_summary": payload.get("plan_summary"),
                    "plan_content": payload.get("plan_content"),
                    "questions": payload.get("questions"),
                }

            if evt_type and evt_type not in {"agent.thought.delta", "context.updated"}:
                recent_activities.append(
                    {
                        "event_type": evt_type,
                        "summary": summary[:240],
                        "tool_name": tool_name or None,
                        "turn_id": act.get("turn_id"),
                    }
                )

        # Check pending ToolPermissionBroker requests on disk or in activities
        pending_permissions: list[dict[str, Any]] = []
        for root in self._candidate_workspace_roots(watch.user_id, watch.target_conversation_id, conv):
            for req in self._scan_pending_permissions_in_root(root, watch.target_conversation_id):
                if not any(r.get("request_id") == req.get("request_id") for r in pending_permissions):
                    pending_permissions.append(req)

        is_waiting = conv_state == "waiting_user" or turn_state == "waiting_user"
        pending_block: dict[str, Any] | None = None
        if pending_permissions:
            pending_block = {
                "kind": "tool_permission",
                "request": pending_permissions[-1],
                "all_requests": pending_permissions,
            }
        elif is_waiting and latest_plan_approval is not None:
            pending_block = {
                "kind": "plan_approval",
                **latest_plan_approval,
            }
        elif is_waiting and latest_questions:
            pending_block = {
                "kind": "ask_user",
                "questions": latest_questions,
            }
        elif is_waiting and str(conv.get("permission_mode") or "") == "plan_mode":
            pending_block = {
                "kind": "plan_approval",
                "plan_path": latest_plan_approval.get("plan_path") if latest_plan_approval else None,
                "plan_summary": latest_plan_approval.get("plan_summary") if latest_plan_approval else None,
            }
        elif is_waiting:
            # Generic waiting_user prompt from assistant message
            messages = [m for m in (conv.get("messages") or ()) if isinstance(m, Mapping)]
            last_assistant = next(
                (m for m in reversed(messages) if str(m.get("role") or "") == "assistant"),
                None,
            )
            q_text = str(last_assistant.get("content") if last_assistant else "").strip()
            if q_text:
                pending_block = {
                    "kind": "ask_user",
                    "question": q_text[:1000],
                }

        effective_state = (
            "waiting_user"
            if (pending_block is not None and turn_state in {"running", "starting", "queued", "waiting_user"})
            else turn_state
        )

        conv_mode = str(conv.get("mode") or "code").strip().lower()
        perm_mode = str(
            conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions")
        )
        candidate_roots = self._candidate_workspace_roots(watch.user_id, watch.target_conversation_id, conv)
        ws_path = str(candidate_roots[0]) if candidate_roots else None

        messages = [m for m in (conv.get("messages") or ()) if isinstance(m, Mapping)]
        last_assistant = next(
            (m for m in reversed(messages) if str(m.get("role") or "") == "assistant"),
            None,
        )
        final_excerpt = strip_residual_speak_tags(str(last_assistant.get("content") if last_assistant else ""))[:1200]
        error_text = str((target_turn or {}).get("error") or (target_turn or {}).get("error_code") or "").strip() or None

        snapshot: dict[str, Any] = {
            "conversation_id": watch.target_conversation_id,
            "target_conversation_id": watch.target_conversation_id,
            "turn_id": current_turn_id,
            "title": str(conv.get("title") or "Sessão"),
            "mode": conv_mode,
            "state": effective_state,
            "project_id": conv.get("project_id"),
            "project_name": conv.get("project_name"),
            "workspace_path": ws_path,
            "provider": conv.get("provider"),
            "model_id": conv.get("model_id"),
            "permission_mode": perm_mode,
            "public_permission_mode": public_permission_mode(perm_mode),
            "todos": todos,
            "files_modified": files_modified,
            "verifications": verifications,
            "recent_activities": recent_activities[-15:],
            "pending_block": pending_block,
            "error": error_text,
            "final_excerpt": final_excerpt,
        }

        return {
            "snapshot": snapshot,
            "turn_state": turn_state,
            "current_turn_id": current_turn_id,
            "max_activity_seq": max_seq,
        }

    async def _ensure_stop_binding(
        self,
        *,
        watch: FriendSessionWatch,
        current_turn_id: str,
        channel_id: str,
    ) -> str | None:
        runner = self._runners.get(watch.user_id)
        if runner is not None and watch.watch_id in runner.stop_bindings_by_watch:
            existing_id = runner.stop_bindings_by_watch[watch.watch_id]
            existing = await asyncio.to_thread(self.discord_store.get_binding, existing_id)
            if existing is not None and not existing.is_consumed and not existing.is_expired(self._clock()):
                return existing.binding_id
            runner.stop_bindings_by_watch.pop(watch.watch_id, None)

        binding = await asyncio.to_thread(
            self.discord_store.create_binding,
            watch.user_id,
            target_conversation_id=watch.target_conversation_id,
            target_turn_id=current_turn_id,
            action_kind="stop_session",
            payload={
                "label": "Parar sessão",
                "watch_id": watch.watch_id,
            },
            discord_channel_id=channel_id,
            discord_message_id=watch.discord_progress_message_id,
        )
        if runner is not None:
            runner.stop_bindings_by_watch[watch.watch_id] = binding.binding_id
        return binding.binding_id

    async def _publish_decision_card(
        self,
        *,
        user_id: str,
        target_conversation_id: str,
        target_turn_id: str,
        session_title: str,
        pending_block: Mapping[str, Any],
        channel_id: str,
        rest_client: DiscordRestClient,
    ) -> str | None:
        block_kind = str(pending_block.get("kind") or "ask_user").strip().lower()
        bindings: list[DiscordInteractionBinding] = []

        if block_kind == "tool_permission":
            req = pending_block.get("request") if isinstance(pending_block.get("request"), Mapping) else {}
            req_id = str(req.get("request_id") or "").strip()
            ws_root = str(req.get("_workspace_root") or "").strip() or None
            for decision, label, style in (
                ("allow_once", "Permitir 1x", 1),
                ("allow_always", "Sempre permitir", 3),
                ("deny", "Negar", 4),
            ):
                b = await asyncio.to_thread(
                    self.discord_store.create_binding,
                    user_id,
                    target_conversation_id=target_conversation_id,
                    target_turn_id=target_turn_id,
                    action_kind="tool_permission",
                    payload={
                        "decision": decision,
                        "label": label,
                        "style": style,
                        "request_id": req_id,
                        "workspace_root": ws_root,
                    },
                    discord_channel_id=channel_id,
                )
                bindings.append(b)

        elif block_kind in {"plan_approval", "plan_decision"}:
            for decision, label, style in (
                ("approve", "Aprovar", 3),
                ("revise", "Pedir ajustes", 2),
                ("reject", "Rejeitar", 4),
            ):
                b = await asyncio.to_thread(
                    self.discord_store.create_binding,
                    user_id,
                    target_conversation_id=target_conversation_id,
                    target_turn_id=target_turn_id,
                    action_kind="plan_decision",
                    payload={
                        "decision": decision,
                        "label": label,
                        "style": style,
                    },
                    discord_channel_id=channel_id,
                )
                bindings.append(b)

        else:
            # ask_user
            questions = pending_block.get("questions")
            first_q = (
                questions[0]
                if isinstance(questions, Sequence)
                and not isinstance(questions, (str, bytes))
                and questions
                and isinstance(questions[0], Mapping)
                else pending_block
            )
            raw_options = first_q.get("options") if isinstance(first_q, Mapping) else None
            options_list = (
                list(raw_options)
                if isinstance(raw_options, Sequence) and not isinstance(raw_options, (str, bytes))
                else []
            )

            if len(options_list) >= 6:
                normalized_opts: list[dict[str, str]] = []
                for idx, opt in enumerate(options_list[:25]):
                    if isinstance(opt, Mapping):
                        lbl = str(opt.get("label") or opt.get("value") or f"Opção {idx + 1}").strip()
                        val = str(opt.get("value") or lbl).strip()
                        desc = str(opt.get("description") or "").strip()
                    else:
                        lbl = str(opt).strip() or f"Opção {idx + 1}"
                        val = lbl
                        desc = ""
                    normalized_opts.append({"label": lbl, "value": val, "description": desc})

                b = await asyncio.to_thread(
                    self.discord_store.create_binding,
                    user_id,
                    target_conversation_id=target_conversation_id,
                    target_turn_id=target_turn_id,
                    action_kind="ask_user",
                    payload={
                        "label": "Selecionar opção",
                        "options": normalized_opts,
                    },
                    discord_channel_id=channel_id,
                )
                bindings.append(b)
            elif options_list:
                for idx, opt in enumerate(options_list[:5]):
                    if isinstance(opt, Mapping):
                        lbl = str(opt.get("label") or opt.get("value") or f"Opção {idx + 1}").strip()
                        val = str(opt.get("value") or lbl).strip()
                        desc = str(opt.get("description") or "").strip()
                    else:
                        lbl = str(opt).strip() or f"Opção {idx + 1}"
                        val = lbl
                        desc = ""
                    b = await asyncio.to_thread(
                        self.discord_store.create_binding,
                        user_id,
                        target_conversation_id=target_conversation_id,
                        target_turn_id=target_turn_id,
                        action_kind="ask_user",
                        payload={
                            "label": lbl,
                            "answer": val,
                            "value": val,
                            "description": desc,
                            "style": 1 if idx == 0 else 2,
                        },
                        discord_channel_id=channel_id,
                    )
                    bindings.append(b)
            else:
                for idx, (lbl, ans, style) in enumerate(
                    (
                        ("Sim / Prosseguir", "Sim, pode prosseguir.", 3),
                        ("Não / Pausar", "Não, aguarde novas instruções.", 2),
                    )
                ):
                    b = await asyncio.to_thread(
                        self.discord_store.create_binding,
                        user_id,
                        target_conversation_id=target_conversation_id,
                        target_turn_id=target_turn_id,
                        action_kind="ask_user",
                        payload={
                            "label": lbl,
                            "answer": ans,
                            "value": ans,
                            "style": style,
                        },
                        discord_channel_id=channel_id,
                    )
                    bindings.append(b)

        prompt_data = {
            **dict(pending_block),
            "title": session_title,
            "conversation_id": target_conversation_id,
        }
        embed, components = build_decision_card(prompt_data, bindings)
        try:
            sent = await rest_client.send_message(
                channel_id,
                embeds=[embed],
                components=components,
            )
        except Exception:
            _LOGGER.warning("Could not send decision card to Discord channel %s", channel_id, exc_info=True)
            return None

        msg_id = str(sent.get("id") or "").strip()
        if msg_id and bindings:
            await asyncio.to_thread(
                self.discord_store.attach_binding_message,
                [b.binding_id for b in bindings],
                discord_channel_id=channel_id,
                discord_message_id=msg_id,
            )
        return msg_id or None

    async def _trigger_friend_wakeup(
        self,
        *,
        watch: FriendSessionWatch,
        snapshot: Mapping[str, Any],
        terminal_status: str,
        channel_id: str | None,
    ) -> None:
        user_id = watch.user_id
        rest_client = self._get_rest_client(user_id)
        digest = {
            "event": "session_terminal_wakeup",
            "watch_id": watch.watch_id,
            "conversation_id": watch.target_conversation_id,
            "turn_id": snapshot.get("turn_id") or watch.target_turn_id,
            "project": snapshot.get("project_name") or snapshot.get("project_id") or snapshot.get("workspace_path"),
            "title": snapshot.get("title"),
            "mode": snapshot.get("mode"),
            "provider": snapshot.get("provider"),
            "model_id": snapshot.get("model_id"),
            "permission_mode": snapshot.get("public_permission_mode") or snapshot.get("permission_mode"),
            "status": terminal_status,
            "files_modified": list(snapshot.get("files_modified") or ())[:20],
            "verifications": list(snapshot.get("verifications") or ())[:10],
            "error": snapshot.get("error"),
            "final_excerpt": snapshot.get("final_excerpt"),
        }
        wakeup_prompt = (
            "[SISTEMA · WAKEUP DE SESSÃO SUPERVISIONADA]\n"
            "A sessão que você estava acompanhando atingiu um estado final. "
            "Envie um resumo direto e objetivo para o usuário sobre o resultado:\n"
            f"```json\n{json.dumps(digest, ensure_ascii=False, indent=2)}\n```"
        )

        friend_lock = self._friend_lock_for(user_id)
        async with friend_lock:
            friend_conv = await asyncio.to_thread(self._ensure_friend_conversation_with_model, user_id)
            friend_cid = str(watch.friend_conversation_id or friend_conv["conversation_id"])
            provider = str(friend_conv["provider"])
            model_id = str(friend_conv["model_id"])

            await self._wait_for_active_friend_turns(
                user_id=user_id,
                friend_conversation_id=friend_cid,
                channel_id=channel_id,
                rest_client=rest_client,
            )

            receipt = await asyncio.to_thread(
                self._send_turn_to_conversation,
                user_id=user_id,
                conversation_id=friend_cid,
                message=wakeup_prompt,
                idempotency_key=f"discord:wakeup:{watch.watch_id}",
                provider=provider,
                model_id=model_id,
                code_mode="friend",
                live_mode=False,
            )

            done_conv = await self._await_turn_with_typing(
                user_id=user_id,
                conversation_id=friend_cid,
                turn_id=str(receipt.turn_id),
                channel_id=channel_id,
                rest_client=rest_client,
            )

            if rest_client is not None and channel_id:
                await self._deliver_friend_turn_response(
                    user_id=user_id,
                    friend_conversation_id=friend_cid,
                    turn_id=str(receipt.turn_id),
                    conversation_snapshot=done_conv,
                    channel_id=channel_id,
                    rest_client=rest_client,
                )


__all__ = [
    "DEFAULT_ATTACHMENT_ONLY_PROMPT",
    "DiscordBridgeService",
    "SessionWatchCoordinator",
]
