"""Automatic per-turn file checkpoints and rewind support for Orin Code mode."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Mapping


def _workspace_root(workspace: Path | object) -> Path:
    if isinstance(workspace, (Path, str)):
        return Path(workspace).resolve()
    root = getattr(workspace, "root", workspace)
    return Path(root).resolve()


def _normalize_rel_path(raw_path: str, workspace_root: Path) -> str:
    cleaned = str(raw_path or "").strip().replace("\\", "/")
    if not cleaned:
        return ""
    candidate = Path(cleaned)
    if candidate.is_absolute():
        try:
            cleaned = candidate.resolve().relative_to(workspace_root).as_posix()
        except ValueError:
            return ""
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.strip("/")
    if not cleaned:
        return ""
    parts = PurePosixPath(cleaned).parts
    if ".." in parts:
        return ""
    return PurePosixPath(*parts).as_posix()


def _safe_turn_dir_name(turn_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(turn_id or "").strip())
    if not cleaned:
        raise ValueError("turn_id must be a non-empty string")
    return cleaned


class FileCheckpointStore:
    """Stores pre-modification file snapshots per turn and supports atomic rewind."""

    def __init__(self, workspace: Path | object) -> None:
        self.workspace_root = _workspace_root(workspace)

    @property
    def checkpoints_dir(self) -> Path:
        return self.workspace_root / ".orin" / "checkpoints"

    def turn_dir(self, turn_id: str) -> Path:
        return self.checkpoints_dir / _safe_turn_dir_name(turn_id)

    def manifest_path(self, turn_id: str) -> Path:
        return self.turn_dir(turn_id) / "manifest.json"

    def _load_manifest(self, turn_id: str) -> dict[str, Any] | None:
        path = self.manifest_path(turn_id)
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return dict(loaded) if isinstance(loaded, Mapping) else None
        except Exception:
            return None

    def _save_manifest(self, turn_id: str, manifest: Mapping[str, Any]) -> None:
        directory = self.turn_dir(turn_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.manifest_path(turn_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def snapshot_before_write(self, turn_id: str | None, path: str) -> dict[str, Any] | None:
        """Capture the original state of ``path`` before its first write in ``turn_id``.

        Subsequent writes to the same ``path`` within the same ``turn_id`` keep
        the initial snapshot so rewinding the turn always restores the pre-turn state.
        Paths inside ``.orin/`` are ignored.
        """
        if not turn_id or not str(turn_id).strip():
            return None
        rel_path = _normalize_rel_path(path, self.workspace_root)
        if not rel_path or rel_path == ".orin" or rel_path.startswith(".orin/"):
            return None

        target = (self.workspace_root / rel_path).resolve()
        try:
            target.relative_to(self.workspace_root)
        except ValueError:
            return None

        manifest = self._load_manifest(turn_id)
        if manifest is None:
            manifest = {
                "turn_id": str(turn_id),
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "rewound_at": None,
                "files": {},
            }

        files_map = manifest.get("files")
        if not isinstance(files_map, dict):
            files_map = {}
            manifest["files"] = files_map

        # Never overwrite the first snapshot captured for this file in this turn.
        if rel_path in files_map and isinstance(files_map[rel_path], dict):
            return dict(files_map[rel_path])

        if target.exists() and target.is_file():
            raw_bytes = target.read_bytes()
            digest = sha256(raw_bytes).hexdigest()
            backup_rel = f"files/{rel_path}"
            backup_file = self.turn_dir(turn_id) / backup_rel
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            backup_file.write_bytes(raw_bytes)
            entry: dict[str, Any] = {
                "path": rel_path,
                "existed": True,
                "sha256": digest,
                "size_bytes": len(raw_bytes),
                "backup_rel_path": backup_rel,
                "captured_at": datetime.now(UTC).isoformat(),
            }
        else:
            entry = {
                "path": rel_path,
                "existed": False,
                "sha256": None,
                "size_bytes": 0,
                "backup_rel_path": None,
                "captured_at": datetime.now(UTC).isoformat(),
            }

        files_map[rel_path] = entry
        manifest["updated_at"] = datetime.now(UTC).isoformat()
        self._save_manifest(turn_id, manifest)
        return dict(entry)

    def get_checkpoint(self, turn_id: str) -> dict[str, Any] | None:
        manifest = self._load_manifest(turn_id)
        if manifest is None:
            return None
        files_map = manifest.get("files")
        files_dict = dict(files_map) if isinstance(files_map, Mapping) else {}
        files_list = list(files_dict.values())
        return {
            "turn_id": manifest.get("turn_id", turn_id),
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
            "rewound_at": manifest.get("rewound_at"),
            "files": files_list,
            "files_by_path": files_dict,
        }

    def list_checkpoints(self) -> list[dict[str, Any]]:
        if not self.checkpoints_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for child in sorted(self.checkpoints_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "manifest.json"
            if not manifest_file.exists():
                continue
            try:
                loaded = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(loaded, Mapping) or loaded.get("kind") == "undo_rewind":
                continue
            files_map = loaded.get("files")
            files_list = list(files_map.values()) if isinstance(files_map, Mapping) else []
            items.append({
                "turn_id": str(loaded.get("turn_id") or child.name),
                "created_at": loaded.get("created_at"),
                "updated_at": loaded.get("updated_at"),
                "rewound_at": loaded.get("rewound_at"),
                "file_count": len(files_list),
                "files": files_list,
            })
        items.sort(key=lambda item: str(item.get("created_at") or ""))
        return items

    def rewind_turn(self, turn_id: str) -> dict[str, Any]:
        """Restore all files modified or created in ``turn_id`` back to their pre-turn state."""
        manifest = self._load_manifest(turn_id)
        if manifest is None:
            raise LookupError(f"Checkpoint for turn '{turn_id}' not found")
        files_map = manifest.get("files")
        if not isinstance(files_map, Mapping) or not files_map:
            return {
                "turn_id": turn_id,
                "status": "rewound",
                "rewound": True,
                "restored_files": [],
                "deleted_files": [],
                "undo_checkpoint_id": None,
                "undo_backup_id": None,
            }

        # Save an undo snapshot before mutating the workspace so rewind itself is reversible.
        undo_id = f"{_safe_turn_dir_name(turn_id)}_undo_{int(datetime.now(UTC).timestamp() * 1000)}"
        undo_manifest: dict[str, Any] = {
            "turn_id": undo_id,
            "source_turn_id": turn_id,
            "kind": "undo_rewind",
            "created_at": datetime.now(UTC).isoformat(),
            "files": {},
        }
        for rel_path in files_map:
            target = (self.workspace_root / rel_path).resolve()
            try:
                target.relative_to(self.workspace_root)
            except ValueError:
                continue
            if target.exists() and target.is_file():
                raw = target.read_bytes()
                backup_rel = f"files/{rel_path}"
                backup_file = self.turn_dir(undo_id) / backup_rel
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                backup_file.write_bytes(raw)
                undo_manifest["files"][rel_path] = {
                    "path": rel_path,
                    "existed": True,
                    "sha256": sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "backup_rel_path": backup_rel,
                }
            else:
                undo_manifest["files"][rel_path] = {
                    "path": rel_path,
                    "existed": False,
                    "sha256": None,
                    "size_bytes": 0,
                    "backup_rel_path": None,
                }
        self._save_manifest(undo_id, undo_manifest)

        restored_files: list[str] = []
        deleted_files: list[str] = []
        turn_directory = self.turn_dir(turn_id)

        for rel_path, meta in files_map.items():
            if not isinstance(meta, Mapping):
                continue
            target = (self.workspace_root / rel_path).resolve()
            try:
                target.relative_to(self.workspace_root)
            except ValueError:
                continue
            existed = bool(meta.get("existed"))
            if existed:
                backup_rel = str(meta.get("backup_rel_path") or f"files/{rel_path}")
                backup_file = (turn_directory / backup_rel).resolve()
                try:
                    backup_file.relative_to(turn_directory.resolve())
                except ValueError:
                    continue
                if not backup_file.exists():
                    raise FileNotFoundError(f"Checkpoint backup missing for '{rel_path}'")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup_file, target)
                restored_files.append(rel_path)
            else:
                if target.exists() and target.is_file():
                    target.unlink()
                    deleted_files.append(rel_path)

        manifest["rewound_at"] = datetime.now(UTC).isoformat()
        self._save_manifest(turn_id, manifest)
        return {
            "turn_id": turn_id,
            "status": "rewound",
            "rewound": True,
            "restored_files": restored_files,
            "deleted_files": deleted_files,
            "undo_checkpoint_id": undo_id,
            "undo_backup_id": undo_id,
        }


__all__ = ["FileCheckpointStore"]
