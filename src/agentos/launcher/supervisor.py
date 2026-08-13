"""The startup plan, and the supervision that follows it.

Startup is a sequence of steps, each of which starts something and then waits for
evidence that it works. A step that fails takes down everything the steps before
it started, in reverse, before the launcher exits — a half-running Orin is worse
than one that did not start, because the user cannot tell which half they have.

The browser opens after the last step, never before, because the last step is the
one that proves the interface is actually being served.
"""

from __future__ import annotations

import logging
import signal
import threading
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep

from agentos.installation import OrinPaths, RuntimeProfile
from agentos.omniroute import OmniRouteRuntimeSettingsStore
from agentos.provider_catalog.omniroute import DEFAULT_OMNIROUTE_BASE_URL

from .environment import RuntimeEnvironment, load_environment
from .desktop import DesktopProcess, DesktopUnavailable, launch_desktop
from .desktop_status import DesktopStatusWriter
from .ports import PortChoice, select_port
from .probes import ProbeResult, frontend_probe, heartbeat_probe, http_probe, wait_until
from .processes import ProcessGroup, SupervisedProcess, child_environment
from .services import apply_migrations, ensure_datastores
from .state import (
    InstanceLock,
    InstanceState,
    clear_state,
    clear_stop_request,
    stop_requested,
    write_state,
)
from .ui import Console

BACKEND_READY_TIMEOUT = 60.0
WORKERS_READY_TIMEOUT = 60.0
# Short on purpose. A cold OmniRoute can take minutes to serve its first
# request, and Orin does not need it to work — so the launcher gives it a
# moment to appear and then gets out of the way, watching for it in the
# background instead of making the user wait.
OMNIROUTE_GRACE = 8.0
OMNIROUTE_WATCH_INTERVAL = 5.0
OMNIROUTE_WATCH_WINDOW = 300.0
FRONTEND_READY_TIMEOUT = 20.0


class StartupFailed(RuntimeError):
    """A startup step failed; the message is written for the person reading it."""


class StartupCancelled(StartupFailed):
    """The desktop window asked the launcher to stop while it was starting."""


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    port: int | None = None
    explicit_port: bool = False
    open_browser: bool = True
    verbose: bool = False
    desktop: bool = False
    desktop_devtools: bool = False
    desktop_reuse: bool = False


