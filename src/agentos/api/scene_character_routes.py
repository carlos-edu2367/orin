"""``/v1/scene/characters``: per-user custom character names, pixel-art sprites, and agent bindings for the Cena."""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import FastAPI, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError

from agentos.agentic.tools.agents.agent import CHARACTER_NAME_MAX_LEN, sanitize_character_name
from agentos.installation import orin_paths
from agentos.profile_files.binding import ProfileFolderRejected, resolve_inside

from .security import AuthenticatedPrincipal

MAX_CHARACTER_NAMES = 200
MAX_CUSTOM_CHARACTERS = 50
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_IMAGE_DIMENSION = 2048
NORMALIZED_MAX_HEIGHT = 256

VALID_POSES: tuple[str, ...] = ("idle", "think", "work", "wait", "scared")
OPTIONAL_POSES: tuple[str, ...] = ("think", "work", "wait", "scared")
ALLOWED_PIL_FORMATS: frozenset[str] = frozenset({"PNG", "WEBP", "GIF"})

_CHARACTER_ID_RE = re.compile(r"^char_[a-z0-9]{8,32}$")
_BINDING_KEY_RE = re.compile(r"^[^\x00-\x1f\x7f-\x9f]{1,64}$")

DEFAULT_SCENE_CHARACTER_NAMES: tuple[str, ...] = (
    "Bento",
    "Cora",
    "Dante",
    "Nina",
    "Otto",
    "Luna",
    "Tico",
    "Mila",
    "Zeca",
    "Iris",
    "Kiko",
    "Lia",
    "Nori",
    "Pipo",
    "Rafa",
    "Sora",
    "Theo",
    "Vivi",
    "Yuri",
    "Bia",
    "Caio",
    "Gabi",
    "Leo",
    "Maju",
)

_LOCKS_GUARD = threading.Lock()
_USER_LOCKS: dict[str, threading.RLock] = {}


def _user_lock(user_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _USER_LOCKS.get(user_id)
        if lock is None:
            lock = threading.RLock()
            _USER_LOCKS[user_id] = lock
        return lock


def scene_characters_root(user_id: str) -> Path:
    root = orin_paths().user_root(user_id) / "scene-characters"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _rejected(code: str, problems: list[str], status: int = 422) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": code,
                "category": "VALIDATION",
                "message_key": code,
                "retryable": False,
                "details": {"problems": problems[:50]},
            }
        },
        status_code=status,
    )


def _not_found(detail: str = "character not found") -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": "character_not_found",
                "category": "NOT_FOUND",
                "message_key": "character_not_found",
                "retryable": False,
                "details": {"problems": [detail[:120]]},
            }
        },
        status_code=404,
    )


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_", suffix=target.suffix or ".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _atomic_write_json(target: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(target, encoded)


def _pose_url(char_id: str, pose: str) -> str:
    return f"/v1/scene/characters/{char_id}/{pose}.png"


def _load_state_unlocked(root: Path) -> dict[str, Any]:
    meta_path = root / "characters.json"
    raw: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}

    raw_names = raw.get("names")
    if isinstance(raw_names, list):
        names: list[str] = []
        seen_names: set[str] = set()
        for item in raw_names:
            clean = sanitize_character_name(item)
            if clean and clean.casefold() not in seen_names:
                seen_names.add(clean.casefold())
                names.append(clean)
    else:
        names = list(DEFAULT_SCENE_CHARACTER_NAMES)

    characters: list[dict[str, Any]] = []
    valid_ids: set[str] = set()
    raw_chars = raw.get("characters")
    if isinstance(raw_chars, list):
        for entry in raw_chars:
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("id") or "").strip()
            cname = sanitize_character_name(entry.get("name"))
            if not _CHARACTER_ID_RE.fullmatch(cid) or not cname:
                continue
            raw_poses = entry.get("poses")
            if not isinstance(raw_poses, dict) or not raw_poses.get("idle"):
                continue
            poses_out: dict[str, str] = {}
            for pose in VALID_POSES:
                if raw_poses.get(pose):
                    poses_out[pose] = _pose_url(cid, pose)
            if "idle" not in poses_out:
                continue
            created_at = str(entry.get("created_at") or "") or datetime.now(UTC).isoformat()
            characters.append(
                {
                    "id": cid,
                    "name": cname,
                    "poses": poses_out,
                    "created_at": created_at,
                }
            )
            valid_ids.add(cid)

    bindings: dict[str, str] = {}
    raw_bindings = raw.get("bindings")
    if isinstance(raw_bindings, dict):
        for k, v in raw_bindings.items():
            clean_key = str(k or "").strip().lower()
            clean_val = str(v or "").strip()
            if clean_key and _BINDING_KEY_RE.fullmatch(clean_key) and clean_val in valid_ids:
                bindings[clean_key] = clean_val

    return {
        "names": names,
        "characters": characters,
        "bindings": bindings,
    }


