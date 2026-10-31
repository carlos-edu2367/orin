from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_HostPath = type(Path())

import pytest

from agentos.live_voice import local_runtime as runtime_module
from agentos.live_voice.local_runtime import LocalEngineRuntime

WORKERS_DIR = str(runtime_module.WORKERS_DIR)
if WORKERS_DIR not in sys.path:
    sys.path.insert(0, WORKERS_DIR)

import _protocol  # noqa: E402
from agentos.live_voice.workers import (  # noqa: E402
    chatterbox_worker,
    kokoro_worker,
    whisper_worker,
)


def test_expose_windows_dll_directories_registers_and_retains_handles_and_updates_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "venv"
    scripts = prefix / "Scripts"
    lib_bin = prefix / "Library" / "bin"
    base_prefix = tmp_path / "base_python"
    base_dlls = base_prefix / "DLLs"
    site = prefix / "Lib" / "site-packages"

    cublas_bin = site / "nvidia" / "cublas" / "bin"
    cudnn_lib = site / "nvidia" / "cudnn" / "lib"
    torch_lib = site / "torch" / "lib"
    ctranslate2_dir = site / "ctranslate2"
    onnx_capi = site / "onnxruntime" / "capi"
    espeak_dir = site / "espeakng_loader"

    for d in (
        scripts,
        lib_bin,
        base_dlls,
        cublas_bin,
        cudnn_lib,
        torch_lib,
        ctranslate2_dir,
        onnx_capi,
        espeak_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(base_prefix))
    # Include duplicate and malformed/empty entries to test adversarial resilience
    monkeypatch.setattr(sys, "path", ["", str(site), str(site), "bad:*?invalid_windows_path"])
    monkeypatch.setenv("PATH", r"C:\Windows\System32")

    added_dirs: list[str] = []
    created_handles: list[object] = []

    def fake_add_dll_directory(path: str) -> object:
        if path.endswith("Library\\bin") or path.endswith("Library/bin"):
            raise OSError("simulated OSError on Library/bin")
        added_dirs.append(path)
        handle = {"dir": path}
        created_handles.append(handle)
        return handle

    monkeypatch.setattr(os, "add_dll_directory", fake_add_dll_directory, raising=False)
    monkeypatch.setattr(_protocol, "_WINDOWS_DLL_DIR_HANDLES", [])

    exposed = _protocol.expose_windows_dll_directories()

    expected_dirs = [
        str(prefix),
        str(scripts),
        str(lib_bin),
        str(base_prefix),
        str(base_dlls),
        str(cublas_bin),
        str(cudnn_lib),
        str(torch_lib),
        str(ctranslate2_dir),
        str(onnx_capi),
        str(espeak_dir),
    ]
    for exp in expected_dirs:
        assert exp in exposed
    # Deduplicated
    assert len(exposed) == len(set(exposed))

    # Every non-failing handle is kept alive in _WINDOWS_DLL_DIR_HANDLES
    assert len(_protocol._WINDOWS_DLL_DIR_HANDLES) == len(created_handles)
    for h in created_handles:
        assert h in _protocol._WINDOWS_DLL_DIR_HANDLES

    # Every exposed directory is prepended to PATH
    path_parts = os.environ["PATH"].split(os.pathsep)
    for exp in expected_dirs:
        assert exp in path_parts

    # Calling a second time does not duplicate PATH entries
    path_len_before = len(os.environ["PATH"].split(os.pathsep))
    _protocol.expose_windows_dll_directories()
    path_len_after = len(os.environ["PATH"].split(os.pathsep))
    assert path_len_after == path_len_before


def test_expose_windows_dll_directories_noop_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    assert _protocol.expose_windows_dll_directories() == []


def test_protocol_serve_and_workers_invoke_expose_windows_dll_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(_protocol, "expose_windows_dll_directories", lambda: calls.append("protocol") or [])
    monkeypatch.setattr(whisper_worker, "expose_windows_dll_directories", lambda: calls.append("whisper") or [])
    monkeypatch.setattr(chatterbox_worker, "expose_windows_dll_directories", lambda: calls.append("chatterbox") or [])
    monkeypatch.setattr(kokoro_worker, "expose_windows_dll_directories", lambda: calls.append("kokoro") or [])

    # 1. _protocol.serve() calls expose_windows_dll_directories()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    _protocol.serve({})
    assert "protocol" in calls

    # 2. whisper_worker._expose_cuda_libraries() calls expose_windows_dll_directories()
    whisper_worker._expose_cuda_libraries()
    assert "whisper" in calls

    # 3. chatterbox_worker._seed_deterministic() calls expose_windows_dll_directories()
    chatterbox_worker._seed_deterministic(42)
    assert "chatterbox" in calls

    # 4. kokoro_worker._kokoro() calls expose_windows_dll_directories() when initializing
    monkeypatch.setitem(sys.modules, "espeakng_loader", type("E", (), {
        "get_data_path": staticmethod(lambda: "C:/short"),
        "get_library_path": staticmethod(lambda: "C:/short/espeak.dll"),
    })())
    monkeypatch.setitem(sys.modules, "kokoro_onnx", type("K", (), {
        "Kokoro": staticmethod(lambda *_a, **_kw: "fake_engine"),
    })())
    monkeypatch.setitem(sys.modules, "kokoro_onnx.config", type("KC", (), {
        "EspeakConfig": staticmethod(lambda **kw: kw),
    })())
    monkeypatch.setattr(kokoro_worker, "_engine", None)
    kokoro_worker._kokoro()
    assert "kokoro" in calls


