"""A real, isolated Orin runtime for `orin code` tests: the launcher, its API and its worker, on a fake Ollama.

Everything the runtime and the CLI write lives under one temporary folder: ORIN_HOME (config, data, logs, cache,
run), the settings layers (ORIN_USER_DIR and friends), ~/.claude.json, and HOME/USERPROFILE. Nothing touches the
developer's own Orin or Claude Code files.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from agentos.installation.api_token import read_api_token
from agentos.installation.paths import OrinPaths
from agentos.launcher.state import running_instance
from tests.fixtures.fake_ollama import MODEL, FakeOllama

START_TIMEOUT = 180.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LiveRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "orin-home"
        self.paths = OrinPaths(self.home / "config", self.home / "data", self.home / "logs", self.home / "cache", self.home / "run")
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.fake = FakeOllama()
        self.env = self._environment()
        self._launcher: subprocess.Popen[bytes] | None = None

    def _environment(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith(("ORIN_", "AGENTOS_"))}
        settings = self.root / "settings"
        for name in ("ORIN_USER_DIR", "ORIN_CLAUDE_HOME", "ORIN_MANAGED_DIR", "ORIN_CLAUDE_MANAGED_DIR"):
            (settings / name).mkdir(parents=True, exist_ok=True)
            env[name] = str(settings / name)
        env["ORIN_CLAUDE_CONFIG"] = str(settings / "claude.json")
        env["ORIN_HOME"] = str(self.home)
        env.update({
            "ORIN_CONFIG_DIR": str(self.paths.config), "ORIN_DATA_DIR": str(self.paths.data),
            "ORIN_LOGS_DIR": str(self.paths.logs), "ORIN_CACHE_DIR": str(self.paths.cache), "ORIN_RUN_DIR": str(self.paths.run),
        })
        user_home = self.root / "home"
        user_home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = env["USERPROFILE"] = str(user_home)
        env["PYTHONIOENCODING"] = "utf-8"
        # A stub web build: the launcher checks that an interface is served, the tests never open it (and CI's
        # backend job has no frontend build).
        web = self.root / "web"
        (web / "assets").mkdir(parents=True, exist_ok=True)
        (web / "index.html").write_text('<!doctype html><html><body><div id="root"></div></body></html>', encoding="utf-8")
        env["ORIN_WEB_DIST"] = str(web)
        return env

    def start(self) -> "LiveRuntime":
        self.fake.start()
        self.paths.ensure()
        log = open(self.root / "launcher.out", "wb")  # noqa: SIM115 - closed with the process
        self._launcher = subprocess.Popen(
            [sys.executable, "-m", "agentos.launcher", "start", "--no-browser", "--port", str(self.port)],
            env=self.env, cwd=str(self.root), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._launcher.poll() is not None:
                raise RuntimeError(f"the launcher exited: {(self.root / 'launcher.out').read_text(errors='replace')[-2000:]}")
            # What `orin code` itself waits for: the launcher's record, the token and a healthy API.
            if running_instance(self.paths) is not None and read_api_token(self.paths.run) and self._healthy():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"Orin did not start: {(self.root / 'launcher.out').read_text(errors='replace')[-2000:]}")
        self._configure_provider()
        return self

    def _healthy(self) -> bool:
        try:
            return httpx.get(f"{self.url}/healthz", timeout=2.0, trust_env=False).status_code == 200
        except httpx.HTTPError:
            return False

    def api(self) -> httpx.Client:
        token = read_api_token(self.paths.run) or ""
        return httpx.Client(base_url=self.url, timeout=30.0, trust_env=False, headers={"Authorization": f"Bearer {token}"})

    def _configure_provider(self) -> None:
        with self.api() as client:
            client.put("/v1/providers/ollama", json={"enabled": True, "base_url": self.fake.base_url, "purpose": "provider.configure"},
                       headers={"Idempotency-Key": "fixture-provider"}).raise_for_status()
            client.post("/v1/providers/ollama/models:refresh", headers={"Idempotency-Key": "fixture-refresh"}).raise_for_status()

    @property
    def model(self) -> str:
        return MODEL

    def workspace(self, name: str) -> Path:
        folder = self.root / "workspaces" / name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def stop(self) -> None:
        try:
            subprocess.run([sys.executable, "-m", "agentos.launcher", "stop"], env=self.env, cwd=str(self.root),
                           capture_output=True, timeout=90, check=False)
        finally:
            if self._launcher is not None:
                try:
                    self._launcher.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    self._launcher.kill()
            self.fake.stop()


__all__ = ["LiveRuntime"]
