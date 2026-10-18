# W1 Permissões e Segurança: ledger

Plano: `2026-10-18-w1-permissions-security.md`. Branch `feat/orin-code-parity-w0-w3`.

## Spec §4 entregue

| Item | Onde | Testes |
|---|---|---|
| Seis modos (incl. `auto`, `dont_ask`) e aliases do Claude Code | `code_mode/models.py`, `conversations/chat.py`, `api/gateway.py`, `agentic/session.py` | `tests/unit/code_mode/test_permission_modes_w1.py` |
| Análise de shell fail-closed (POSIX e cmd) | `permissions/shell.py` | `tests/unit/permissions/test_shell.py` |
| Especificadores de caminho e caminhos protegidos | `permissions/paths.py` | `tests/unit/permissions/test_paths.py` |
| Gramática de regras (`Bash`, `Edit`, `Read`, `WebFetch(domain:)`, `Agent`, `Skill`, `mcp__`) | `permissions/rules.py` | `tests/unit/permissions/test_rules.py` |
| Pipeline deny → plano → ask → protegido → allow → built-ins → modo | `permissions/engine.py` | `tests/unit/permissions/test_engine.py` |
| Broker delega ao engine; "sempre permitir" vai para arquivo do usuário | `code_mode/permissions.py`, `settings/paths.py`, `settings/loader.py`, `agentic/agent_tools.py` | `tests/unit/code_mode/test_broker_engine.py`, `tests/unit/settings/test_grants_and_trust_report.py` |
| Repositório só vale após confiança; store, evento e API de confiança | `permissions/trust.py`, `agentic/events.py`, `agentic/session.py`, `api/gateway.py` | `tests/unit/permissions/test_trust.py`, `tests/unit/agentic/test_session_trust.py`, `tests/unit/api/test_workspace_trust_api.py` |
| Classificador local do modo auto, hard-deny e disjuntor | `permissions/auto_mode.py` | `tests/unit/permissions/test_auto_mode.py` |
| Modos e cartão de confiança na interface | `frontend/src/api/codeMode.ts`, `frontend/src/features/conversations/WorkspaceTrustCard.tsx`, `ActivityStream.tsx`, `SessionContextPopover.tsx`, `activityTypes.ts`, `api/conversations.ts`, `styles/agentos.css` | `frontend/src/features/conversations/__tests__/WorkspaceTrustCard.test.tsx` |

## Desvios

Os cinco desvios do plano (sem tree-sitter; modos internos mantidos; grants em `~/.orin/workspaces/<id>/settings.local.json`; `additionalDirectories` adiado para W4; ciclo do Shift+Tab inalterado) foram mantidos.

Desvios adicionais, todos durante a execução:

1. **Validação de modo (Task 1).** O plano usa `normalize_permission_mode` antes da checagem de `_VALID_PERMISSION_MODES`. Essa função converte texto desconhecido em `ask_permissions`, o que faria entradas inválidas passarem. Adicionei `known_permission_mode` (em `models.py`), que só converte nomes conhecidos e devolve o resto sem alteração. O chat continua rejeitando valores inválidos e a sessão mantém o fallback anterior.
2. **Passo RED não observado (Task 2).** Escrevi o teste e a implementação antes de rodar o teste. Ele passou (55/55) só depois da implementação.
3. **`*.env` casa com `.env` (Task 3).** O plano afirmava `glob_to_regex("*.env").match(".env") is None`. A própria implementação do plano (`*` → `[^/]*`) casa, e uma regra `deny`/`ask` precisa cobrir `.env`. Troquei a expectativa para casar. Isso só aumenta a cobertura.
4. **`dir/**` cobre o próprio diretório (Task 4).** `Read(secrets/**)` não bloqueava uma busca com `path=secrets`. Corrigi em `paths.py`. O teste do plano ficou inalterado.
5. **Teste de loader (Task 5).** `writable_file("local")` agora aponta para o arquivo de concessões, conforme a decisão 3 do plano.
6. **Ciclo de importação (Task 7).** `code_mode/permissions.py` importa `agentos.permissions.*` dentro das funções que usam, como o plano permite. Sem isso, `code_mode/__init__` → `permissions/engine` → `code_mode.models` gerava um ciclo.
7. **Expectativas legadas (Task 7).** Em `tests/unit/test_orin_code_phase_b.py` e `tests/unit/agentic/test_subagents_and_permissions_v076.py`:
   - sugestões passaram a usar a sintaxe pública (`run_command(npm test:*)` → `Bash(npm test:*)`, `edit_file(src/**)` → `Edit(src/**)`);
   - asserções de "sempre permitir" passaram a ler o arquivo de concessões via o helper `_grant_allow`.
   As regras legadas `run_command(...)` continuam sendo lidas e casando.
8. **Remapeamentos do broker (Task 7).** Removi `notebook_edit`→`edit_file` e `task_*`→`todo_write`; o engine trata essas famílias diretamente. `grep`/`grep_files` passam a mapear para `search_files`.
9. **Teste da API de confiança (Task 8).** O plano esperava `path == str(workspace_root)`. O gateway resolve a raiz pela conversa, então o teste compara o `path` do GET com o do POST e exige um texto não vazio.
10. **Lint do frontend (Task 10).** `npm run lint` falha com 2 erros que já existiam em arquivos que este trabalho não tocou: `CodeSessionProposalCard.tsx:145` e `BrowserSection.tsx:62` (`react-hooks/set-state-in-effect`). Não corrigi, porque estão fora do escopo do W1.

## Pendências e limitações conhecidas

- **`autoMode.model` é ignorado pelo provedor real.** O classificador envia `model` na requisição, mas o transporte HTTP de `provider_stream.py` monta o payload com `self.model`, fixado na criação do provedor. O veredito do modo auto usa, na prática, o modelo do turno. Correção: criar um provedor para o modelo do classificador.
- **Disjuntor do modo auto vale por turno.** O classificador é recriado em `build_runtime`, então os contadores de negação zeram a cada turno.
- **Classificador não testado contra o provedor real.** Os testes usam um provedor falso com o contrato `stream(request)`. O contrato real bate nas chaves `messages`, `tools` e `max_output_tokens`, mas nenhuma execução ponta a ponta foi feita.
- **Turnos não interativos ainda executam decisões `ask` comuns.** Só as decisões `requires_human` (regra `ask` explícita ou caminho protegido) são bloqueadas. Isso é o comportamento documentado no plano para W2/W7.
- **Verificação visual não feita.** O cartão de confiança e os novos rótulos de modo não foram abertos num navegador real. Só há testes de unidade com Vitest.

## Pendências diferidas (plano)

- `additionalDirectories` / `--add-dir` / `DirectoryAdded` → W4.
- Análise de PowerShell → W4 (ferramenta `PowerShell`).
- Subcomandos `auto-mode` da CLI e `--permission-prompt-tool` → W6.
- Editor `/permissions` e cartão de permissão com três opções → W7.
- Itens de W0 ainda abertos: R10, R11, R14, R15, R18, R20, R21.
