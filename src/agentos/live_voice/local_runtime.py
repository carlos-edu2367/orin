"""Isolated local voice engines: install on demand, run as warm worker processes.

Each engine (Whisper, Kokoro, Chatterbox) lives in its own virtual environment
under ``~/.orin/voice-engines/<engine>``, created by ``uv``. Orin's own
interpreter is never modified: Chatterbox pins torch/transformers/gradio
versions that would otherwise replace the backend's dependencies, and a frozen
release (``orin.exe``) cannot install packages into itself at all. ``uv`` is
used from PATH, or downloaded once as a standalone binary.

A worker is a small script (``agentos/live_voice/workers``) speaking one JSON
object per line over stdin/stdout. It is started on first use and kept warm so
models stay loaded in VRAM between utterances.
"""
from __future__ import annotations

import base64
import io
import itertools
import json
import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from agentos.settings.paths import orin_user_dir

_LOGGER = logging.getLogger("agentos.live_voice.local_runtime")

UV_VERSION = "0.13.6"
PYTORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu124"
KOKORO_MODEL_FILES = (
    ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx", "kokoro-v1.0.onnx"),
    ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin", "voices-v1.0.bin"),
)
WORKERS_DIR = Path(__file__).resolve().parent / "workers"
VOICE_CLIP_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")
MAX_VOICE_CLIP_BYTES = 12 * 1024 * 1024
INSTALL_TIMEOUT_SECONDS = 3600
REQUEST_TIMEOUT_SECONDS = {"load": 1800.0, "transcribe": 120.0, "synthesize": 180.0}

# Voices shipped in Kokoro v1.0's voices-v1.0.bin; Brazilian Portuguese first.
KOKORO_VOICES: tuple[str, ...] = (
    "pf_dora", "pm_alex", "pm_santa",
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
    "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis", "ef_dora", "em_alex", "em_santa", "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola", "jf_alpha", "jf_gongitsune",
    "jf_nezumi", "jf_tebukuro", "jm_kumo", "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
)


