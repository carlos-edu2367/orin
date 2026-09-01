# Release Linux do Orin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar uma release Linux (Debian/Ubuntu) do Orin com paridade completa — CLI e Orin Desktop — a partir do design já validado empiricamente em Ubuntu 24.04 real.

**Architecture:** Corrige um bug de correção pré-existente (processo zumbi), fecha os 3 pontos de mão única que hoje assumem Windows, ensina o instalador e o empacotamento a produzir o mesmo layout de diretórios que o Windows já usa (sem AppImage), funde o manifesto de release numa forma retrocompatível, e reestrutura o workflow de release de monolítico-Windows para fan-in de duas plataformas.

**Tech Stack:** Python 3.13 + pytest, Bash (install.sh, build-linux.sh), PowerShell (verificação cruzada do manifesto), PyInstaller, Electron/electron-builder, GitHub Actions.

**Spec:** [docs/superpowers/specs/2026-09-01-linux-release-design.md](../specs/2026-09-01-linux-release-design.md)

## Global Constraints

- **O instalador nunca roda `apt`, nunca pede sudo, nunca mexe em dotfiles do usuário sem confirmação.** Falta de lib de sistema vira mensagem acionável, não instalação automática.
- **Retrocompatibilidade do manifesto é obrigatória, não presumida.** `install.ps1` já instalado numa máquina existente não pode quebrar quando o manifesto ganhar a chave `platforms`.
- **Formato de empacotamento é `.tar.gz` de diretório "unpacked", não AppImage.** `orin --desktop` no POSIX já espera um binário chamado `"Orin Desktop"` lado a lado com `resources/runtime/orin`.
- **Nenhum teste que hoje passa em Windows pode passar a falhar quando `ubuntu-latest` entrar na matriz de CI.** Todo teste que hoje hardcoda `install.ps1`/`powershell.exe` precisa virar consciente de `os.name`.
- Comandos de teste Python: `.venv/Scripts/python.exe -m pytest <caminho> -v` (Windows) — os mesmos comandos, com `.venv/bin/python`, valem no WSL usado para verificação real em Linux.
- **Distro alvo de validação: Debian/Ubuntu.** Outras famílias de distro ficam "deve funcionar, não testado".

---

### Task 1: Corrigir o bug do processo zumbi (pré-requisito)

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py:278-301` (`_process_is_running`)
- Test: `tests/unit/agentic/test_process_liveness.py` (criar)

**Interfaces:**
- Consumes: nada.
- Produces: `_process_is_running(pid: int) -> bool` com o mesmo nome e assinatura, comportamento POSIX corrigido. `stop_process`/`read_process_output` (já existentes em `agent_tools.py`) continuam chamando essa mesma função sem nenhuma mudança própria.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/unit/agentic/test_process_liveness.py`:

```python
import pytest

from agentos.agentic import agent_tools


def test_a_child_that_already_exited_is_reaped_and_reported_as_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools.os, "name", "posix")
    monkeypatch.setattr(agent_tools.os, "waitpid", lambda pid, options: (pid, 0))

    def kill_should_not_be_needed(pid, sig):
        raise AssertionError("kill(pid, 0) should not run once waitpid already reaped the child")

    monkeypatch.setattr(agent_tools.os, "kill", kill_should_not_be_needed)

    assert agent_tools._process_is_running(4242) is False


def test_a_child_still_running_is_reported_as_running_without_touching_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools.os, "name", "posix")
    # WNOHANG with the child still alive returns (0, 0), not the child's own pid.
    monkeypatch.setattr(agent_tools.os, "waitpid", lambda pid, options: (0, 0))

    def kill_should_not_be_needed(pid, sig):
        raise AssertionError("kill(pid, 0) should not run once waitpid already answered")

    monkeypatch.setattr(agent_tools.os, "kill", kill_should_not_be_needed)

    assert agent_tools._process_is_running(4242) is True


def test_a_pid_that_is_no_longer_our_child_falls_back_to_kill_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracked across an AgentToolset rebuilt in a later turn, or a backend
    restart: waitpid() only works while the original launching process is
    still the one asking."""
    monkeypatch.setattr(agent_tools.os, "name", "posix")

    def waitpid_not_our_child(pid, options):
        raise ChildProcessError("no such child")

    monkeypatch.setattr(agent_tools.os, "waitpid", waitpid_not_our_child)
    monkeypatch.setattr(agent_tools.os, "kill", lambda pid, sig: None)  # succeeds -- pid exists

    assert agent_tools._process_is_running(4242) is True


def test_a_pid_that_is_gone_and_not_our_child_is_reported_as_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools.os, "name", "posix")
    monkeypatch.setattr(agent_tools.os, "waitpid", lambda pid, options: (_ for _ in ()).throw(ChildProcessError()))

    def kill_raises_lookup_error(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(agent_tools.os, "kill", kill_raises_lookup_error)

    assert agent_tools._process_is_running(4242) is False
```

- [ ] **Step 2: Rode o teste e confirme que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_process_liveness.py -v`
Expected: FAIL — a primeira `AssertionError` ("kill(pid, 0) should not run...") dispara, provando que a implementação atual sempre cai no `kill(pid, 0)` mesmo quando o filho já foi de fato recolhido.

- [ ] **Step 3: Corrija `_process_is_running`**

Em `src/agentos/agentic/agent_tools.py`, substitua o ramo POSIX de `_process_is_running` (linhas 293-301 aproximadamente, o corpo depois do `if os.name == "nt": ...`):

```python
    try:
        reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
        if reaped_pid == 0:
            return True
    except ChildProcessError:
        # Not our child -- tracked across a new AgentToolset (a later turn)
        # or a backend restart. waitpid() only works while the original
        # launching process is still the one asking; fall back to a plain
        # existence check.
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
```

O docstring da função (já existente, "Whether ``pid`` is alive...") continua válido; acrescente uma frase explicando o motivo do `waitpid`:

```python
def _process_is_running(pid: int) -> bool:
    """Whether ``pid`` is alive, checked without needing the original ``Popen``.

    A background process is tracked in a manifest file that outlives the
    ``AgentToolset`` that started it (a new one is built every turn), so
    liveness has to be answerable from the pid alone.

    On POSIX, ``os.kill(pid, 0)`` alone is not enough: it reports a zombie
    (a child that exited but was never ``wait()``-ed on) as alive, because a
    zombie still occupies a process-table entry. ``run_command(background=True)``
    never waits on the child it starts, so every background process on
    Linux/macOS would otherwise be reported as running forever. Reaping it
    with ``waitpid(pid, WNOHANG)`` first fixes that for the common case
    (we are still the parent); the ``kill(pid, 0)`` fallback covers the case
    where the process was tracked across a restart and we no longer are.
    """
```

- [ ] **Step 4: Rode os testes e confirme que passam**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_process_liveness.py tests/unit/agentic/test_agent_tools.py -v`
Expected: PASS em todos, incluindo `test_background_process_output_can_be_read_listed_and_stopped` (que hoje passa em Windows sem exercitar o ramo corrigido — a prova real do ramo POSIX vem no Step 5).

- [ ] **Step 5: Verifique de verdade em Linux (WSL)**

Este é o teste que originalmente falhava por este bug. Rode em Ubuntu real:

```bash
wsl.exe -d Ubuntu -e bash -lc "source \$HOME/.local/bin/env && cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && TMPDIR=/tmp uv run pytest -q tests/unit/agentic/test_agent_tools.py::test_background_process_output_can_be_read_listed_and_stopped -v"
```

Expected: PASS, e rápido (frações de segundo depois do `print` rodar) — não mais os ~10s de timeout que o bug causava. Se `uv`/o venv não estiverem disponíveis no WSL, rode primeiro `uv sync --frozen --all-groups` no mesmo diretório.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/agentic/agent_tools.py tests/unit/agentic/test_process_liveness.py
git commit -m "fix(agentic): reap background processes so Linux liveness checks are accurate"
```

---

### Task 2: Abstração de plataforma — instalador correto por SO

**Files:**
- Modify: `src/agentos/installation/profile.py` (`installer` property, ~linha 99)
- Modify: `src/agentos/launcher/cli.py` (`command_update` ~linha 254, `command_uninstall` ~linha 280)
- Modify: `src/agentos/installation/versions.py` (`start_update` ~linha 143)
- Modify: `packaging/orin.spec` (linha 28, dados do PyInstaller)
- Modify: `tests/unit/launcher/test_sqlite_runtime.py` (teste existente, OS-dependente hoje)
- Modify: `tests/unit/launcher/test_uninstall.py` (teste existente, OS-dependente hoje)
- Modify: `tests/unit/installation/test_versions.py` (3 testes existentes, OS-dependentes hoje)
- Test: `tests/unit/installation/test_profile_installer.py` (criar)
- Test: `tests/unit/launcher/test_update_command.py` (criar — `command_update` hoje não tem nenhum teste)

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `RuntimeProfile.installer` continua uma `@property` retornando `Path`, mas aponta para `install.sh` quando `os.name != "nt"`. Nenhuma assinatura pública muda.

- [ ] **Step 1: Escreva o teste que falha para `RuntimeProfile.installer`**

Crie `tests/unit/installation/test_profile_installer.py`:

```python
from pathlib import Path

