"""Durable persistence for Discord bot configurations, Orin Friend session watches, and interaction bindings.

Security invariants:
- Bot tokens are encrypted at rest via ``ProviderSecretCipher`` (``enc:v1:...``).
- ``DiscordBotConfig`` returned by ``get_config`` / ``list_enabled_configs`` never carries the plaintext token;
  only ``has_bot_token`` and a safe masked suffix ``masked_token_hint`` (e.g. ``"...a9Xz"``) are exposed.
- Fail-closed allowlist: enabling the Discord integration requires both a bot token and at least one valid
  Discord user Snowflake ID (17..20 ASCII digits) in ``allowed_discord_user_ids``.
- Button interaction bindings are consumed via an atomic conditional ``UPDATE ... WHERE consumed_at IS NULL
  AND expires_at > :now`` so concurrent button clicks cannot double-execute an action.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import re
import secrets
from types import MappingProxyType
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.persistence.postgres.schema import (
    discord_bot_configs,
    discord_interaction_bindings,
    friend_session_watches,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher

_SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")

ALLOWED_WATCH_STATUSES = frozenset({
    "watching",
    "waiting_user",
    "completed",
    "failed",
    "cancelled",
})

ALLOWED_BINDING_ACTION_KINDS = frozenset({
    "ask_user",
    "plan_decision",
    "tool_permission",
    "stop_session",
})

_UPDATABLE_WATCH_FIELDS = frozenset({
    "friend_conversation_id",
    "discord_channel_id",
    "discord_progress_message_id",
    "discord_prompt_message_id",
    "status",
    "last_activity_seq",
    "last_digest_hash",
    "last_prompt_fingerprint",
    "last_patched_at",
    "notified_terminal",
})

_UNSET = object()


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=UTC)


def _require_non_empty_str(value: object, field_name: str, *, max_len: int = 255) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_len:
        raise ValueError(f"{field_name} must be a non-empty string (<= {max_len} chars)")
    return cleaned


def normalize_snowflake_ids(
    values: Iterable[object] | str | None,
    *,
    field_name: str = "snowflake_ids",
) -> tuple[str, ...]:
    """Validate and deduplicate Discord Snowflake IDs (strictly 17..20 ASCII numeric digits)."""
    if values is None:
        return ()
    raw_items: list[object]
    if isinstance(values, str):
        stripped = values.strip()
        if not stripped:
            return ()
        raw_items = [part for part in re.split(r"[\s,]+", stripped) if part]
    elif isinstance(values, Iterable) and not isinstance(values, (bytes, bytearray, Mapping)):
        raw_items = list(values)
    else:
        raise ValueError(f"{field_name} must be a sequence of Discord Snowflake IDs")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, bool) or item is None:
            raise ValueError(f"{field_name} contains an invalid Discord Snowflake ID: {item!r}")
        candidate = str(item).strip()
        if not candidate:
            raise ValueError(f"{field_name} cannot contain blank Snowflake IDs")
        if not candidate.isascii() or not _SNOWFLAKE_RE.fullmatch(candidate) or int(candidate) <= 0:
            raise ValueError(
                f"Invalid Discord Snowflake ID {candidate!r} in {field_name}: "
                "must be 17 to 20 ASCII numeric digits."
            )
        if candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return tuple(normalized)


def normalize_optional_snowflake_id(
    value: object,
    *,
    field_name: str = "channel_id",
) -> str | None:
    """Validate an optional single Discord Snowflake ID (or return None if empty/None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Invalid Discord Snowflake ID in {field_name}")
    candidate = str(value).strip()
    if not candidate:
        return None
    if not candidate.isascii() or not _SNOWFLAKE_RE.fullmatch(candidate) or int(candidate) <= 0:
        raise ValueError(
            f"Invalid Discord Snowflake ID {candidate!r} in {field_name}: "
            "must be 17 to 20 ASCII numeric digits."
        )
    return candidate


def mask_bot_token_hint(token: str) -> str:
    """Produce a non-sensitive masked token hint (e.g. '...a9Xz'), never storing plaintext."""
    cleaned = token.strip()
    if len(cleaned) < 8:
        raise ValueError("Discord bot token is too short")
    suffix = cleaned[-4:]
    return f"...{suffix}"


def _parse_json_str_tuple(raw: object) -> tuple[str, ...]:
    if not raw or not isinstance(raw, str):
        return ()
    try:
        parsed = json.loads(raw)
    except Exception:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item).strip() for item in parsed if str(item).strip())


