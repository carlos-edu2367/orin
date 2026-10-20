"""``orin code plugin``: Orin's plugin library from a terminal, through the local API.

``install`` inspects the package and approves it, like the interface's install; a plugin's hooks and language servers
stay off until the person allows them in the interface (consent is per content, W5-R3), and the command says so.
``validate`` reads a plugin folder locally without installing anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TextIO

from ..args import CliUsageError
from ..errors import CliRuntimeError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _client():
    from agentos.installation import orin_paths

    from ..runtime import connect_local

    return connect_local(orin_paths())


def _items(listed: Any) -> list[dict[str, Any]]:
    items = listed.get("items") if isinstance(listed, dict) else listed
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _line(item: dict[str, Any]) -> str:
    return (f"{item.get('plugin_id')}: {item.get('display_name') or item.get('name')} {item.get('version') or ''} "
            f"[{item.get('state')}{', enabled' if item.get('enabled') else ''}]").replace("  ", " ")


def _validate(path: Path, stdout: TextIO) -> int:
    from agentos.plugins.inspector import inspect_plugin_package
    from agentos.plugins.manifest import ManifestRejected

    if not path.is_dir():
        raise CliRuntimeError(f"{path} is not a folder")
    try:
        inspection = inspect_plugin_package(path, package_digest="local-validation")
    except ManifestRejected as error:
        raise CliRuntimeError(f"the plugin is not valid: {error}") from error
    report = {key: value for key, value in vars(inspection).items() if not key.startswith("_")} if hasattr(inspection, "__dict__") else {}
    if not report:
        report = {name: getattr(inspection, name) for name in getattr(inspection, "__slots__", ())}
    stdout.write("The plugin is valid.\n" + json.dumps(report, indent=2, default=str)[:20000] + "\n")
    return 0


def run(argv: list[str], *, cwd: Path, stdout: TextIO, stderr: TextIO) -> int:
    parser = _Parser(prog="orin code plugin", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for verb in ("install", "uninstall", "enable", "disable", "details", "update"):
        commands.add_parser(verb).add_argument("plugin")
    commands.add_parser("validate").add_argument("path")
    configure = commands.add_parser("configure")
    configure.add_argument("plugin")
    configure.add_argument("values", nargs="+")
    market = commands.add_parser("marketplace")
    market_commands = market.add_subparsers(dest="action", required=True)
    market_commands.add_parser("list")
    market_commands.add_parser("add").add_argument("reference")
    for verb in ("remove", "update"):
        market_commands.add_parser(verb).add_argument("name")
    options = parser.parse_args(argv)

    if options.command == "validate":
        return _validate((cwd / options.path).resolve(), stdout)
    if options.command == "update":
        raise CliRuntimeError("plugin updates are reviewed in Orin's interface (Settings > Plugins), where the new content is shown before it is approved")
    if options.command == "marketplace" and options.action in {"remove", "update"}:
        raise CliRuntimeError(f"marketplace {options.action} is done in Orin's interface (Settings > Plugins)")
    client = _client()
    try:
        if options.command == "list":
            for item in _items(client.get("/v1/plugins")):
                stdout.write(_line(item) + "\n")
            return 0
        if options.command == "details":
            match = next((item for item in _items(client.get("/v1/plugins")) if options.plugin in {item.get("plugin_id"), item.get("name"), item.get("display_name")}), None)
            if match is None:
                raise CliRuntimeError(f"no installed plugin {options.plugin!r}")
            stdout.write(json.dumps(match, indent=2, default=str) + "\n")
            return 0
        if options.command == "install":
            inspected = client.post("/v1/plugins/inspect", {"reference": options.plugin})
            plugin_id = str((inspected or {}).get("plugin_id") or "")
            if not plugin_id:
                raise CliRuntimeError("the plugin could not be inspected")
            approved = client.post(f"/v1/plugins/{plugin_id}/approve", {})
            stdout.write(f"Installed {_line(approved if isinstance(approved, dict) else inspected)}\n")
            stdout.write("Its hooks and language servers stay off until you allow them in Orin's interface (Settings > Plugins).\n")
            return 0
        if options.command == "uninstall":
            client.delete(f"/v1/plugins/{options.plugin}")
            stdout.write(f"Removed {options.plugin}\n")
            return 0
        if options.command in {"enable", "disable"}:
            changed = client.put(f"/v1/plugins/{options.plugin}/enabled", {"enabled": options.command == "enable"})
            stdout.write(_line(changed if isinstance(changed, dict) else {"plugin_id": options.plugin}) + "\n")
            return 0
        if options.command == "configure":
            values: dict[str, Any] = {}
            for pair in options.values:
                key, found, value = pair.partition("=")
                if not found or not key:
                    raise CliUsageError(f"configure takes KEY=VALUE, not {pair!r}")
                values[key] = value
            client.put(f"/v1/plugins/{options.plugin}/config", {"values": values})
            stdout.write(f"Configured {options.plugin}: {', '.join(sorted(values))}\n")
            return 0
        if options.action == "list":
            for item in _items(client.get("/v1/plugins/marketplaces")):
                stdout.write(f"{item.get('name') or item.get('marketplace_id')}: {item.get('reference') or item.get('source') or ''}\n")
            return 0
        added = client.post("/v1/plugins/marketplaces", {"reference": options.reference})
        stdout.write(f"Added marketplace {(added or {}).get('name') or options.reference}\n")
        return 0
    finally:
        client.close()


__all__ = ["run"]