import pytest

from agentos.installation import profile as profile_module
from agentos.installation.profile import RuntimeProfile


def test_installer_is_install_ps1_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_module.os, "name", "nt")
    (tmp_path / "install.ps1").write_text("# installer", encoding="utf-8")
    (tmp_path / "install.sh").write_text("# installer", encoding="utf-8")

    profile = RuntimeProfile("installed", tmp_path, "1.0.0", None)

    assert profile.installer == tmp_path / "install.ps1"


def test_installer_is_install_sh_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_module.os, "name", "posix")
    (tmp_path / "install.ps1").write_text("# installer", encoding="utf-8")
    (tmp_path / "install.sh").write_text("# installer", encoding="utf-8")

    profile = RuntimeProfile("installed", tmp_path, "1.0.0", None)

    assert profile.installer == tmp_path / "install.sh"
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/installation/test_profile_installer.py -v`
Expected: FAIL — `test_installer_is_install_sh_on_posix` falha porque `installer` hoje sempre retorna `install.ps1`.

- [ ] **Step 3: Corrija `RuntimeProfile.installer`**

Em `src/agentos/installation/profile.py`, acrescente `import os` ao topo (junto de `import sys`), e substitua a property:

```python
    @property
    def installer(self) -> Path:
        """The verified release installer shipped beside a frozen runtime."""
        name = "install.ps1" if os.name == "nt" else "install.sh"
        candidates = [self.root / name, self.root / "_internal" / name]
        if self.repository is not None:
            candidates.append(self.repository / name)
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/installation/test_profile_installer.py -v`
Expected: PASS, 2 testes.

- [ ] **Step 5: Corrija os testes existentes que agora quebrariam em Linux**

Estes três arquivos hoje hardcodam `install.ps1` sem considerar `os.name`. Eles continuam passando hoje em Windows porque `os.name == "nt"` ali, mas quebrariam silenciosamente assim que a Task 6 (matriz `ubuntu-latest`) rodar, porque a fixture só cria o arquivo `.ps1` e `profile.installer` (já corrigido) passaria a procurar `.sh`.

Em `tests/unit/launcher/test_sqlite_runtime.py`, no `test_frozen_layout_finds_assets_under_pyinstaller_internal_directory`, troque:

```python
    (internal / "install.ps1").write_text("# installer", encoding="utf-8")
    profile = RuntimeProfile("installed", root, "1", None)

    assert profile.web_dist == internal / "web"
    assert profile.installer == internal / "install.ps1"
```

por:

```python
    import os
    installer_name = "install.ps1" if os.name == "nt" else "install.sh"
    (internal / installer_name).write_text("# installer", encoding="utf-8")
    profile = RuntimeProfile("installed", root, "1", None)

    assert profile.web_dist == internal / "web"
    assert profile.installer == internal / installer_name
```

Em `tests/unit/launcher/test_uninstall.py`, acrescente `import os` já está presente no topo do arquivo — reaproveite. Troque:

```python
    installer = runtime / "install.ps1"
    installer.write_text("# packaged installer", encoding="utf-8")
```

por:

```python
    installer_name = "install.ps1" if os.name == "nt" else "install.sh"
    installer = runtime / installer_name
    installer.write_text("# packaged installer", encoding="utf-8")
```

E a asserção do comando invocado (mais adiante no mesmo teste) precisa saber qual comando esperar por SO — trate isso no Step 7 (depois de `command_uninstall` estar corrigido), não aqui.

Em `tests/unit/installation/test_versions.py`, acrescente `import os` ao topo do arquivo (junto de `import json`), e nos três testes que escrevem `profile.root / "install.ps1"` (`test_start_update_runs_the_installer_and_reports_started`, `test_start_update_surfaces_the_installer_stderr_on_failure`, `test_start_update_turns_an_installer_timeout_into_a_clear_error`), substitua cada ocorrência de:

```python
    installer = profile.root / "install.ps1"
    installer.write_text("# fake installer", encoding="utf-8")
```

(ou, nos outros dois testes, a forma inline `(profile.root / "install.ps1").write_text(...)`) por uma variável `installer_name = "install.ps1" if os.name == "nt" else "install.sh"` e o caminho derivado dela. Trate as asserções de conteúdo do comando (`-NoDesktopShortcut`) no Step 6, junto da correção de `start_update`.

- [ ] **Step 6: Corrija `start_update` (versions.py) e seu teste**

Em `src/agentos/installation/versions.py`, substitua o bloco `subprocess.run` dentro de `start_update`:

```python
    try:
        if os.name == "nt":
            command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer), "-NoDesktopShortcut"]
        else:
            command = ["bash", str(installer), "--no-desktop-shortcut"]
        result = subprocess.run(
            command,
            check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300,
        )
```

(O comentário existente sobre por que `-NoDesktopShortcut`/`--no-desktop-shortcut` é necessário continua válido para os dois ramos — mantenha-o acima do `try`.)

Em `tests/unit/installation/test_versions.py`, atualize a asserção de `test_start_update_runs_the_installer_and_reports_started` para ser consciente de SO:

```python
    assert versions.start_update(profile) == {"started": True}
    assert str(installer) in captured["command"]
    if os.name == "nt":
        assert "-NoDesktopShortcut" in captured["command"]
    else:
        assert "--no-desktop-shortcut" in captured["command"]
    assert captured["stdin"] is versions.subprocess.DEVNULL
```

- [ ] **Step 7: Corrija `command_update`/`command_uninstall` (cli.py) e o teste de uninstall**

Em `src/agentos/launcher/cli.py`, dentro de `command_update` (~linha 254):

```python
    try:
        if os.name == "nt":
            command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)]
        else:
            command = ["bash", str(installer)]
        result = subprocess.run(command, check=False)
```

Dentro de `command_uninstall` (~linha 280):

```python
    try:
        if os.name == "nt":
            command = [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer),
                "-Uninstall", "-Force", "-WaitForPid", str(os.getpid()),
            ]
        else:
            command = ["bash", str(installer), "--uninstall", "--force", "--wait-for-pid", str(os.getpid())]
        result = subprocess.run(command, check=False)
```

Confirme que `import os` já existe no topo de `cli.py` (deve existir, já que `os.getpid()` já é usado nesse mesmo arquivo hoje).

Em `tests/unit/launcher/test_uninstall.py`, complete a correção do Step 5 atualizando a asserção final de `test_installed_uninstall_schedules_the_packaged_helper`:

```python
    assert cli.command_uninstall(paths, RuntimeProfile("installed", runtime, "1.0.0", None), Console(io.StringIO(), colour=False)) == 0
    if os.name == "nt":
        assert invoked == [[
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer),
            "-Uninstall", "-Force", "-WaitForPid", str(os.getpid()),
        ]]
    else:
        assert invoked == [["bash", str(installer), "--uninstall", "--force", "--wait-for-pid", str(os.getpid())]]
