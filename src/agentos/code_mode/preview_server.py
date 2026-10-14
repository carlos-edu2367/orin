"""Local preview server manager (`.orin/launch.json` & `.claude/launch.json`) with AgentBrowserView integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from typing import Any, Mapping


_LAUNCH_CONFIG_PATHS = (
    ".orin/launch.json",
    ".claude/launch.json",
)
_PREVIEW_MANIFEST_PATH = ".orin/preview_servers.json"
_PREVIEW_LOG_DIR = ".orin/logs"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PORT_REGEX = re.compile(r"(?:--port|-p)\s*[= ]?\s*(\d{2,5})\b")

_LIVE_PROCESSES_LOCK = threading.Lock()
_LIVE_PROCESSES: dict[tuple[str, str], subprocess.Popen[bytes]] = {}


class PreviewServerError(RuntimeError):
    """Raised when a preview server configuration or lifecycle operation fails."""


@dataclass(frozen=True, slots=True)
class LaunchConfiguration:
    """One named preview server configuration from `.orin/launch.json` or `.claude/launch.json`."""

    name: str
    runtimeExecutable: str
    runtimeArgs: tuple[str, ...] = ()
    port: int = 3000
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    autoPort: bool = False
    url: str | None = None
    source: str = ".orin/launch.json"

    @property
    def runtime_executable(self) -> str:
        return self.runtimeExecutable

    @property
    def runtime_args(self) -> tuple[str, ...]:
        return self.runtimeArgs

    def target_url(self) -> str:
        if self.url and self.url.strip():
            return self.url.strip()
        return f"http://127.0.0.1:{self.port}"

    def command_list(self) -> list[str]:
        executable = shutil.which(self.runtimeExecutable) or self.runtimeExecutable
        return [executable, *self.runtimeArgs]

    def command_string(self) -> str:
        parts = [self.runtimeExecutable, *self.runtimeArgs]
        return " ".join(shlex.quote(part) if " " in part else part for part in parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "runtimeExecutable": self.runtimeExecutable,
            "runtimeArgs": list(self.runtimeArgs),
            "port": self.port,
            "cwd": self.cwd,
            "env": dict(self.env),
            "autoPort": self.autoPort,
            "url": self.target_url(),
            "source": self.source,
        }


def is_port_open(port: int, *, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    """Return True if a TCP server is accepting connections on ``host:port``."""
    if not isinstance(port, int) or port <= 0 or port > 65535:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class PreviewServerManager:
    """Loads launch configurations and manages local dev server lifecycles for a workspace."""

    def __init__(self, workspace: Any, *, browser: Any | None = None) -> None:
        if isinstance(workspace, (Path, str)):
            root = workspace
        else:
            root = getattr(workspace, "root", workspace)
        self.workspace_root = Path(root).resolve()
        self.browser = browser

    def load_configurations(self) -> list[LaunchConfiguration]:
        """Load launch configurations from `.orin/launch.json`, `.claude/launch.json`, or infer from manifests."""
        for rel_path in _LAUNCH_CONFIG_PATHS:
            config_file = self.workspace_root / rel_path
            if not config_file.is_file():
                continue
            parsed = self._parse_launch_file(config_file, source=rel_path)
            if parsed:
                return parsed
        return self.infer_configurations()

    def _parse_launch_file(self, path: Path, *, source: str) -> list[LaunchConfiguration]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as error:
            raise PreviewServerError(f"invalid JSON in {source}: {error}") from error
        if not isinstance(raw, Mapping):
            raise PreviewServerError(f"{source} must contain a JSON object")
        raw_configs = raw.get("configurations")
        if not isinstance(raw_configs, list):
            return []
        configs: list[LaunchConfiguration] = []
        for idx, item in enumerate(raw_configs):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or f"server-{idx + 1}").strip()
            executable = str(item.get("runtimeExecutable") or item.get("command") or "").strip()
            if not executable:
                continue
            raw_args = item.get("runtimeArgs") if "runtimeArgs" in item else item.get("args", [])
            if isinstance(raw_args, str):
                args = tuple(shlex.split(raw_args))
            elif isinstance(raw_args, list):
                args = tuple(str(arg) for arg in raw_args)
            else:
                args = ()
            try:
                port = int(item.get("port") or 3000)
            except (TypeError, ValueError):
                port = 3000
            cwd = str(item["cwd"]).strip() if isinstance(item.get("cwd"), str) and item.get("cwd") else None
            raw_env = item.get("env")
            env = (
                {str(k): str(v) for k, v in raw_env.items() if isinstance(k, str)}
                if isinstance(raw_env, Mapping)
                else {}
            )
            url = str(item["url"]).strip() if isinstance(item.get("url"), str) and item.get("url") else None
            configs.append(
                LaunchConfiguration(
                    name=name,
                    runtimeExecutable=executable,
                    runtimeArgs=args,
                    port=port,
                    cwd=cwd,
                    env=env,
                    autoPort=bool(item.get("autoPort", False)),
                    url=url,
                    source=source,
                )
            )
        return configs

    def infer_configurations(self) -> list[LaunchConfiguration]:
        """Auto-detect preview server configuration from `package.json` or `pyproject.toml`."""
        pkg_file = self.workspace_root / "package.json"
        if pkg_file.is_file():
            try:
                pkg = json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pkg = None
            if isinstance(pkg, Mapping):
                scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), Mapping) else {}
                deps: dict[str, Any] = {}
                if isinstance(pkg.get("dependencies"), Mapping):
                    deps.update(pkg["dependencies"])
                if isinstance(pkg.get("devDependencies"), Mapping):
                    deps.update(pkg["devDependencies"])
                inferred: list[LaunchConfiguration] = []
                for script_name in ("dev", "start", "preview", "serve"):
                    script_cmd = scripts.get(script_name)
                    if not isinstance(script_cmd, str) or not script_cmd.strip():
                        continue
                    port = self._infer_port_from_package(script_name, script_cmd, deps)
                    inferred.append(
                        LaunchConfiguration(
                            name=script_name,
                            runtimeExecutable="npm",
                            runtimeArgs=("run", script_name),
                            port=port,
                            source="package.json",
                        )
                    )
                if inferred:
                    return inferred

        pyproject_file = self.workspace_root / "pyproject.toml"
        if pyproject_file.is_file():
            try:
                text = pyproject_file.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                text = ""
            if "fastapi" in text or "uvicorn" in text:
                return [
                    LaunchConfiguration(
                        name="api",
                        runtimeExecutable="python",
                        runtimeArgs=("-m", "uvicorn", "main:app", "--port", "8000"),
                        port=8000,
                        source="pyproject.toml",
                    )
                ]
        return []

    @staticmethod
    def _infer_port_from_package(script_name: str, script_cmd: str, deps: Mapping[str, Any]) -> int:
        match = _PORT_REGEX.search(script_cmd)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        lowered = script_cmd.lower()
        if "vite" in lowered or "vite" in deps:
            return 4173 if script_name == "preview" else 5173
        if "astro" in lowered or "astro" in deps:
            return 4321
        if "ng serve" in lowered or "@angular/core" in deps:
            return 4200
        return 3000

    def resolve_configuration(self, name: str | None = None) -> LaunchConfiguration:
        """Return the requested configuration by name, or the default (first) configuration."""
        configs = self.load_configurations()
        if not configs:
            raise PreviewServerError(
                "no launch configuration found in .orin/launch.json, .claude/launch.json, or package.json"
            )
        target = (name or "").strip()
        if not target:
            return configs[0]
        for config in configs:
            if config.name == target:
                return config
        for config in configs:
            if config.name.lower() == target.lower():
                return config
        available = ", ".join(cfg.name for cfg in configs)
        raise PreviewServerError(f"launch configuration '{target}' not found (available: {available})")

    def wait_for_port(
        self,
        port: int,
        *,
        host: str = "127.0.0.1",
        timeout_seconds: float = 10.0,
        proc: subprocess.Popen[bytes] | None = None,
    ) -> bool:
        """Poll ``host:port`` until it accepts connections or ``timeout_seconds`` elapses."""
        deadline = time.monotonic() + max(0.05, float(timeout_seconds))
        while time.monotonic() < deadline:
            if is_port_open(port, host=host):
                return True
            if proc is not None and proc.poll() is not None:
                return is_port_open(port, host=host)
            time.sleep(0.1)
        return is_port_open(port, host=host)

    def start_server(
        self,
        name: str | None = None,
        *,
        wait_timeout: float = 10.0,
        navigate_browser: bool = True,
        browser: Any | None = None,
    ) -> dict[str, Any]:
        """Start the configured preview server, wait for its port, and optionally open in AgentBrowserView."""
        config = self.resolve_configuration(name)
        key = (str(self.workspace_root), config.name)
        safe_name = _SAFE_NAME_RE.sub("-", config.name).strip("-") or "default"
        rel_log_path = f"{_PREVIEW_LOG_DIR}/preview-{safe_name}.log"
        abs_log_path = self.workspace_root / rel_log_path
        abs_log_path.parent.mkdir(parents=True, exist_ok=True)

        cwd = self.workspace_root
        if config.cwd:
            candidate_cwd = (self.workspace_root / config.cwd).resolve()
            try:
                candidate_cwd.relative_to(self.workspace_root)
                if candidate_cwd.is_dir():
                    cwd = candidate_cwd
            except ValueError:
                raise PreviewServerError("configuration cwd must stay inside the workspace")

        with _LIVE_PROCESSES_LOCK:
            existing_proc = _LIVE_PROCESSES.get(key)
            if existing_proc is not None and existing_proc.poll() is None:
                proc = existing_proc
            else:
                log_handle = abs_log_path.open("ab")
                env = {**os.environ, **{str(k): str(v) for k, v in config.env.items()}}
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
                try:
                    proc = subprocess.Popen(
                        config.command_list(),
                        cwd=cwd,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=(os.name != "nt"),
                        creationflags=creationflags,
                    )
                except OSError as error:
                    log_handle.close()
                    raise PreviewServerError(
                        f"failed to start preview server '{config.name}' ({config.command_string()}): {error}"
                    ) from error
                finally:
                    try:
                        log_handle.close()
                    except Exception:
                        pass
                _LIVE_PROCESSES[key] = proc

        ready = self.wait_for_port(config.port, timeout_seconds=wait_timeout, proc=proc)
        exit_code = proc.poll()
        status = "running" if exit_code is None else ("ready" if ready else "exited")
        url = config.target_url()

        record = {
            "name": config.name,
            "pid": proc.pid,
            "port": config.port,
            "url": url,
            "command": config.command_string(),
            "source": config.source,
            "log_path": rel_log_path,
            "status": status,
            "ready": ready,
            "exit_code": exit_code,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._save_server_record(config.name, record)

        browser_view = browser if browser is not None else self.browser
        browser_result: dict[str, Any] | None = None
        if navigate_browser and browser_view is not None and (ready or exit_code is None):
            try:
                nav = browser_view.navigate(url)
                if isinstance(nav, Mapping):
                    browser_result = {
                        "url": str(nav.get("url") or url),
                        "title": str(nav.get("title") or ""),
                        "text_preview": str(nav.get("text") or "")[:500],
                    }
            except Exception as error:
                browser_result = {"url": url, "error": str(error)}

        logs_snapshot = self.read_logs(config.name, tail_lines=30)
        return {
            **record,
            "logs_tail": logs_snapshot.get("logs", ""),
            "browser": browser_result,
        }

    def stop_server(self, name: str | None = None) -> dict[str, Any]:
        """Stop a running preview server by name (or the active/default one)."""
        manifest = self._load_manifest()
        target_name = (name or "").strip()
        if not target_name:
            if manifest:
                target_name = next(iter(manifest.keys()))
            else:
                try:
                    target_name = self.resolve_configuration(None).name
                except PreviewServerError:
                    target_name = "default"

        key = (str(self.workspace_root), target_name)
        record = dict(manifest.get(target_name) or {})
        pid = record.get("pid")

        with _LIVE_PROCESSES_LOCK:
            proc = _LIVE_PROCESSES.pop(key, None)

        stopped = False
        if proc is not None:
            pid = proc.pid
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                except OSError:
                    pass
            stopped = True
        elif isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped = True
            except OSError:
                stopped = False

        if target_name in manifest:
            record["status"] = "stopped"
            record["stopped_at"] = datetime.now(UTC).isoformat()
            self._save_server_record(target_name, record)

        return {
            "name": target_name,
            "stopped": stopped,
            "pid": pid,
            "port": record.get("port"),
            "status": "stopped",
        }

    def read_logs(self, name: str | None = None, *, tail_lines: int = 100) -> dict[str, Any]:
        """Return bounded tail lines from the preview server's log file."""
        manifest = self._load_manifest()
        target_name = (name or "").strip()
        if not target_name:
            if manifest:
                target_name = next(iter(manifest.keys()))
            else:
                try:
                    target_name = self.resolve_configuration(None).name
                except PreviewServerError:
                    target_name = "default"

        record = dict(manifest.get(target_name) or {})
        safe_name = _SAFE_NAME_RE.sub("-", target_name).strip("-") or "default"
        rel_log_path = str(record.get("log_path") or f"{_PREVIEW_LOG_DIR}/preview-{safe_name}.log")
        abs_log_path = self.workspace_root / rel_log_path

        bounded_lines = max(1, min(int(tail_lines), 500))
        if abs_log_path.is_file():
            try:
                raw_text = abs_log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw_text = ""
        else:
            raw_text = ""

        lines = raw_text.splitlines()
        selected = lines[-bounded_lines:] if lines else []
        joined = "\n".join(selected)[-8_000:]

        key = (str(self.workspace_root), target_name)
        with _LIVE_PROCESSES_LOCK:
            proc = _LIVE_PROCESSES.get(key)
        status = str(record.get("status") or "unknown")
        if proc is not None:
            status = "running" if proc.poll() is None else "exited"

        return {
            "name": target_name,
            "status": status,
            "pid": record.get("pid"),
            "port": record.get("port"),
            "url": record.get("url"),
            "log_path": rel_log_path,
            "logs": joined,
            "line_count": len(selected),
        }

    def _manifest_file(self) -> Path:
        return self.workspace_root / _PREVIEW_MANIFEST_PATH

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        path = self._manifest_file()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return {}
        if not isinstance(data, Mapping):
            return {}
        return {
            str(k): dict(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, Mapping)
        }

    def _save_server_record(self, name: str, record: Mapping[str, Any]) -> None:
        manifest = self._load_manifest()
        manifest[name] = dict(record)
        path = self._manifest_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except OSError:
            return


__all__ = [
    "LaunchConfiguration",
    "PreviewServerError",
    "PreviewServerManager",
    "is_port_open",
]
