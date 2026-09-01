# Release Linux do Orin — design

**Data:** 2026-09-01
**Estado:** design aprovado, aguardando plano de implementação
**Motivação:** o Orin hoje só é distribuído para Windows. Um spike textual seguido de
validação empírica em Ubuntu 24.04 real (via WSL) mostrou que a maior parte do trabalho é
empacotamento, não portabilidade — e revelou um bug de correção real que precisa ser
corrigido antes de qualquer release Linux.

## 1. Relação com os planos de distribuição já existentes

Este plano é **aditivo**. Os dois planos anteriores —
[2026-08-13-windows-standalone-distribution-plan.md](2026-08-13-windows-standalone-distribution-plan.md)
e [2026-08-13-desktop-cli-release-github-actions.md](2026-08-13-desktop-cli-release-github-actions.md) —
marcam macOS/Linux como fora de escopo explicitamente. A Fase A de ambos (SQLite local,
sem Docker/Redis/Postgres) já está implementada no código atual: `install.ps1` e
`build-windows.ps1` de hoje não mencionam nenhum desses serviços. Este design não
contradiz nenhuma decisão já tomada; estende o mesmo modelo para uma segunda plataforma.

## 2. Evidência do spike

### 2.1 Leitura estática (primeira rodada)

27 ramos de plataforma em `src/agentos`, todos com caminho POSIX correto. Só 3 pontos de
mão única, todos girando em torno de "qual script de instalador chamar" (seção 4).
`orin.spec` (PyInstaller) e o layout de diretórios não têm nada Windows-específico por
leitura. O maior risco identificado por leitura: o manifesto de release (`release.json`)
é de plataforma única, sem versionamento por SO.

### 2.2 Validação empírica (segunda rodada, Ubuntu 24.04 real via WSL)

Instalação de `uv` + Python 3.13 e `uv sync --frozen --all-groups` contra o projeto real,
seguido de `uv run pytest -q`:

- **2087 passed, 4 failed, 66 skipped** (baseline Windows: 2024 passed, 4 skipped).
- Das 4 falhas, 3 são fragilidade de teste (não bug de produto):
  - `TMPDIR` não setada no shell nu do WSL — passa com a variável setada.
  - Um teste assume que a raiz do drive (`tmp_path.anchor`) é gravável pelo usuário
    comum, verdade no Windows, falsa no Linux (`/` pertence a `root`).
  - Um teste de browser isolado precisa de Chromium provisionado (não instalado no
    ambiente de teste nu) — comportamento esperado, a própria mensagem de erro instrui
    a correção.
- **A quarta falha é um bug real de produção**, encontrado e confirmado com reprodução
  isolada (não apenas pelo teste): ver seção 3.

Também instalei e tentei rodar o Chromium do Playwright de verdade: faltam
`libnspr4.so`, `libnss3.so`, `libnssutil3.so`, `libasound.so.2` — mapeiam para os
pacotes Debian/Ubuntu `libnss3` e `libasound2`/`libasound2t64`. Confirma a fricção
prevista na leitura estática, agora com a lista exata.

**O que não foi verificado empiricamente:** PyInstaller congelando o runtime em Linux, e
`electron-builder --linux dir` empacotando o Electron. Nenhum dos dois foi testado nesta
rodada (sem Node no WSL usado). São design razoável por leitura de código, não fato
comprovado — o plano de implementação precisa prová-los em CI antes de declarar sucesso.

## 3. Bug pré-existente encontrado: processo zumbi em background

`_process_is_running()` em `src/agentos/agentic/agent_tools.py` (~linha 278) checa
liveness POSIX com `os.kill(pid, 0)`. Essa chamada retorna sucesso para um processo
**zumbi** (que já terminou mas cujo pai nunca chamou `wait()`/`waitpid()`), porque um
zumbi ainda ocupa uma entrada na tabela de processos.

