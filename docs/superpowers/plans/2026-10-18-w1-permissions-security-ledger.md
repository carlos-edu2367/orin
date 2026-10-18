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

## Correções da revisão adversarial (`reviews/2026-10-18-w1-review.md`)

Cada item foi corrigido com teste escrito antes da implementação (exceto onde indicado) e um commit `fix(w1): W1-Rn ...`.

| ID | Status | O que mudou | Testes |
|---|---|---|---|
| R1 | corrigido | `\<espaço>` e `\<tab>` marcam o comando como complexo; `#` só inicia comentário em fronteira de palavra não escapada | `test_shell.py`, `test_engine.py` |
| R2 | corrigido | regras deny/ask casam também formas normalizadas (aspas, caminho, `.exe/.cmd/.bat/.com`, caixa no cmd, prefixos `command/builtin/exec/env/sudo/nice/xargs`). Allow é estrito: caminho com `/` ou `\` nunca vira nome; prefixos de privilégio não são removidos; a forma normalizada precisa reanalisar como um comando simples | `test_rules.py`, `test_engine.py` |
| R3 | corrigido | alvos de ferramenta seguem `ConversationWorkspace.resolve` (`workspace_path`); tokens de shell iniciados por `/` ou `\` são verificados como caminho real e como caminho do workspace; no Windows, componentes ignoram ponto/espaço finais e sufixo de stream NTFS | `test_w1_review_regressions.py`, `test_paths.py` |
| R4 | corrigido | `cp`/`mv`: `-t DIR`, `--target-directory=DIR`, `-T` entendidos; flag desconhecida pede confirmação; glob que não pode ser canonizado não é seguro; destino protegido vira `ask` + `requires_human` mesmo em acceptEdits | `test_w1_review_regressions.py` |
| R5 | corrigido | `autoMode` é filtrado até a confiança (entra no cartão de confiança); `autoMode.model` é sempre ignorado quando vem do projeto | `test_grants_and_trust_report.py` |
| R6 | corrigido | pedidos pendentes ficam em `ORIN_USER_DIR/permissions/<sha256 do root normalizado, 16 hex>`; worker e API calculam o mesmo caminho; `allow_always` só persiste a `suggested_rule` do próprio pedido, nunca uma regra informada no arquivo de resposta | `test_w1_review_regressions.py` |
| R7 | corrigido | o veredito lê todos os objetos com `decision`; vereditos divergentes viram `ask`; iguais usam o último | `test_w1_review_regressions.py` |
| R8 | corrigido | comando com mais de 10.000 caracteres pede confirmação sem regex e sem modelo; padrões com lacunas limitadas a 10.000 | `test_w1_review_regressions.py` |
| R11 | corrigido | `BrowserSection` busca dentro do efeito com guarda `cancelled`; `CodeSessionProposalCard` tem `eslint-disable-next-line` justificado | `npm run lint` verde |
| R12 | corrigido | `ShellAnalysis.redirect_targets` registra o alvo de redirecionamento não trivial; o engine o verifica como argumento: `ask` + `requires_human` em default/acceptEdits/auto, `deny` em dontAsk | `test_w1_review_regressions.py` |
| R9 | diferido (W2/W7) | — | — |
| R10 | diferido (W6) | — | — |

Ajustes de testes existentes: nenhum teste foi removido. `tests/unit/permissions/test_w1_review_regressions.py` é novo e concentra os regressivos de R3–R8 e R12.

### Riscos residuais (honestos)

- **R6 — escopo.** Processos do mesmo usuário fora do workspace continuam fora do modelo de ameaça: podem escrever no diretório do usuário. O que mudou é que nada rodando dentro do repositório consegue mais emitir uma resposta.
- **R2 — Windows.** Um `git.cmd` plantado no diretório atual pode ser executado por `cmd` antes do programa do PATH. Allow não acompanha isso (o nome nu já era confiado antes); só o deny é reforçado.
- **R2 — `command_matches` legado.** `code_mode/permissions.py::matches_permission_rule` ainda usa a semântica antiga de primeiro token. Não é usado para decidir (apenas exportado e coberto por testes legados), mas é um caminho que um futuro uso poderia pegar por engano.
- **R7 — formato.** A leitura só considera objetos planos `{...}` sem chaves aninhadas. Um veredito aninhado vira `ask`, nunca `allow`.
- **R3 — links simbólicos e junções.** Um `.orin` que seja junção para fora do repositório continua fora do filtro de confiança (já registrado na revisão como baixo, para W4).
- **R12 — alvo entre aspas com espaço.** O alvo do redirecionamento é lido até o primeiro espaço; um alvo como `> ".git/hooks/x y"` tem o nome cortado. Os demais casos, incluindo aspas simples/duplas sem espaço, são verificados.
- **Verificação visual** dos cartões e dos modos continua não feita (já registrada acima).
- **Passo RED.** Observado (falha antes da correção) para R1, R2, R4, R5, R6, R7 e R8. Para R3 e R12 os testes foram escritos e rodados só depois da implementação; a falha prévia não foi observada.
- **Commits.** R7 e R8 foram commitados juntos (`324ca38`); os demais têm um commit por item.
