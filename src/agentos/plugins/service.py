from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.plugins import row_to_plugin
from agentos.persistence.postgres.schema import plugin_contributions, plugin_marketplaces, plugins

from .activator import PluginActivator
from .discovery import PluginDiscoveryService
from .fetcher import FetchRejected, PluginFetcher
from .inspector import inspect_plugin_package
from .manifest import ManifestRejected
from .mcp_inference import infer_mcp_launch as _infer_mcp_launch
from .models import PluginState
from .marketplace import parse_marketplace
from .models import plugin_id_from_name
from .sources import resolve_source


class PluginServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "plugin_operation_rejected") -> None:
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(UTC)


class PluginService:
    def __init__(self, engine: Engine, *, plugin_root: Path, skill_library, mcp_service, fetcher=None, activator=None, search_client=None, manifest_probe=None) -> None:
        self.engine, self.plugin_root = engine, Path(plugin_root).resolve()
        self.skill_library, self.mcp_service = skill_library, mcp_service
        self.fetcher = fetcher or PluginFetcher(self.plugin_root)
        self.activator = activator or PluginActivator(skill_library=skill_library, mcp_service=mcp_service)
        self.discovery = PluginDiscoveryService(self, search_client=search_client, manifest_probe=manifest_probe)

    def search(self, query: str) -> list[dict[str, Any]]:
        needle = str(query or "").casefold()
        results: list[dict[str, Any]] = []
        if not needle or "superpowers" in needle:
            results.append({"name": "superpowers", "reference": "obra/superpowers", "description": "Skills de processo", "marketplace": "default"})
        with self.engine.connect() as connection:
            rows = connection.execute(select(plugin_marketplaces)).mappings().all()
        for row in rows:
            if not row["clone_path"]:
                continue
            index = Path(str(row["clone_path"])) / "marketplace.json"
            if not index.is_file():
                continue
            try:
                marketplace = parse_marketplace(json.loads(index.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for item in marketplace.plugins:
                if not needle or needle in item.name.casefold() or needle in item.description.casefold():
                    results.append({"name": item.name, "reference": item.reference, "description": item.description, "marketplace": marketplace.name})
        return results

    def discover_library(self, *, refresh: bool = False, query: str | None = None) -> dict[str, Any]:
        entries, web_available = self.discovery.entries(refresh=refresh, query=query)
        return {
            "entries": [{"name": e.name, "description": e.description, "source_url": e.source_url, "origin": e.origin, "installable_kind": e.installable_kind} for e in entries],
            "web_search_available": web_available,
        }

    def infer_mcp_launch(self, *, source_url: str) -> dict[str, Any]:
        source = resolve_source(source_url)
        if source.kind not in ("git", "path"):
            raise PluginServiceError("only a public GitHub repository can be added as an MCP server", code="plugin_invalid_source")
        try:
            with self.fetcher.fetch_raw(source) as path:
                guess = _infer_mcp_launch(path, suggested_name=source.suggested_name)
        except FetchRejected as error:
            raise PluginServiceError(str(error), code="plugin_fetch_failed") from error
        return asdict(guess)

    def list_marketplaces(self, user_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(plugin_marketplaces).where(plugin_marketplaces.c.user_id == user_id)).mappings().all()
        return [{"marketplace_id": str(row["marketplace_id"]), "name": str(row["name"]), "reference": str(row["reference"]), "clone_path": str(row["clone_path"])} for row in rows]

    def add_marketplace(self, *, user_id: str, reference: str) -> dict[str, Any]:
        if not reference.startswith("https://") and "://" in reference:
            raise PluginServiceError("marketplace reference must use HTTPS")
        name = Path(reference.rstrip("/")).name or "marketplace"
        marketplace_id = plugin_id_from_name(name)
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(delete(plugin_marketplaces).where((plugin_marketplaces.c.marketplace_id == marketplace_id) & (plugin_marketplaces.c.user_id == user_id)))
            connection.execute(insert(plugin_marketplaces).values(marketplace_id=marketplace_id, user_id=user_id, name=name, reference=reference[:1024], clone_path="", updated_at=now))
        return {"marketplace_id": marketplace_id, "name": name, "reference": reference, "clone_path": "", "user_id": user_id}

    def inspect(self, *, user_id: str, reference: str) -> dict[str, Any]:
        source = resolve_source(reference)
        if source.kind == "marketplace":
            entries = self.search(source.plugin_name or "")
            candidate = next((item for item in entries if str(item["name"]).casefold() == str(source.plugin_name).casefold()), None)
            if candidate is None:
                raise PluginServiceError("plugin name was not found in a configured marketplace")
            source = resolve_source(str(candidate["reference"]))
        try:
            fetched = self.fetcher.fetch(source)
            inspection = inspect_plugin_package(fetched.path, package_digest=fetched.package_digest)
        except Exception as error:
            if isinstance(error, PluginServiceError):
                raise
            if isinstance(error, ManifestRejected):
                raise PluginServiceError(str(error), code="plugin_manifest_path_rejected") from error
            if isinstance(error, FetchRejected) and "no valid manifest" in str(error):
                raise PluginServiceError(str(error), code="plugin_no_manifest") from error
            raise PluginServiceError("plugin could not be inspected") from error
        if not inspection.is_installable:
            raise PluginServiceError("this package contributes nothing Orin can use")
        prior = self.get(user_id, inspection.ref.plugin_id, missing_ok=True)
        if prior is not None and prior["version"] != inspection.ref.version:
            self._delete_record(user_id, inspection.ref.plugin_id)
            self._safe_remove(Path(prior["install_path"]))
        now = _now()
        values = {"plugin_id": inspection.ref.plugin_id, "user_id": user_id, "version": inspection.ref.version, "display_name": inspection.display_name, "description": inspection.description, "author": inspection.author, "homepage": inspection.homepage, "source_reference": reference[:1024], "install_path": str(fetched.path), "package_digest": fetched.package_digest, "state": PluginState.PENDING_APPROVAL.value, "state_reason": "awaiting explicit approval", "warnings": list(inspection.warnings), "created_at": now, "updated_at": now}
        with self.engine.begin() as connection:
            connection.execute(delete(plugins).where((plugins.c.plugin_id == inspection.ref.plugin_id) & (plugins.c.user_id == user_id)))
            connection.execute(insert(plugins).values(**values))
        return self._inspection_result(inspection, values)

    def approve(self, *, user_id: str, plugin_id: str) -> dict[str, Any]:
        record = self.get(user_id, plugin_id)
        if record["state"] != PluginState.PENDING_APPROVAL.value:
            raise PluginServiceError("plugin is not awaiting approval")
        inspection = inspect_plugin_package(Path(record["install_path"]), package_digest=record["package_digest"])
        try:
            result = self.activator.activate(user_id=user_id, install_path=Path(record["install_path"]), inspection=inspection)
        except Exception as error:
            raise PluginServiceError("plugin activation failed") from error
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(delete(plugin_contributions).where((plugin_contributions.c.plugin_id == plugin_id) & (plugin_contributions.c.user_id == user_id)))
            for item in result.contributions:
                connection.execute(insert(plugin_contributions).values(plugin_id=plugin_id, user_id=user_id, kind=item["kind"], reference=item["reference"], display_name=item["display_name"], enabled=True, created_at=now))
            connection.execute(update(plugins).where((plugins.c.plugin_id == plugin_id) & (plugins.c.user_id == user_id)).values(state=PluginState.ACTIVE.value, state_reason="", updated_at=now))
        return self.get(user_id, plugin_id)

    def set_enabled(self, *, user_id: str, plugin_id: str, enabled: bool) -> dict[str, Any]:
        record = self.get(user_id, plugin_id)
        target = PluginState.ACTIVE if enabled else PluginState.DISABLED
        current = PluginState(record["state"])
        if current != target and not current.can_transition_to(target):
            raise PluginServiceError(f"plugin cannot transition from {current.value} to {target.value}")
        contributions = self._contributions(user_id, plugin_id)
        inspection = inspect_plugin_package(Path(record["install_path"]), package_digest=record["package_digest"])
        if enabled:
            activation = self.activator.activate(user_id=user_id, install_path=Path(record["install_path"]), inspection=inspection)
            now = _now()
            with self.engine.begin() as connection:
                connection.execute(delete(plugin_contributions).where((plugin_contributions.c.plugin_id == plugin_id) & (plugin_contributions.c.user_id == user_id)))
                for item in activation.contributions:
                    connection.execute(insert(plugin_contributions).values(plugin_id=plugin_id, user_id=user_id, kind=item["kind"], reference=item["reference"], display_name=item["display_name"], enabled=True, created_at=now))
        else:
            self.activator.deactivate(user_id=user_id, plugin_id=plugin_id, contributions=contributions)
        with self.engine.begin() as connection:
            connection.execute(update(plugins).where((plugins.c.plugin_id == plugin_id) & (plugins.c.user_id == user_id)).values(state=target.value, updated_at=_now()))
            connection.execute(update(plugin_contributions).where((plugin_contributions.c.plugin_id == plugin_id) & (plugin_contributions.c.user_id == user_id)).values(enabled=enabled))
        return self.get(user_id, plugin_id)

    def remove(self, *, user_id: str, plugin_id: str) -> dict[str, Any]:
        record = self.get(user_id, plugin_id)
        if record["state"] in {PluginState.ACTIVE.value, PluginState.DISABLED.value}:
            self.activator.deactivate(user_id=user_id, plugin_id=plugin_id, contributions=self._contributions(user_id, plugin_id))
        self._delete_record(user_id, plugin_id)
        self._safe_remove(Path(record["install_path"]))
        return {"plugin_id": plugin_id, "removed": True}

    def get(self, user_id: str, plugin_id: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(plugins).where((plugins.c.user_id == user_id) & (plugins.c.plugin_id == plugin_id))).mappings().first()
        if row is None:
            if missing_ok: return None
            raise PluginServiceError("plugin was not found")
        result = row_to_plugin(row)
        result["contribution_count"] = len(self._contributions(user_id, plugin_id))
        return result

    def list(self, user_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(plugins).where(plugins.c.user_id == user_id).order_by(plugins.c.display_name)).mappings().all()
        return [{**row_to_plugin(row), "contribution_count": len(self._contributions(user_id, str(row["plugin_id"]))) } for row in rows]

    def _contributions(self, user_id, plugin_id):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(plugin_contributions).where((plugin_contributions.c.user_id == user_id) & (plugin_contributions.c.plugin_id == plugin_id))).mappings()]

    def _delete_record(self, user_id, plugin_id):
        with self.engine.begin() as connection:
            connection.execute(delete(plugin_contributions).where((plugin_contributions.c.plugin_id == plugin_id) & (plugin_contributions.c.user_id == user_id)))
            connection.execute(delete(plugins).where((plugins.c.plugin_id == plugin_id) & (plugins.c.user_id == user_id)))

    def _safe_remove(self, path: Path):
        resolved = path.resolve(strict=False)
        if resolved.exists() and resolved.is_relative_to(self.plugin_root):
            shutil.rmtree(resolved)

    @staticmethod
    def _inspection_result(inspection, values):
        return {"plugin_id": inspection.ref.plugin_id, "version": inspection.ref.version, "display_name": inspection.display_name, "description": inspection.description, "author": inspection.author, "homepage": inspection.homepage, "state": PluginState.PENDING_APPROVAL.value, "install_path": values["install_path"], "package_digest": inspection.package_digest, "warnings": list(inspection.warnings), "skills": [item.__dict__ if hasattr(item, "__dict__") else {"skill_id": item.skill_id, "name": item.name, "description": item.description, "relative_path": item.relative_path} for item in inspection.skills], "mcp_servers": [{"slug": item.slug, "display_name": item.display_name, "transport": item.transport, "secret_names": list(item.secret_names)} for item in inspection.mcp_servers], "agents": [{"agent_id": item.agent_id, "name": item.name, "role": item.role} for item in inspection.agents], "contribution_count": inspection.contribution_count}
