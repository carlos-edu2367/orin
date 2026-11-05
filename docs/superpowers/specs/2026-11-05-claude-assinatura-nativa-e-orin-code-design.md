# Especificação de Design: Claude pela assinatura com adapter próprio e Orin Code confiável

- **Data:** 2026-11-05
- **Status:** Aguardando aprovação do Carlos (só análise; nada implementado)
- **Base:** auditoria `docs/audits/2026-11-05-claude-assinatura-e-orin-code.md` (sessões reais do Orin 0.11.6 nos projetos
  Educly e Orin, banco `%LOCALAPPDATA%\Orin\data\orin.db`)
- **Objetivo final:** o Claude dentro do Orin e o Orin Code 100% funcionais, como pré-requisito para os provedores
  Antigravity (`agy -p`) e Codex (`codex -p`) pela assinatura.

---

## 1. Problema

O provedor "Assinatura Claude" usa o `claude -p` como gerador de texto: `--tools "" --max-turns 1`, um processo novo
por iteração, todo o histórico achatado numa única mensagem de usuário, ferramentas declaradas como JSON no system
prompt e chamadas feitas por tags `<orin_tool_call>` no texto. Esse protocolo vem de helpers privados do
`provider_catalog/opencode.py`. Consequências observadas nas sessões:

- **Lote cego:** nada para a geração depois de uma chamada. O modelo escreveu 31 chamadas numa resposta (read e edit
  do mesmo arquivo, mais `verify_project`), e 16 em outra, com caminhos inventados.
- **Resultados alucinados:** o modelo escreveu blocos `[Tool Result id=call_…]` falsos no próprio texto.
- **Minutos de silêncio:** as chamadas ficam escondidas e só são emitidas quando o processo termina; o thinking do
  Opus 5.5 chega vazio.
- **Custo quadrático:** cada iteração reenvia o transcript inteiro (216k tokens de entrada em um turno de 10
  iterações), com cache praticamente inútil.
- **Modelo errado:** `_LEGACY_CLAUDE_MODEL_MAP` troca Opus 5.5 por Opus 4.7 (e os outros 5.x por 4.x).
- **Janela errada:** `workers/chat.py:940` corta para 200k qualquer modelo do bridge.

Além disso, o Orin Code tem defeitos próprios, independentes de provedor, que transformam pequenos erros do modelo em
voltas inteiras desperdiçadas: edit que falha em CRLF, "Bash" que roda cmd.exe, contexto descartado no meio do turno,
estado de leitura que zera a cada mensagem e arquivos do Orin gravados dentro do repositório do usuário.

## 2. Decisões

