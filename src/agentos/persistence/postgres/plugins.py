from __future__ import annotations

from typing import Any, Mapping


def row_to_plugin(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"plugin_id": str(row["plugin_id"]), "user_id": str(row["user_id"]), "version": str(row["version"]), "display_name": str(row["display_name"]), "description": str(row["description"] or ""), "author": str(row["author"] or ""), "homepage": row["homepage"], "source_reference": str(row["source_reference"]), "install_path": str(row["install_path"]), "package_digest": str(row["package_digest"]), "state": str(row["state"]), "state_reason": str(row["state_reason"] or ""), "warnings": list(row["warnings"] or [])}


__all__ = ["row_to_plugin"]
