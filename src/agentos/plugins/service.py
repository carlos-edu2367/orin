from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.plugins import row_to_plugin
from agentos.persistence.postgres.schema import plugin_contributions, plugin_marketplaces, plugins

from .activator import PluginActivator
from .discovery import PluginDiscoveryService
from .fetcher import FetchRejected, PluginFetcher
from .hooks_manifest import consented_hooks
from .inspector import inspect_plugin_package
from .manifest import ManifestRejected, manifest_file, parse_plugin_manifest
from .mcp_inference import infer_mcp_launch as _infer_mcp_launch
from .models import PluginState
from .marketplace import marketplace_file, parse_marketplace
from .models import plugin_id_from_name
from .sources import SourceRejected, resolve_source


class PluginServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "plugin_operation_rejected") -> None:
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(UTC)


# Contribution kinds whose enabled flag is the person's own consent to run code (W5): a plugin's hooks and its
# language servers. Switched apart from the blanket toggle of the other kinds, so pausing never grants them.
CONSENT_KINDS = ("hook", "lsp")


class PluginService:
    def __init__(self, engine: Engine, *, plugin_root: Path, skill_library, mcp_service, fetcher=None, activator=None, search_client=None, manifest_probe=None, command_library=None, hook_engine=None) -> None:
        self.engine, self.plugin_root = engine, Path(plugin_root).resolve()
        self.skill_library, self.mcp_service = skill_library, mcp_service
        self.fetcher = fetcher or PluginFetcher(self.plugin_root)
        self.activator = activator or PluginActivator(skill_library=skill_library, mcp_service=mcp_service, command_library=command_library, hook_engine=hook_engine)
        self.discovery = PluginDiscoveryService(self, search_client=search_client, manifest_probe=manifest_probe)

    # -- options (userConfig, W5) ---------------------------------------------

    @property
    def _config_store(self):
        store = getattr(self, "_config_store_instance", None)
        if store is None:
            from .user_config import PluginConfigStore

            store = self._config_store_instance = PluginConfigStore(self.engine)
        return store

    def _declared_options(self, record: Mapping[str, Any]) -> dict[str, Any]:
        target = manifest_file(Path(record["install_path"]))
        if target is None:
            return {}
        manifest = parse_plugin_manifest(json.loads(target.read_text(encoding="utf-8")))
        return {item.key: item for item in manifest.user_config}

    def user_config(self, *, user_id: str, plugin_id: str) -> dict[str, Any]:
        from .user_config import public_view

        options = self._declared_options(self.get(user_id, plugin_id))
        values = self._config_store.values(user_id, plugin_id) if options else {}
        return {"plugin_id": plugin_id, "options": public_view(options, values)}

    def set_user_config(self, *, user_id: str, plugin_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        """Change a plugin's options. An active plugin's MCP servers take new values when it is turned off and on."""
        from .user_config import PluginConfigError

        options = self._declared_options(self.get(user_id, plugin_id))
        try:
            self._config_store.update(user_id, plugin_id, dict(values), options)
        except PluginConfigError as error:
            raise PluginServiceError(str(error), code="plugin_config_invalid") from error
        return self.user_config(user_id=user_id, plugin_id=plugin_id)

    def option_values(self, *, user_id: str, plugin_id: str) -> dict[str, str]:
        """A plugin's option values for its hooks (W5): defaults, then the person's."""
        options = self._declared_options(self.get(user_id, plugin_id))
        if not options:
            return {}
        values = {key: item.default for key, item in options.items() if item.default is not None}
        values.update(self._config_store.values(user_id, plugin_id))
        return values

    def _option_values(self, user_id: str, plugin_id: str, inspection: Any) -> dict[str, str]:
        """The values to activate with: defaults, then the person's; refuses a missing required or misplaced sensitive one."""
        from .user_config import referenced_options

        options = {item.key: item for item in inspection.options}
        if not options:
            return {}
        values = {key: item.default for key, item in options.items() if item.default is not None}
        values.update(self._config_store.values(user_id, plugin_id))
        missing = [key for key, item in options.items() if item.required and key not in values]
        if missing:
            raise PluginServiceError(f"set the plugin option(s) {', '.join(missing)} before turning it on", code="plugin_config_required")
        for server in inspection.mcp_servers:
            for text in (server.command or "", *server.args, server.url or ""):
                for key in referenced_options(text):
                    if key in options and options[key].sensitive:
                        raise PluginServiceError(
                            f"the sensitive option {key} cannot be written into an MCP server's command line or URL",
                            code="plugin_config_sensitive",
                        )
                    if key not in values:
                        raise PluginServiceError(f"set the plugin option {key} before turning it on", code="plugin_config_required")
        return values

    def _activate(self, user_id: str, record: Mapping[str, Any], inspection: Any):
        values = self._option_values(user_id, str(record["plugin_id"]), inspection)
        extra = {"user_config": values} if inspection.options else {}
        return self.activator.activate(user_id=user_id, install_path=Path(record["install_path"]), inspection=inspection, **extra)

    def search(self, query: str, *, user_id: str | None = None) -> list[dict[str, Any]]:
        """Plugins in the known marketplaces; with ``user_id``, only that person's marketplaces (W5)."""
        needle = str(query or "").casefold()
        results: list[dict[str, Any]] = []
        if not needle or "superpowers" in needle:
            results.append({"name": "superpowers", "reference": "obra/superpowers", "description": "Skills de processo", "marketplace": "default"})
        statement = select(plugin_marketplaces)
        if user_id is not None:
            statement = statement.where(plugin_marketplaces.c.user_id == user_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        for row in rows:
            if not row["clone_path"]:
                continue
            root = Path(str(row["clone_path"]))
            index = marketplace_file(root)
            if index is None:
                continue
            try:
                marketplace = parse_marketplace(json.loads(index.read_text(encoding="utf-8")), root=root)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for item in marketplace.plugins:
                if not needle or needle in item.name.casefold() or needle in item.description.casefold():
                    results.append({"name": item.name, "reference": item.reference, "description": item.description, "marketplace": marketplace.name})
        return results

    def discover_library(self, *, user_id: str, refresh: bool = False, query: str | None = None) -> dict[str, Any]:
        """The plugin library for one person: their marketplaces' plugins and the web's (W7, W5-D1)."""
        entries, web_available = self.discovery.entries(user_id=user_id, refresh=refresh, query=query)
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
        """Fetch a marketplace (a GitHub repository or, on this computer, a folder), keep a copy and read its index (W5)."""
        import hashlib

        try:
            source = resolve_source(reference)
        except SourceRejected as error:
            raise PluginServiceError(str(error), code="marketplace_invalid_source") from error
        if source.kind not in ("git", "path"):
            raise PluginServiceError("a marketplace is a GitHub repository or a local folder", code="marketplace_invalid_source")
        person = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        try:
            with self.fetcher.fetch_raw(source) as staged:
                index = marketplace_file(staged)
                if index is None:
                    raise PluginServiceError("the repository has no .claude-plugin/marketplace.json", code="marketplace_not_found")
                try:
                    marketplace = parse_marketplace(json.loads(index.read_text(encoding="utf-8")), root=staged)
                except (OSError, ValueError) as error:
                    raise PluginServiceError(f"the marketplace index is not valid: {str(error)[:200]}", code="marketplace_invalid") from error
                try:
                    marketplace_id = plugin_id_from_name(marketplace.name)
                except ValueError as error:
                    raise PluginServiceError("the marketplace has no usable name", code="marketplace_invalid") from error
                destination = self.plugin_root / "marketplaces" / person / marketplace_id
                self._safe_remove(destination)  # the previous copy of this marketplace, inside Orin's plugin folder
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(staged, destination, symlinks=False)
        except FetchRejected as error:
            raise PluginServiceError(str(error), code="marketplace_fetch_failed") from error
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(delete(plugin_marketplaces).where((plugin_marketplaces.c.marketplace_id == marketplace_id) & (plugin_marketplaces.c.user_id == user_id)))
            connection.execute(insert(plugin_marketplaces).values(
                marketplace_id=marketplace_id, user_id=user_id, name=marketplace.name, reference=reference[:1024],
                clone_path=str(destination), updated_at=now,
            ))
        return {"marketplace_id": marketplace_id, "name": marketplace.name, "reference": reference, "clone_path": str(destination),
                "user_id": user_id, "plugins": len(marketplace.plugins)}

    def inspect(self, *, user_id: str, reference: str) -> dict[str, Any]:
        source = resolve_source(reference)
        if source.kind == "marketplace":
            # W5: "name@marketplace" picks the plugin from that marketplace only (the person's own marketplaces).
            wanted, _, market = str(source.plugin_name or "").partition("@")
            entries = self.search(wanted, user_id=user_id)
            candidate = next((
                item for item in entries
                if str(item["name"]).casefold() == wanted.casefold() and (not market or str(item["marketplace"]).casefold() == market.casefold())
            ), None)
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
        if prior is not None and getattr(self.activator, "hook_engine", None) is not None:
            # W5: until the new package is approved, the hooks of the previous one stop running.
            self.activator.hook_engine.unregister(user_id=user_id, plugin_id=inspection.ref.plugin_id)
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
            result = self._activate(user_id, record, inspection)
        except PluginServiceError:
            raise
        except Exception as error:
            raise PluginServiceError("plugin activation failed") from error
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(delete(plugin_contributions).where((plugin_contributions.c.plugin_id == plugin_id) & (plugin_contributions.c.user_id == user_id)))
            for item in result.contributions:
                connection.execute(insert(plugin_contributions).values(plugin_id=plugin_id, user_id=user_id, kind=item["kind"], reference=item["reference"], display_name=item["display_name"], enabled=bool(item.get("enabled", True)), created_at=now))
            connection.execute(update(plugins).where((plugins.c.plugin_id == plugin_id) & (plugins.c.user_id == user_id)).values(state=PluginState.ACTIVE.value, state_reason="", updated_at=now))
        return self.get(user_id, plugin_id)

    def set_enabled(self, *, user_id: str, plugin_id: str, enabled: bool) -> dict[str, Any]:
        record = self.get(user_id, plugin_id)
        target = PluginState.ACTIVE if enabled else PluginState.DISABLED
        current = PluginState(record["state"])
        if current not in {PluginState.ACTIVE, PluginState.DISABLED}:
            # W5: an update that waits for approval is approved first; switching it on would skip the person's review.
            raise PluginServiceError("plugin awaits approval: approve it before turning it on or off", code="plugin_not_approved")
        if current != target and not current.can_transition_to(target):
            raise PluginServiceError(f"plugin cannot transition from {current.value} to {target.value}")
        contributions = self._contributions(user_id, plugin_id)
        inspection = inspect_plugin_package(Path(record["install_path"]), package_digest=record["package_digest"])
        if enabled:
            # A hook's execution consent is a separate, explicitly revocable
            # decision (see set_hooks_enabled). Re-activating after a disable
            # keeps it only for a hook whose exact reference (id and digest of
            # what it runs) was allowed before; a hook changed while the plugin
            # was off waits for a new consent.
            allowed_before = {str(item["reference"]) for item in contributions if item.get("kind") in CONSENT_KINDS and item.get("enabled")}
            activation = self._activate(user_id, record, inspection)
            now = _now()
            allowed_now = [str(item["reference"]) for item in activation.contributions if item["kind"] == "hook" and str(item["reference"]) in allowed_before]
            with self.engine.begin() as connection:
                connection.execute(delete(plugin_contributions).where((plugin_contributions.c.plugin_id == plugin_id) & (plugin_contributions.c.user_id == user_id)))
                for item in activation.contributions:
                    if item["kind"] in CONSENT_KINDS:
                        row_enabled = str(item["reference"]) in allowed_before
                    else:
                        row_enabled = bool(item.get("enabled", True))
                    connection.execute(insert(plugin_contributions).values(plugin_id=plugin_id, user_id=user_id, kind=item["kind"], reference=item["reference"], display_name=item["display_name"], enabled=row_enabled, created_at=now))
            if self.activator.hook_engine is not None:
                self.activator.hook_engine.register(
                    user_id=user_id, plugin_id=plugin_id, install_path=Path(record["install_path"]),
                    hooks=consented_hooks(inspection.hooks, allowed_now), enabled=True,
                )
        else:
            self.activator.deactivate(user_id=user_id, plugin_id=plugin_id, contributions=contributions)
        with self.engine.begin() as connection:
            connection.execute(update(plugins).where((plugins.c.plugin_id == plugin_id) & (plugins.c.user_id == user_id)).values(state=target.value, updated_at=_now()))
            # Hook rows carry their own independently revocable consent (see
            # set_hooks_enabled) and are excluded here: this blanket toggle is
            # for the other contribution kinds only, so pausing/resuming a
            # plugin can never grant hook-execution consent that was never
            # explicitly given.
            connection.execute(update(plugin_contributions).where((plugin_contributions.c.plugin_id == plugin_id) & (plugin_contributions.c.user_id == user_id) & (plugin_contributions.c.kind.not_in(CONSENT_KINDS))).values(enabled=enabled))
        return self.get(user_id, plugin_id)

    def remove(self, *, user_id: str, plugin_id: str) -> dict[str, Any]:
        record = self.get(user_id, plugin_id)
        if record["state"] in {PluginState.ACTIVE.value, PluginState.DISABLED.value}:
            self.activator.deactivate(user_id=user_id, plugin_id=plugin_id, contributions=self._contributions(user_id, plugin_id))
        self._delete_record(user_id, plugin_id)
        self._safe_remove(Path(record["install_path"]))
        try:
            self._config_store.remove(user_id, plugin_id)  # W5: a removed plugin's options go with it
        except Exception:  # noqa: BLE001 - the plugin is removed either way
            pass
        return {"plugin_id": plugin_id, "removed": True}

    def get(self, user_id: str, plugin_id: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(plugins).where((plugins.c.user_id == user_id) & (plugins.c.plugin_id == plugin_id))).mappings().first()
        if row is None:
            if missing_ok: return None
            raise PluginServiceError("plugin was not found")
        result = row_to_plugin(row)
        contributions = self._contributions(user_id, plugin_id)
        result["contribution_count"] = len(contributions)
        result["contributions"] = self._public_contributions(contributions)
        return result

    def list(self, user_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(plugins).where(plugins.c.user_id == user_id).order_by(plugins.c.display_name)).mappings().all()
        results = []
        for row in rows:
            contributions = self._contributions(user_id, str(row["plugin_id"]))
            results.append({**row_to_plugin(row), "contribution_count": len(contributions), "contributions": self._public_contributions(contributions)})
        return results

    @staticmethod
    def _public_contributions(contributions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"kind": str(item["kind"]), "reference": str(item["reference"]), "display_name": str(item["display_name"]), "enabled": bool(item["enabled"])}
            for item in contributions
        ]

    def list_commands(self, user_id: str) -> list[dict[str, Any]]:
        library = getattr(self.activator, "command_library", None)
        return [dict(item) for item in library.list(user_id)] if library is not None else []

    def set_hooks_enabled(self, *, user_id: str, plugin_id: str, enabled: bool) -> dict[str, Any]:
        """Authorize, or revoke authorization for, this plugin's hooks.

        Separate from ``set_enabled`` on purpose: approving an install and
        allowing third-party code to run are two decisions, revocable apart.
        """
        record = self.get(user_id, plugin_id)
        state = PluginState(record["state"])
        if state not in {PluginState.ACTIVE, PluginState.DISABLED}:
            # W5: an update that waits for approval is approved first; its hooks are code the person has not seen.
            raise PluginServiceError("plugin awaits approval: approve it before allowing its hooks", code="plugin_not_approved")
        with self.engine.begin() as connection:
            connection.execute(update(plugin_contributions).where(
                (plugin_contributions.c.plugin_id == plugin_id)
                & (plugin_contributions.c.user_id == user_id)
                & (plugin_contributions.c.kind.in_(CONSENT_KINDS))
            ).values(enabled=enabled))
        hook_engine = getattr(self.activator, "hook_engine", None)
        if hook_engine is not None:
            inspection = inspect_plugin_package(Path(record["install_path"]), package_digest=record["package_digest"])
            allowed = [str(item["reference"]) for item in self._contributions(user_id, plugin_id) if item.get("kind") == "hook" and item.get("enabled")]
            # A disabled plugin's hooks do not run; set_enabled registers them again when the plugin is turned on.
            running = consented_hooks(inspection.hooks, allowed) if state is PluginState.ACTIVE else ()
            hook_engine.register(
                user_id=user_id, plugin_id=plugin_id, install_path=Path(record["install_path"]),
                hooks=running, enabled=enabled,
            )
        return self.get(user_id, plugin_id)

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
        return {"plugin_id": inspection.ref.plugin_id, "version": inspection.ref.version, "display_name": inspection.display_name, "description": inspection.description, "author": inspection.author, "homepage": inspection.homepage, "state": PluginState.PENDING_APPROVAL.value, "install_path": values["install_path"], "package_digest": inspection.package_digest, "warnings": list(inspection.warnings), "skills": [item.__dict__ if hasattr(item, "__dict__") else {"skill_id": item.skill_id, "name": item.name, "description": item.description, "relative_path": item.relative_path} for item in inspection.skills], "mcp_servers": [{"slug": item.slug, "display_name": item.display_name, "transport": item.transport, "secret_names": list(item.secret_names)} for item in inspection.mcp_servers], "agents": [{"agent_id": item.agent_id, "name": item.name, "role": item.role} for item in inspection.agents], "commands": [{"command_id": item.command_id, "slug": item.slug, "description": item.description, "argument_hint": item.argument_hint} for item in inspection.commands], "hooks": [{"hook_id": item.hook_id, "event": item.event, "matcher": item.matcher, "command": item.command, "timeout_seconds": item.timeout_seconds} for item in inspection.hooks], "lsp_servers": [{"name": item.name, "command": item.command, "args": list(item.args), "extensions": [extension for extension, _language in item.extensions]} for item in inspection.lsp_servers], "contribution_count": inspection.contribution_count}