| # | Decisão | Motivo |
|---|---|---|
| D1 | O Claude ganha um **adapter próprio** em `src/agentos/providers/claude_cli/`, sem importar nada de `opencode.py`. | O protocolo do OpenCode não serve ao `claude -p`, e acoplar os dois faz uma mudança num quebrar o outro. |
| D2 | O caminho principal usa o **loop nativo de ferramentas do `claude -p`**: as ferramentas do Orin são expostas por um **servidor MCP local** (HTTP em `127.0.0.1`, token por turno), e a CLI roda com `--max-turns N`. | É a única forma de o modelo parar em cada `tool_use`, receber `tool_result` de verdade, ter ids nativos e cache incremental. |
| D3 | A execução de cada ferramenta continua no Orin: o handler MCP chama o **mesmo caminho de execução** que o runtime usa hoje (permissões, hooks, journal de efeitos, eventos de atividade). | Mantém segurança, auditoria e UI sem reimplementar nada. |
| D4 | O isolamento da CLI usa `--restricted --strict-mcp-config --mcp-config <orin> --tools ""`, e não `--safe-mode`. | A ajuda da CLI 2.1.309 diz que `--safe-mode` desliga "MCP servers"; `--restricted` ignora os settings de usuário/projeto/local (fecha o vazamento do `~/.claude/settings.json`) e, com `--strict-mcp-config`, usa só o MCP passado. `--bare` está descartado porque não lê OAuth. **Hipótese a confirmar no spike (Fase 2, tarefa 1).** |
| D5 | O protocolo de texto vira **fallback** dentro do próprio adapter (`claude_cli/text_protocol.py`), corrigido (uma rodada de chamadas por resposta, corte no primeiro `[Tool Result`, ids nas chamadas do histórico, conteúdo de ferramenta escapado). | Rede de segurança se o spike reprovar algo ou a CLI mudar. |
| D6 | Ids de modelo passam **sem tradução**. O catálogo vem de `~/.claude/cache/model-catalog/*.json`; os aliases de subagente apontam para a família atual (`opus`→`claude-opus-5-5`, `sonnet`→`claude-sonnet-5-5`, `haiku`→`claude-haiku-5-5`, `fable`→`claude-fable-5-1`), resolvidos pelo catálogo quando disponível. | A CLI aceita os ids 5.x; o mapa é regressão do commit `5aebf7c`. |
| D7 | A janela de contexto vem do catálogo (1M para Opus 5.5) nos **dois** caminhos. No nativo, o cache é o da própria CLI. No fallback de texto, o protocolo fica **amigável ao cache**: histórico enviado como vários blocos de conteúdo (um por mensagem/resultado) em vez de um texto único; histórico só cresce dentro da janela (sem o aging reescrever o prefixo, `runtime.py:1310-1311,1556-1561`); system prompt e lista de ferramentas idênticos entre iterações. O orçamento configurável (`ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET`) só volta a ter padrão `200000` se o spike (R2.1 item 7) mostrar que o cache não é lido. | O cache da Anthropic só reaproveita prefixos em fronteiras de bloco; com um bloco único que cresce nunca há fronteira igual à da chamada anterior, e com o prefixo reescrito a cada iteração nada é reaproveitado. Corrigido isso, 1M não significa reenviar 1M sem cache. |
| D8 | Anexos e estado do Orin dentro do workspace passam a viver em `<workspace>/.orin/` (`uploads` vira `.orin/uploads/`), e o Orin garante a linha `.orin/` em `.git/info/exclude` quando o workspace é um repositório git. | As ferramentas continuam enxergando os anexos (estão dentro do workspace), e nada aparece no `git status` do usuário. Mover para fora do workspace exigiria abrir o path guard. |
| D9 | No Windows, a ferramenta `Bash` roda pelo **Git Bash** (`bash.exe -c`), com fallback para cmd.exe só quando o Git Bash não existe. O system prompt do Orin Code ganha uma seção **# Environment** (diretório de trabalho, se é repositório git, plataforma, shell real, versão do SO, data). | É o que o Claude Code faz e o que o modelo espera. |
| D10 | Antigravity e Codex **não** entram nesta spec. A Fase 3 entrega o contrato de adapter de CLI e o servidor MCP reutilizável para que eles entrem depois. | Escopo; o usuário pediu Claude 100% primeiro. |

## 3. Fases

Cada fase entrega software funcionando e testado sozinho, e tem um plano próprio em `docs/superpowers/plans/`.

### Fase 1 — Orin Code confiável (independente de provedor)

Plano: `docs/superpowers/plans/2026-11-05-fase-1-orin-code-confiavel.md`

Requisitos:

- **R1.1 Edit em CRLF.** `edit_file` encontra `old_text` com `\n` em arquivo CRLF, grava o resultado mantendo o final de
  linha original do arquivo; `write_file` sobre arquivo existente mantém o final de linha original
  (`agentic/tools/files/edit.py:61-73`, `agentic/workspace.py:181-202`).
- **R1.2 Shell real.** `run_command` (público "Bash") roda via Git Bash no Windows quando disponível
  (`agentic/tools/shell/bash.py:186,237`); o pré-check de `cd` entende caminhos POSIX (`/c/Users/...`) e o
  `_resolve_cwd_after_command` usa os separadores do shell real; `environment_facts` (`agentic/session.py:273-275`)
  e o dialeto de permissões refletem o shell real; fallback para cmd.exe com o prompt dizendo isso.
- **R1.3 Seção de ambiente completa.** A seção `# Environment` do contexto do turno já existe
  (`code_mode/system_prompt.py:178-184`) mas só traz o shell; ela passa a trazer o que D9 lista (git, plataforma,
  versão do SO, data, regras do shell real). Saem as frases que só valem para Unix quando o shell é cmd.exe
  (`system_prompt.py:87`, prompt de subagente em `session.py:3020`).
- **R1.4 Leitura honesta.** O registro de leitura grava só as linhas que o modelo realmente recebeu, depois do corte de
  12.000 caracteres (`agentic/tools/files/read.py:402-408`, `agentic/agent_tools.py:1050-1075`); o resultado avisa
  onde parou e como continuar (`offset`).
- **R1.5 Stub só quando o conteúdo ainda está no contexto.** Quando um resultado de `read_file` é envelhecido
  (`runtime.py:2113-2143`) ou microcompactado (`compaction.py:39`), o registro de leitura daquele arquivo deixa de
  autorizar o stub "File unchanged since last read" (`read.py:346-397`); a próxima leitura devolve o conteúdo.