```

- [ ] **Step 8: Escreva o teste que faltava para `command_update`**

`command_update` hoje não tem nenhum teste. Crie `tests/unit/launcher/test_update_command.py`, espelhando exatamente `tests/unit/launcher/test_uninstall.py`:

```python
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher import cli
from agentos.launcher.ui import Console


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def test_update_invokes_the_platform_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    installer_name = "install.ps1" if os.name == "nt" else "install.sh"
    installer = runtime / installer_name
    installer.write_text("# packaged installer", encoding="utf-8")
    invoked: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run", lambda command, check: invoked.append(command) or Result())

    assert cli.command_update(paths, RuntimeProfile("installed", runtime, "1.0.0", None), Console(io.StringIO(), colour=False)) == 0
    if os.name == "nt":
        assert invoked == [["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)]]
    else:
        assert invoked == [["bash", str(installer)]]


def test_update_reports_a_missing_installer(tmp_path: Path) -> None:
    stream = io.StringIO()
    paths = _paths(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    assert cli.command_update(paths, RuntimeProfile("installed", runtime, "1.0.0", None), Console(stream, colour=False)) == 1
    assert "release installer is missing" in stream.getvalue()
```

Confira a assinatura exata de `command_update` em `cli.py` antes de rodar — se ela diferir (por exemplo, `command_stop` sendo chamado antes quando há uma instância rodando), ajuste o teste para a assinatura real; o comportamento sob teste é "nenhuma instância rodando, instalador presente, invoca o comando certo por SO".

- [ ] **Step 9: Corrija `orin.spec` para embutir o instalador certo**

Hoje `packaging/orin.spec` linha 28 embute `install.ps1` incondicionalmente nos dados do PyInstaller — um build feito em Linux embutiria o script errado. Em `packaging/orin.spec`, troque:

```python
datas = collect_data_files("agentos")
datas += copy_metadata("agentos")
datas += [
    (str(WEB), "web"),
    (str(BROWSERS), "playwright"),
    (str(ROOT / "install.ps1"), "."),
    (str(ROOT / "src" / "agentos" / "persistence" / "postgres" / "migrations"), "agentos/persistence/postgres/migrations"),
]
```

por:

```python
INSTALLER_SCRIPT = ROOT / ("install.ps1" if os.name == "nt" else "install.sh")

datas = collect_data_files("agentos")
datas += copy_metadata("agentos")
datas += [
    (str(WEB), "web"),
    (str(BROWSERS), "playwright"),
    (str(INSTALLER_SCRIPT), "."),
    (str(ROOT / "src" / "agentos" / "persistence" / "postgres" / "migrations"), "agentos/persistence/postgres/migrations"),
]
```

`import os` já está no topo do arquivo (linha 9), nenhum import novo necessário. Este arquivo não tem teste automatizado (é consumido pelo PyInstaller, não importado por pytest); a prova real vem na Task 7, quando o build roda de verdade em Linux.

- [ ] **Step 10: Rode a suíte completa e confirme zero regressão**

Run: `.venv/Scripts/python.exe -m pytest -q tests/unit`
Expected: todos os testes passam, incluindo os modificados.

- [ ] **Step 11: Verifique o ramo POSIX de verdade em Linux (WSL)**

```bash
wsl.exe -d Ubuntu -e bash -lc "source \$HOME/.local/bin/env && cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && TMPDIR=/tmp uv run pytest -q tests/unit/installation tests/unit/launcher -v 2>&1 | tail -60"
```

Expected: PASS em todos — esta é a única forma de provar que o ramo `os.name != "nt"` (que nenhum teste no Windows exercita de verdade, mesmo com o `monkeypatch`, já que o monkeypatch simula mas não prova nada sobre comandos reais como `bash <script>`) funciona de fato. Se `bash` não for encontrado como executável no PATH do WSL (não deveria acontecer, mas confira), ajuste o comando para o caminho absoluto (`/bin/bash`) nos três arquivos modificados nos Steps 6 e 7.

- [ ] **Step 12: Commit**

```bash
git add src/agentos/installation/profile.py src/agentos/launcher/cli.py src/agentos/installation/versions.py packaging/orin.spec
git add tests/unit/installation/test_profile_installer.py tests/unit/launcher/test_update_command.py
git add tests/unit/launcher/test_sqlite_runtime.py tests/unit/launcher/test_uninstall.py tests/unit/installation/test_versions.py
git commit -m "feat(installation): resolve the platform-correct installer script"
```

---

### Task 3: Mensagem acionável para lib de sistema faltando no Chromium

**Files:**
- Modify: `src/agentos/browser/conversation_worker.py:258-270` (`_launch_failure_message`)
- Test: `tests/unit/browser/test_conversation_worker.py` (estender, ~linha 883)

**Interfaces:**
- Consumes: nada.
- Produces: `_launch_failure_message(error: Exception) -> str` — mesma assinatura, ramo novo.

- [ ] **Step 1: Escreva o teste que falha**

Em `tests/unit/browser/test_conversation_worker.py`, logo depois de `test_launch_failure_message_distinguishes_missing_chromium_from_other_errors`:

```python
def test_launch_failure_message_explains_a_missing_system_library() -> None:
    error = Exception(
        "playwright._impl._errors.TargetClosedError: BrowserType.launch: Target page, context or browser has been closed\n"
        "[pid=2661][err] .../chrome-headless-shell: error while loading shared libraries: libnspr4.so: cannot open shared object file: No such file or directory"
    )

    message = conversation_worker._launch_failure_message(error)

    assert "bibliotecas de sistema" in message
    assert "apt install" in message
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/browser/test_conversation_worker.py::test_launch_failure_message_explains_a_missing_system_library -v`
Expected: FAIL — cai no ramo genérico `f"browser engine failed to start: {type(error).__name__}"`, que não contém "bibliotecas de sistema" nem "apt install".

- [ ] **Step 3: Implemente**

Em `src/agentos/browser/conversation_worker.py`, dentro de `_launch_failure_message`:

```python
def _launch_failure_message(error: Exception) -> str:
    """Distinguish "Chromium is not provisioned" and "system libraries are
    missing" from any other launch failure.

    ``playwright_available()`` only checks that the Python package is
    installed; the Chromium binary itself is a separate download
    (``scripts/install-browser.ps1``). Without this, a package-present,
    binary-absent install spawns a process per turn that immediately dies,
    and the caller only ever sees an opaque timeout.

    On Linux, a *present* Chromium binary can still fail to start because a
    shared library it links against (libnss3, libasound2, ...) is missing
    from the host. Playwright's own exception text includes the loader's
    exact "error while loading shared libraries" line, so this is detected
    the same way as the missing-binary case rather than left as an opaque
    ``TargetClosedError``.
    """
    text = str(error)
    if "Executable doesn't exist" in text or "playwright install" in text.lower():
        return "Chromium is not provisioned for the isolated browser. Run scripts/install-browser.ps1 (or `python -m playwright install chromium`) and try again."
    if "error while loading shared libraries" in text:
        return (
            "O motor de navegador não conseguiu iniciar por faltar bibliotecas de sistema. "
            "Em Debian/Ubuntu, rode: sudo apt install libnss3 libasound2t64 (ou libasound2 em "
            "versões mais antigas) e tente de novo."
        )
    return f"browser engine failed to start: {type(error).__name__}"
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/browser/test_conversation_worker.py -v`
Expected: PASS em todos, incluindo o teste novo e o teste existente de binário ausente (a ordem dos `if` importa: o teste existente usa um texto que não contém "error while loading shared libraries", então não é afetado).

- [ ] **Step 5: Verifique a lista de pacotes contra o binário completo (não só o headless-shell)**

O spike original só testou `chrome-headless-shell`. Confirme a lista real contra o binário `chrome` completo, que é o que o produto de fato usa:

```bash
wsl.exe -d Ubuntu -e bash -lc "source \$HOME/.local/bin/env && cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && TMPDIR=/tmp uv run playwright install chromium 2>&1 | tail -5 && ldd ~/.cache/ms-playwright/chromium-*/chrome-linux/chrome 2>&1 | grep 'not found'"
```

Expected: a lista de `.so` ausentes. Se divergir da lista já usada na mensagem (`libnss3`, `libasound2t64`/`libasound2`), atualize a string em `_launch_failure_message` e o teste do Step 1 para refletir o que foi observado de verdade, não a suposição do spike original.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/browser/conversation_worker.py tests/unit/browser/test_conversation_worker.py
git commit -m "feat(browser): explain a missing system library instead of an opaque launch failure"
```

---

### Task 4: Manifesto de release multi-plataforma

**Files:**
- Create: `scripts/merge_release_manifest.py`
- Test: `tests/unit/scripts/test_merge_release_manifest.py` (criar; crie também `tests/unit/scripts/__init__.py` se o diretório não existir)
- Modify: `packaging/release.json.example`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `merge_manifest(windows: dict, linux: dict) -> dict`, usada pela Task 9 (workflow de release) para montar o `release.json` final. `main(argv)` como CLI (`python scripts/merge_release_manifest.py <windows.json> <linux.json> <release_url> > release.json`), usada diretamente pelo `release.yml`.

- [ ] **Step 1: Escreva o teste que falha**

Verifique primeiro se `tests/unit/scripts/` já existe:

Run: `ls tests/unit/scripts 2>&1`

Se não existir, crie `tests/unit/scripts/__init__.py` vazio.

Crie `tests/unit/scripts/test_merge_release_manifest.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

from scripts.merge_release_manifest import merge_manifest


def test_merge_keeps_windows_fields_flat_for_backward_compatibility():
    windows = {"version": "0.3.0", "archive_url": "https://x/Orin-0.3.0-windows-x64.zip", "archive_sha256": "a" * 64}
    linux = {"archive_url": "https://x/Orin-0.3.0-linux-x64.tar.gz", "archive_sha256": "b" * 64}

    manifest = merge_manifest(windows, linux, release_url="https://x/releases/tag/v0.3.0")

    assert manifest["version"] == "0.3.0"
    assert manifest["archive_url"] == windows["archive_url"]
    assert manifest["archive_sha256"] == windows["archive_sha256"]
    assert manifest["release_url"] == "https://x/releases/tag/v0.3.0"


def test_merge_nests_linux_under_platforms():
    windows = {"version": "0.3.0", "archive_url": "https://x/win.zip", "archive_sha256": "a" * 64}
    linux = {"archive_url": "https://x/Orin-0.3.0-linux-x64.tar.gz", "archive_sha256": "b" * 64}

    manifest = merge_manifest(windows, linux, release_url="https://x/releases/tag/v0.3.0")

    assert manifest["platforms"]["linux-x64"] == {
        "archive_url": "https://x/Orin-0.3.0-linux-x64.tar.gz",
        "archive_sha256": "b" * 64,
    }
    # A raiz do manifesto não ganha nenhuma chave nova além de "platforms":
    # um install.ps1 já instalado só sabe procurar as quatro chaves originais.
    assert set(manifest.keys()) == {"version", "archive_url", "archive_sha256", "release_url", "platforms"}


def test_merge_rejects_a_windows_manifest_missing_required_fields():
    import pytest

    with pytest.raises(ValueError, match="archive_sha256"):
        merge_manifest({"version": "0.3.0", "archive_url": "https://x/win.zip"}, {"archive_url": "https://x/l.tar.gz", "archive_sha256": "b" * 64}, release_url="https://x")


def test_cli_writes_the_merged_manifest_to_stdout(tmp_path: Path):
    windows_file = tmp_path / "windows.json"
    linux_file = tmp_path / "linux.json"
    windows_file.write_text(json.dumps({"version": "0.3.0", "archive_url": "https://x/win.zip", "archive_sha256": "a" * 64}), encoding="utf-8")
    linux_file.write_text(json.dumps({"archive_url": "https://x/l.tar.gz", "archive_sha256": "b" * 64}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/merge_release_manifest.py", str(windows_file), str(linux_file), "https://x/releases/tag/v0.3.0"],
        capture_output=True, text=True, check=True,
    )

    output = json.loads(result.stdout)
    assert output["version"] == "0.3.0"
    assert output["platforms"]["linux-x64"]["archive_url"] == "https://x/l.tar.gz"
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/scripts/test_merge_release_manifest.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.merge_release_manifest'` (ou `No module named 'scripts'` se `scripts/` não tiver `__init__.py` — não é necessário criá-lo, `pytest` com `pythonpath = ["src", "."]` já configurado em `pyproject.toml` resolve `scripts.merge_release_manifest` como módulo solto se o arquivo existir; confirme rodando o teste depois do Step 3).

- [ ] **Step 3: Implemente**

Crie `scripts/merge_release_manifest.py`:

```python
"""Fold the Windows and Linux build outputs into one release manifest.

The flat top-level fields (``archive_url``, ``archive_sha256``) keep
representing Windows exactly as they always have: an ``install.ps1`` already
sitting on someone's machine only ever reads those four names, and it has no
way to learn about a ``platforms`` key it was written before. Breaking that
would silently break ``orin update`` for every existing Windows install the
next time this script runs. Linux — which never existed in the manifest
before this script did — is added purely as a new nested key, never as a
replacement for the flat shape.
"""
from __future__ import annotations

import json
import sys

_REQUIRED_WINDOWS_FIELDS = ("version", "archive_url", "archive_sha256")
_REQUIRED_LINUX_FIELDS = ("archive_url", "archive_sha256")


def merge_manifest(windows: dict, linux: dict, *, release_url: str) -> dict:
    for field in _REQUIRED_WINDOWS_FIELDS:
        if not windows.get(field):
            raise ValueError(f"windows manifest is missing '{field}'")
    for field in _REQUIRED_LINUX_FIELDS:
        if not linux.get(field):
            raise ValueError(f"linux manifest is missing '{field}'")
    return {
        "version": windows["version"],
        "archive_url": windows["archive_url"],
        "archive_sha256": windows["archive_sha256"],
        "release_url": release_url,
        "platforms": {
            "linux-x64": {
                "archive_url": linux["archive_url"],
                "archive_sha256": linux["archive_sha256"],
            },
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: merge_release_manifest.py <windows.json> <linux.json> <release_url>", file=sys.stderr)
        return 2
    windows_path, linux_path, release_url = argv[1], argv[2], argv[3]
    with open(windows_path, encoding="utf-8") as handle:
        windows = json.load(handle)
    with open(linux_path, encoding="utf-8") as handle:
        linux = json.load(handle)
    manifest = merge_manifest(windows, linux, release_url=release_url)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/scripts/test_merge_release_manifest.py -v`
Expected: PASS, 4 testes.

- [ ] **Step 5: Verifique com PowerShell real que `install.ps1` continua lendo o manifesto novo**

Este é o ponto mais importante do design: um `install.ps1` já instalado precisa continuar funcionando sem mudança alguma quando o manifesto ganhar `platforms`. Prove isso rodando a mesma extração de campos que `Get-Manifest`/o corpo do script fazem:

```powershell
$json = '{"version":"0.3.0","archive_url":"https://x/win.zip","archive_sha256":"' + ('a' * 64) + '","release_url":"https://x/tag/v0.3.0","platforms":{"linux-x64":{"archive_url":"https://x/l.tar.gz","archive_sha256":"' + ('b' * 64) + '"}}}'
$manifest = $json | ConvertFrom-Json
foreach ($property in 'version', 'archive_url', 'archive_sha256') {
    if ([string]::IsNullOrWhiteSpace([string]$manifest.$property)) { throw "Release manifest is missing '$property'." }
}
Write-Host "version=$($manifest.version) archive_url=$($manifest.archive_url)"
```

Expected: imprime `version=0.3.0 archive_url=https://x/win.zip` sem lançar exceção — prova que `ConvertFrom-Json` e o acesso por propriedade nomeada (exatamente o que `install.ps1` já faz hoje) ignoram a chave `platforms` desconhecida sem quebrar.

- [ ] **Step 6: Atualize o exemplo de manifesto**

Em `packaging/release.json.example`, substitua o conteúdo por:

```json
{
  "version": "0.1.0",
  "archive_url": "https://github.com/carlos-edu2367/orin/releases/download/v0.1.0/Orin-0.1.0-windows-x64.zip",
  "archive_sha256": "replace-with-the-sha256-of-the-windows-zip",
  "release_url": "https://github.com/carlos-edu2367/orin/releases/tag/v0.1.0",
  "platforms": {
    "linux-x64": {
      "archive_url": "https://github.com/carlos-edu2367/orin/releases/download/v0.1.0/Orin-0.1.0-linux-x64.tar.gz",
      "archive_sha256": "replace-with-the-sha256-of-the-linux-tarball"
    }
  }
}
```

- [ ] **Step 7: Commit**

```bash
git add scripts/merge_release_manifest.py tests/unit/scripts/test_merge_release_manifest.py packaging/release.json.example
git commit -m "feat(release): merge Windows and Linux artifacts into one backward-compatible manifest"
```

---
### Task 5: `install.sh`

**Files:**
- Create: `install.sh`

**Interfaces:**
- Consumes: o formato de manifesto da Task 4 (`archive_url`/`archive_sha256` para Windows, `platforms.linux-x64.*` para Linux).
- Produces: um script `install.sh` funcionalmente equivalente a `install.ps1`, aceitando `--version`, `--force`, `--uninstall`, `--wait-for-pid`, `--no-desktop-shortcut`, e duas variáveis de ambiente: `ORIN_RELEASE_REPOSITORY` (mesmo nome que `versions.py:21` já usa) e `ORIN_RELEASE_BASE_URL` (nova — necessária para testar o script contra um servidor local, já que `ORIN_RELEASE_REPOSITORY` sozinha só troca o `owner/repo` dentro de uma URL que continua fixada em `github.com`).

Este script não é testável por `pytest` — é bash puro. A verificação é execução real em WSL contra um servidor HTTP local, espelhando como o spike já provou o Chromium.

- [ ] **Step 1: Escreva `install.sh`**

Crie `install.sh` na raiz do repositório:

```bash
#!/usr/bin/env bash
# Distribution installer for a packaged Orin release on Linux.
#
# Mirrors install.ps1's flow (fetch manifest, download, verify SHA-256,
# stage, promote, shim, offer a launcher entry) using the Linux-native
# equivalents: ~/.local/bin instead of a registry PATH edit, and a
# freedesktop.org .desktop file instead of a .lnk shortcut.
set -euo pipefail

REPOSITORY="${ORIN_RELEASE_REPOSITORY:-carlos-edu2367/orin}"
# Overridable purely for testing against a local server. Production installs
# never set this. The origin-pin check below still only trusts whatever this
# resolves to -- overriding it points the *whole* flow (fetch and origin pin
# together) at a different base; it is not a way to accept an archive from an
# untrusted host while still claiming to trust the real one.
BASE_URL="${ORIN_RELEASE_BASE_URL:-https://github.com/$REPOSITORY/releases}"
PROGRAMS_ROOT="$HOME/.local/share/Orin/versions"
BIN_ROOT="$HOME/.local/bin"
SHIM="$BIN_ROOT/orin"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/orin-desktop.desktop"

VERSION="latest"
FORCE=0
UNINSTALL=0
WAIT_FOR_PID=0
NO_DESKTOP_SHORTCUT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --wait-for-pid) WAIT_FOR_PID="$2"; shift 2 ;;
    --no-desktop-shortcut) NO_DESKTOP_SHORTCUT=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

fetch_manifest() {
  local asset
  if [ "$VERSION" = "latest" ]; then
    asset="latest/download/release.json"
  else
    local normalized="${VERSION#v}"
    if ! [[ "$normalized" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
      echo "Version must use semantic version format, for example 0.1.0." >&2
      exit 1
    fi
    asset="download/v$normalized/release.json"
  fi
  curl -fsSL "$BASE_URL/$asset"
}

if [ "$UNINSTALL" = "1" ]; then
  if [ "$FORCE" != "1" ]; then
    read -r -p "Completely remove Orin, including all local data and configuration? [y/N] " answer
    case "$answer" in
      y|Y|yes|YES) ;;
      *) exit 0 ;;
    esac
  fi
  rm -f "$SHIM" "$DESKTOP_FILE"
  # Deferred removal: wait for the running instance's pid to exit (it may be
  # the process that invoked this script), then remove the versioned install
  # and its state directory. Backgrounded so this script returns immediately,
  # mirroring install.ps1's Start-DeferredRemoval.
  (
    if [ "$WAIT_FOR_PID" -gt 0 ] 2>/dev/null; then
      while kill -0 "$WAIT_FOR_PID" 2>/dev/null; do sleep 1; done
    fi
    for _ in $(seq 1 180); do
      rm -rf "$PROGRAMS_ROOT" "$HOME/.local/share/orin"
      [ -d "$PROGRAMS_ROOT" ] || [ -d "$HOME/.local/share/orin" ] || exit 0
      sleep 1
    done
  ) >/dev/null 2>&1 &
  disown
  echo "Orin removal was scheduled. The runtime, local data and configuration will be removed after Orin exits."
  exit 0
fi

manifest_json="$(fetch_manifest)"
install_version="$(echo "$manifest_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
if ! [[ "$install_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
  echo "Release manifest contains an invalid version." >&2
  exit 1
fi
archive_url="$(echo "$manifest_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("platforms",{}).get("linux-x64",{}).get("archive_url",""))')"
archive_sha256="$(echo "$manifest_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("platforms",{}).get("linux-x64",{}).get("archive_sha256",""))')"
if [ -z "$archive_url" ] || [ -z "$archive_sha256" ]; then
  echo "Release manifest is missing a linux-x64 build for this version." >&2
  exit 1
fi
if [[ "$archive_url" != "$BASE_URL/download/"* ]]; then
  echo "Release archive URL must belong to the official Orin release." >&2
  exit 1
fi
if ! [[ "$archive_sha256" =~ ^[A-Fa-f0-9]{64}$ ]]; then
  echo "Release manifest contains an invalid SHA-256." >&2
  exit 1
fi

target="$PROGRAMS_ROOT/$install_version"
staging="$target.staging"
download="$(mktemp -t "orin-$install_version-XXXXXX.tar.gz")"

mkdir -p "$PROGRAMS_ROOT" "$BIN_ROOT"
rm -rf "$staging"
cleanup() { rm -f "$download"; rm -rf "$staging"; }
trap cleanup EXIT

curl -fsSL "$archive_url" -o "$download"
actual_sha256="$(sha256sum "$download" | cut -d' ' -f1)"
expected_sha256="$(echo "$archive_sha256" | tr '[:upper:]' '[:lower:]')"
if [ "$actual_sha256" != "$expected_sha256" ]; then
  echo "Downloaded release hash does not match release.json." >&2
  exit 1
fi
mkdir -p "$staging"
tar -xzf "$download" -C "$staging"
runtime="$staging/resources/runtime/orin"
desktop="$staging/Orin Desktop"
if [ ! -f "$runtime" ] || [ ! -f "$desktop" ]; then
  echo "Release archive does not contain the required Orin runtime." >&2
  exit 1
fi
chmod +x "$runtime" "$desktop"
"$runtime" --version >/dev/null

if [ -d "$target" ]; then
  if [ "$FORCE" != "1" ]; then
    echo "Orin $install_version is already installed. Use --force to reinstall it." >&2
    exit 1
  fi
  rm -rf "$target"
fi
mv "$staging" "$target"
trap - EXIT
rm -f "$download"

current="$PROGRAMS_ROOT/current"
rm -f "$current"
ln -s "$target" "$current"

cat > "$SHIM" <<SHIM_EOF
#!/usr/bin/env bash
exec "$PROGRAMS_ROOT/current/resources/runtime/orin" "\$@"
SHIM_EOF
chmod +x "$SHIM"

case ":$PATH:" in
  *":$BIN_ROOT:"*) ;;
  *) echo "Add this to your shell config to use the 'orin' command: export PATH=\"$BIN_ROOT:\$PATH\"" ;;
esac

update_desktop_entry=0
if [ ! -f "$DESKTOP_FILE" ] && [ "$NO_DESKTOP_SHORTCUT" != "1" ]; then
  read -r -p "Do you want to add Orin Desktop to your application menu? [Y/n] " answer
  case "$answer" in
    n|N|no|NO) ;;
    *) update_desktop_entry=1 ;;
  esac
elif [ -f "$DESKTOP_FILE" ]; then
  update_desktop_entry=1
fi
if [ "$update_desktop_entry" = "1" ]; then
  mkdir -p "$APPS_DIR"
  cat > "$DESKTOP_FILE" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=Orin Desktop
Exec="$PROGRAMS_ROOT/current/resources/runtime/orin" --desktop
Icon=$PROGRAMS_ROOT/current/resources/runtime/_internal/web/orin-logo.png
Terminal=false
Categories=Development;
DESKTOP_EOF
fi

echo "Orin $install_version is installed. Run 'orin' or find Orin Desktop in your application menu."
```

- [ ] **Step 2: Dê permissão de execução e valide a sintaxe**

```bash
chmod +x install.sh
wsl.exe -d Ubuntu -e bash -lc "cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && bash -n install.sh && echo 'sintaxe OK'"
```

Expected: `sintaxe OK`.

- [ ] **Step 3: Monte um pacote falso e um servidor local, e instale de verdade**

Nenhum mock — um tarball real, um `http.server` real, o script real. Use um `HOME` isolado para não tocar no perfil real do usuário do WSL:

```bash
wsl.exe -d Ubuntu -e bash -lc '
set -e
export FAKE_HOME=/tmp/orin-install-test/fakehome
rm -rf /tmp/orin-install-test && mkdir -p "$FAKE_HOME"
mkdir -p /tmp/orin-install-test/server/download/v9.9.9
cd /tmp/orin-install-test/server/download/v9.9.9

mkdir -p pkg/resources/runtime
printf "#!/usr/bin/env bash\nif [ \"\$1\" = \"--version\" ]; then echo \"orin 9.9.9 (fake)\"; fi\n" > pkg/resources/runtime/orin
chmod +x pkg/resources/runtime/orin
printf "#!/usr/bin/env bash\necho \"Orin Desktop (fake)\"\n" > "pkg/Orin Desktop"
chmod +x "pkg/Orin Desktop"
tar -czf Orin-9.9.9-linux-x64.tar.gz -C pkg .
sha256=$(sha256sum Orin-9.9.9-linux-x64.tar.gz | cut -d" " -f1)

cat > release.json <<EOF
{"version":"9.9.9","platforms":{"linux-x64":{"archive_url":"http://127.0.0.1:8934/download/v9.9.9/Orin-9.9.9-linux-x64.tar.gz","archive_sha256":"$sha256"}}}
EOF

cd /tmp/orin-install-test/server
python3 -m http.server 8934 >/tmp/orin-install-test/server.log 2>&1 &
echo $! > /tmp/orin-install-test/server.pid
sleep 1

cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin
HOME="$FAKE_HOME" ORIN_RELEASE_BASE_URL="http://127.0.0.1:8934" ./install.sh --version 9.9.9 --no-desktop-shortcut

echo "--- shim ---"
cat "$FAKE_HOME/.local/bin/orin"
echo "--- versao instalada ---"
"$FAKE_HOME/.local/bin/orin" --version
echo "--- symlink current ---"
readlink -f "$FAKE_HOME/.local/share/Orin/versions/current"

kill $(cat /tmp/orin-install-test/server.pid) 2>/dev/null || true
'
```

Expected:
- O shim em `$FAKE_HOME/.local/bin/orin` existe e aponta para `$PROGRAMS_ROOT/current/resources/runtime/orin`.
- `orin --version` imprime `orin 9.9.9 (fake)`.
- `current` resolve para `$FAKE_HOME/.local/share/Orin/versions/9.9.9`.

Se qualquer passo falhar, corrija `install.sh` e rode de novo — este script nunca foi executado de verdade antes desta task.

- [ ] **Step 4: Verifique `--force` (reinstalação) e a recusa sem `--force`**

Na mesma sessão do WSL (servidor ainda de pé, ou reinicie-o com o mesmo comando do Step 3):

```bash
wsl.exe -d Ubuntu -e bash -lc '
export FAKE_HOME=/tmp/orin-install-test/fakehome
cd /tmp/orin-install-test/server
python3 -m http.server 8934 >/tmp/orin-install-test/server.log 2>&1 &
echo $! > /tmp/orin-install-test/server.pid
sleep 1

cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin
echo "--- reinstalar sem --force (deve recusar) ---"
if HOME="$FAKE_HOME" ORIN_RELEASE_BASE_URL="http://127.0.0.1:8934" ./install.sh --version 9.9.9 --no-desktop-shortcut; then
  echo "FALHA: deveria ter recusado"
else
  echo "OK: recusou como esperado"
fi

echo "--- reinstalar com --force (deve aceitar) ---"
HOME="$FAKE_HOME" ORIN_RELEASE_BASE_URL="http://127.0.0.1:8934" ./install.sh --version 9.9.9 --force --no-desktop-shortcut

kill $(cat /tmp/orin-install-test/server.pid) 2>/dev/null || true
'
```

Expected: a primeira tentativa imprime "Orin 9.9.9 is already installed. Use --force to reinstall it." e sai com código de erro (capturado pelo `else`); a segunda conclui normalmente.

- [ ] **Step 5: Verifique a entrada de menu (`.desktop`) e o `--uninstall`**

```bash
wsl.exe -d Ubuntu -e bash -lc '
export FAKE_HOME=/tmp/orin-install-test/fakehome
cd /tmp/orin-install-test/server
python3 -m http.server 8934 >/tmp/orin-install-test/server.log 2>&1 &
echo $! > /tmp/orin-install-test/server.pid
sleep 1

cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin
echo y | HOME="$FAKE_HOME" ORIN_RELEASE_BASE_URL="http://127.0.0.1:8934" ./install.sh --version 9.9.9 --force
echo "--- .desktop ---"
cat "$FAKE_HOME/.local/share/applications/orin-desktop.desktop"

kill $(cat /tmp/orin-install-test/server.pid) 2>/dev/null || true

echo "--- uninstall ---"
HOME="$FAKE_HOME" ./install.sh --uninstall --force --wait-for-pid 0
sleep 3
ls "$FAKE_HOME/.local/share/Orin/versions" 2>&1 || echo "OK: diretorio de versoes removido"
'
```

Expected: o `.desktop` contém `Name=Orin Desktop` e um `Exec=` apontando para `resources/runtime/orin --desktop`; depois do uninstall (que espera até 180s em loops de 1s, mas com `--wait-for-pid 0` pula a espera do PID e vai direto para a remoção), o diretório de versões deixa de existir dentro de poucos segundos.

- [ ] **Step 6: Commit**

```bash
git add install.sh
git commit -m "feat(installation): add the Linux distribution installer"
```

---

### Task 6: CI de PR roda em Ubuntu também

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/unit/api/test_local_workspace_api.py` (`test_attach_requires_acknowledgement_only_for_a_risky_folder` — falha hoje em Linux, precisa ser corrigido antes de o job `ubuntu-latest` nascer verde)

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: o job `backend` passa a rodar em `windows-latest` e `ubuntu-latest`.

Das 4 falhas originais do spike, só a do zumbi (Task 1) ganhou uma correção de produto. As outras três eram fragilidade de teste/ambiente: `TMPDIR` ausente e Chromium não provisionado resolvem sozinhos rodando dentro do próprio job de CI (que já configura `PLAYWRIGHT_BROWSERS_PATH`/instala o Chromium, e cujo ambiente de execução real ainda não foi confirmado quanto a `TMPDIR` — trate isso no Step 3). A falha de `/` não-gravável, porém, **nunca foi corrigida** em nenhuma task anterior — sem o Step 1 abaixo, o job `ubuntu-latest` nasceria com um teste vermelho.

- [ ] **Step 1: Corrija o teste que assume que a raiz do drive é gravável**

`test_attach_requires_acknowledgement_only_for_a_risky_folder` usa `Path(tmp_path.anchor)` (a raiz do drive) como exemplo de "pasta ampla, arriscada, mas ainda assim gravável" — verdade no Windows, falsa no Linux (`/` pertence a `root`). Olhando `classify_risk()` em `src/agentos/local_workspace/paths.py:57`, existe uma segunda classificação de risco igualmente ampla e que não depende de escrever na raiz do sistema: `"home_root"`, disparada quando o caminho é exatamente `home` (o parâmetro que `inspect_folder` recebe). `gateway.py` passa `home=Path.home()` ao vivo — dá para redirecionar isso com `monkeypatch` para uma pasta dentro de `tmp_path`, sempre gravável em qualquer SO, sem tocar no home real de quem roda o teste.

Rode primeiro para confirmar a falha atual (só reproduz em Linux; em Windows já passa hoje):

```bash
wsl.exe -d Ubuntu -e bash -lc "source \$HOME/.local/bin/env && cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && TMPDIR=/tmp uv run pytest -q tests/unit/api/test_local_workspace_api.py::test_attach_requires_acknowledgement_only_for_a_risky_folder -v"
```

Expected: FAIL — `422 == 409`.

Em `tests/unit/api/test_local_workspace_api.py`, acrescente `import pytest` ao topo do arquivo (não está importado hoje, e a anotação `monkeypatch: pytest.MonkeyPatch` abaixo precisa dele):

```python
from pathlib import Path

import pytest

from fastapi.testclient import TestClient
```

Em seguida, substitua o corpo de `test_attach_requires_acknowledgement_only_for_a_risky_folder`:

```python
def test_attach_requires_acknowledgement_only_for_a_risky_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broad folder stays possible; it just cannot happen by accident."""
    client, store = _client(tmp_path)
    plain = tmp_path / "site"
    plain.mkdir()

    ok = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i2"}, json={"path": str(plain), "acknowledged_risk": False})
    assert ok.status_code == 200
    assert store.root_for("chat_a", "owner") == str(plain.resolve())

    # A drive root is not writable by a non-root user on POSIX (this used to
    # point at tmp_path.anchor, i.e. "/" on Linux -- true only on Windows).
    # "home_root" is an equally broad risk classification that stays
    # writable everywhere, so redirect Path.home() at a tmp_path folder
    # instead of touching whoever actually runs this test's real home.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    refused = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i3"}, json={"path": str(fake_home), "acknowledged_risk": False})
    assert refused.status_code == 409
    assert store.root_for("chat_a", "owner") == str(plain.resolve())

    accepted = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i4"}, json={"path": str(fake_home), "acknowledged_risk": True})
    assert accepted.status_code == 200
    assert store.root_for("chat_a", "owner") == str(fake_home.resolve())
```

Rode em Windows e em Linux:

```bash
.venv/Scripts/python.exe -m pytest tests/unit/api/test_local_workspace_api.py -v
```

```bash
wsl.exe -d Ubuntu -e bash -lc "source \$HOME/.local/bin/env && cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && TMPDIR=/tmp uv run pytest -q tests/unit/api/test_local_workspace_api.py -v"
```

Expected: PASS nos dois.

- [ ] **Step 2: Vire o job `backend` numa matriz**

Em `.github/workflows/ci.yml`, troque:

```yaml
jobs:
  backend:
    runs-on: windows-latest
    steps:
```

por:

```yaml
jobs:
  backend:
    strategy:
      fail-fast: false
      matrix:
        os: [windows-latest, ubuntu-latest]
    runs-on: ${{ matrix.os }}
    steps:
```

Os passos existentes (`uv sync --frozen --all-groups`, `uv run playwright install chromium`, `uv run pytest -q --tb=short`) não mudam — `uv` já abstrai a diferença de shell entre os dois runners. `fail-fast: false` garante que uma falha específica de uma plataforma não cancele a outra, o que importa justamente para ver os dois resultados quando algo divergir entre SOs.

O job `frontend` permanece só `windows-latest` — Vite/TS/React são ferramentas cross-platform por natureza; o risco real de portabilidade do Orin está no backend Python e no empacotamento nativo, não no toolchain Node.

- [ ] **Step 3: Verifique a sintaxe do YAML**

```powershell
Get-Content .github/workflows/ci.yml -Raw | python -c "import sys, yaml; yaml.safe_load(sys.stdin.read()); print('YAML valido')"
```

Se `pyyaml` não estiver disponível no ambiente de desenvolvimento, use `.venv/Scripts/python.exe` (já tem `pyyaml` como dependência transitiva de `alembic`/outros — confirme com `.venv/Scripts/python.exe -c "import yaml"`; se falhar, instale com `uv pip install pyyaml` só para esta checagem local, sem adicionar ao `pyproject.toml`).

Expected: `YAML valido`.

- [ ] **Step 4: Reproduza localmente o que o job `ubuntu-latest` vai rodar**

Isto não substitui rodar de verdade no GitHub Actions, mas prova que os comandos funcionam num Ubuntu real antes de gastar minutos de CI:

```bash
wsl.exe -d Ubuntu -e bash -lc "source \$HOME/.local/bin/env && cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && TMPDIR=/tmp uv sync --frozen --all-groups && TMPDIR=/tmp uv run playwright install chromium && TMPDIR=/tmp uv run pytest -q --tb=short 2>&1 | tail -20"
```

Expected: todos passando, sem falha nenhuma — a do zumbi (Task 1), a de `/` não-gravável (Step 1) e as duas de ambiente (`TMPDIR`, Chromium) já resolvidas.

**Ressalva honesta:** este comando roda com `TMPDIR=/tmp` explícito, porque foi assim que o spike original confirmou a causa da falha de `TMPDIR`. Não está confirmado se o runner `ubuntu-latest` real do GitHub Actions já define `TMPDIR` por padrão — só que `/tmp` existe e é gravável nele, o que é o que a variável precisa apontar. Se o job real do GitHub Actions falhar nesse teste especificamente por `TMPDIR` ausente (algo que só se descobre rodando de verdade, per a nota abaixo), a correção é uma linha em `ci.yml`: `env: { TMPDIR: /tmp }` no job `backend`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml tests/unit/api/test_local_workspace_api.py
git commit -m "ci: run the backend test suite on Ubuntu as well as Windows"
```

**Nota importante:** este commit por si só não *prova* que o job `ubuntu-latest` funciona no GitHub Actions de verdade — isso só é confirmado quando o commit é enviado (push) e o workflow roda. Peça autorização explícita ao usuário antes de dar `git push`; não é uma ação que este plano executa sozinho.

---

### Task 7: `build-linux.sh` — provar PyInstaller e electron-builder em Linux de verdade

**Files:**
- Create: `scripts/build-linux.sh`
- Modify: `desktop/package.json` (bloco `linux`, novo script `build:dir:linux`)

**Interfaces:**
- Consumes: `packaging/orin.spec` já corrigido (Task 2), `install.sh` (Task 5, embutido pelo spec corrigido).
- Produces: um diretório `desktop/dist/linux-unpacked/` contendo `Orin Desktop` e `resources/runtime/orin` lado a lado — o mesmo layout que a Task 8 empacota em tar.gz.

**Isto é o maior risco não verificado do spec.** PyInstaller congelando em Linux e `electron-builder --linux dir` nunca foram executados nesta investigação — só lidos. Esta task fecha essa lacuna com execução real em WSL, não com leitura de código.

- [ ] **Step 1: Ajuste `desktop/package.json`**

Em `desktop/package.json`, dentro de `scripts`, acrescente:

```json
    "build:dir:linux": "electron-builder --linux dir"
```

E dentro de `build`, depois do bloco `win`:

```json
    "linux": {
      "target": ["dir"],
      "icon": "electron/assets/orin-logo.png",
      "executableName": "Orin Desktop",
      "category": "Development"
    },
```

`executableName: "Orin Desktop"` é obrigatório: o padrão do `electron-builder` em Linux é kebab-case (`orin-desktop`), e o código POSIX do launcher já espera literalmente `"Orin Desktop"` como nome de binário (`src/agentos/launcher/desktop.py:121`).

- [ ] **Step 2: Instale Node no WSL**

```bash
wsl.exe -d Ubuntu -e bash -lc "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - 2>&1 | tail -10 && sudo apt-get install -y nodejs 2>&1 | tail -10 && node --version"
```

Se `sudo` pedir senha interativa (não há sudo sem senha nesta sessão, confirmado no spike original), rode este passo manualmente numa sessão interativa do WSL, ou instale Node via `nvm` (não exige root):

```bash
wsl.exe -d Ubuntu -e bash -lc "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash && source ~/.nvm/nvm.sh && nvm install 22 && node --version"
```

Expected: `v22.x.x` impresso.

- [ ] **Step 3: Escreva `scripts/build-linux.sh`**

Crie `scripts/build-linux.sh`:

```bash
#!/usr/bin/env bash
# Linux counterpart of scripts/build-windows.ps1. Freezes the Python runtime
# with PyInstaller, packages the Electron shell around it with
# electron-builder, and leaves an unpacked directory tree ready for
# package-release.sh to tar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
BROWSER_ROOT="$ROOT/build/playwright"

if [ ! -x "$PYTHON" ]; then
  echo "Create the development virtual environment (uv sync) before building a release." >&2
  exit 1
fi

cd "$ROOT"

npm ci --prefix frontend
npm run build --prefix frontend

export PLAYWRIGHT_BROWSERS_PATH="$BROWSER_ROOT"
"$PYTHON" -m playwright install chromium
export ORIN_PLAYWRIGHT_BROWSERS_PATH="$BROWSER_ROOT"

"$PYTHON" -m PyInstaller packaging/orin.spec --noconfirm --clean

frozen_runtime="$ROOT/dist/runtime"
chromium=$(find "$frozen_runtime" -type f -name chrome -path '*chrome-linux*' | head -n1)
if [ -z "$chromium" ]; then
  echo "Frozen runtime was built without a Chromium executable." >&2
  exit 1
fi
echo "Bundled Chromium: $chromium"

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  packaging_browser_path="${ORIN_PLAYWRIGHT_BROWSERS_PATH:-}"
  playwright_browser_path="${PLAYWRIGHT_BROWSERS_PATH:-}"
  unset ORIN_PLAYWRIGHT_BROWSERS_PATH PLAYWRIGHT_BROWSERS_PATH
  "$PYTHON" -m pytest -q tests/unit
  [ -n "$packaging_browser_path" ] && export ORIN_PLAYWRIGHT_BROWSERS_PATH="$packaging_browser_path"
  [ -n "$playwright_browser_path" ] && export PLAYWRIGHT_BROWSERS_PATH="$playwright_browser_path"
fi

cd desktop
npm ci
npm run build:dir:linux
cd "$ROOT"

scripts/package-release.sh
```

- [ ] **Step 4: Rode de verdade em WSL e observe onde quebra**

```bash
wsl.exe -d Ubuntu -e bash -lc "
source \$HOME/.local/bin/env
source ~/.nvm/nvm.sh 2>/dev/null || true
cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin
TMPDIR=/tmp uv sync --frozen --all-groups
chmod +x scripts/build-linux.sh
SKIP_TESTS=1 TMPDIR=/tmp ./scripts/build-linux.sh 2>&1 | tail -100
"
```

Expected: **desconhecido até rodar** — este é exatamente o gap que o spec marca como não verificado. Trate qualquer erro real como um achado a corrigir agora, não como algo a contornar:

- Se o PyInstaller falhar coletando algum binário nativo (`collect_dynamic_libs("pypdfium2")` é a linha mais provável de precisar de um `.so` que não existe no ambiente WSL nu), instale a dependência de sistema faltante no WSL e repita — e anote a dependência encontrada como um requisito de build documentado (não de runtime do usuário final).
- Se `electron-builder --linux dir` reclamar de um ícone (tamanho mínimo, formato), gere um `.png` maior a partir do `.ico` existente (`orin-logo.ico` já tem múltiplos tamanhos embutidos; extraia o maior com uma ferramenta como `icotool` ou re-exporte do design original) e ajuste o campo `icon` do Step 1.
- Se `Orin Desktop` não aparecer no diretório de saída com esse nome exato, o `executableName` do Step 1 não está sendo respeitado — confira a versão do `electron-builder` (`^26.0.12`) e a documentação da opção para essa versão específica.

Depois de qualquer correção, rode de novo até o script completar sem erro e produzir `desktop/dist/linux-unpacked/Orin Desktop` e `desktop/dist/linux-unpacked/resources/runtime/orin`.

- [ ] **Step 5: Verifique o binário congelado de verdade**

```bash
wsl.exe -d Ubuntu -e bash -lc "cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && 'desktop/dist/linux-unpacked/resources/runtime/orin' --version && 'desktop/dist/linux-unpacked/resources/runtime/orin' --help | head -5"
```

Expected: imprime a versão real do Orin (deve bater com `pyproject.toml`/`src/agentos/version.py`) e o texto de ajuda, sem exigir Python instalado no host — a prova de que o congelamento produziu um binário standalone de verdade.

- [ ] **Step 6: Commit**

```bash
git add desktop/package.json scripts/build-linux.sh
git commit -m "feat(build): freeze and package the Orin runtime for Linux"
```

---

### Task 8: `package-release.sh` — montar o tar.gz e os campos parciais do manifesto

**Files:**
- Create: `scripts/package-release.sh`

**Interfaces:**
- Consumes: `desktop/dist/linux-unpacked/` (produzido pela Task 7).
- Produces: `dist/Orin-<versão>-linux-x64.tar.gz` e `dist/linux-release.json` (formato `{"archive_name": "...", "archive_sha256": "..."}` — sem `archive_url`, porque a URL de download só existe depois do upload no GitHub Release; a Task 9 monta `archive_url` a partir de `archive_name` e do repositório/tag antes de chamar `merge_release_manifest.py`).

- [ ] **Step 1: Escreva `scripts/package-release.sh`**

Crie `scripts/package-release.sh`, espelhando `scripts/package-release.ps1`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(python3 -c 'import json; print(json.load(open("desktop/package.json"))["version"])')"
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
  echo "Version must use semantic version format, for example 0.1.0." >&2
  exit 1
fi

source="desktop/dist/linux-unpacked"
runtime="$source/resources/runtime/orin"
desktop="$source/Orin Desktop"
if [ ! -f "$runtime" ] || [ ! -f "$desktop" ]; then
  echo "The Electron directory package is incomplete. Run scripts/build-linux.sh first." >&2
  exit 1
fi

output="dist"
archive_name="Orin-$VERSION-linux-x64.tar.gz"
archive="$output/$archive_name"
mkdir -p "$output"
rm -f "$archive"
tar -czf "$archive" -C "$source" .
sha256="$(sha256sum "$archive" | cut -d' ' -f1)"

cat > "$output/linux-release.json" <<EOF
{
  "archive_name": "$archive_name",
  "archive_sha256": "$sha256"
}
EOF

echo "Release archive: $archive"
echo "Manifest fragment: $output/linux-release.json"
```

- [ ] **Step 2: Rode contra a saída real da Task 7**

```bash
wsl.exe -d Ubuntu -e bash -lc "cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && chmod +x scripts/package-release.sh && ./scripts/package-release.sh 0.9.9-test 2>&1"
```

Expected: `dist/Orin-0.9.9-test-linux-x64.tar.gz` e `dist/linux-release.json` existem. Confirme que o tar.gz extrai corretamente e reproduz o layout esperado:

```bash
wsl.exe -d Ubuntu -e bash -lc "cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && rm -rf /tmp/verify-tar && mkdir /tmp/verify-tar && tar -xzf dist/Orin-0.9.9-test-linux-x64.tar.gz -C /tmp/verify-tar && ls /tmp/verify-tar && ls /tmp/verify-tar/resources/runtime/orin && ls '/tmp/verify-tar/Orin Desktop'"
```

Expected: ambos os arquivos existem depois de extraído, confirmando que o tar.gz preserva o layout que `install.sh` espera.

- [ ] **Step 3: Commit**

```bash
git add scripts/package-release.sh
git commit -m "feat(build): assemble the Linux release archive"
```

---

### Task 9: `release.yml` — fan-in de duas plataformas

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `scripts/build-linux.sh` (Task 7), `scripts/package-release.sh` (Task 8), `scripts/merge_release_manifest.py` (Task 4), `scripts/build-windows.ps1`/`scripts/package-release.ps1` (já existentes, intocados).
- Produces: uma única release do GitHub por tag, com os artefatos de ambas as plataformas e um `release.json` fundido.

- [ ] **Step 1: Reestruture `release.yml` em três jobs**

Substitua o conteúdo de `.github/workflows/release.yml` por:

```yaml
name: Publish release

on:
  push:
    tags: ['v*']
  workflow_dispatch:
    inputs:
      version:
        description: Semantic version to publish, without v
        required: true
        type: string

permissions:
  contents: write

jobs:
  resolve-version:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.resolve.outputs.version }}
    steps:
      - uses: actions/checkout@v4
      - id: resolve
        shell: bash
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            version="${{ inputs.version }}"
          else
            version="${GITHUB_REF_NAME#v}"
          fi
          if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
            echo "Release version must be semantic version format." >&2
            exit 1
          fi
          package_version="$(python3 -c 'import json; print(json.load(open("desktop/package.json"))["version"])')"
          if [ "$package_version" != "$version" ]; then
            echo "Tag version $version does not match desktop/package.json version $package_version." >&2
            exit 1
          fi
          echo "version=$version" >> "$GITHUB_OUTPUT"

  build-windows:
    needs: resolve-version
    runs-on: windows-latest
    env:
      ORIN_RELEASE_VERSION: ${{ needs.resolve-version.outputs.version }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: |
            frontend/package-lock.json
            desktop/package-lock.json
      - name: Create isolated Python runtime
        run: uv sync --frozen --all-groups
      - name: Build packaged Windows release
        shell: pwsh
        run: .\scripts\build-windows.ps1 -SkipTests
      - name: Upload Windows artifact
        uses: actions/upload-artifact@v4
        with:
          name: windows-release
          path: |
            dist/Orin-${{ env.ORIN_RELEASE_VERSION }}-windows-x64.zip
            dist/release.json
            dist/install.ps1

  build-linux:
    needs: resolve-version
    runs-on: ubuntu-latest
    env:
      ORIN_RELEASE_VERSION: ${{ needs.resolve-version.outputs.version }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: |
            frontend/package-lock.json
            desktop/package-lock.json
      - name: Create isolated Python runtime
        run: uv sync --frozen --all-groups
      - name: Build packaged Linux release
        run: |
          chmod +x scripts/build-linux.sh scripts/package-release.sh
          SKIP_TESTS=1 scripts/build-linux.sh "$ORIN_RELEASE_VERSION"
      - name: Upload Linux artifact
        uses: actions/upload-artifact@v4
        with:
          name: linux-release
          path: |
            dist/Orin-${{ env.ORIN_RELEASE_VERSION }}-linux-x64.tar.gz
            dist/linux-release.json
          if-no-files-found: error
      - name: Upload install.sh
        uses: actions/upload-artifact@v4
        with:
          name: linux-installer
          path: install.sh

  publish:
    needs: [resolve-version, build-windows, build-linux]
    runs-on: ubuntu-latest
    env:
      GH_TOKEN: ${{ github.token }}
      ORIN_RELEASE_VERSION: ${{ needs.resolve-version.outputs.version }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: windows-release
          path: dist/windows
      - uses: actions/download-artifact@v4
        with:
          name: linux-release
          path: dist/linux
      - uses: actions/download-artifact@v4
        with:
          name: linux-installer
          path: dist/linux
      - name: Merge the release manifest
        run: |
          version="$ORIN_RELEASE_VERSION"
          release_url="https://github.com/${{ github.repository }}/releases/tag/v$version"
          # archive_name is the one source of truth for the tar.gz's filename
          # (set by package-release.sh); reading it here instead of
          # reconstructing the pattern keeps the naming convention defined
          # in exactly one place.
          python3 -c "
          import json
          fragment_in = json.load(open('dist/linux/linux-release.json'))
          fragment = {
              'archive_url': 'https://github.com/${{ github.repository }}/releases/download/v$version/' + fragment_in['archive_name'],
              'archive_sha256': fragment_in['archive_sha256'],
          }
          json.dump(fragment, open('dist/linux-release-final.json', 'w'))
          "
          python3 scripts/merge_release_manifest.py dist/windows/release.json dist/linux-release-final.json "$release_url" > dist/release.json
      - name: Verify the merged manifest
        run: |
          python3 -c "
          import hashlib, json
          manifest = json.load(open('dist/release.json'))
          assert manifest['version'] == '$ORIN_RELEASE_VERSION'
          windows_hash = hashlib.sha256(open('dist/windows/Orin-$ORIN_RELEASE_VERSION-windows-x64.zip', 'rb').read()).hexdigest()
          assert windows_hash == manifest['archive_sha256'], 'windows hash mismatch'
          linux_hash = hashlib.sha256(open('dist/linux/Orin-$ORIN_RELEASE_VERSION-linux-x64.tar.gz', 'rb').read()).hexdigest()
          assert linux_hash == manifest['platforms']['linux-x64']['archive_sha256'], 'linux hash mismatch'
          print('manifest OK')
          "
      - name: Publish GitHub release assets
        run: |
          tag="v$ORIN_RELEASE_VERSION"
          gh release create "$tag" \
            "dist/windows/Orin-$ORIN_RELEASE_VERSION-windows-x64.zip" \
            "dist/linux/Orin-$ORIN_RELEASE_VERSION-linux-x64.tar.gz" \
            dist/release.json \
            dist/windows/install.ps1 \
            dist/linux/install.sh \
            --title "Orin $ORIN_RELEASE_VERSION" --generate-notes
```

Note que `build-windows.ps1`/`package-release.ps1` (existentes, intocados) continuam escrevendo `dist/release.json` no formato antigo, plano — o job `publish` usa **esse mesmo arquivo** como a metade Windows do manifesto fundido, então nada no lado Windows do pipeline precisa saber que Linux existe.

- [ ] **Step 2: Verifique a sintaxe do YAML**

```powershell
Get-Content .github/workflows/release.yml -Raw | .venv/Scripts/python.exe -c "import sys, yaml; yaml.safe_load(sys.stdin.read()); print('YAML valido')"
```

Expected: `YAML valido`. Se `pyyaml` faltar, siga a mesma solução do Step 2 da Task 6.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: publish Windows and Linux artifacts under one merged manifest"
```

**Nota importante:** assim como a Task 6, este workflow só é provado de verdade quando uma tag `v*` é enviada e o pipeline roda no GitHub Actions de verdade — algo que exige a autorização explícita do usuário (é uma ação visível externamente: publica uma release pública). Não dispare isso sozinho.

---

## Verificação final

- [ ] `.venv/Scripts/python.exe -m pytest -q tests/unit` — suíte completa em Windows, zero regressão.
- [ ] `wsl.exe -d Ubuntu -e bash -lc "source \$HOME/.local/bin/env && cd /mnt/c/Users/User/Documents/GitHub/Carlos/pessoal/orin && TMPDIR=/tmp uv run pytest -q"` — suíte completa em Linux real, **as 4 falhas do spike original devem estar zeradas** (3 por correção de teste, 1 pela correção do zumbi).
- [ ] `install.sh` instala, reinstala com `--force`, cria a entrada de menu, e desinstala — tudo verificado com execução real na Task 5.
- [ ] `scripts/build-linux.sh` produz um `orin` congelado que roda `--version`/`--help` sem Python instalado — verificado com execução real na Task 7.
- [ ] O manifesto fundido preserva os campos planos do Windows inalterados — provado com `ConvertFrom-Json` real na Task 4.
- [ ] Nenhum teste que passava em Windows antes deste plano passou a falhar.
- [ ] `git push` e o disparo real dos workflows de CI/release ficam para quando o usuário autorizar explicitamente — não fazem parte da execução automática deste plano.