@dataclass(frozen=True, slots=True)
class DiscordBotConfig:
    user_id: str
    enabled: bool
    has_bot_token: bool
    masked_token_hint: str | None
    allowed_discord_user_ids: tuple[str, ...]
    allowed_channel_ids: tuple[str, ...]
    default_notify_channel_id: str | None
    bot_application_id: str | None
    bot_user_id: str | None
    bot_username: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def bot_token_hint(self) -> str | None:
        return self.masked_token_hint

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "enabled": self.enabled,
            "has_bot_token": self.has_bot_token,
            "masked_token_hint": self.masked_token_hint,
            "allowed_discord_user_ids": list(self.allowed_discord_user_ids),
            "allowed_channel_ids": list(self.allowed_channel_ids),
            "default_notify_channel_id": self.default_notify_channel_id,
            "bot_application_id": self.bot_application_id,
            "bot_user_id": self.bot_user_id,
            "bot_username": self.bot_username,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class FriendSessionWatch:
    watch_id: str
    user_id: str
    friend_conversation_id: str | None
    target_conversation_id: str
    target_turn_id: str
    origin: str
    discord_channel_id: str | None
    discord_progress_message_id: str | None
    discord_prompt_message_id: str | None
    status: str
    last_activity_seq: int
    last_digest_hash: str | None
    last_prompt_fingerprint: str | None
    last_patched_at: datetime | None
    notified_terminal: bool
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "watch_id": self.watch_id,
            "user_id": self.user_id,
            "friend_conversation_id": self.friend_conversation_id,
            "target_conversation_id": self.target_conversation_id,
            "target_turn_id": self.target_turn_id,
            "origin": self.origin,
            "discord_channel_id": self.discord_channel_id,
            "discord_progress_message_id": self.discord_progress_message_id,
            "discord_prompt_message_id": self.discord_prompt_message_id,
            "status": self.status,
            "last_activity_seq": self.last_activity_seq,
            "last_digest_hash": self.last_digest_hash,
            "last_prompt_fingerprint": self.last_prompt_fingerprint,
            "last_patched_at": self.last_patched_at.isoformat() if self.last_patched_at else None,
            "notified_terminal": self.notified_terminal,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class DiscordInteractionBinding:
    binding_id: str
    user_id: str
    discord_channel_id: str | None
    discord_message_id: str | None
    target_conversation_id: str
    target_turn_id: str
    action_kind: str
    payload: Mapping[str, Any]
    payload_json: str
    consumed_at: datetime | None
    expires_at: datetime
    created_at: datetime

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        ref = _as_aware(now) or _now()
        return self.expires_at <= ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "user_id": self.user_id,
            "discord_channel_id": self.discord_channel_id,
            "discord_message_id": self.discord_message_id,
            "target_conversation_id": self.target_conversation_id,
            "target_turn_id": self.target_turn_id,
            "action_kind": self.action_kind,
            "payload": dict(self.payload),
            "consumed_at": self.consumed_at.isoformat() if self.consumed_at else None,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