`run_command(..., background=True)` (mesmo arquivo, ~linha 1511) inicia o filho via
`subprocess.Popen(command, shell=True, start_new_session=True, ...)` e, no caminho de
background, nunca chama `.wait()`, `.poll()` ou `.communicate()` depois — nada nunca
recolhe o processo. Em Linux, isso significa que **todo processo em background vira
zumbi permanente** no momento em que termina, e `_process_is_running()` o reporta como
rodando para sempre.

Reprodução isolada, independente do teste que falhou:

```python
import subprocess, os, time
p = subprocess.Popen('true', shell=True, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
os.kill(p.pid, 0)  # sucede -- parece "vivo"
# `ps -o pid,stat,cmd -p <pid>` mostra: Zs  [sh] <defunct>
```

Isso nunca se manifestou no Windows porque o ramo Windows de `_process_is_running()`
chama `tasklist`, que não tem conceito de zumbi — o bug é latente e invisível hoje porque
o produto só roda em Windows.

**Correção:** antes de cair no `os.kill(pid, 0)`, tentar recolher o processo se ele ainda
for nosso filho:

```python
try:
    reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
    if reaped_pid == pid:
        return False
except ChildProcessError:
    pass  # não é mais nosso filho (rastreado através de um restart do backend) -- cai no kill(pid, 0)
```

O fallback `os.kill(pid, 0)` continua necessário: o manifesto de processos em background
sobrevive a um novo `AgentToolset` (entre turnos, possivelmente entre reinícios do
backend), e `waitpid()` só funciona enquanto o processo original que chamou `Popen()`
ainda é quem pergunta.

Este é um **pré-requisito**, não uma tarefa paralela: sem ele, `read_process_output` e
`stop_process` nunca funcionam corretamente em Linux.

## 4. Abstração de plataforma no Python

Apenas 3 pontos de mão única, todos sobre qual script de instalador invocar:

| Arquivo | Mudança |
|---|---|
| `src/agentos/installation/profile.py:102` (`installer` property) | busca `install.ps1`; ganha ramo `install.sh` quando `os.name != "nt"` |
| `src/agentos/launcher/cli.py:254`, `:280` (`orin update`, `orin --uninstall`) | invocam `powershell.exe` incondicionalmente; passam a invocar `bash <installer>` em POSIX |
| `src/agentos/installation/versions.py:143` (update via API) | mesma troca |

Nenhuma outra mudança de código Python é necessária. Em particular, `_active_version_dir()`
(`installation/versions.py:35`) já infere a versão instalada só pelo layout de diretórios
(`<versão>/resources/runtime/`), sem nenhuma string Windows-específica — o `install.sh`
só precisa produzir a mesma estrutura relativa que o Windows já produz, sem os sufixos
`.exe`. O `restart_command` que o atualizador do Electron usa (`desktop_status.py`,
`profile.launcher_command()`) também já é inteiramente portável.

## 5. Formato de empacotamento: `.tar.gz`, não AppImage

`orin --desktop` no POSIX já resolve `host_root / "Orin Desktop"` como um binário lado a
lado (`launcher/desktop.py:121`) — exatamente o layout que `electron-builder --linux dir`
produz (equivalente Linux do `--win --dir` que o Windows já usa). AppImage é um arquivo
único montado via FUSE; não combina com esse modelo de "dois binários lado a lado num
mesmo diretório" sem ensinar o launcher a montar/desmontar uma imagem, e fica fora de
escopo desta entrega.

O ícone `.png` já usado no Windows (`desktop/electron/assets/orin-logo.png`) serve
diretamente para o bloco `linux` do `package.json` — nenhum asset novo.

**Gotcha confirmado por leitura:** o padrão do `electron-builder` para o nome do binário
em Linux é kebab-case (`orin-desktop`). Como o código POSIX já espera literalmente
`"Orin Desktop"` (com espaço), o bloco `linux` do `package.json` precisa de
`executableName: "Orin Desktop"` explícito — sem isso, o desktop nunca inicia.

## 6. `install.sh`

Decisões específicas de Linux sem equivalente direto no Windows:

- **PATH:** shim em `~/.local/bin/orin` (já no PATH por padrão na maioria das distros
  modernas, incluindo Ubuntu). Se o script detectar que não está no PATH, imprime a
  linha exata para adicionar (`export PATH="$HOME/.local/bin:$PATH"`) em vez de editar
  `.bashrc`/`.zshrc`/`.profile` sozinho — evitar mexer em dotfile pessoal sem confirmação
  explícita, e não colidir com setups de dotfiles gerenciados (chezmoi, stow).
- **Atalho:** entrada em `~/.local/share/applications/orin-desktop.desktop` (padrão
  freedesktop.org), equivalente real ao ícone de Menu Iniciar do Windows — aparece no
  launcher de apps do GNOME/KDE/XFCE. Mesma pergunta de confirmação `[Y/n]` do `.ps1`.

Raízes: `programsRoot = ~/.local/share/Orin/versions` (maiúscula proposital: o Orin já
usa `~/.local/share/orin`, minúsculo, para dados de aplicação — sqlite, logs, config,
via `_user_state_root()` em `installation/paths.py:53`. Filesystems Linux são
case-sensitive, então os dois nomes nunca colidem, mas a diferença de caixa é
deliberada, não um erro de digitação a ser "corrigido" depois). Fluxo idêntico ao `install.ps1`:
busca manifesto → valida `version`/`archive_url`/`archive_sha256` → baixa → verifica
SHA-256 → extrai em `<versão>.staging` → confere `Orin Desktop` e
`resources/runtime/orin` presentes → roda `orin --version` como sanity check → promove
para `<versão>` → symlink `current -> <versão>` → grava o shim → oferece a entrada de
menu → se `~/.local/bin` não estiver no PATH, imprime a instrução.

`--uninstall`: mesma lógica de remoção adiada esperando o PID do processo em execução
sair (espelha `Start-DeferredRemoval`), via `nohup`/subshell em vez de um processo
PowerShell oculto. Flags: `--version`, `--force`, `--uninstall`, `--wait-for-pid`,
`--no-desktop-shortcut`.

## 7. Degradação do Chromium e libs de sistema

`_launch_failure_message()` em `src/agentos/browser/conversation_worker.py:258` já
distingue "binário ausente" de "outra falha de lançamento", mas trata falha por lib de
sistema ausente como genérica e não acionável. O texto da exceção do Playwright já
inclui a linha exata de erro (`error while loading shared libraries: libnspr4.so: cannot
open shared object file`), então a detecção é direta:

```python
if "error while loading shared libraries" in text:
    return (
        "O motor de navegador não conseguiu iniciar por faltar bibliotecas de sistema. "
        "Em Debian/Ubuntu, rode: sudo apt install libnss3 libasound2t64 (ou libasound2 em "
        "versões mais antigas) e tente de novo."
    )
```

A lista exata de pacotes precisa ser reverificada contra o binário Chromium **completo**
empacotado (o spike testou só `chrome-headless-shell`), via `ldd <binário> | grep 'not
found'` num container `ubuntu-latest` limpo, como parte da implementação — não por
suposição.

O instalador nunca roda `apt` nem pede sudo — mesmo princípio que o Windows já segue
("o instalador não instala nada além do próprio Orin"). Sem as libs, o browser isolado do
agente falha com essa mensagem; o resto do Orin funciona normalmente.

## 8. Manifesto de release multi-plataforma

O Electron (`desktop/electron/main.cjs:130`) só lê `version` e `release_url` do
`release.json` — já é agnóstico de plataforma. Quem lê `archive_url`/`archive_sha256` é
exclusivamente o script de instalador de cada SO; o Python nunca faz parsing do
manifesto (`versions.py` só invoca o script).

**O risco:** um `install.ps1` já instalado numa máquina Windows hoje só sabe ler
`archive_url` plano. Se uma release remover esse campo em favor de uma estrutura
aninhada, `orin update` quebra silenciosamente para toda instalação existente.

**Solução:** os campos planos continuam representando Windows sem nenhuma mudança
(retrocompatibilidade total — `install.ps1` não muda), e uma chave nova `platforms`
carrega as demais plataformas:

```json
{
  "version": "0.3.0",
  "archive_url": "https://github.com/carlos-edu2367/orin/releases/download/v0.3.0/Orin-0.3.0-windows-x64.zip",
  "archive_sha256": "...",
  "release_url": "https://github.com/carlos-edu2367/orin/releases/tag/v0.3.0",
  "platforms": {
    "linux-x64": {
      "archive_url": "https://github.com/carlos-edu2367/orin/releases/download/v0.3.0/Orin-0.3.0-linux-x64.tar.gz",
      "archive_sha256": "..."
    }
  }
}
```

`install.sh` lê `platforms.linux-x64.*`.

## 9. CI e scripts de build

**`ci.yml`:** o job `backend` vira matriz `os: [windows-latest, ubuntu-latest]` — os
passos (`uv sync`, `playwright install chromium`, `pytest`) já são shell-agnósticos via
`uv`. O job `frontend` fica só Windows: Vite/TS/React são ferramentas cross-platform por
natureza; o risco real de Orin em Linux está no backend Python e no empacotamento
nativo, não no toolchain Node.

**`build-linux.sh`** (novo, espelha `build-windows.ps1`):
1. `npm ci && npm run build` no frontend (buildado de novo neste runner — mais simples
   que passar artefato entre jobs, custa segundos a mais).
2. `playwright install chromium` com `PLAYWRIGHT_BROWSERS_PATH` local.
3. `pyinstaller packaging/orin.spec --noconfirm --clean` — não verificado
   empiricamente nesta rodada.
4. Verificar o Chromium empacotado via `chrome-linux/chrome` (equivalente do
   `chrome.exe` que o script Windows procura).
5. `electron-builder --linux dir` com `executableName: "Orin Desktop"` explícito.
6. `package-release.sh`: tar.gz do diretório unpacked, SHA-256, grava os campos
   parciais do manifesto (`platforms.linux-x64`).

**`release.yml`** precisa de reestruturação real: hoje é um job monolítico Windows que
baixa, builda, empacota e publica tudo numa tacada. Para duas plataformas viverem numa
release, vira **fan-in**: jobs `build-windows` e `build-linux` cada um builda e sobe seu
artefato via `actions/upload-artifact`; um job `publish` final baixa os dois, funde o
manifesto (campos planos = Windows, `platforms.linux-x64` = Linux) e cria a release do
GitHub com todos os arquivos numa única chamada — evita duas execuções concorrentes
escrevendo na mesma tag.

## 10. Critérios de aceite

- Uma VM Ubuntu 24.04 limpa instala via `install.sh` sem Python, Node ou Docker
  pré-instalados.
- `orin` e `orin --desktop` funcionam depois da instalação, produzindo o mesmo layout de
  diretórios que o Windows já usa.
- `read_process_output`/`stop_process` refletem corretamente processos em background
  (prova de que a seção 3 foi resolvida).
- Sem Chromium provisionado ou com lib de sistema faltando, o browser isolado do agente
  falha com mensagem acionável; o resto do Orin continua funcionando.
- `orin update`/`orin --uninstall` funcionam via `install.sh`.
- Uma instalação Windows já existente continua atualizando normalmente depois que o
  manifesto ganhar a chave `platforms` (retrocompatibilidade comprovada por teste, não
  presumida).
- CI (`ubuntu-latest`) roda a suíte Python completa a cada PR.
- O workflow de release publica os dois pacotes numa mesma tag com um manifesto único.

## 11. Fora de escopo desta primeira entrega

- AppImage, `.deb`/`.rpm` nativos, Flatpak.
- macOS.
- Distros fora da família Debian/Ubuntu (Fedora, Arch) — "deve funcionar, não
  validado".
- Assinatura de pacote Linux — hash SHA-256 é a única verificação de integridade,
  igual ao que já vale hoje sem Authenticode configurado no Windows.
- Instalar dependências de sistema automaticamente (`apt`) — o instalador nunca roda
  comandos privilegiados.
- Atualização automática em background — já fora de escopo para Windows nos dois
  planos anteriores; Linux segue a mesma regra.
- Sincronização/multiusuário e servidor acessível pela rede.