def test_local_runtime_install_includes_msvc_runtime_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    runtime = LocalEngineRuntime(root=tmp_path, runner=runner, gpu_probe=lambda: True)
    runtime.ensure_uv = lambda: _HostPath("C:/uv/uv.exe")  # type: ignore[method-assign]
    runtime.python_path = lambda _e: _HostPath(sys.executable)  # type: ignore[method-assign]

    class FakeWorker:
        def request(self, _payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
            return {"ok": True, "device": "cuda"}

        def stop(self) -> None:
            pass

    runtime.worker = lambda _e: FakeWorker()  # type: ignore[method-assign]
    runtime._jobs["whisper"] = runtime_module.InstallJob(state="running")
    runtime._install("whisper")

    pip_install = next(argv for argv in calls if argv[1:3] == ["pip", "install"])
    assert "msvc-runtime" in pip_install
    assert runtime.is_installed("whisper") is True
    assert "msvc-runtime" in runtime.installed_info("whisper")["packages"]


def test_ensure_windows_msvc_runtime_heals_venv_and_copies_dlls_to_venv_and_base_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    base_py = tmp_path / "python" / "cpython-3.12.16-windows-x86_64-none"
    base_py.mkdir(parents=True)
    venv = tmp_path / "whisper" / "venv"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"exe")
    (venv / "pyvenv.cfg").write_text(f"home = {base_py}\nimplementation = CPython\n", encoding="utf-8")

    uv_bin = tmp_path / "tools" / "uv.exe"
    uv_bin.parent.mkdir(parents=True)
    uv_bin.write_bytes(b"uv")

    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        # Simulate msvc-runtime wheel installing DLLs into venv/Scripts
        for dll in (
            "msvcp140.dll",
            "msvcp140_1.dll",
            "vcomp140.dll",
            "concrt140.dll",
            "vcruntime140.dll",
            "vcruntime140_1.dll",
        ):
            (scripts / dll).write_bytes(b"dll-bytes")
        return subprocess.CompletedProcess(argv, 0, "installed msvc-runtime", "")

    runtime = LocalEngineRuntime(root=tmp_path, runner=runner)
    runtime._ensure_windows_msvc_runtime("whisper", venv)

    assert len(calls) == 1
    assert calls[0][:3] == [str(uv_bin), "pip", "install"]
    assert "msvc-runtime" in calls[0]

    for dll in ("msvcp140.dll", "msvcp140_1.dll", "vcomp140.dll", "concrt140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        assert (venv / dll).is_file(), f"Missing {dll} in venv root"
        assert (base_py / dll).is_file(), f"Missing {dll} in base_prefix"

    # Second call should NOT re-run uv pip install since msvcp140.dll is now present
    runtime._ensure_windows_msvc_runtime("whisper", venv)
    assert len(calls) == 1

    # Fake venv without pyvenv.cfg must NOT trigger uv pip install
    fake_venv = tmp_path / "fake" / "venv"
    (fake_venv / "Scripts").mkdir(parents=True)
    runtime._ensure_windows_msvc_runtime("whisper", fake_venv)
    assert len(calls) == 1


def test_ensure_windows_msvc_runtime_survives_locked_dll_or_runner_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    venv = tmp_path / "chatterbox" / "venv"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"exe")
    (venv / "pyvenv.cfg").write_text("home = Z:\\nonexistent\\dir\nmalformed_line\n", encoding="utf-8")

    uv_bin = tmp_path / "tools" / "uv.exe"
    uv_bin.parent.mkdir(parents=True)
    uv_bin.write_bytes(b"uv")

    def failing_runner(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("network offline")

    runtime = LocalEngineRuntime(root=tmp_path, runner=failing_runner)
    # Must not raise even if runner fails
    runtime._ensure_windows_msvc_runtime("chatterbox", venv)

    # Now place a DLL in Scripts and simulate PermissionError on copy2 (locked DLL)
    (scripts / "msvcp140.dll").write_bytes(b"dll")
    monkeypatch.setattr(shutil, "copy2", lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("DLL locked")))
    runtime._ensure_windows_msvc_runtime("chatterbox", venv)


def test_local_runtime_worker_prepends_venv_scripts_and_root_to_path_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    runtime = LocalEngineRuntime(root=tmp_path)
    worker = runtime.worker("whisper")
    venv = runtime.engine_dir("whisper") / "venv"
    assert worker._env is not None
    path_entries = worker._env["PATH"].split(os.pathsep)
    assert path_entries[0] == str(venv / "Scripts")
    assert path_entries[1] == str(venv)