- **R1.6 Imagens repetidas.** Reabrir com `view_file` uma imagem cujo resultado anterior foi envelhecido devolve os
  pixels de novo; o cache de ferramentas somente leitura (`runtime.py:2242-2262, 2366`) não serve resultado sem
  imagem para `view_file`.
- **R1.7 Estado de leitura por conversa.** O registro de leitura sobrevive entre turnos da mesma conversa
  (`session.py:1125`, `workers/chat.py:1501`); invalidado quando o arquivo muda no disco (hash). Um fork de subagente
  recebe uma **cópia** do estado do pai, não o mesmo dicionário (`session.py:3287`). O estado vive na memória do
  processo worker (aceito: se o worker reiniciar, o modelo relê os arquivos, como hoje).
- **R1.8 Lote coerente.** Chamadas idênticas no mesmo lote executam uma vez e as repetições recebem o mesmo resultado;
  `edit_file`/`write_file` de um arquivo que só foi lido **no mesmo lote** é recusado com a mensagem
  `"You read this file in the same batch; look at the read result before editing it."`
  (`runtime.py:2229, 2264-2267, 2387-2426`); a métrica `redundant_tool_calls` conta as repetições.
- **R1.9 Validação com referência.** O stage do code mode só vai para "implementing" depois de um edit **bem-sucedido**
  e só vai para "fixing" quando a falha é nova em relação à primeira verificação do turno
  (`session.py:1333-1337, 1447-1479`). `verify_project` usa o executor do projeto (`uv run` quando há `uv.lock`,
  `poetry run` com `poetry.lock`, venv local quando existe), **não** roda `pip install` por padrão
  (`agentic/diagnostics.py:98-111`), e respeita o cancelamento do turno (`agentic/tools/orin/verify.py:73-90`).
- **R1.10 Nada do Orin no git do usuário.** Conforme D8 (`uploads/promotion.py:18,48-57`, `bash.py:44,118-127`,
  `tools/_shared.py:132-133`, `agent_tools.py:1098-1104`, `tools/planning/plan_mode.py`). Anexos antigos em
  `uploads/` continuam legíveis (migração não destrutiva: novos anexos vão para `.orin/uploads/`).
- **R1.11 Prompt colado não deixa a mensagem vazia.** Quando o texto longo vira anexo (commit `a9494ca`), a mensagem do
  usuário guarda uma linha que aponta para o anexo, para o modelo saber que aquilo é a instrução
  (investigar o fluxo exato antes; evidência: sessão `chat_2186…` com mensagem vazia e anexo `writing-block.md`).

Aceite da Fase 1: os testes unitários de cada requisito passam, a suíte existente continua verde, e um replay manual
de duas tarefas reais (a análise de upsell do Educly e a investigação de contexto do Orin) termina sem
`FILE_NOT_READ` falso, sem `old_text was not found` em arquivo CRLF e sem `uploads/` no `git status`.

### Fase 2 — Adapter próprio do Claude CLI com loop nativo

Plano: `docs/superpowers/plans/2026-11-05-fase-2-adapter-claude-cli-nativo.md`

Requisitos:

- **R2.1 Spike com portão de decisão (primeira tarefa, antes de qualquer código de produção).** Confirmar com a CLI
  instalada (2.1.309), usando chamadas mínimas:
  1. OAuth da assinatura funciona com `--restricted`;
  2. um servidor MCP HTTP passado em `--mcp-config` com `--strict-mcp-config` aparece como `connected` no evento
     `system/init` e suas ferramentas são chamáveis com `--tools "" --allowedTools "mcp__orin__*"`;
  3. com `--max-turns N`, o modelo para em cada `tool_use` e recebe o `tool_result` do servidor;
  4. imagens retornadas no `tool_result` do MCP chegam ao modelo;
  5. como a CLI se comporta durante thinking longo (há linhas no stdout? qual intervalo máximo de silêncio?);
  6. persistência de sessão: `--session-id`/`--resume` funcionam para continuar uma conversa entre processos, e onde
     a CLI grava esses arquivos (risco de aparecer no `/resume` do Claude Code do usuário);
  7. cache no protocolo de texto: duas chamadas seguidas com `--input-format stream-json`, a segunda com o mesmo
     histórico em vários blocos de texto mais um bloco novo no fim; medir `cache_read_input_tokens` da segunda. Lido
     ≥ 90% do prefixo = fallback com a janela do catálogo; abaixo disso = orçamento de 200k no fallback.
  O resultado vira um documento curto em `docs/agent_memory/` e decide: se 1–3 falharem, a Fase 2 entrega só o
  adapter próprio com o protocolo de texto corrigido (D5) e as correções R2.4–R2.8.
