# Spike Windows: Git Worktree, Junções NTFS e Isolamento de Subagentes (WS-B0 / WS-B1)

- **Data:** 2026-11-21
- **Ambiente medido:**
  - OS: `Windows-10-10.0.19045-SP0` (executado via Git Bash / Python nativo Windows)
  - Python: `3.13.15 (MSC v.1944 64 bit AMD64)`
  - Git: `git version 2.56.0.windows.4`
- **Escopo:** Validação empírica dos 5 requisitos do Spike Windows descritos em `docs/superpowers/plans/2026-11-18-orin-0.13-pacotes-e-d-a-b.md` (WS-B1) antes da implementação de `AgentWorktreeManager`, para fundamentar o padrão de `orchestration.worktree.linkDirs`, regras de caminho/remoção segura e mensagens de erro.

---

## Resumo Executivo e Decisões Arquiteturais

| Item | Resultado Empírico | Decisão para WS-B1 / WS-B2 |
|---|---|---|
| 1. `git stash create` | Captura alterações rastreadas sem tocar na working tree nem em `git stash list`; retorna vazio se limpa; **não** inclui não rastreados. | Usar `git stash create` com fallback para `HEAD` quando vazio; copiar não rastreados separadamente via `git ls-files --others --exclude-standard -z`. |
| 2. Caminhos longos, espaços e acentos | `core.longpaths=true` resolve checkout de arquivos internos de até 377+ chars, mas **raiz da worktree >= 220 chars falha com `fatal: '$GIT_DIR' too big` mesmo com `core.longpaths=true`**. | Sempre passar `-c core.longpaths=true`; manter caminho raiz da worktree curto (`<= 160` chars, ex.: `<repo>/.orin/wt/<id8>` ou fallback em `<temp>/orin-wt/<id8>`). |
| 3. Junções NTFS (`node_modules`, `.venv`) | `_winapi.CreateJunction` cria 2 junções em **1,29–1,75 ms** sem privilégio admin; `git status --porcelain` fica vazio quando ignoradas; `.venv/Scripts/python.exe` executa normalmente (`rc=0`). | Manter padrão `linkDirs = ["node_modules", ".venv"]`; usar `_winapi.CreateJunction` no Windows (sem `cmd.exe`) e avisar no prompt do subagente para não instalar dependências pela junção. |
| 4. Remoção segura (junção + arquivo travado) | `os.rmdir` em cada junção antes de `git worktree remove --force` preserva 100% dos alvos no pai (`rc=0`). Handle aberto gera `git rc=255` (`Invalid argument`) e `PermissionError` (`WinError 32`). | **Sempre** desvincular junções via `os.path.isjunction` + `os.rmdir` antes de qualquer remoção; nunca usar `rm -rf`/`shutil.rmtree` sobre junções; implementar retry curto com backoff e mensagem acionável em `WinError 32`. |
| 5. Benchmark 5.000 arquivos | Setup total: **3.151 ms** (~3,15 s); remoção segura: **903 ms** (~0,90 s). | Viável para subagentes de longa duração sem degradar a UX; criação deve ser assíncrona/reportada via evento `worktree.created`. |

---

## Evidências Empíricas por Item

### 1. `git stash create` com alterações rastreadas e não rastreadas

Executado em repositório temporário com `.gitignore` (`node_modules/`, `.venv/`), arquivo rastreado `tracked.txt` modificado (`v1` → `v2-dirty`) e arquivo novo `untracked.txt`:

- Working tree limpa: `git stash create` retornou string vazia (`""`) → exige fallback para `HEAD`.
- Working tree suja:
  - Retornou SHA-1 válido de 40 caracteres.
  - `git stash list` permaneceu vazio (`""`).
  - `git status --porcelain` antes e depois permaneceu idêntico (`M tracked.txt`, `?? untracked.txt`) — zero mutação no workspace do pai.
  - `git show <sha>:tracked.txt` devolveu `v2-dirty` (rastreadas capturadas).
  - `git ls-tree -r --name-only <sha>` **não** conteve `untracked.txt`.
  - `git ls-files --others --exclude-standard -z` listou exatamente `["untracked.txt"]`, sem incluir itens ignorados.

**Regra para WS-B1:**
1. Rodar `git stash create`; se vazio, usar `HEAD`.
2. Criar a worktree a partir desse `base_ref`.
3. Copiar arquivos não rastreados listados por `git ls-files --others --exclude-standard -z` (respeitando limites e evitando symlinks/junções fora do workspace) e os padrões de `.worktreeinclude`, quando existir.

