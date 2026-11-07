# CI "Validate" verde — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o workflow `Validate` passar nos três jobs (`backend (ubuntu-latest)`, `backend (windows-latest)`, `frontend`) sem enfraquecer nenhum teste red team.

**Architecture:** São quatro falhas independentes, todas no ambiente de teste ou no lint, nenhuma no código publicado. Cada tarefa corrige uma e prova a correção no sistema operacional onde a falha acontece.

**Tech Stack:** pytest, GitHub Actions (ubuntu-latest e windows-latest), ESLint com `react-hooks` (regra de pureza do React Compiler), Vitest e `tsc -b`.

**Spec:** não há spec. As falhas vêm do run `42261642231` (Validate da v0.12.1, 2026-11-06), que também falhou na 0.12.0 e na 0.11.6.

## Global Constraints

- Nenhum teste red team pode ser apagado ou marcado `skip` sem um teste equivalente que rode em todo sistema operacional.
- O mesmo comando tem de passar localmente no Windows e no Linux do CI: `uv run pytest -q`.
- No front-end, `npm run lint`, `npx vitest run` e `npm run build` (que roda `tsc -b`) precisam passar em `frontend/`.

## Review Focus

1. **Linux deixando de testar a barreira:** o teste continua provando a barreira de permissão no Linux. Uma ferramenta deferida e mutável que exista em todo sistema operacional precisa ser recusada.
2. **Windows com caminho curto:** o teste do pytest avulso passa quando o `TEMP` usa nome 8.3 (`RUNNER~1`), como no runner do GitHub.
3. **Correção do lint mudando o comportamento:** os ids de mensagem pendente continuam únicos dentro da sessão.
4. **Variável sem uso que era uma asserção esquecida:** antes de apagar uma variável sem uso num teste, confira se ela deveria estar numa asserção.
5. **Uma falha escondendo outra:** o run inteiro precisa ficar verde no GitHub, não só os testes citados aqui.

---

### Task 1: Testes red team da Fase 3 sem supor `powershell` no Linux

**Diagnóstico:** `powershell_executable()` (`src/agentos/agentic/tools/shell/powershell.py:25`) devolve `None` fora do Windows. Por isso, no Linux, `powershell` não entra em `definitions()` nem em `deferred_tool_names`. Os três testes perguntam por essa ferramenta. Resultado: `assert 'powershell' in frozenset()`.

**Files:**
- Modify: `tests/unit/redteam/test_fase3_full_branch.py:155-185`
- Modify: `tests/unit/redteam/test_fase3_group1_tools_and_read.py:235-280`
- Create: `tests/unit/redteam/_platform_tools.py`

**Interfaces:**
- Produces: `DEFERRED_MUTATION_TOOL: str` em `tests/unit/redteam/_platform_tools.py`. Vale `"powershell"` quando `os.name == "nt"` e, nos outros sistemas, uma ferramenta deferida e mutável que exista em todos eles. A candidata é `"notebook_edit"`. Antes de fixar, confirme que ela está em `CODE_MODE_CORE_TOOLS - ALWAYS_LOADED_CODE_TOOLS` (`src/agentos/agentic/agent_tools.py`). Se não estiver, use a primeira ferramenta desse conjunto que esteja em `{"write_file", "edit_file", "notebook_edit", "run_command"}`.

- [ ] **Step 1: Rode e confirme a falha fora do Windows.** Rode no WSL ou num container `python:3.12`: `uv run pytest tests/unit/redteam/test_fase3_full_branch.py tests/unit/redteam/test_fase3_group1_tools_and_read.py -q`. Esperado: 3 falhas com `assert 'powershell' in ...`.
- [ ] **Step 2: Crie `_platform_tools.py`** com `DEFERRED_MUTATION_TOOL`. Em todo sistema operacional, ele precisa satisfazer `DEFERRED_MUTATION_TOOL in toolset.deferred_tool_names` para um toolset em modo code.
- [ ] **Step 3: Troque `powershell` por `DEFERRED_MUTATION_TOOL`** nos três testes: `test_surface1_subagents_do_not_inherit_discovered_tools`, `test_surface1_calling_deferred_tool_by_public_or_internal_name_executes_and_discovers` e `test_deferred_and_discovered_tools_cannot_bypass_permissions_plan_mode_readonly_or_hooks`. O payload `{"command": ...}` só serve para shell. Para outra ferramenta, monte argumentos válidos dela, e a asserção continua sendo a recusa (permissão, plan mode e somente leitura).
- [ ] **Step 4: Proteja o caso Windows.** Adicione `test_powershell_specific_barriers_on_windows`, com `@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell só existe no Windows")`. Ele repete as três asserções de recusa com `powershell`, para que a cobertura no Windows não diminua.
- [ ] **Step 5: Rode nos dois sistemas.** O mesmo comando do Step 1 no Linux e no Windows. Esperado: todos passam e o teste novo aparece como `skipped` no Linux.
- [ ] **Step 6: Commit.** `test(redteam): use a cross-platform deferred mutation tool in Fase 3 barriers`

### Task 2: `test_pytest_user_dir_isolation` no Windows do GitHub