- **R2.2 Pacote próprio** `src/agentos/providers/claude_cli/` com módulos separados por responsabilidade: comando
  (flags), catálogo de modelos, parser do stream-json, classificação de erros, servidor MCP das ferramentas, loop
  nativo e protocolo de texto (fallback). `provider_catalog/claude_code.py` vira uma fachada fina ou é removido; nada
  importa `opencode.py`.
- **R2.3 Loop nativo.** Uma mensagem do usuário = um processo `claude -p` (ou um processo persistente por conversa, se o
  spike aprovar `--input-format stream-json` com várias mensagens). O adapter traduz os eventos da CLI para os eventos
  de atividade já existentes (deltas de texto, `tool.started`/`tool.finished`, thinking, uso), de modo que a UI não
  muda. Chamadas MCP passam pelo caminho de execução do Orin (D3). `ask_user` espera a resposta **dentro** do turno
  (aceito: exige um estado `waiting_user_in_turn` e o `ChatPage.tsx` responder pela fila de mensagens; muda lógica, não
  aparência). **Paralelismo mantido:** as chamadas MCP de uma mesma mensagem do assistente são agrupadas e executadas
  como um lote único do Orin (leituras e subagentes em paralelo, escritas em ordem); se o spike mostrar que a CLI faz
  as chamadas MCP em série, fica em série e a limitação é registrada.
- **R2.4 Modelos sem tradução** (D6): remover `_LEGACY_CLAUDE_MODEL_MAP` e atualizar `DEFAULT_MODEL_ALIASES`
  (`agentic/agent_definitions.py:31-36`); o uso é gravado com o modelo que a CLI informa (`system/init.model`,
  `result.modelUsage`); os testes que travam o mapa antigo
  (`tests/unit/provider_catalog/test_claude_code_context_and_subagents.py:153-156,170,383-391`) são reescritos.
- **R2.5 Janela, cache e compactação** (D7): o teto fixo de `workers/chat.py:938-941` sai. No caminho nativo, a janela
  é a do catálogo, a compactação é a da CLI (`--autocompact`), e o medidor de contexto usa o uso reportado pela CLI. No
  fallback de texto, o transcript vai em vários blocos, só cresce dentro da janela (aging desligado enquanto cabe;
  compactação do Orin só perto do limite), e a janela é a do catálogo ou o orçamento configurável, conforme o item 7
  do spike. O uso de cada iteração registra `cache_read` e `cache_creation` para acompanhar.
- **R2.6 Erros e limites.** Classificar por `result.api_error_status` (404 vai para o modelo reserva, não é retentado);
  ler `rateLimitType` e mostrar o reset no horário local; repassar `CLAUDE_CODE_MAX_OUTPUT_TOKENS` quando o turno define
  limite de saída; o timeout de inatividade considera o comportamento medido no spike (item 5).
- **R2.7 Progresso visível.** Em vez de silêncio, a UI recebe "pensando…" enquanto chegam deltas de thinking (mesmo
  vazios) e o progresso de cada `tool_use` assim que ele começa (`input_json_delta`).
- **R2.8 Observabilidade.** Cada execução da CLI registra no log do worker: comando com argumentos sensíveis omitidos,
  modelo pedido e modelo real, duração, código de saída, últimas linhas do stderr e uso de tokens.
- **R2.9 Ativação.** Variável `ORIN_CLAUDE_CLI_NATIVE_TOOLS` (`auto` padrão: nativo quando o spike aprovou e a CLI
  responde; `off`: protocolo de texto). Primeiro no modo código, depois em chat e friend.

Aceite da Fase 2: replay das duas tarefas reais no modo código com Opus 5.5 mostra no log o modelo real
`claude-opus-5-5`, o medidor de contexto em 1M, nenhuma resposta com mais de uma rodada de chamadas dependentes,
nenhum `[Tool Result` escrito pelo modelo, progresso visível durante toda a execução, e tokens de entrada do turno
menores que os da sessão original (216k em 10 iterações) para a mesma tarefa.

### Fase 3 — Paridade com o Claude Code e base para outras CLIs

Plano: `docs/superpowers/plans/2026-11-05-fase-3-paridade-e-base-multi-cli.md`

Requisitos:

- **R3.1 Ferramentas sob demanda.** No modo código, cerca de 15 ferramentas principais ficam sempre carregadas (Read,
  Write, Edit, Glob, Grep, Bash, TodoWrite, Agent, AskUserQuestion, SearchCode, ProjectMap, VerifyProject,
  PreviewStart/PreviewStop, ToolSearch); as demais (~60, `runtime.py:79-96`) são encontradas por uma ferramenta
  `ToolSearch`, como no Claude Code. Meta: `tools_tokens` abaixo de 4.000 no primeiro contexto do turno nos
  provedores de API e no fallback de texto; no caminho nativo, só se a CLI recarregar a lista de ferramentas depois de
  `notifications/tools/list_changed` (verificado na própria tarefa; se não recarregar, o nativo recebe a lista
  completa).