class PostgresDiscordStore:
    """Transactional store for Discord bot config, session watches, and interaction bindings."""

    def __init__(
        self,
        engine: Engine,
        secret_cipher: ProviderSecretCipher | None = None,
        *,
        cipher: ProviderSecretCipher | None = None,
    ) -> None:
        self._engine = engine
        self._secret_cipher: ProviderSecretCipher | None = secret_cipher or cipher

    def _get_cipher(self) -> ProviderSecretCipher:
        if self._secret_cipher is None:
            self._secret_cipher = ProviderSecretCipher.from_environment()
        return self._secret_cipher

    @staticmethod
    def _row_to_config(row: Mapping[str, Any]) -> DiscordBotConfig:
        ciphertext = row.get("bot_token_ciphertext")
        has_token = bool(isinstance(ciphertext, str) and ciphertext.strip())
        hint = row.get("bot_token_hint")
        return DiscordBotConfig(
            user_id=str(row["user_id"]),
            enabled=bool(row["enabled"]),
            has_bot_token=has_token,
            masked_token_hint=str(hint) if (has_token and hint) else None,
            allowed_discord_user_ids=_parse_json_str_tuple(row.get("allowed_discord_user_ids_json")),
            allowed_channel_ids=_parse_json_str_tuple(row.get("allowed_channel_ids_json")),
            default_notify_channel_id=row.get("default_notify_channel_id") or None,
            bot_application_id=row.get("bot_application_id") or None,
            bot_user_id=row.get("bot_user_id") or None,
            bot_username=row.get("bot_username") or None,
            created_at=_as_aware(row["created_at"]) or _now(),
            updated_at=_as_aware(row["updated_at"]) or _now(),
        )

    @staticmethod
    def _row_to_watch(row: Mapping[str, Any]) -> FriendSessionWatch:
        return FriendSessionWatch(
            watch_id=str(row["watch_id"]),
            user_id=str(row["user_id"]),
            friend_conversation_id=row.get("friend_conversation_id") or None,
            target_conversation_id=str(row["target_conversation_id"]),
            target_turn_id=str(row["target_turn_id"]),
            origin=str(row.get("origin") or "friend"),
            discord_channel_id=row.get("discord_channel_id") or None,
            discord_progress_message_id=row.get("discord_progress_message_id") or None,
            discord_prompt_message_id=row.get("discord_prompt_message_id") or None,
            status=str(row["status"]),
            last_activity_seq=int(row.get("last_activity_seq") or 0),
            last_digest_hash=row.get("last_digest_hash") or None,
            last_prompt_fingerprint=row.get("last_prompt_fingerprint") or None,
            last_patched_at=_as_aware(row.get("last_patched_at")),
            notified_terminal=bool(row.get("notified_terminal")),
            created_at=_as_aware(row["created_at"]) or _now(),
            updated_at=_as_aware(row["updated_at"]) or _now(),
        )

    @staticmethod
    def _row_to_binding(row: Mapping[str, Any]) -> DiscordInteractionBinding:
        raw_payload = str(row.get("payload_json") or "{}")
        try:
            parsed = json.loads(raw_payload)
            if not isinstance(parsed, dict):
                parsed = {}
        except Exception:
            parsed = {}
        return DiscordInteractionBinding(
            binding_id=str(row["binding_id"]),
            user_id=str(row["user_id"]),
            discord_channel_id=row.get("discord_channel_id") or None,
            discord_message_id=row.get("discord_message_id") or None,
            target_conversation_id=str(row["target_conversation_id"]),
            target_turn_id=str(row["target_turn_id"]),
            action_kind=str(row["action_kind"]),
            payload=MappingProxyType(parsed),
            payload_json=raw_payload,
            consumed_at=_as_aware(row.get("consumed_at")),
            expires_at=_as_aware(row["expires_at"]) or _now(),
            created_at=_as_aware(row["created_at"]) or _now(),
        )

    # ------------------------------------------------------------------
    # discord_bot_configs
    # ------------------------------------------------------------------

    def get_config(self, user_id: str, *, include_secret: bool = False) -> DiscordBotConfig | None:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(discord_bot_configs).where(discord_bot_configs.c.user_id == clean_uid)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if include_secret and row.get("bot_token_ciphertext"):
            _ = self._get_cipher().decrypt(str(row["bot_token_ciphertext"]))
        return self._row_to_config(row)

    def list_enabled_configs(self) -> list[DiscordBotConfig]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(discord_bot_configs)
                    .where(
                        discord_bot_configs.c.enabled.is_(True),
                        discord_bot_configs.c.bot_token_ciphertext.is_not(None),
                    )
                    .order_by(discord_bot_configs.c.user_id.asc())
                )
                .mappings()
                .all()
            )
        return [self._row_to_config(row) for row in rows]

    def get_decrypted_bot_token(self, user_id: str) -> str | None:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(discord_bot_configs.c.bot_token_ciphertext).where(
                        discord_bot_configs.c.user_id == clean_uid
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or not row["bot_token_ciphertext"]:
            return None
        return self._get_cipher().decrypt(str(row["bot_token_ciphertext"]))

    def save_config(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        bot_token: str | None = None,
        allowed_discord_user_ids: Iterable[object] | str | None = None,
        allowed_channel_ids: Iterable[object] | str | None = None,
        default_notify_channel_id: object = _UNSET,
        bot_application_id: object = _UNSET,
        bot_user_id: object = _UNSET,
        bot_username: object = _UNSET,
    ) -> DiscordBotConfig:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        now = _now()

        new_ciphertext: str | None = None
        new_hint: str | None = None
        if bot_token is not None:
            if not isinstance(bot_token, str) or not bot_token.strip():
                raise ValueError("bot_token must be a non-empty string when provided")
            clean_token = bot_token.strip()
            new_hint = mask_bot_token_hint(clean_token)
            new_ciphertext = self._get_cipher().encrypt(clean_token)

        normalized_users = (
            normalize_snowflake_ids(allowed_discord_user_ids, field_name="allowed_discord_user_ids")
            if allowed_discord_user_ids is not None
            else None
        )
        normalized_channels = (
            normalize_snowflake_ids(allowed_channel_ids, field_name="allowed_channel_ids")
            if allowed_channel_ids is not None
            else None
        )
        normalized_notify_channel = (
            normalize_optional_snowflake_id(
                default_notify_channel_id, field_name="default_notify_channel_id"
            )
            if default_notify_channel_id is not _UNSET
            else _UNSET
        )

        with self._engine.begin() as conn:
            existing = (
                conn.execute(
                    select(discord_bot_configs).where(discord_bot_configs.c.user_id == clean_uid)
                )
                .mappings()
                .one_or_none()
            )

            effective_enabled = (
                bool(enabled)
                if enabled is not None
                else (bool(existing["enabled"]) if existing is not None else False)
            )
            effective_ciphertext = (
                new_ciphertext
                if new_ciphertext is not None
                else (existing["bot_token_ciphertext"] if existing is not None else None)
            )
            effective_hint = (
                new_hint
                if new_hint is not None
                else (existing["bot_token_hint"] if existing is not None else None)
            )
            effective_users = (
                normalized_users
                if normalized_users is not None
                else (
                    _parse_json_str_tuple(existing["allowed_discord_user_ids_json"])
                    if existing is not None
                    else ()
                )
            )
            effective_channels = (
                normalized_channels
                if normalized_channels is not None
                else (
                    _parse_json_str_tuple(existing["allowed_channel_ids_json"])
                    if existing is not None
                    else ()
                )
            )

            if effective_enabled:
                if not effective_ciphertext:
                    raise ValueError(
                        "Cannot enable Discord bot without a configured bot_token (fail-closed)."
                    )
                if not effective_users:
                    raise ValueError(
                        "Cannot enable Discord bot without at least one Snowflake ID in "
                        "allowed_discord_user_ids (fail-closed)."
                    )

            values: dict[str, Any] = {
                "enabled": effective_enabled,
                "bot_token_ciphertext": effective_ciphertext,
                "bot_token_hint": effective_hint,
                "allowed_discord_user_ids_json": json.dumps(list(effective_users)),
                "allowed_channel_ids_json": json.dumps(list(effective_channels)),
                "updated_at": now,
            }
            if normalized_notify_channel is not _UNSET:
                values["default_notify_channel_id"] = normalized_notify_channel
            elif existing is None:
                values["default_notify_channel_id"] = None

            for col_name, passed_val in (
                ("bot_application_id", bot_application_id),
                ("bot_user_id", bot_user_id),
                ("bot_username", bot_username),
            ):
                if passed_val is not _UNSET:
                    values[col_name] = str(passed_val).strip() if passed_val else None
                elif existing is None:
                    values[col_name] = None

            if existing is None:
                values["user_id"] = clean_uid
                values["created_at"] = now
                conn.execute(discord_bot_configs.insert().values(**values))
            else:
                conn.execute(
                    update(discord_bot_configs)
                    .where(discord_bot_configs.c.user_id == clean_uid)
                    .values(**values)
                )

            row = (
                conn.execute(
                    select(discord_bot_configs).where(discord_bot_configs.c.user_id == clean_uid)
                )
                .mappings()
                .one()
            )
        return self._row_to_config(row)

    def update_bot_identity(
        self,
        user_id: str,
        *,
        application_id: str | None = None,
        bot_user_id: str | None = None,
        bot_username: str | None = None,
    ) -> DiscordBotConfig | None:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        now = _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                update(discord_bot_configs)
                .where(discord_bot_configs.c.user_id == clean_uid)
                .values(
                    bot_application_id=str(application_id).strip() if application_id else None,
                    bot_user_id=str(bot_user_id).strip() if bot_user_id else None,
                    bot_username=str(bot_username).strip() if bot_username else None,
                    updated_at=now,
                )
            )
            if result.rowcount == 0:
                return None
            row = (
                conn.execute(
                    select(discord_bot_configs).where(discord_bot_configs.c.user_id == clean_uid)
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_config(row) if row is not None else None

    def update_default_notify_channel(
        self,
        user_id: str,
        channel_id: str | None,
    ) -> DiscordBotConfig | None:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        clean_channel = normalize_optional_snowflake_id(
            channel_id, field_name="default_notify_channel_id"
        )
        now = _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                update(discord_bot_configs)
                .where(discord_bot_configs.c.user_id == clean_uid)
                .values(default_notify_channel_id=clean_channel, updated_at=now)
            )
            if result.rowcount == 0:
                return None
            row = (
                conn.execute(
                    select(discord_bot_configs).where(discord_bot_configs.c.user_id == clean_uid)
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_config(row) if row is not None else None

    def delete_config(self, user_id: str) -> bool:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(discord_bot_configs).where(discord_bot_configs.c.user_id == clean_uid)
            )
        return bool(result.rowcount and result.rowcount > 0)

    # ------------------------------------------------------------------
    # friend_session_watches
    # ------------------------------------------------------------------

    def upsert_watch(
        self,
        user_id: str,
        target_conversation_id: str,
        target_turn_id: str,
        *,
        friend_conversation_id: str | None = None,
        origin: str = "friend",
        discord_channel_id: str | None = None,
    ) -> FriendSessionWatch:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        clean_target_cid = _require_non_empty_str(target_conversation_id, "target_conversation_id")
        clean_turn_id = _require_non_empty_str(target_turn_id, "target_turn_id")
        clean_friend_cid = (
            str(friend_conversation_id).strip()
            if isinstance(friend_conversation_id, str) and friend_conversation_id.strip()
            else None
        )
        clean_origin = _require_non_empty_str(origin or "friend", "origin", max_len=32)
        clean_channel_id = normalize_optional_snowflake_id(
            discord_channel_id, field_name="discord_channel_id"
        )
        now = _now()

        with self._engine.begin() as conn:
            existing = (
                conn.execute(
                    select(friend_session_watches).where(
                        friend_session_watches.c.user_id == clean_uid,
                        friend_session_watches.c.target_conversation_id == clean_target_cid,
                        friend_session_watches.c.target_turn_id == clean_turn_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                watch_id = f"fsw_{secrets.token_hex(12)}"
                try:
                    with conn.begin_nested():
                        conn.execute(
                            friend_session_watches.insert().values(
                                watch_id=watch_id,
                                user_id=clean_uid,
                                friend_conversation_id=clean_friend_cid,
                                target_conversation_id=clean_target_cid,
                                target_turn_id=clean_turn_id,
                                origin=clean_origin,
                                discord_channel_id=clean_channel_id,
                                discord_progress_message_id=None,
                                discord_prompt_message_id=None,
                                status="watching",
                                last_activity_seq=0,
                                last_digest_hash=None,
                                last_prompt_fingerprint=None,
                                last_patched_at=None,
                                notified_terminal=False,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                except IntegrityError:
                    existing = (
                        conn.execute(
                            select(friend_session_watches).where(
                                friend_session_watches.c.user_id == clean_uid,
                                friend_session_watches.c.target_conversation_id == clean_target_cid,
                                friend_session_watches.c.target_turn_id == clean_turn_id,
                            )
                        )
                        .mappings()
                        .one()
                    )

            if existing is not None:
                watch_id = str(existing["watch_id"])
                updates: dict[str, Any] = {"updated_at": now}
                if clean_friend_cid is not None and not existing.get("friend_conversation_id"):
                    updates["friend_conversation_id"] = clean_friend_cid
                if clean_channel_id is not None and not existing.get("discord_channel_id"):
                    updates["discord_channel_id"] = clean_channel_id
                conn.execute(
                    update(friend_session_watches)
                    .where(friend_session_watches.c.watch_id == watch_id)
                    .values(**updates)
                )

            row = (
                conn.execute(
                    select(friend_session_watches).where(
                        friend_session_watches.c.watch_id == watch_id
                    )
                )
                .mappings()
                .one()
            )
        return self._row_to_watch(row)

    def list_open_watches(self, *, user_id: str | None = None) -> list[FriendSessionWatch]:
        stmt = select(friend_session_watches).where(
            or_(
                friend_session_watches.c.notified_terminal.is_(False),
                friend_session_watches.c.status.in_(("watching", "waiting_user")),
            )
        )
        if user_id is not None:
            clean_uid = _require_non_empty_str(user_id, "user_id")
            stmt = stmt.where(friend_session_watches.c.user_id == clean_uid)
        stmt = stmt.order_by(
            friend_session_watches.c.created_at.asc(),
            friend_session_watches.c.watch_id.asc(),
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._row_to_watch(row) for row in rows]

    def get_watch(self, watch_id: str) -> FriendSessionWatch | None:
        clean_id = _require_non_empty_str(watch_id, "watch_id", max_len=64)
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(friend_session_watches).where(
                        friend_session_watches.c.watch_id == clean_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_watch(row) if row is not None else None

    def get_watch_by_turn(
        self,
        user_id: str,
        target_conversation_id: str,
        target_turn_id: str,
    ) -> FriendSessionWatch | None:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        clean_target_cid = _require_non_empty_str(target_conversation_id, "target_conversation_id")
        clean_turn_id = _require_non_empty_str(target_turn_id, "target_turn_id")
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(friend_session_watches).where(
                        friend_session_watches.c.user_id == clean_uid,
                        friend_session_watches.c.target_conversation_id == clean_target_cid,
                        friend_session_watches.c.target_turn_id == clean_turn_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_watch(row) if row is not None else None

    def update_watch(self, watch_id: str, **fields: Any) -> FriendSessionWatch | None:
        clean_id = _require_non_empty_str(watch_id, "watch_id", max_len=64)
        unknown = set(fields.keys()) - _UPDATABLE_WATCH_FIELDS
        if unknown:
            raise ValueError(f"Unsupported friend_session_watches update fields: {sorted(unknown)}")

        updates: dict[str, Any] = {"updated_at": _now()}
        for key, value in fields.items():
            if key == "status":
                status_str = str(value or "").strip()
                if status_str not in ALLOWED_WATCH_STATUSES:
                    raise ValueError(
                        f"Invalid watch status {value!r}; must be one of {sorted(ALLOWED_WATCH_STATUSES)}"
                    )
                updates["status"] = status_str
            elif key == "last_activity_seq":
                if isinstance(value, bool) or value is None or int(value) < 0:
                    raise ValueError("last_activity_seq must be a non-negative integer")
                updates["last_activity_seq"] = int(value)
            elif key == "notified_terminal":
                updates["notified_terminal"] = bool(value)
            elif key == "last_patched_at":
                updates["last_patched_at"] = _as_aware(value) if value is not None else None
            elif key == "discord_channel_id":
                updates["discord_channel_id"] = normalize_optional_snowflake_id(
                    value, field_name="discord_channel_id"
                )
            elif key in ("discord_progress_message_id", "discord_prompt_message_id"):
                updates[key] = normalize_optional_snowflake_id(value, field_name=key)
            else:
                updates[key] = str(value).strip() if value is not None and str(value).strip() else None

        with self._engine.begin() as conn:
            result = conn.execute(
                update(friend_session_watches)
                .where(friend_session_watches.c.watch_id == clean_id)
                .values(**updates)
            )
            if result.rowcount == 0:
                return None
            row = (
                conn.execute(
                    select(friend_session_watches).where(
                        friend_session_watches.c.watch_id == clean_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_watch(row) if row is not None else None

    # ------------------------------------------------------------------
    # discord_interaction_bindings
    # ------------------------------------------------------------------

    def create_binding(
        self,
        user_id: str,
        *,
        target_conversation_id: str,
        target_turn_id: str,
        action_kind: str,
        payload: Mapping[str, Any],
        ttl_seconds: int = 86400,
        discord_channel_id: str | None = None,
        discord_message_id: str | None = None,
    ) -> DiscordInteractionBinding:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        clean_target_cid = _require_non_empty_str(target_conversation_id, "target_conversation_id")
        clean_turn_id = _require_non_empty_str(target_turn_id, "target_turn_id")
        clean_kind = str(action_kind or "").strip()
        if clean_kind not in ALLOWED_BINDING_ACTION_KINDS:
            raise ValueError(
                f"Invalid action_kind {action_kind!r}; must be one of {sorted(ALLOWED_BINDING_ACTION_KINDS)}"
            )
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be a mapping")
        if isinstance(ttl_seconds, bool) or int(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be a positive integer")

        clean_channel_id = normalize_optional_snowflake_id(
            discord_channel_id, field_name="discord_channel_id"
        )
        clean_message_id = normalize_optional_snowflake_id(
            discord_message_id, field_name="discord_message_id"
        )

        binding_id = f"dbnd_{secrets.token_hex(10)}"
        now = _now()
        expires_at = now + timedelta(seconds=int(ttl_seconds))
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)

        with self._engine.begin() as conn:
            conn.execute(
                discord_interaction_bindings.insert().values(
                    binding_id=binding_id,
                    user_id=clean_uid,
                    discord_channel_id=clean_channel_id,
                    discord_message_id=clean_message_id,
                    target_conversation_id=clean_target_cid,
                    target_turn_id=clean_turn_id,
                    action_kind=clean_kind,
                    payload_json=payload_json,
                    consumed_at=None,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
            row = (
                conn.execute(
                    select(discord_interaction_bindings).where(
                        discord_interaction_bindings.c.binding_id == binding_id
                    )
                )
                .mappings()
                .one()
            )
        return self._row_to_binding(row)

    def get_binding(self, binding_id: str) -> DiscordInteractionBinding | None:
        if not isinstance(binding_id, str) or not binding_id.strip():
            return None
        clean_id = binding_id.strip()
        if len(clean_id) > 32:
            return None
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(discord_interaction_bindings).where(
                        discord_interaction_bindings.c.binding_id == clean_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_binding(row) if row is not None else None

    def consume_binding(
        self,
        binding_id: str,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> DiscordInteractionBinding | None:
        """Atomically claim and consume an unconsumed, non-expired interaction binding."""
        if not isinstance(binding_id, str) or not binding_id.strip():
            return None
        clean_id = binding_id.strip()
        if len(clean_id) > 32:
            return None
        clean_uid = _require_non_empty_str(user_id, "user_id")
        effective_now = _as_aware(now) or _now()

        with self._engine.begin() as conn:
            result = conn.execute(
                update(discord_interaction_bindings)
                .where(
                    discord_interaction_bindings.c.binding_id == clean_id,
                    discord_interaction_bindings.c.user_id == clean_uid,
                    discord_interaction_bindings.c.consumed_at.is_(None),
                    discord_interaction_bindings.c.expires_at > effective_now,
                )
                .values(consumed_at=effective_now)
            )
            if result.rowcount != 1:
                return None
            row = (
                conn.execute(
                    select(discord_interaction_bindings).where(
                        discord_interaction_bindings.c.binding_id == clean_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_binding(row) if row is not None else None

    def attach_binding_message(
        self,
        binding_ids: Sequence[str],
        *,
        discord_channel_id: str,
        discord_message_id: str,
    ) -> int:
        clean_ids = [
            str(bid).strip()
            for bid in binding_ids
            if isinstance(bid, str) and 1 <= len(bid.strip()) <= 32
        ]
        if not clean_ids:
            return 0
        clean_channel_id = normalize_optional_snowflake_id(
            discord_channel_id, field_name="discord_channel_id"
        )
        clean_message_id = normalize_optional_snowflake_id(
            discord_message_id, field_name="discord_message_id"
        )
        if clean_channel_id is None or clean_message_id is None:
            raise ValueError("discord_channel_id and discord_message_id are required")

        with self._engine.begin() as conn:
            result = conn.execute(
                update(discord_interaction_bindings)
                .where(discord_interaction_bindings.c.binding_id.in_(clean_ids))
                .values(
                    discord_channel_id=clean_channel_id,
                    discord_message_id=clean_message_id,
                )
            )
        return int(result.rowcount or 0)

    def list_bindings_for_message(
        self,
        user_id: str,
        discord_message_id: str,
    ) -> list[DiscordInteractionBinding]:
        clean_uid = _require_non_empty_str(user_id, "user_id")
        clean_msg_id = normalize_optional_snowflake_id(
            discord_message_id, field_name="discord_message_id"
        )
        if clean_msg_id is None:
            return []
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(discord_interaction_bindings)
                    .where(
                        discord_interaction_bindings.c.user_id == clean_uid,
                        discord_interaction_bindings.c.discord_message_id == clean_msg_id,
                    )
                    .order_by(
                        discord_interaction_bindings.c.created_at.asc(),
                        discord_interaction_bindings.c.binding_id.asc(),
                    )
                )
                .mappings()
                .all()
            )
        return [self._row_to_binding(row) for row in rows]

    def update_binding_payload(
        self,
        binding_id: str,
        *,
        user_id: str,
        payload: Mapping[str, Any],
    ) -> DiscordInteractionBinding | None:
        if not isinstance(binding_id, str) or not binding_id.strip():
            return None
        clean_id = binding_id.strip()
        if len(clean_id) > 32:
            return None
        clean_uid = _require_non_empty_str(user_id, "user_id")
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be a mapping")
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._engine.begin() as conn:
            result = conn.execute(
                update(discord_interaction_bindings)
                .where(
                    discord_interaction_bindings.c.binding_id == clean_id,
                    discord_interaction_bindings.c.user_id == clean_uid,
                )
                .values(payload_json=payload_json)
            )
            if result.rowcount != 1:
                return None
            row = (
                conn.execute(
                    select(discord_interaction_bindings).where(
                        discord_interaction_bindings.c.binding_id == clean_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_binding(row) if row is not None else None

    def purge_expired_bindings(self, now: datetime | None = None) -> int:
        effective_now = _as_aware(now) or _now()
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(discord_interaction_bindings).where(
                    discord_interaction_bindings.c.expires_at <= effective_now
                )
            )
        return int(result.rowcount or 0)