def _save_state_unlocked(root: Path, state: Mapping[str, Any]) -> None:
    _atomic_write_json(
        root / "characters.json",
        {
            "names": list(state.get("names") or []),
            "characters": list(state.get("characters") or []),
            "bindings": dict(state.get("bindings") or {}),
        },
    )


def normalize_character_sprite(raw_bytes: bytes, *, field_name: str = "image") -> tuple[bytes | None, list[str]]:
    """Validate an uploaded sprite with Pillow and normalize to an RGBA PNG <= 256px tall."""
    if not raw_bytes:
        return None, [f"'{field_name}' file is empty"]
    if len(raw_bytes) > MAX_IMAGE_BYTES:
        return None, [f"'{field_name}' exceeds the 2 MB size limit"]

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            fmt = (img.format or "").upper()
            if fmt not in ALLOWED_PIL_FORMATS:
                return None, [f"'{field_name}' must be a valid PNG, WebP, or GIF image"]
            width, height = img.size
            if width < 1 or height < 1 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                return None, [
                    f"'{field_name}' dimensions ({width}x{height}) exceed the maximum of {MAX_IMAGE_DIMENSION}px"
                ]
            if fmt in {"GIF", "WEBP"} and getattr(img, "is_animated", False):
                img.seek(0)
            rgba = img.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError):
        return None, [f"'{field_name}' is not a valid PNG, WebP, or GIF image"]

    width, height = rgba.size
    if height > NORMALIZED_MAX_HEIGHT:
        target_h = NORMALIZED_MAX_HEIGHT
        target_w = max(1, round(width * (target_h / float(height))))
        is_integer_factor = (height % target_h == 0) and (width % (height // target_h) == 0)
        resample = Image.Resampling.NEAREST if is_integer_factor else Image.Resampling.LANCZOS
        rgba = rgba.resize((target_w, target_h), resample=resample)

    out = io.BytesIO()
    rgba.save(out, format="PNG")
    return out.getvalue(), []


def _strict_character_name(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, str):
        return None, "character name must be a string"
    if any(ord(ch) < 32 or (0x7F <= ord(ch) <= 0x9F) for ch in raw):
        return None, "character name must not contain control characters"
    trimmed = raw.strip()
    if not trimmed:
        return None, "character name cannot be blank"
    if len(trimmed) > CHARACTER_NAME_MAX_LEN:
        return None, f"character name must be at most {CHARACTER_NAME_MAX_LEN} characters"
    return trimmed, None


async def _read_upload_bounded(upload: UploadFile) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            return b"", True
        chunks.append(chunk)
    return b"".join(chunks), False


def _parse_remove_poses(raw_values: list[Any]) -> tuple[set[str], list[str]]:
    to_remove: set[str] = set()
    problems: list[str] = []
    for item in raw_values:
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        for sub in parsed:
                            sub_s = str(sub or "").strip().lower()
                            if sub_s:
                                to_remove.add(sub_s)
                        continue
                except ValueError:
                    pass
            for part in text.split(","):
                p = part.strip().lower()
                if p:
                    to_remove.add(p)
        elif isinstance(item, list):
            for sub in item:
                sub_s = str(sub or "").strip().lower()
                if sub_s:
                    to_remove.add(sub_s)
    for pose in sorted(to_remove):
        if pose == "idle":
            problems.append("the 'idle' pose is required and cannot be removed")
        elif pose not in OPTIONAL_POSES:
            problems.append(f"unknown pose '{pose[:32]}'")
    return to_remove, problems


def register_scene_character_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
) -> None:
    def principal(request: Request, *, mutable: bool, purpose: str) -> AuthenticatedPrincipal:
        current = principal_for(request, mutable=mutable)
        action = "scene.characters.write" if mutable else "scene.characters.read"
        if mutable:
            services.security.check_rate_limit(current, action=action, origin=request.headers.get("origin"))
        services.security.authorize(current, action=action, resource_id=None, purpose=purpose)
        return current

    @app.get("/v1/scene/characters")
    async def get_scene_characters(request: Request) -> JSONResponse:
        current = principal(request, mutable=False, purpose="scene.characters.get")

        def load() -> dict[str, Any]:
            with _user_lock(current.user_id):
                root = scene_characters_root(current.user_id)
                state = _load_state_unlocked(root)
                return {
                    **state,
                    "default_names": list(DEFAULT_SCENE_CHARACTER_NAMES),
                }

        return JSONResponse(await run_in_threadpool(load))

    @app.put("/v1/scene/characters/names")
    async def put_scene_character_names(request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="scene.characters.names.update")
        try:
            body = await request.json()
        except Exception:
            return _rejected("invalid_names", ["request body must be valid JSON"])

        raw_list: Any = body.get("names") if isinstance(body, dict) else body
        if not isinstance(raw_list, list):
            return _rejected("invalid_names", ["'names' must be a list of strings"])
        if len(raw_list) > MAX_CHARACTER_NAMES:
            return _rejected("invalid_names", [f"at most {MAX_CHARACTER_NAMES} names are allowed"])

        cleaned_names: list[str] = []
        seen_lower: set[str] = set()
        problems: list[str] = []
        for idx, item in enumerate(raw_list):
            clean, err = _strict_character_name(item)
            if err or clean is None:
                problems.append(f"names[{idx}]: {err}")
                continue
            folded = clean.casefold()
            if folded in seen_lower:
                problems.append(f"duplicate name '{clean}' (case-insensitive)")
                continue
            seen_lower.add(folded)
            cleaned_names.append(clean)

        if problems:
            return _rejected("invalid_names", problems)

        def save() -> dict[str, Any]:
            with _user_lock(current.user_id):
                root = scene_characters_root(current.user_id)
                state = _load_state_unlocked(root)
                state["names"] = cleaned_names
                _save_state_unlocked(root, state)
                return state

        updated = await run_in_threadpool(save)
        return JSONResponse({"names": updated["names"]})

    @app.post("/v1/scene/characters", status_code=201)
    async def create_scene_character(request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="scene.characters.create")
        content_type = (request.headers.get("content-type") or "").lower()
        if "multipart/form-data" not in content_type:
            return _rejected("invalid_character", ["multipart/form-data with 'name' and 'idle' is required"])

        form = await request.form()
        raw_name = form.get("name")
        clean_name, name_err = _strict_character_name(raw_name if isinstance(raw_name, str) else "")
        problems: list[str] = []
        if name_err or clean_name is None:
            problems.append(name_err or "name is required")

        idle_field = form.get("idle")
        if not isinstance(idle_field, UploadFile) and not hasattr(idle_field, "read"):
            problems.append("'idle' pose image is required")

        raw_uploads: dict[str, bytes] = {}
        for pose in VALID_POSES:
            field_val = form.get(pose)
            if field_val is None or isinstance(field_val, str):
                continue
            if hasattr(field_val, "read"):
                data, too_large = await _read_upload_bounded(field_val)  # type: ignore[arg-type]
                if too_large:
                    problems.append(f"'{pose}' exceeds the 2 MB size limit")
                else:
                    raw_uploads[pose] = data

        if "idle" not in raw_uploads and not problems:
            problems.append("'idle' pose image is required")

        if problems:
            return _rejected("invalid_character", problems)

        def process_and_create() -> JSONResponse:
            normalized_poses: dict[str, bytes] = {}
            norm_problems: list[str] = []
            for pose, raw_bytes in raw_uploads.items():
                png_bytes, errs = normalize_character_sprite(raw_bytes, field_name=pose)
                if errs or png_bytes is None:
                    norm_problems.extend(errs)
                else:
                    normalized_poses[pose] = png_bytes
            if norm_problems:
                return _rejected("invalid_character", norm_problems)

            with _user_lock(current.user_id):
                root = scene_characters_root(current.user_id)
                state = _load_state_unlocked(root)
                if len(state["characters"]) >= MAX_CUSTOM_CHARACTERS:
                    return _rejected(
                        "character_limit",
                        [f"you can create at most {MAX_CUSTOM_CHARACTERS} characters"],
                        status=409,
                    )
                char_id = f"char_{uuid.uuid4().hex[:16]}"
                char_dir = resolve_inside(root, char_id)
                char_dir.mkdir(parents=True, exist_ok=True)
                poses_urls: dict[str, str] = {}
                for pose, png_bytes in normalized_poses.items():
                    target = resolve_inside(char_dir, f"{pose}.png")
                    _atomic_write_bytes(target, png_bytes)
                    poses_urls[pose] = _pose_url(char_id, pose)
                character = {
                    "id": char_id,
                    "name": clean_name,
                    "poses": poses_urls,
                    "created_at": datetime.now(UTC).isoformat(),
                }
                state["characters"].append(character)
                _save_state_unlocked(root, state)
                return JSONResponse({"character": character}, status_code=201)

        return await run_in_threadpool(process_and_create)

    @app.patch("/v1/scene/characters/{character_id}")
    async def update_scene_character(character_id: str, request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="scene.characters.update")
        clean_id = (character_id or "").strip()
        if not _CHARACTER_ID_RE.fullmatch(clean_id):
            return _not_found(clean_id)

        with _user_lock(current.user_id):
            root = scene_characters_root(current.user_id)
            initial_state = _load_state_unlocked(root)
            if not any(c["id"] == clean_id for c in initial_state["characters"]):
                return _not_found(clean_id)

        content_type = (request.headers.get("content-type") or "").lower()
        new_name_raw: Any = None
        has_new_name = False
        raw_uploads: dict[str, bytes] = {}
        remove_raw_list: list[Any] = []
        problems: list[str] = []

        if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            if "name" in form:
                has_new_name = True
                new_name_raw = form.get("name")
            if "remove_poses" in form:
                remove_raw_list.extend(form.getlist("remove_poses"))
            for pose in OPTIONAL_POSES:
                flag = form.get(f"remove_{pose}")
                if isinstance(flag, str) and flag.strip().lower() in {"1", "true", "yes"}:
                    remove_raw_list.append(pose)
            for pose in VALID_POSES:
                field_val = form.get(pose)
                if field_val is None or isinstance(field_val, str):
                    continue
                if hasattr(field_val, "read"):
                    data, too_large = await _read_upload_bounded(field_val)  # type: ignore[arg-type]
                    if too_large:
                        problems.append(f"'{pose}' exceeds the 2 MB size limit")
                    else:
                        raw_uploads[pose] = data
        else:
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                return _rejected("invalid_character", ["request body must be an object"])
            if "name" in body:
                has_new_name = True
                new_name_raw = body.get("name")
            if "remove_poses" in body:
                remove_raw_list.append(body.get("remove_poses"))
            poses_patch = body.get("poses")
            if isinstance(poses_patch, dict):
                for k, v in poses_patch.items():
                    if v is None:
                        remove_raw_list.append(k)

        clean_new_name: str | None = None
        if has_new_name:
            clean_new_name, name_err = _strict_character_name(new_name_raw)
            if name_err or clean_new_name is None:
                problems.append(name_err or "invalid character name")

        to_remove, remove_problems = _parse_remove_poses(remove_raw_list)
        problems.extend(remove_problems)

        if problems:
            return _rejected("invalid_character", problems)

        def apply_patch() -> JSONResponse:
            normalized_poses: dict[str, bytes] = {}
            norm_problems: list[str] = []
            for pose, raw_bytes in raw_uploads.items():
                png_bytes, errs = normalize_character_sprite(raw_bytes, field_name=pose)
                if errs or png_bytes is None:
                    norm_problems.extend(errs)
                else:
                    normalized_poses[pose] = png_bytes
            if norm_problems:
                return _rejected("invalid_character", norm_problems)

            with _user_lock(current.user_id):
                root = scene_characters_root(current.user_id)
                state = _load_state_unlocked(root)
                target_char = next((c for c in state["characters"] if c["id"] == clean_id), None)
                if target_char is None:
                    return _not_found(clean_id)
                char_dir = resolve_inside(root, clean_id)
                char_dir.mkdir(parents=True, exist_ok=True)
                if clean_new_name is not None:
                    target_char["name"] = clean_new_name
                poses_map: dict[str, str] = dict(target_char.get("poses") or {})
                for pose in to_remove:
                    if pose in OPTIONAL_POSES and pose not in normalized_poses:
                        poses_map.pop(pose, None)
                        try:
                            resolve_inside(char_dir, f"{pose}.png").unlink(missing_ok=True)
                        except OSError:
                            pass
                for pose, png_bytes in normalized_poses.items():
                    target_file = resolve_inside(char_dir, f"{pose}.png")
                    _atomic_write_bytes(target_file, png_bytes)
                    poses_map[pose] = _pose_url(clean_id, pose)
                target_char["poses"] = poses_map
                _save_state_unlocked(root, state)
                return JSONResponse({"character": target_char})

        return await run_in_threadpool(apply_patch)

    @app.delete("/v1/scene/characters/{character_id}")
    async def delete_scene_character(character_id: str, request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="scene.characters.delete")
        clean_id = (character_id or "").strip()
        if not _CHARACTER_ID_RE.fullmatch(clean_id):
            return _not_found(clean_id)

        def delete() -> JSONResponse:
            with _user_lock(current.user_id):
                root = scene_characters_root(current.user_id)
                state = _load_state_unlocked(root)
                before_len = len(state["characters"])
                state["characters"] = [c for c in state["characters"] if c["id"] != clean_id]
                if len(state["characters"]) == before_len:
                    return _not_found(clean_id)
                state["bindings"] = {
                    k: v for k, v in state["bindings"].items() if v != clean_id
                }
                _save_state_unlocked(root, state)
                try:
                    char_dir = resolve_inside(root, clean_id)
                    if char_dir.is_dir():
                        for child in char_dir.iterdir():
                            if child.is_file():
                                child.unlink(missing_ok=True)
                        char_dir.rmdir()
                except (OSError, ProfileFolderRejected):
                    pass
                return JSONResponse({"deleted": True, "id": clean_id, "bindings": state["bindings"]})

        return await run_in_threadpool(delete)

    @app.get("/v1/scene/characters/{character_id}/{pose}.png")
    async def get_scene_character_pose_image(character_id: str, pose: str, request: Request):
        current = principal(request, mutable=False, purpose="scene.characters.image")
        clean_id = (character_id or "").strip()
        clean_pose = (pose or "").strip().lower()
        if not _CHARACTER_ID_RE.fullmatch(clean_id) or clean_pose not in VALID_POSES:
            return _not_found(clean_id)

        def locate() -> Path | None:
            with _user_lock(current.user_id):
                root = scene_characters_root(current.user_id)
                state = _load_state_unlocked(root)
                target_char = next((c for c in state["characters"] if c["id"] == clean_id), None)
                if target_char is None or clean_pose not in (target_char.get("poses") or {}):
                    return None
                try:
                    img_path = resolve_inside(root, f"{clean_id}/{clean_pose}.png")
                except ProfileFolderRejected:
                    return None
                return img_path if img_path.is_file() else None

        img_path = await run_in_threadpool(locate)
        if img_path is None:
            return _not_found(clean_id)
        return FileResponse(
            img_path,
            media_type="image/png",
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.put("/v1/scene/characters/bindings")
    async def put_scene_character_bindings(request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="scene.characters.bindings.update")
        try:
            body = await request.json()
        except Exception:
            return _rejected("invalid_bindings", ["request body must be valid JSON"])

        if not isinstance(body, dict):
            return _rejected("invalid_bindings", ["request body must be an object"])

        raw_map = body.get("bindings") if isinstance(body.get("bindings"), dict) else body
        if not isinstance(raw_map, dict):
            return _rejected("invalid_bindings", ["'bindings' must be an object mapping agent name/type to character id"])

        def save_bindings() -> JSONResponse:
            with _user_lock(current.user_id):
                root = scene_characters_root(current.user_id)
                state = _load_state_unlocked(root)
                valid_ids = {c["id"] for c in state["characters"]}
                cleaned_bindings: dict[str, str] = {}
                problems: list[str] = []
                for raw_key, raw_val in raw_map.items():
                    if not isinstance(raw_key, str) or not isinstance(raw_val, str):
                        problems.append("binding keys and values must be strings")
                        continue
                    key = raw_key.strip().lower()
                    val = raw_val.strip()
                    if not key or not _BINDING_KEY_RE.fullmatch(key):
                        problems.append(f"invalid agent name/type '{raw_key[:32]}'")
                        continue
                    if not val:
                        continue
                    if val not in valid_ids:
                        problems.append(f"unknown character id '{val[:40]}' for '{key}'")
                        continue
                    cleaned_bindings[key] = val
                if problems:
                    return _rejected("invalid_bindings", problems)
                state["bindings"] = cleaned_bindings
                _save_state_unlocked(root, state)
                return JSONResponse({"bindings": state["bindings"]})

        return await run_in_threadpool(save_bindings)


__all__ = [
    "DEFAULT_SCENE_CHARACTER_NAMES",
    "MAX_CHARACTER_NAMES",
    "MAX_CUSTOM_CHARACTERS",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_DIMENSION",
    "NORMALIZED_MAX_HEIGHT",
    "VALID_POSES",
    "normalize_character_sprite",
    "register_scene_character_routes",
    "scene_characters_root",
]