class LocalEngineError(RuntimeError):
    """A local engine is missing or failed; the message is safe to show the person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class EngineSpec:
    id: str
    label: str
    worker: str
    packages: tuple[str, ...]
    cuda_packages: tuple[str, ...] = ()
    torch_cuda_index: bool = False
    downloads: tuple[tuple[str, str], ...] = ()
    size_hint: str = ""
    python: str = "3.12"


ENGINE_SPECS: dict[str, EngineSpec] = {
    "whisper": EngineSpec(
        id="whisper",
        label="Whisper local",
        worker="whisper_worker.py",
        packages=("faster-whisper==1.2.1",),
        # CTranslate2 loads cuBLAS/cuDNN at runtime; these wheels carry them.
        cuda_packages=("nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"),
        size_hint="~1,6 GB com o modelo",
    ),
    "kokoro": EngineSpec(
        id="kokoro",
        label="Kokoro",
        worker="kokoro_worker.py",
        packages=("kokoro-onnx==0.6.1",),
        downloads=KOKORO_MODEL_FILES,
        size_hint="~400 MB",
    ),
    "chatterbox": EngineSpec(
        id="chatterbox",
        label="Chatterbox Multilingual",
        worker="chatterbox_worker.py",
        packages=("chatterbox-tts==0.1.7", "setuptools<81"),
        torch_cuda_index=True,
        size_hint="~6 GB com PyTorch e o modelo",
    ),
}


def nvidia_gpu_present() -> bool:
    if platform.system() == "Darwin":
        return False
    return shutil.which("nvidia-smi") is not None


def _uv_asset() -> tuple[str, str]:
    machine = platform.machine().lower()
    arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    system = platform.system()
    if system == "Windows":
        return f"uv-{arch}-pc-windows-msvc.zip", "uv.exe"
    if system == "Darwin":
        return f"uv-{arch}-apple-darwin.tar.gz", "uv"
    return f"uv-{arch}-unknown-linux-gnu.tar.gz", "uv"


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


@dataclass(slots=True)
class InstallJob:
    state: str = "idle"  # idle | running | installed | failed
    step: str = ""
    message: str = ""
    started_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "step": self.step, "message": self.message}


class WorkerProcess:
    """One warm worker, spoken to by one caller at a time."""

    def __init__(self, argv: list[str], *, log_path: Path, env: dict[str, str] | None = None) -> None:
        self._argv = argv
        self._log_path = log_path
        self._env = env
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def _start(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self._log_path, "a", encoding="utf-8")  # noqa: SIM115 - owned by the child for its lifetime
        env = {**os.environ, **(self._env or {}), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
            creationflags=_no_window(),
        )
        log.close()
        self._lines = queue.Queue()
        stdout = self._proc.stdout
        assert stdout is not None

        def pump() -> None:
            for line in stdout:
                self._lines.put(line)
            self._lines.put(None)

        threading.Thread(target=pump, name="orin-voice-worker-stdout", daemon=True).start()
        ready = self._next_line(60.0)
        if not ready or '"ready"' not in ready:
            self.stop()
            raise LocalEngineError("local_engine_start_failed", "O motor de voz local não iniciou. Veja o log do motor.")

    def _next_line(self, timeout: float) -> str | None:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self._lock:
            for attempt in range(2):
                if not self.alive():
                    self._start()
                assert self._proc is not None and self._proc.stdin is not None
                request_id = next(self._ids)
                try:
                    self._proc.stdin.write(json.dumps({**payload, "id": request_id}) + "\n")
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    self.stop()
                    if attempt == 0:
                        continue
                    raise LocalEngineError("local_engine_crashed", "O motor de voz local parou inesperadamente.")
                deadline = time.monotonic() + timeout
                while True:
                    line = self._next_line(max(0.1, deadline - time.monotonic()))
                    if line is None:
                        timed_out = (time.monotonic() >= deadline) and self.alive()
                        self.stop()
                        if timed_out:
                            raise LocalEngineError("local_engine_timeout", "O motor de voz local demorou demais para responder.")
                        break  # crashed mid-request: retry once on a fresh process
                    try:
                        response = json.loads(line)
                    except ValueError:
                        continue
                    if response.get("id") != request_id:
                        continue
                    if not response.get("ok"):
                        raise LocalEngineError("local_engine_failed", str(response.get("error") or "falha no motor local"))
                    return response
            raise LocalEngineError("local_engine_crashed", "O motor de voz local parou inesperadamente. Veja o log do motor.")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


@dataclass
class LocalEngineRuntime:
    root: Path = field(default_factory=lambda: orin_user_dir() / "voice-engines")
    downloader: Callable[[str, Path], None] | None = None
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    gpu_probe: Callable[[], bool] = nvidia_gpu_present
    _jobs: dict[str, InstallJob] = field(default_factory=dict)
    _workers: dict[str, WorkerProcess] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── layout ──
    def engine_dir(self, engine: str) -> Path:
        return self.root / engine

    def python_path(self, engine: str) -> Path:
        venv = self.engine_dir(engine) / "venv"
        return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"

    def _marker(self, engine: str) -> Path:
        return self.engine_dir(engine) / "installed.json"

    def is_installed(self, engine: str) -> bool:
        return self._marker(engine).is_file() and self.python_path(engine).exists()

    def installed_info(self, engine: str) -> dict[str, Any]:
        try:
            return json.loads(self._marker(engine).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def status(self, engine: str) -> dict[str, Any]:
        spec = ENGINE_SPECS[engine]
        job = self._jobs.get(engine) or InstallJob()
        installed = self.is_installed(engine)
        state = "running" if job.state == "running" else ("installed" if installed else job.state)
        info = self.installed_info(engine) if installed else {}
        return {
            "engine": engine,
            "label": spec.label,
            "installed": installed,
            "state": state,
            "step": job.step if state == "running" else "",
            "message": job.message,
            "device": info.get("device"),
            "size_hint": spec.size_hint,
            "gpu": self.gpu_probe(),
        }

    # ── install ──
    def start_install(self, engine: str) -> dict[str, Any]:
        if engine not in ENGINE_SPECS:
            raise KeyError(engine)
        with self._lock:
            job = self._jobs.setdefault(engine, InstallJob())
            if job.state == "running":
                return self.status(engine)
            job.state, job.step, job.message, job.started_at = "running", "Preparando", "Preparando a instalação…", time.time()
        threading.Thread(target=self._install, args=(engine,), name=f"orin-install-{engine}", daemon=True).start()
        return self.status(engine)

    def _step(self, engine: str, step: str, message: str) -> None:
        job = self._jobs[engine]
        job.step, job.message = step, message

    def _run(self, argv: list[str], *, log: Path, timeout: float = INSTALL_TIMEOUT_SECONDS, env: dict[str, str] | None = None) -> None:
        completed = self.runner(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, **(env or {})},
            creationflags=_no_window(),
        )
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(f"$ {' '.join(argv)}\n{completed.stdout or ''}{completed.stderr or ''}\n")
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:] or ["sem detalhes"]
            raise LocalEngineError("install_failed", f"Falhou: {tail[0][:240]}")

    def _install(self, engine: str) -> None:
        spec = ENGINE_SPECS[engine]
        engine_dir = self.engine_dir(engine)
        log = engine_dir / "install.log"
        try:
            engine_dir.mkdir(parents=True, exist_ok=True)
            self._marker(engine).unlink(missing_ok=True)
            self._step(engine, "uv", "Preparando o gerenciador de pacotes (uv)…")
            uv = self.ensure_uv()
            env = {"UV_CACHE_DIR": str(self.root / "uv-cache"), "UV_PYTHON_INSTALL_DIR": str(self.root / "python")}
            python = self.python_path(engine)
            if not python.exists():
                self._step(engine, "python", f"Criando um Python {spec.python} isolado…")
                self._run([str(uv), "venv", "--python", spec.python, str(engine_dir / "venv")], log=log, env=env)
            gpu = self.gpu_probe()
            packages = list(spec.packages) + (list(spec.cuda_packages) if gpu else [])
            argv = [str(uv), "pip", "install", "--python", str(python), *packages]
            if spec.torch_cuda_index and gpu:
                argv += ["--extra-index-url", PYTORCH_CUDA_INDEX, "--index-strategy", "unsafe-best-match"]
            self._step(engine, "packages", f"Instalando {spec.label} ({spec.size_hint}) — pode levar alguns minutos…")
            self._run(argv, log=log, env=env)
            for url, name in spec.downloads:
                target = engine_dir / name
                if not target.is_file():
                    self._step(engine, "download", f"Baixando {name}…")
                    self._download(url, target)
            self._step(engine, "warmup", "Baixando o modelo e testando o motor (primeira vez demora)…")
            self.stop_worker(engine)
            response = self.worker(engine).request({"op": "load", "model": ""}, timeout=REQUEST_TIMEOUT_SECONDS["load"])
            info = {"engine": engine, "packages": packages, "device": response.get("device"), "installed_at": time.time()}
            self._marker(engine).write_text(json.dumps(info), encoding="utf-8")
            job = self._jobs[engine]
            job.state, job.step = "installed", ""
            job.message = f"{spec.label} pronto ({'GPU' if response.get('device') in {'cuda', 'mps'} else 'CPU'})."
        except Exception as exc:  # noqa: BLE001 - the job reports every failure
            _LOGGER.warning("voice engine %s install failed", engine, exc_info=True)
            job = self._jobs[engine]
            job.state, job.step = "failed", ""
            job.message = exc.message if isinstance(exc, LocalEngineError) else f"A instalação falhou: {exc.__class__.__name__}. Veja {log}."
            self.stop_worker(engine)

    def ensure_uv(self) -> Path:
        found = shutil.which("uv")
        if found:
            return Path(found)
        asset, binary = _uv_asset()
        target = self.root / "tools" / binary
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        archive = target.parent / asset
        self._download(f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{asset}", archive)
        data = archive.read_bytes()
        if asset.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                member = next(name for name in bundle.namelist() if name.rsplit("/", 1)[-1] == binary)
                target.write_bytes(bundle.read(member))
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as bundle:
                member = next(item for item in bundle.getmembers() if item.name.rsplit("/", 1)[-1] == binary)
                extracted = bundle.extractfile(member)
                assert extracted is not None
                target.write_bytes(extracted.read())
        target.chmod(0o755)
        archive.unlink(missing_ok=True)
        return target

    def _download(self, url: str, target: Path) -> None:
        if self.downloader is not None:
            self.downloader(url, target)
            return
        partial = target.with_name(target.name + ".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0)) as response:
            response.raise_for_status()
            with open(partial, "wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)
        os.replace(partial, target)

    # ── workers ──
    def worker(self, engine: str) -> WorkerProcess:
        with self._lock:
            existing = self._workers.get(engine)
            if existing is not None:
                return existing
            argv = [str(self.python_path(engine)), str(WORKERS_DIR / ENGINE_SPECS[engine].worker)]
            if engine == "kokoro":
                argv.append(str(self.engine_dir(engine)))
            env = {"HF_HUB_DISABLE_TELEMETRY": "1", "HF_HOME": str(self.root / "hf-cache")}
            created = WorkerProcess(argv, log_path=self.engine_dir(engine) / "worker.log", env=env)
            self._workers[engine] = created
            return created

    def stop_worker(self, engine: str) -> None:
        with self._lock:
            worker = self._workers.pop(engine, None)
        if worker is not None:
            worker.stop()

    def stop_all(self) -> None:
        for engine in list(self._workers):
            self.stop_worker(engine)

    def call(self, engine: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_installed(engine):
            raise LocalEngineError("local_engine_not_installed", f"{ENGINE_SPECS[engine].label} ainda não está instalado.")
        op = str(payload.get("op") or "")
        return self.worker(engine).request(payload, timeout=REQUEST_TIMEOUT_SECONDS.get(op, 120.0))

    # ── chatterbox voice clips ──
    def voice_clips_dir(self) -> Path:
        return self.engine_dir("chatterbox") / "voices"

    def list_voice_clips(self) -> list[dict[str, str]]:
        directory = self.voice_clips_dir()
        if not directory.is_dir():
            return []
        return [
            {"id": path.name, "name": path.stem}
            for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix.lower() in VOICE_CLIP_EXTENSIONS
        ]

    def voice_clip_path(self, clip_id: str) -> Path | None:
        if not clip_id or "/" in clip_id or "\\" in clip_id or clip_id.startswith("."):
            return None
        path = self.voice_clips_dir() / clip_id
        return path if path.is_file() else None

    def save_voice_clip(self, name: str, filename: str, data_base64: str) -> dict[str, str]:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in VOICE_CLIP_EXTENSIONS:
            raise ValueError("use um arquivo .wav, .mp3, .flac, .ogg ou .m4a")
        try:
            data = base64.b64decode(data_base64, validate=True)
        except ValueError as exc:
            raise ValueError("arquivo de áudio inválido") from exc
        if not data or len(data) > MAX_VOICE_CLIP_BYTES:
            raise ValueError("o clipe precisa ter até 12 MB")
        stem = re.sub(r"[^\w\- ]+", "", str(name or Path(filename).stem), flags=re.UNICODE).strip()[:40] or "voz"
        directory = self.voice_clips_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{stem}{suffix}"
        counter = 2
        while target.exists():
            target = directory / f"{stem} {counter}{suffix}"
            counter += 1
        target.write_bytes(data)
        return {"id": target.name, "name": target.stem}

    def delete_voice_clip(self, clip_id: str) -> bool:
        path = self.voice_clip_path(clip_id)
        if path is None:
            return False
        path.unlink()
        return True


_RUNTIME: LocalEngineRuntime | None = None


def local_runtime() -> LocalEngineRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = LocalEngineRuntime()
    return _RUNTIME


__all__ = [
    "ENGINE_SPECS",
    "EngineSpec",
    "KOKORO_VOICES",
    "LocalEngineError",
    "LocalEngineRuntime",
    "WorkerProcess",
    "local_runtime",
    "nvidia_gpu_present",
]