- **R3.2 Leitura e busca como no Claude Code.** `read_file` padrão de 2.000 linhas com limite por tokens no lugar do
  corte fixo de 12.000 caracteres; `Grep` diferencia maiúsculas por padrão e usa `files_with_matches` como modo padrão
  (`agentic/tools/search/grep.py:43,50`).
- **R3.3 Conclusão sem portão forçado.** O `code_completion_gate` (`agent_tools.py:1239-1260`, `runtime.py:1349-1385`)
  deixa de recusar a resposta final; a orientação de verificar vira instrução no prompt. Como não existe card de
  conclusão nos turnos normais do modo código, um evento novo `code_mode.unverified` alimenta uma marcação pequena
  "não verificado" na timeline do turno quando houve edição e nenhuma verificação rodou.
- **R3.4 Todos intactos.** `complete_remaining_todos` (`tools/planning/todo.py:78-91`) deixa de marcar itens como
  concluídos no fim do turno.
- **R3.5 Lint sob controle.** O lint automático depois de cada edit fica limitado às linhas alteradas e a um resumo
  curto, ou desligado por padrão com opção nas configurações.
- **R3.6 Sem limite fixo de processos.** O semáforo global de 3 processos (`claude_code.py:51`) sai já na Fase 2: por
  padrão não há teto de contagem (10 subagentes rodam juntos; o teto real é o de subagentes que já existe,
  `ORIN_MAX_CONCURRENT_SUBAGENTS`, padrão 16). `ORIN_CLI_MAX_PROCESSES` continua opcional para quem quiser um teto. Na
  Fase 3, a proteção passa a ser de memória: um processo novo só abre com pelo menos `ORIN_CLI_MIN_FREE_MEMORY_MB`
  (padrão 1024) de RAM livre, nunca segurando o primeiro; quem espera entra numa fila justa por conversa, com evento
  "aguardando memória/vaga" visível na UI.
- **R3.7 Contrato de adapter de CLI.** Extrair da Fase 2 a interface `CliAgentAdapter` (montar comando, traduzir
  eventos do stream, classificar erros, informar catálogo e janela, plugar o servidor MCP de ferramentas) com testes de
  conformidade que qualquer adapter novo precisa passar. O adapter do Claude é a primeira implementação. Antigravity e
  Codex ficam para specs próprias (D10).

Aceite da Fase 3: suíte de conformidade passa para o adapter do Claude; `tools_tokens` medido abaixo de 4.000; replay
das tarefas reais sem portão de conclusão forçando `verify_project` e sem todos auto-concluídos.

## 4. Fora do escopo

- Implementar os adapters de Antigravity e Codex.
- Mudanças visuais na UI além dos eventos de progresso já existentes, da marcação "não verificado" (R3.3) e do aviso
  de espera (R3.6). A mudança de lógica do `ChatPage.tsx` para o `ask_user` dentro do turno (R2.3) está dentro do
  escopo.
- Reescrever a compactação do Orin para os outros provedores.

## 5. Riscos

| Risco | Mitigação |
|---|---|
| `--restricted` não aceitar OAuth ou bloquear MCP | Spike R2.1 decide antes de escrever código; fallback D5. |
| Sessões persistentes da CLI aparecerem no `/resume` do Claude Code do usuário | Spike item 6; se acontecer, um processo por mensagem com histórico reenviado em stream-json, sem persistência. |
| Mudança de flags em versões futuras da CLI | Adapter verifica `claude --version` e cai para o fallback quando a versão não foi validada. |
| Git Bash ausente em outras máquinas | Fallback para cmd.exe com o prompt dizendo o shell real (R1.2). |
| Subir a janela para 1M aumentar o custo | Cache incremental nos dois caminhos (D7); orçamento de 200k só se o spike reprovar o cache do texto. |
| Muitos processos da CLI ao mesmo tempo esgotarem a RAM ou esbarrarem em limite de concorrência da assinatura | Proteção de memória (R3.6); erros 429 de concorrência classificados e retentados com espera (R2.6). |

## 6. Ligação com as metas do Q4/2026

Esta spec não move nenhuma das 8 metas diretamente; ela torna o Orin Code utilizável para o trabalho que move
(principalmente as metas 1, 3, 5 e 8, que são código no Educly e no helpdesk).
