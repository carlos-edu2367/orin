"""JSON-lines protocol shared by the isolated voice engine workers.

A worker runs inside its own virtual environment (never Orin's), so it may
import only the standard library, numpy and its engine. One request per line
on stdin, one response per line on stdout; everything libraries print goes to
stderr, which the host keeps as the engine log.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

Handler = Callable[[dict[str, Any]], dict[str, Any]]

_WINDOWS_DLL_DIR_HANDLES: list[Any] = []


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def expose_windows_dll_directories() -> list[str]:
    """Register venv, base Python, and native wheel DLL directories on Windows.

    Handles returned by ``os.add_dll_directory`` are kept in ``_WINDOWS_DLL_DIR_HANDLES``
    so garbage collection never revokes the registered search paths.
    """
    if os.name != "nt":
        return []
    candidates: list[Path] = [
        Path(sys.prefix),
        Path(sys.prefix) / "Scripts",
        Path(sys.prefix) / "Library" / "bin",
        Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs",
    ]
    for entry in list(sys.path):
        if not entry:
            continue
        site = Path(entry)
        nvidia = site / "nvidia"
        if _safe_is_dir(nvidia):
            candidates.extend(nvidia.glob("*/bin"))
            candidates.extend(nvidia.glob("*/lib"))
        for sub in (
            site / "torch" / "lib",
            site / "ctranslate2",
            site / "onnxruntime" / "capi",
            site / "espeakng_loader",
        ):
            if _safe_is_dir(sub):
                candidates.append(sub)

    exposed: list[str] = []
    seen: set[str] = set()
    add_dll_dir = getattr(os, "add_dll_directory", None)
    for d in candidates:
        dir_str = str(d)
        if dir_str in seen or not _safe_is_dir(d):
            continue
        seen.add(dir_str)
        if add_dll_dir is not None:
            try:
                handle = add_dll_dir(dir_str)
                if handle is not None:
                    _WINDOWS_DLL_DIR_HANDLES.append(handle)
            except OSError:
                pass
        current_path = os.environ.get("PATH", "")
        path_parts = current_path.split(os.pathsep) if current_path else []
        if dir_str not in path_parts:
            os.environ["PATH"] = f"{dir_str}{os.pathsep}{current_path}" if current_path else dir_str
        exposed.append(dir_str)
    return exposed


expose_windows_dll_directories()


def pcm16_base64(samples: Any) -> str:
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    return base64.b64encode(pcm.tobytes()).decode("ascii")


def pcm16_from_base64(data: str) -> Any:
    import numpy as np

    raw = base64.b64decode(data or "")
    if len(raw) % 2:
        raw = raw[:-1]
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def serve(handlers: dict[str, Handler]) -> None:
    expose_windows_dll_directories()
    out = sys.stdout
    sys.stdout = sys.stderr
    handlers = {"ping": lambda _req: {"pong": True}, **handlers}
    out.write(json.dumps({"ready": True}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            handler = handlers.get(str(request.get("op") or ""))
            if handler is None:
                raise ValueError(f"unknown op {request.get('op')!r}")
            response = {"id": request_id, "ok": True, **handler(request)}
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the host
            traceback.print_exc()
            response = {"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
        out.write(json.dumps(response) + "\n")
        out.flush()