---

### 2. `git worktree add` com caminho longo (> 200 chars), espaços, acentos e `core.longpaths`

Testado com segmentos contendo espaços e caracteres acentuados (`área de trabalho revisão`, `espaço revisão`, `wt_subagente_ação`, `file_ação.txt`):

| Comprimento da raiz da worktree | Comprimento do arquivo interno | `core.longpaths=false` | `core.longpaths=true` | Observação |
|---:|---:|---|---|---|
| 115 chars | 297 chars | `rc=128` (`fatal: cannot create directory...`) | `rc=0`, `git status rc=0` | Git cria/gerencia arquivos > 260 chars; API Win32 sem `\\?\` e sem `LongPathsEnabled=1` não enxerga > 260 chars (`Path.exists() == False`). |
| 155 chars | 337 chars | `rc=128` (`fatal: cannot create directory...`) | `rc=0`, `git status rc=0` | Mesmo comportamento. |
| 195 chars | 377 chars | `rc=128` (`fatal: cannot create directory...`) | `rc=0`, `git status rc=0` | Limite operacional superior observado para a raiz da worktree. |
| 220 chars | 402 chars | `rc=128` (`fatal: '$GIT_DIR' too big`) | `rc=128` (`fatal: '$GIT_DIR' too big`) | **Falha dura do Git for Windows mesmo com `core.longpaths=true`.** |
| 224 chars | 257 chars | `rc=128` (`fatal: '$GIT_DIR' too big`) | `rc=128` (`fatal: '$GIT_DIR' too big`) | Confirmado independente da profundidade interna. |
| 235 chars | 417 chars | `rc=128` (`fatal: '$GIT_DIR' too big`) | `rc=128` (`fatal: '$GIT_DIR' too big`) | Mesmo erro. |

Além disso, criar diretórios pais com comprimento > 260 chars via `Path.mkdir()` sem `\\?\` falhou com `FileNotFoundError: [WinError 3]` quando `LongPathsEnabled` não está ativo no registro do Windows.

**Regras para WS-B1:**
- Invocar sempre `git -c core.longpaths=true ...` nas operações de worktree no Windows.
- Gerar nomes curtos para o diretório da worktree (ex.: `.orin/wt/<short8>`). Se `len(str(worktree_root)) > 160` no Windows, usar raiz alternativa curta em diretório temporário do usuário (ex.: `%LOCALAPPDATA%\orin\wt\<short8>` ou `tempfile.gettempdir()/orin-wt/<short8>`), evitando atingir o teto de ~200 chars que provoca `fatal: '$GIT_DIR' too big`.
- Se o Git retornar `'$GIT_DIR' too big` ou erro de caminho longo, emitir `isolation.unavailable` / erro claro explicando que o caminho do projeto no Windows é longo demais para worktree isolada.

---

### 3. Junções Windows (`_winapi.CreateJunction` e `mklink /J`) para `node_modules` e `.venv`

Testado com `node_modules/demo-pkg/index.js` e um ambiente virtual Python real (`python -m venv --without-pip .venv`), ambos listados no `.gitignore`:

- `_winapi.CreateJunction` criou as 2 junções em **1,749 ms** (e **1,29 ms** no repo de 5.000 arquivos), **sem** elevação de administrador e **sem** spawn de `cmd.exe`.
- `cmd /c mklink /J` também funcionou com paridade (`os.path.isjunction(path) == True`).
- `os.path.isjunction()` (Python 3.12+/3.13) identificou corretamente ambas as junções (`True`).
- `git status --porcelain` dentro da worktree retornou **vazio (`""`)** — as junções ignoradas pelo `.gitignore` não poluem o status nem o diff do subagente.
- Leitura de `wt/node_modules/demo-pkg/index.js` funcionou normalmente.
- Execução de `wt/.venv/Scripts/python.exe -c "import sys; print('OK', sys.prefix)"` retornou `rc=0` com `OK <wt>\.venv`.

**Decisões:**
- Padrão confirmado: `orchestration.worktree.linkDirs = ["node_modules", ".venv"]`.
- No Windows, preferir `_winapi.CreateJunction(str(target), str(link))` (atômico, nativo no stdlib CPython, ~1 ms, sem shell injection).
- Validar cada entrada de `linkDirs` para aceitar apenas caminhos relativos seguros (sem `..`, sem barra inicial, sem letra de drive) e somente criar junção se o diretório existir no pai **e** estiver ignorado ou não rastreado para evitar poluir diffs.
- Instrução obrigatória no prompt do subagente em worktree: *não executar `npm install`, `uv sync` ou `pip install` dentro da worktree*, pois as junções apontam para os diretórios do pai.

---

### 4. Remoção Segura de Worktree (Junção Presente e Arquivo Travado)

#### (a) Remoção com junções presentes
- Sequência executada:
  1. Varrer/inspecionar diretórios ligados (`os.path.isjunction(p)`) e removê-los com `os.rmdir(p)` (no Windows, `os.rmdir` remove apenas o reparse point da junção).
  2. Executar `git worktree remove --force <wt>` e `git branch -D <branch>`.
- Resultado:
  - `git worktree remove --force`: `rc=0`.
  - Diretório da worktree removido completamente (`exists == False`).
  - Alvos no pai (`parent/node_modules/demo-pkg/index.js` e `parent/.venv/Scripts/python.exe`) permaneceram **100% intactos**.
- **Regra de segurança crítica:** **NUNCA** executar `rm -rf` (Git Bash/MSYS2) nem `shutil.rmtree` antes de remover todos os reparse points/junções com `os.rmdir()`. Uma travessia indevida de junção destruiria `node_modules` ou `.venv` do workspace principal.

#### (b) Remoção com arquivo aberto/travado por outro processo
- Cenário: arquivo `locked_by_process.tmp` mantido aberto com handle ativo (`open(..., "r+b")`) dentro da worktree.
- Comportamento observado (após desvincular junções primeiro):
  - `git worktree remove --force` falhou com `rc=255` e stderr:
    `error: failed to delete '<wt>': Invalid argument`
  - `os.remove(lock_file)` levantou `PermissionError` com `winerror=32`:
    `[WinError 32] O arquivo já está sendo usado por outro processo`
- Recuperação após liberar o handle:
  - Remoção de diretórios restantes + `git worktree prune` (`rc=0`) + `git branch -D` (`rc=0`) concluíram sem resíduos e com o pai intacto.

**Fallback seguro e mensagem de erro clara para WS-B1:**
1. Desvincular todas as junções conhecidas e quaisquer junções descobertas via `os.walk(..., topdown=True)` com `os.path.isjunction` + `os.rmdir` (podando `dirnames` para nunca descer dentro de junções).
2. Tentar `git worktree remove --force` (com até 3 retries curtos: 200 ms, 500 ms, 1000 ms para absorver scans transitórios do Windows Defender/indexador).
3. Se ainda falhar por arquivo travado (`WinError 32`, `WinError 5`, `PermissionError` ou `Invalid argument` do git):
   - **Não** corromper o pai nem silenciar o erro.
   - Registrar estado de limpeza pendente / tentar `git worktree prune` na próxima inicialização.
   - Retornar erro claro ao usuário:
     > "Não foi possível remover a worktree em `<caminho>` porque um arquivo está em uso por outro processo (ex.: servidor de desenvolvimento, terminal aberto ou antivírus). Feche os processos que usam essa pasta e tente remover novamente."

---

### 5. Benchmark em Repositório com 5.000 Arquivos

Repositório temporário com 5.000 arquivos Python distribuídos em 50 subdiretórios (`pkg_00`..`pkg_49`, 100 arquivos cada), + `node_modules` e `.venv` ignorados + 1 edição rastreada + 1 arquivo não rastreado:

| Etapa | Tempo medido (ms) |
|---|---:|
| `git stash create` | 118,33 ms |
| `git -c core.longpaths=true worktree add` | 3.001,66 ms |
| Criação de 2 junções (`_winapi.CreateJunction`) | 1,29 ms |
| Descoberta e cópia de não rastreados (`ls-files -z`) | 29,97 ms |
| **Total de criação (setup completo)** | **3.151,26 ms (~3,15 s)** |
| **Remoção segura** (`os.rmdir` junções + `worktree remove --force` + `branch -D`) | **902,82 ms (~0,90 s)** |

**Conclusão:** Em repositórios de porte médio (5.000 arquivos rastreados), o isolamento via worktree + junções leva ~3,15 s no Windows 10/11 (custo dominado pelo checkout inicial do Git, enquanto as junções custam ~1,3 ms vs copiar dezenas de milhares de arquivos de `node_modules`/`.venv`). O desenho com `linkDirs = ["node_modules", ".venv"]` é tecnicamente sólido e pronto para WS-B1.
