"""The one engine that installs a release: ``orin update``, the app's update
button and (later) the graphical installer all run this.

A packaged install is versioned side by side (``<root>/<version>`` plus a
``current`` pointer), and user data never lives inside either, so an update is
"stage the new version next to the old one, prove it starts, flip the pointer".
Every step before the flip leaves the running installation untouched, and a flip
whose result fails its smoke test is flipped straight back. The user is never
left with a half-installed Orin.

Progress is reported as plain ``UpdateEvent`` values to whatever front end is
listening (a terminal, a JSON stream, a window); this module prints nothing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")
STATE_FILE = "update-state.json"
PREPARED_FILE = "prepared.json"
READY_SUFFIX = ".ready"
CHUNK = 256 * 1024
MANIFEST_ATTEMPTS = 3
DOWNLOAD_ATTEMPTS = 2
NETWORK_TIMEOUT = 20.0
SMOKE_TIMEOUT = 30.0

#: The visible stages, in order. ``index`` in an event is a position in this list.
STEPS: tuple[tuple[str, str], ...] = (
    ("check", "Procurando a versão mais recente"),
    ("download", "Baixando"),
    ("verify", "Verificando a integridade"),
    ("extract", "Extraindo os arquivos"),
    ("validate", "Testando a nova versão"),
    ("activate", "Ativando"),
)


def version_key(value: str) -> tuple[int, int, int, int, str]:
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("invalid semantic version")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4) or ""
    return major, minor, patch, 1 if not prerelease else 0, prerelease


@dataclass(frozen=True, slots=True)
class UpdateEvent:
    """One thing a front end may want to show.

    ``phase`` is ``start`` | ``progress`` | ``done`` | ``info`` | ``rolled_back``.
    Byte counts are set while downloading; ``progress`` is 0..1 when known.
    """

    phase: str
    step: str
    index: int
    total: int
    label: str
    progress: float | None = None
    bytes_done: int | None = None
    bytes_total: int | None = None
    speed: float | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class UpdateError(Exception):
    """A failure with a message fit to show to the person, and what to do next."""

    def __init__(self, message: str, *, step: str = "", hint: str | None = None, rolled_back: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.step = step
        self.hint = hint
        self.rolled_back = rolled_back


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    archive_url: str
    archive_sha256: str
    release_url: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateResult:
    status: str  # "updated" | "up_to_date" | "available" | "rolled_back"
    previous_version: str
    version: str
    release_url: str | None = None
    notes: str | None = None
    downloaded_bytes: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


Emit = Callable[[UpdateEvent], None]
Opener = Callable[..., Any]


def default_repository() -> str:
    return os.environ.get("ORIN_RELEASE_REPOSITORY", "carlos-edu2367/orin")


def default_base_url() -> str:
    # Overridable purely so tests and local release rehearsals can point the
    # whole flow (manifest and origin pin together) at another server.
    return os.environ.get("ORIN_RELEASE_BASE_URL", f"https://github.com/{default_repository()}/releases")


def current_platform() -> str:
    return "windows-x64" if os.name == "nt" else "linux-x64"


def runtime_relative_path(system: str | None = None) -> Path:
    system = system or current_platform()
    return Path("resources") / "runtime" / ("orin.exe" if system.startswith("windows") else "orin")


class Updater:
    def __init__(
        self,
        *,
        versions_root: Path,
        current_version: str,
        emit: Emit | None = None,
        base_url: str | None = None,
        platform: str | None = None,
        opener: Opener | None = None,
        before_activate: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = versions_root
        self.current_version = current_version
        self._emit = emit or (lambda event: None)
        self._base_url = (base_url or default_base_url()).rstrip("/")
        self._platform = platform or current_platform()
        self._open = opener or urlopen
        self._before_activate = before_activate
        self._sleep = sleep
        self._clock = clock
        self._started = clock()

    # -- public ---------------------------------------------------------

    def check(self, requested: str = "latest") -> Release:
        """Fetch and validate the manifest. Changes nothing on disk."""
        self._step("check", "start")
        release = self._fetch_release(requested)
        self._step("check", "done", detail=f"versão {release.version}")
        return release

    def run(self, requested: str = "latest", *, force: bool = False, check_only: bool = False) -> UpdateResult:
        """Prepare and immediately activate (``orin update`` in a terminal)."""
        if check_only:
            self._started = self._clock()
            release = self.check(requested)
            newer = version_key(release.version) > version_key(self.current_version)
            return self._result("available" if newer else "up_to_date", release)
        prepared = self.prepare(requested, force=force)
        if prepared.status != "ready":
            return prepared
        applied = self.apply_prepared()
        return UpdateResult(
            applied.status, applied.previous_version, applied.version, applied.release_url, applied.notes,
            prepared.downloaded_bytes, self._clock() - self._started,
        )

    def prepare(self, requested: str = "latest", *, force: bool = False) -> UpdateResult:
        """Download, verify, extract and test a release, then leave it ready to activate.

        Nothing the running installation uses is touched, so this can run in the
        background of a live Orin. The result is ``<version>.ready`` plus
        ``prepared.json``; ``apply_prepared`` turns it into the active version.
        """
        self._started = self._clock()
        release = self.check(requested)
        if release.version == self.current_version and not force:
            return self._result("up_to_date", release)
        if release.version == self.active_version():
            if not force:
                return self._result("up_to_date", release)
            raise UpdateError(
                f"A versão {release.version} é a que está ativa agora e não pode ser reinstalada por cima de si mesma.",
                step="check", hint="Instale outra versão ou aguarde a próxima release.",
            )

        self.root.mkdir(parents=True, exist_ok=True)
        ready = self.root / f"{release.version}{READY_SUFFIX}"
        if self._ready_matches(release):
            # Downloaded and verified earlier (e.g. by the app, minutes ago).
            for name, _ in STEPS[1:5]:
                self._step(name, "done", detail="já preparada")
            return self._result("ready", release)
        staging = self.root / f"{release.version}.staging"
        archive = self.root / f".download-{release.version}-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(ready, ignore_errors=True)
        self._clear_prepared()
        try:
            size = self._download(release, archive)
            self._extract(archive, staging)
            self._validate(release, staging)
            os.replace(staging, ready)
            self._write_prepared(release)
        finally:
            archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
        return self._result("ready", release, downloaded_bytes=size)

    def prepared_release(self) -> Release | None:
        """The release waiting to be activated, if one is on disk and still intact."""
        try:
            data = json.loads((self.root / PREPARED_FILE).read_text(encoding="utf-8"))
            release = Release(str(data["version"]), "", str(data["sha256"]), data.get("release_url"), data.get("notes"))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if SEMVER.fullmatch(release.version) is None or not (self.root / f"{release.version}{READY_SUFFIX}").is_dir():
            return None
        if version_key(release.version) <= version_key(self.active_version() or self.current_version):
            return None
        return release

    def apply_prepared(self) -> UpdateResult:
        """Activate the prepared release (stops the running Orin first, via ``before_activate``)."""
        self._started = self._clock()
        release = self.prepared_release()
        if release is None:
            raise UpdateError("Não há uma atualização preparada para instalar.", step="activate", hint="Rode `orin update` para baixar a versão mais recente.")
        ready = self.root / f"{release.version}{READY_SUFFIX}"
        try:
            self._run_version_check(ready / runtime_relative_path(self._platform), release.version, step="validate")
            self._activate(release, ready, self.root / release.version)
        finally:
            # ``_activate`` consumes the ready directory (a rename). If it is still
            # there, activation never started (e.g. the running Orin could not be
            # stopped) and the downloaded update stays available for a retry.
            if not ready.exists():
                self._clear_prepared()
        return self._result("updated", release)

    def last_attempt(self) -> dict[str, Any] | None:
        attempt = self._read_state().get("last_attempt")
        return attempt if isinstance(attempt, dict) else None

    def active_version(self) -> str | None:
        link = self.root / "current"
        if not os.path.lexists(link):
            return None
        name = Path(os.path.realpath(link)).name
        return name if SEMVER.fullmatch(name) else None

    def previous_version(self) -> str | None:
        state = self._read_state()
        previous = state.get("previous")
        if isinstance(previous, str) and SEMVER.fullmatch(previous) and (self.root / previous).is_dir():
            return previous
        return None

    def rollback(self) -> UpdateResult:
        """Point ``current`` back at the version the last update replaced."""
        active = self.active_version()
        previous = self.previous_version()
        if previous is None or previous == active:
            raise UpdateError("Não há uma versão anterior guardada para voltar.", step="activate")
        self._step("activate", "start", label=f"Voltando para a versão {previous}")
        target = self.root / previous
        self._smoke(target)
        point_current(self.root, target)
        self._write_state(previous=active, current=previous)
        self._step("activate", "done", label=f"Voltou para a versão {previous}")
        return UpdateResult("rolled_back", active or self.current_version, previous, seconds=self._clock() - self._started)

    # -- steps ----------------------------------------------------------

    def _fetch_release(self, requested: str) -> Release:
        if requested == "latest":
            asset = "latest/download/release.json"
        else:
            normalized = requested.strip().lstrip("v")
            if SEMVER.fullmatch(normalized) is None:
                raise UpdateError("A versão precisa ter o formato 1.2.3.", step="check")
            asset = f"download/v{normalized}/release.json"
        body = self._get_bytes(f"{self._base_url}/{asset}", step="check", what="a lista de versões")
        try:
            manifest = json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise UpdateError("A lista de versões publicada está corrompida.", step="check", hint="Tente de novo em alguns minutos.") from None
        return self._release_from(manifest)

    def _release_from(self, manifest: Any) -> Release:
        if not isinstance(manifest, dict):
            raise UpdateError("A lista de versões publicada não tem o formato esperado.", step="check")
        version = str(manifest.get("version") or "")
        if SEMVER.fullmatch(version) is None:
            raise UpdateError("A lista de versões publicada traz uma versão inválida.", step="check")
        source: Any = manifest
        if self._platform != "windows-x64":
            platforms = manifest.get("platforms")
            source = platforms.get(self._platform) if isinstance(platforms, dict) else None
        if not isinstance(source, dict) or not source.get("archive_url") or not source.get("archive_sha256"):
            raise UpdateError(f"A versão {version} ainda não tem um pacote para este sistema ({self._platform}).", step="check")
        url, digest = str(source["archive_url"]), str(source["archive_sha256"])
        if not url.startswith(f"{self._base_url}/download/"):
            raise UpdateError("O endereço do pacote não pertence à release oficial do Orin.", step="check")
        if re.fullmatch(r"[A-Fa-f0-9]{64}", digest) is None:
            raise UpdateError("A lista de versões traz uma assinatura (SHA-256) inválida.", step="check")
        release_url = manifest.get("release_url")
        notes = manifest.get("notes")
        return Release(version, url, digest.lower(), release_url if isinstance(release_url, str) else None, notes if isinstance(notes, str) else None)

    def _download(self, release: Release, destination: Path) -> int:
        label = f"Baixando a versão {release.version}"
        last_error: UpdateError | None = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            self._step("download", "start", label=label if attempt == 1 else f"{label} (nova tentativa)")
            try:
                digest, size = self._stream_to(release.archive_url, destination, label)
            except UpdateError as error:
                last_error = error
                destination.unlink(missing_ok=True)
                if attempt < DOWNLOAD_ATTEMPTS:
                    self._sleep(2.0)
                continue
            self._step("download", "done", label=f"Baixado ({_megabytes(size)})")
            self._step("verify", "start")
            if digest != release.archive_sha256:
                destination.unlink(missing_ok=True)
                raise UpdateError(
                    "O arquivo baixado não confere com a assinatura publicada e foi descartado.",
                    step="verify", hint="Nada foi instalado. Tente de novo; se persistir, a conexão pode estar corrompendo o download.",
                )
            self._step("verify", "done", label="Integridade confirmada")
            return size
        assert last_error is not None
        raise last_error

    def _stream_to(self, url: str, destination: Path, label: str) -> tuple[str, int]:
        hasher = hashlib.sha256()
        done = 0
        started = self._clock()
        last_emit = 0.0
        try:
            with self._open(_request(url), timeout=NETWORK_TIMEOUT) as response, destination.open("wb") as out:
                header = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
                total = int(header) if header and header.isdigit() else None
                if total is not None:
                    self._require_space(total)
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    hasher.update(chunk)
                    done += len(chunk)
                    now = self._clock()
                    if now - last_emit >= 0.1 or (total is not None and done >= total):
                        last_emit = now
                        elapsed = max(now - started, 1e-6)
                        self._emit(UpdateEvent(
                            "progress", "download", 1, len(STEPS), label,
                            progress=(done / total) if total else None, bytes_done=done, bytes_total=total, speed=done / elapsed,
                        ))
        except HTTPError as error:
            raise UpdateError(_http_message(error.code, "o pacote"), step="download") from None
        except (URLError, TimeoutError, ConnectionError) as error:
            raise UpdateError("A conexão caiu durante o download.", step="download", hint=_network_hint(error)) from None
        except OSError as error:
            raise _disk_error(error, "download") from None
        if total is not None and done != total:
            raise UpdateError("O download terminou antes do fim do arquivo.", step="download", hint="Verifique sua conexão e tente de novo.")
        return hasher.hexdigest(), done

    def _extract(self, archive: Path, staging: Path) -> None:
        self._step("extract", "start")
        try:
            staging.mkdir(parents=True)
            with _open_archive(archive) as bundle:
                members = list(_safe_members(bundle))
                for index, extract_one in enumerate(members, start=1):
                    extract_one(staging)
                    if index % 50 == 0 or index == len(members):
                        self._emit(UpdateEvent("progress", "extract", 3, len(STEPS), "Extraindo os arquivos", progress=index / len(members)))
        except UpdateError:
            raise
        except (zipfile.BadZipFile, tarfile.TarError, EOFError):
            raise UpdateError("O pacote baixado está corrompido e não pôde ser aberto.", step="extract", hint="Tente de novo.") from None
        except OSError as error:
            raise _disk_error(error, "extract") from None
        self._step("extract", "done")

    def _validate(self, release: Release, staging: Path) -> None:
        self._step("validate", "start")
        runtime = staging / runtime_relative_path(self._platform)
        desktop_name = "Orin Desktop.exe" if self._platform.startswith("windows") else "Orin Desktop"
        if not runtime.is_file() or not (staging / desktop_name).is_file():
            raise UpdateError("O pacote não contém o programa do Orin. Nada foi instalado.", step="validate", hint="Tente de novo; se persistir, avise a equipe.")
        if os.name != "nt":
            for executable in (runtime, staging / desktop_name):
                executable.chmod(executable.stat().st_mode | 0o111)
        self._run_version_check(runtime, release.version, step="validate")
        self._step("validate", "done", label="A nova versão responde corretamente")

    def _activate(self, release: Release, staging: Path, target: Path) -> None:
        if self._before_activate is not None:
            self._before_activate()
        self._step("activate", "start", label=f"Ativando a versão {release.version}")
        previous = self.active_version()
        previous_target = (self.root / previous) if previous else None
        if target.exists():
            # A leftover from an interrupted run or an earlier rollback; the
            # staged copy was just verified, so it supersedes it.
            shutil.rmtree(target, ignore_errors=True)
            if target.exists():
                raise UpdateError(f"Não foi possível substituir a pasta da versão {release.version}.", step="activate", hint="Feche o Orin por completo e tente de novo.")
        os.replace(staging, target)
        try:
            point_current(self.root, target)
            self._smoke(target, expected=release.version)
        except BaseException as error:
            restored = self._restore(previous_target)
            shutil.rmtree(target, ignore_errors=True)
            reason = error.message if isinstance(error, UpdateError) else "a nova versão não iniciou depois de ativada"
            self._write_state(attempt={"status": "rolled_back", "version": release.version, "restored": previous, "message": reason})
            if restored or previous_target is None:
                self._emit(UpdateEvent("rolled_back", "activate", 5, len(STEPS), "Voltando à versão anterior", detail=reason))
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise UpdateError(
                f"A versão {release.version} não passou no teste final ({reason}).",
                step="activate", rolled_back=restored,
                hint=(f"A versão {previous} foi restaurada e continua funcionando. Nada foi perdido." if restored else None),
            ) from None
        self._write_state(previous=previous, current=release.version, attempt={"status": "updated", "version": release.version})
        self._step("activate", "done", label=f"Versão {release.version} ativa")

    # -- helpers --------------------------------------------------------

    def _restore(self, previous_target: Path | None) -> bool:
        if previous_target is None or not previous_target.is_dir():
            return False
        try:
            point_current(self.root, previous_target)
            return True
        except OSError:
            return False

    def _smoke(self, version_dir: Path, expected: str | None = None) -> None:
        runtime = version_dir / runtime_relative_path(self._platform)
        # Through ``current`` when it already points here: that is the exact
        # path the shim and the desktop shortcut will launch.
        via_current = self.root / "current" / runtime_relative_path(self._platform)
        candidate = via_current if os.path.realpath(self.root / "current") == os.path.realpath(version_dir) else runtime
        self._run_version_check(candidate, expected or version_dir.name, step="activate")

    def _run_version_check(self, runtime: Path, version: str, *, step: str) -> None:
        try:
            result = subprocess.run([str(runtime), "--version"], capture_output=True, text=True, timeout=SMOKE_TIMEOUT, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise UpdateError("A nova versão não respondeu a tempo.", step=step) from None
        except OSError as error:
            raise UpdateError(f"A nova versão não pôde ser executada ({type(error).__name__}).", step=step) from None
        if result.returncode != 0 or f"orin {version}" not in result.stdout:
            raise UpdateError("A nova versão respondeu de forma inesperada.", step=step)

    def _get_bytes(self, url: str, *, step: str, what: str) -> bytes:
        failure: UpdateError | None = None
        for attempt in range(1, MANIFEST_ATTEMPTS + 1):
            try:
                with self._open(_request(url), timeout=NETWORK_TIMEOUT) as response:
                    return response.read()
            except HTTPError as error:
                # A missing release is an answer, not a flaky network: retrying cannot change it.
                raise UpdateError(_http_message(error.code, what), step=step) from None
            except (URLError, TimeoutError, ConnectionError) as error:
                failure = UpdateError(f"Não consegui buscar {what}: sem conexão com o servidor de releases.", step=step, hint=_network_hint(error))
                if attempt < MANIFEST_ATTEMPTS:
                    self._sleep(1.5 * attempt)
        assert failure is not None
        raise failure

    def _require_space(self, archive_bytes: int) -> None:
        # The archive, its extracted copy, and headroom for the swap.
        needed = archive_bytes * 4
        free = shutil.disk_usage(self.root).free
        if free < needed:
            raise UpdateError(
                f"Falta espaço em disco: preciso de cerca de {_megabytes(needed)} livres e há {_megabytes(free)}.",
                step="download", hint="Libere espaço (ou apague versões antigas em Configurações > Sobre) e tente de novo.",
            )

    def _step(self, step: str, phase: str, *, label: str | None = None, detail: str | None = None) -> None:
        names = [name for name, _ in STEPS]
        index = names.index(step)
        self._emit(UpdateEvent(phase, step, index, len(STEPS), label or STEPS[index][1], detail=detail))

    def _ready_matches(self, release: Release) -> bool:
        found = self.prepared_release()
        return found is not None and found.version == release.version and found.archive_sha256 == release.archive_sha256

    def _write_prepared(self, release: Release) -> None:
        payload = {"version": release.version, "sha256": release.archive_sha256, "release_url": release.release_url, "notes": release.notes}
        temporary = self.root / f".{PREPARED_FILE}.{os.getpid()}"
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, self.root / PREPARED_FILE)

    def _clear_prepared(self) -> None:
        (self.root / PREPARED_FILE).unlink(missing_ok=True)

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads((self.root / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, *, previous: str | None = None, current: str | None = None, attempt: dict[str, Any] | None = None) -> None:
        payload = self._read_state()
        if current is not None:
            payload.update({"previous": previous, "current": current})
        if attempt is not None:
            payload["last_attempt"] = {**attempt, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        temporary = self.root / f".{STATE_FILE}.{os.getpid()}"
        try:
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temporary, self.root / STATE_FILE)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _result(self, status: str, release: Release, *, downloaded_bytes: int = 0) -> UpdateResult:
        return UpdateResult(
            status, self.current_version, release.version, release.release_url, release.notes,
            downloaded_bytes, self._clock() - self._started,
        )


def point_current(root: Path, target: Path) -> None:
    """Repoint ``<root>/current`` at ``target``.

    A symlink swap on POSIX (atomic: build the new link, rename over the old). A
    junction on Windows, where a directory link cannot be renamed over, so the
    old one is removed first; callers restore the previous target on failure.
    """
    link = root / "current"
    if os.name == "nt":
        import _winapi  # type: ignore[import-not-found]

        if os.path.lexists(link):
            os.rmdir(link)
        _winapi.CreateJunction(str(target), str(link))
        return
    temporary = root / f".current.{os.getpid()}"
    if os.path.lexists(temporary):
        temporary.unlink()
    os.symlink(target, temporary)
    os.replace(temporary, link)


def _request(url: str) -> Request:
    return Request(url, headers={"User-Agent": "Orin-updater", "Accept": "*/*"})


def _open_archive(archive: Path) -> zipfile.ZipFile | tarfile.TarFile:
    return zipfile.ZipFile(archive) if zipfile.is_zipfile(archive) else tarfile.open(archive, "r:*")


def _safe_members(bundle: zipfile.ZipFile | tarfile.TarFile) -> Iterator[Callable[[Path], None]]:
    """One extraction callable per member, refusing anything that escapes the destination."""
    if isinstance(bundle, zipfile.ZipFile):
        for info in bundle.infolist():
            yield _guarded(info.filename, lambda destination, info=info: bundle.extract(info, destination))
        return
    for member in bundle.getmembers():
        if member.issym() or member.islnk():
            # The runtime ships no links; refusing them closes the classic
            # "link out of the tree, then write through it" archive attack.
            raise UpdateError("O pacote contém um atalho não permitido e foi recusado.", step="extract")
        if not (member.isfile() or member.isdir()):
            continue
        yield _guarded(member.name, lambda destination, member=member: bundle.extract(member, destination, filter="data"))


def _guarded(name: str, extract: Callable[[Path], None]) -> Callable[[Path], None]:
    def run(destination: Path) -> None:
        resolved = (destination / name).resolve()
        if os.path.isabs(name) or not resolved.is_relative_to(destination.resolve()):
            raise UpdateError("O pacote contém um caminho inseguro e foi recusado.", step="extract")
        extract(destination)
    return run


def _megabytes(value: float) -> str:
    return f"{value / 1_048_576:.1f} MB".replace(".", ",")


def _http_message(code: int, what: str) -> str:
    if code == 404:
        return f"Não encontrei {what} publicado. Talvez essa versão ainda não exista."
    if code in (403, 429):
        return f"O servidor recusou o pedido por excesso de acessos ({code}). Aguarde alguns minutos."
    return f"O servidor respondeu com erro {code} ao buscar {what}."


def _network_hint(error: BaseException) -> str:
    return "Verifique sua conexão com a internet (e proxy/firewall) e tente de novo."


def _disk_error(error: OSError, step: str) -> UpdateError:
    if error.errno == errno.ENOSPC:
        return UpdateError("O disco ficou sem espaço durante a atualização.", step=step, hint="Libere espaço e tente de novo. Nada foi alterado.")
    if error.errno in (errno.EACCES, errno.EPERM):
        return UpdateError("Sem permissão para gravar na pasta do Orin.", step=step, hint="Feche o Orin por completo (e antivírus que bloqueie a pasta) e tente de novo.")
    return UpdateError(f"Erro ao gravar arquivos ({type(error).__name__}).", step=step, hint="Tente de novo.")


__all__ = [
    "Release", "STEPS", "UpdateError", "UpdateEvent", "UpdateResult", "Updater", "current_platform",
    "default_base_url", "point_current", "runtime_relative_path", "version_key",
]