@dataclass
class Supervisor:
    paths: OrinPaths
    profile: RuntimeProfile
    console: Console
    options: LaunchOptions = field(default_factory=LaunchOptions)
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("orin.launcher"))

    group: ProcessGroup = field(default_factory=ProcessGroup, init=False)
    children: list[SupervisedProcess] = field(default_factory=list, init=False)
    environment: RuntimeEnvironment | None = field(default=None, init=False)
    choice: PortChoice | None = field(default=None, init=False)
    omniroute_expected: bool = field(default=False, init=False)
    omniroute_ready: bool = field(default=False, init=False)
    _omniroute_next_check: float = field(default=0.0, init=False)
    _omniroute_deadline: float = field(default=0.0, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    desktop_status: DesktopStatusWriter | None = field(default=None, init=False)
    desktop_process: DesktopProcess | None = field(default=None, init=False)
    _desktop_current_service: str = field(default="database", init=False)

    # -- addresses ------------------------------------------------------

    @property
    def port(self) -> int:
        return self.choice.port if self.choice is not None else 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- startup --------------------------------------------------------

    def start(self, lock: InstanceLock) -> int:
        self.console.banner()
        try:
            self._prepare()
            self._step_services()
            self._step_backend()
            self._step_workers()
            self._step_omnirouter()
            self._step_frontend()
        except StartupCancelled as error:
            self._rollback()
            self._desktop_stopped(str(error))
            self.console.stopped()
            return 130
        except StartupFailed as error:
            self._rollback()
            self._desktop_failed(str(error))
            self.console.error(str(error))
            self.log.error("startup failed: %s", error)
            return 1
        except KeyboardInterrupt:
            self._rollback()
            self.console.stopped()
            return 130

        write_state(
            self.paths,
            InstanceState.create(port=self.port, url=self.base_url, version=self.profile.version, profile=self.profile.kind),
        )
        if self.desktop_status is not None:
            self.desktop_status.ready(self.base_url)
        self.console.ready(self.base_url)
        if self.choice is not None and self.choice.message:
            self.console.detail(self.choice.message)
        if self.options.open_browser:
            self._open_browser()
        return self._supervise(lock)

    def _prepare(self) -> None:
        self.paths.ensure()
        try:
            self.environment = load_environment(self.paths, self.profile)
        except Exception as error:
            raise StartupFailed(str(error)) from error
        if self.environment.created_configuration is not None:
            self.console.detail(f"created {self.environment.created_configuration}")
        self.log.info("profile=%s root=%s", self.profile.kind, self.profile.root)
        self.log.debug("configuration sources: %s", ", ".join(str(path) for path in self.environment.sources))
        try:
            self.choice = select_port(self.options.port, explicit=self.options.explicit_port)
        except Exception as error:
            raise StartupFailed(str(error)) from error
        if self.choice.message:
            self.console.warning(self.choice.message)
        clear_stop_request(self.paths)
        if self.options.desktop:
            self.desktop_status = DesktopStatusWriter(self.paths, restart_command=self.profile.launcher_command())
            self.desktop_status.set_url(self.base_url)
            self._desktop_service("database", "starting", "Preparando banco de dados local")
            if not self.options.desktop_reuse:
                try:
                    self.desktop_process = launch_desktop(
                        self.paths,
                        self.profile,
                        self.desktop_status,
                        devtools=self.options.desktop_devtools,
                    )
                except DesktopUnavailable as error:
                    self._desktop_failed(str(error))
                    raise StartupFailed(str(error)) from error

    def _step_services(self) -> None:
        assert self.environment is not None
        self._desktop_current_service = "database"
        try:
            status = ensure_datastores(self.environment, self.profile, log=self.log)
            if stop_requested(self.paths):
                raise StartupCancelled("A inicialização foi cancelada.")
            self._desktop_service("database", "ready", status.database.detail)
            self._desktop_current_service = "migrations"
            self._desktop_service("migrations", "starting", "Aplicando atualizações do banco")
            apply_migrations(self.environment, self.profile, log=self.log)
        except StartupCancelled:
            raise
        except Exception as error:
            self.console.failed("Services")
            self._desktop_failed(str(error))
            raise StartupFailed(str(error)) from error
        self._desktop_service("migrations", "ready", "Banco de dados atualizado")
        self.console.step("Services")

    def _step_backend(self) -> None:
        self._desktop_current_service = "backend"
        self._desktop_service("backend", "starting", "Iniciando API local")
        process = self._spawn("backend")
        self._desktop_current_service = "health"
        self._desktop_service("health", "starting", "Aguardando /healthz")
        ready = wait_until(
            lambda: http_probe(f"{self.base_url}/healthz", timeout=2.0),
            timeout=BACKEND_READY_TIMEOUT,
            abort=lambda: self._startup_abort(process),
        )
        if ready:
            self._desktop_service("health", "ready", ready.detail)
            self._desktop_current_service = "ready"
            self._desktop_service("ready", "starting", "Aguardando /readyz")
            # /healthz says the process is up; /readyz says it can reach the
            # database and the queue, which is what the workers depend on.
            ready = wait_until(
                lambda: http_probe(f"{self.base_url}/readyz", timeout=3.0),
                timeout=BACKEND_READY_TIMEOUT,
                abort=lambda: self._startup_abort(process),
            )
        if not ready:
            self.console.failed("Backend")
            self._raise_if_startup_cancelled(ready)
            raise StartupFailed(self._child_failure("Backend", process, ready))
        self._desktop_service("ready", "ready", ready.detail)
        self._desktop_service("backend", "ready", "API local pronta")
        self.console.step("Backend")

    def _step_workers(self) -> None:
        assert self.environment is not None
        self._desktop_current_service = "worker"
        self._desktop_service("worker", "starting", "Iniciando worker")
        worker = self._spawn("worker")
        self._desktop_current_service = "scheduler"
        self._desktop_service("scheduler", "starting", "Iniciando tarefas agendadas")
        scheduler = self._spawn("scheduler")
        ready = wait_until(
            lambda: heartbeat_probe(self.environment.database_url, ("chat-worker", "scheduled-chat-worker")),  # type: ignore[union-attr]
            timeout=WORKERS_READY_TIMEOUT,
            interval=0.5,
            abort=lambda: self._startup_abort(worker) or self._startup_abort(scheduler),
        )
        if not ready:
            self.console.failed("Workers")
            self._raise_if_startup_cancelled(ready)
            dead = next((child for child in (worker, scheduler) if child.exited() is not None), None)
            raise StartupFailed(self._child_failure("Workers", dead or worker, ready))
        self._desktop_service("worker", "ready", "Worker conectado")
        self._desktop_service("scheduler", "ready", "Tarefas agendadas conectadas")
        self.console.step("Workers")

    def _step_omnirouter(self) -> None:
        """Only if the user's existing preference says so.

        The preference lives in the OmniRoute settings store and is written only
        by Settings -> OmniRoute. The launcher reads it and never touches it. The
        gateway process itself stays owned by the backend, because that is what
        the Start/Stop/Restart controls in the interface act on.
        """
        self.omniroute_expected = self._omniroute_enabled()
        if stop_requested(self.paths):
            raise StartupCancelled("A inicialização foi cancelada.")
        if not self.omniroute_expected:
            # Disabled is an ordinary choice, not a warning and not a log line
            # the user has to read. The step simply does not exist.
            self.log.info("omniroute auto-start is disabled; not part of this startup")
            return
        ready = wait_until(
            lambda: self._omniroute_probe(),
            timeout=OMNIROUTE_GRACE,
            interval=0.5,
            abort=self._startup_abort,
        )
        self._raise_if_startup_cancelled(ready)
        if ready:
            self.omniroute_ready = True
            self.console.step("OmniRouter")
            return
        # Still coming up. Orin does not depend on it, so nothing waits: the
        # supervise loop reports it the moment it answers.
        self.console.line("  · OmniRouter starting")
        self._omniroute_deadline = monotonic() + OMNIROUTE_WATCH_WINDOW
        self.log.info("omniroute not ready within the grace window: %s", ready.detail)

    def _step_frontend(self) -> None:
        self._desktop_current_service = "frontend"
        self._desktop_service("frontend", "starting", "Verificando a interface do Orin")
        ready = wait_until(
            lambda: frontend_probe(self.base_url),
            timeout=FRONTEND_READY_TIMEOUT,
            abort=lambda: self._startup_abort(self.children[0]) if self.children else self._startup_abort(),
        )
        if not ready:
            self.console.failed("Frontend")
            self._raise_if_startup_cancelled(ready)
            raise StartupFailed(
                "The web interface is not being served.\n"
                f"  {ready.detail}\n"
                f"  Backend log: {self.paths.service_log('backend')}"
            )
        self._desktop_service("frontend", "ready", ready.detail)
        self.console.step("Frontend")

    # -- omniroute ------------------------------------------------------

    def _omniroute_store(self) -> OmniRouteRuntimeSettingsStore:
        return OmniRouteRuntimeSettingsStore(self.paths.data / "omniroute-runtime.json")

    def _omniroute_enabled(self) -> bool:
        try:
            return self._omniroute_store().any_enabled()
        except Exception:
            self.log.exception("could not read the omniroute preference; treating it as disabled")
            return False

    def _omniroute_probe(self) -> ProbeResult:
        return http_probe(f"{self._omniroute_base_url()}/models", timeout=2.0)

    def _watch_omniroute(self) -> None:
        """Report the optional gateway once it answers, without ever waiting for it.

        Polled sparsely and only for a bounded window: a gateway that has not
        appeared after several minutes is not appearing, and Orin has been fully
        usable the whole time regardless.
        """
        if not self.omniroute_expected or self.omniroute_ready:
            return
        now = monotonic()
        if now < self._omniroute_next_check:
            return
        if now > self._omniroute_deadline:
            self.omniroute_expected = False
            self.log.warning("stopped watching for omniroute; it never became ready")
            return
        self._omniroute_next_check = now + OMNIROUTE_WATCH_INTERVAL
        if self._omniroute_probe():
            self.omniroute_ready = True
            self.console.step("OmniRouter")

    def _owned_omniroute_is_gone(self) -> bool:
        """Whether the gateway went away with the backend that owned it.

        Only the process the backend spawned is stopped; an instance that was
        already running when Orin started is left alone. Rather than claim
        credit either way, the shutdown line is printed only when the gateway
        has actually stopped answering.
        """
        return not http_probe(f"{self._omniroute_base_url()}/models", timeout=2.0)

    def _omniroute_base_url(self) -> str:
        assert self.environment is not None
        configured = self.environment.values.get("AGENTOS_OMNIROUTE_BASE_URL", "").strip()
        return (configured or DEFAULT_OMNIROUTE_BASE_URL).rstrip("/")

    # -- children -------------------------------------------------------

    def _spawn(self, service: str) -> SupervisedProcess:
        assert self.environment is not None
        command = self.profile.service_command(service)
        child = SupervisedProcess(service, command, self.paths.service_log(service))
        self.log.info("starting %s: %s", service, " ".join(command))
        child.start(
            environment=child_environment(self.environment.for_port(self.port), {}),
            # A fixed, existing directory the launcher controls. Never the
            # caller's cwd, which may vanish or be a network path.
            cwd=self._child_cwd(),
            group=self.group,
        )
        self.children.append(child)
        return child

    def _child_cwd(self) -> Path:
        return self.profile.repository if self.profile.repository is not None else self.paths.data

    @staticmethod
    def _child_died(child: SupervisedProcess) -> str | None:
        code = child.exited()
        if code is None:
            return None
        return f"{child.name} exited with code {code} before becoming ready"

    def _child_failure(self, label: str, child: SupervisedProcess, result: ProbeResult) -> str:
        tail = child.tail()
        message = [f"{label} did not become ready.", f"  {result.detail}", f"  Log: {child.log_path}"]
        if tail:
            message.append("")
            message.extend(f"  {line}" for line in tail.splitlines()[-8:])
        return "\n".join(message)

    def _startup_abort(self, child: SupervisedProcess | None = None) -> str | None:
        if stop_requested(self.paths):
            return "startup canceled by the desktop window"
        return self._child_died(child) if child is not None else None

    @staticmethod
    def _raise_if_startup_cancelled(result: ProbeResult) -> None:
        if "startup canceled" in result.detail:
            raise StartupCancelled("A inicialização foi cancelada.")

    def _desktop_service(self, name: str, state: str, detail: str = "") -> None:
        if self.desktop_status is not None:
            self.desktop_status.service(name, state, detail)  # type: ignore[arg-type]

    def _desktop_failed(self, message: str) -> None:
        if self.desktop_status is not None:
            self.desktop_status.failed(self._desktop_current_service, message)

    def _desktop_stopped(self, message: str) -> None:
        if self.desktop_status is not None:
            self.desktop_status.stopped(message)

    # -- shutdown -------------------------------------------------------

    def _rollback(self) -> None:
        """Undo a partial startup, newest first."""
        if not self.children:
            self.group.close()
            return
        self.console.stopping()
        self._stop_children()
        self.group.close()

    def _stop_children(self) -> None:
        reported: set[str] = set()
        for child in reversed(self.children):
            if child.process is None:
                continue
            self.log.info("stopping %s (pid %s)", child.name, child.pid)
            child.stop()
            # The chat and scheduler workers are one thing to the user, so they
            # get one line rather than two identical ones.
            label = _label(child.name)
            if label not in reported:
                reported.add(label)
                self.console.step(f"{label} stopped")
        self.children.clear()

    def shutdown(self) -> None:
        self.console.stopping()
        self._stop_children()
        if self.omniroute_ready and self._owned_omniroute_is_gone():
            self.console.step("OmniRouter stopped")
        # Backstop: anything that ignored a graceful stop, and any grandchild,
        # dies with the job object.
        self.group.terminate_all()
        self.group.close()
        clear_state(self.paths)
        clear_stop_request(self.paths)
        self._desktop_stopped("Orin foi encerrado.")
        self.console.stopped()

    # -- supervision ----------------------------------------------------

    def _supervise(self, lock: InstanceLock) -> int:
        self._install_signal_handlers()
        exit_code = 0
        try:
            while not self._stop.is_set():
                if stop_requested(self.paths):
                    self.log.info("stop requested by another process")
                    break
                self._watch_omniroute()
                dead = next((child for child in self.children if child.exited() is not None), None)
                if dead is not None:
                    self._desktop_current_service = {"backend": "backend", "worker": "worker", "scheduler": "scheduler"}.get(dead.name, "backend")
                    self._desktop_failed(f"{_label(dead.name)} parou inesperadamente. Veja os logs para mais detalhes.")
                    self.console.error(
                        f"{_label(dead.name)} stopped unexpectedly (exit code {dead.exited()}).\n"
                        f"  Log: {dead.log_path}"
                    )
                    self.log.error("%s exited with %s", dead.name, dead.exited())
                    exit_code = 1
                    break
                self._stop.wait(0.5)
        except KeyboardInterrupt:
            exit_code = 130
        finally:
            self.shutdown()
            lock.release()
        return exit_code

    def _install_signal_handlers(self) -> None:
        def request_stop(signum, _frame) -> None:  # noqa: ANN001 - signal handler signature
            self.log.info("received signal %s", signum)
            self._stop.set()

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            handler = getattr(signal, name, None)
            if handler is None:
                continue
            try:
                signal.signal(handler, request_stop)
            except (ValueError, OSError):
                # Not the main thread, or unsupported on this platform. The
                # stop-request file still works either way.
                continue

    # -- browser --------------------------------------------------------

    def _open_browser(self) -> None:
        """Open exactly one tab, and only once the interface answered."""
        try:
            opened = webbrowser.open(self.base_url, new=2, autoraise=True)
        except Exception:
            opened = False
        if not opened:
            self.console.detail(f"Could not open a browser automatically. Open {self.base_url} yourself.")


def _label(name: str) -> str:
    return {"backend": "Backend", "worker": "Workers", "scheduler": "Workers", "frontend": "Frontend"}.get(name, name.title())


def attach_to_running(console: Console, state: InstanceState, *, open_browser: bool = True) -> int:
    """A second ``orin`` opens the instance that already exists."""
    console.already_running(state.url, opening=open_browser)
    if open_browser:
        try:
            webbrowser.open(state.url, new=2, autoraise=True)
        except Exception:
            console.detail(f"Open {state.url} manually.")
    return 0


def wait_for_exit(pid: int, timeout: float) -> bool:
    from .state import process_is_alive

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if not process_is_alive(pid):
            return True
        sleep(0.25)
    return not process_is_alive(pid)


__all__ = ["LaunchOptions", "StartupFailed", "Supervisor", "attach_to_running", "wait_for_exit"]