**Diagnóstico (hipótese a confirmar no Step 1):** no runner, `tmp_path` fica sob `C:\Users\RUNNER~1\...`, um nome curto 8.3. O pytest filho recebe `--rootdir=<curto>` e o arquivo pelo caminho curto. Ao normalizar, ele não reconhece o arquivo como parte do rootdir e passa a percorrer o disco a partir de `C:\`. Ali encontra a junction `C:\Documents and Settings` e para com `PermissionError [WinError 5]`. O id de nó `test_standalone_pytest_outside0::::Documents and Settings` aponta para isso. No PC do Carlos o `TEMP` não tem nome curto, e o teste passa.

**Files:**
- Modify: `tests/unit/hooks/test_pytest_user_dir_isolation.py:129-181`

- [ ] **Step 1: Reproduza localmente com o caminho curto.** Escreva `test_standalone_pytest_survives_short_8dot3_tmp_path`, com `skipif(os.name != "nt")`. Ele obtém o nome curto de `tmp_path` via `ctypes.windll.kernel32.GetShortPathNameW` e roda o mesmo subprocesso com esse caminho. Se o volume não gerar nomes 8.3 (o buffer devolvido é igual ao longo), o teste chama `pytest.skip("volume sem nomes 8.3")`. Rode e confirme que ele falha com `PermissionError` ou com coleta fora do `tmp_path`. Se não falhar, a hipótese está errada: pare e investigue com `--collect-only -q` no runner antes de mudar código.
- [ ] **Step 2: Corrija.** Use `tmp_path = tmp_path.resolve()` no começo dos dois testes, o que expande o nome curto, e passe `--confcutdir` e `--rootdir` com o caminho resolvido. Não mude `pyproject.toml`: `addopts = ["-p", "tests.conftest"]` é o que o teste protege.
- [ ] **Step 3: Rode.** `uv run pytest tests/unit/hooks/test_pytest_user_dir_isolation.py -q`. Esperado: tudo passa, e o teste do Step 1 passa ou fica `skipped` pelo motivo declarado.
- [ ] **Step 4: Commit.** `test(hooks): resolve 8.3 short tmp paths before the standalone pytest run`

### Task 3: Lint do front-end

**Diagnóstico:** são 9 erros.
- 1 de pureza em `frontend/src/features/conversations/ChatPage.tsx:1788`. O `Date.now()` dentro de um handler declarado no corpo do componente é lido pelo React Compiler como chamada impura durante o render.
- 8 de `@typescript-eslint/no-unused-vars` nos testes:
  - `frontend/src/app/__tests__/HomeAndChatRefactored.test.tsx` (`TurnTimeline`)
  - `RedTeamFase3Group2.test.tsx`
  - `RedTeamRound4NativeAskUser.test.tsx`
  - `RedTeamRound5NativeAskUser.test.tsx`
  - `RedTeamRound6NativeAskUser.test.tsx`

  Nesses quatro, os nomes são `Q2`, `userBubbles`, `queuedInputActivity` e `emit`. Todos ficam em `frontend/src/features/conversations/__tests__/`.

**Files:**
- Modify: `frontend/src/features/conversations/ChatPage.tsx:1788`
- Create: `frontend/src/features/conversations/pendingMessageId.ts`
- Test: `frontend/src/features/conversations/__tests__/pendingMessageId.test.ts`
- Modify: os 5 arquivos de teste listados acima

**Interfaces:**
- Produces: `export function pendingMessageId(): string`, que devolve `pending-<n>-<timestamp>` com um contador de módulo. É único mesmo com duas chamadas no mesmo milissegundo.

- [ ] **Step 1: Escreva o teste que falha.** `pendingMessageId.test.ts`: duas chamadas seguidas devolvem valores diferentes e os dois começam com `pending-`.
- [ ] **Step 2: Rode.** `npx vitest run src/features/conversations/__tests__/pendingMessageId.test.ts`. Esperado: FAIL, porque o módulo ainda não existe.
- [ ] **Step 3: Implemente `pendingMessageId()`** e troque `` `pending-${Date.now()}` `` em `ChatPage.tsx:1788` por `pendingMessageId()`.
- [ ] **Step 4: Variáveis sem uso.** Em cada uma, leia o teste. Se ela deveria estar numa asserção (por exemplo, `userBubbles` contado e nunca conferido), adicione a asserção que falta. Se não, apague a variável. Registre a decisão de cada uma na mensagem do commit.
- [ ] **Step 5: Rode tudo.** Em `frontend/`: `npm run lint`, `npx vitest run` e `npm run build`. Esperado: `0 problems`, Vitest verde e build sem erro.
- [ ] **Step 6: Commit.** `fix(frontend): pure pending message ids and unused test bindings`

### Task 4: Confirmar o CI inteiro

- [ ] **Step 1: Suíte completa local no Windows.** `uv run pytest -q`. Esperado: 0 falhas.
- [ ] **Step 2: Peça ao Carlos autorização para o push.** Pela regra "nada sai do PC sem pedido", o push para uma branch só sai com o ok dele. Depois do push, acompanhe o `Validate` e confirme os três jobs com `conclusion: success` (`gh run view <id> --json jobs`).
- [ ] **Step 3: Se aparecer uma falha nova, que estava escondida pelas antigas**, abra uma tarefa nova neste plano com o diagnóstico. Não a corrija sem antes registrar a causa aqui.
