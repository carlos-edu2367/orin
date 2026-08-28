# Orin — Trilha A: assertividade do runtime agêntico

Data: 2026-08-28
Base: `main`, commit `876bd01`, versão `0.2.11`
Escopo: kernel do turno (`agentos.agentic`), projeção de histórico (`agentos.conversations.chat`), configuração de limites (`agentos.workers.chat`)
Decisões já tomadas: paridade real com modelos locais pequenos; migration nova autorizada; entrega em três trilhas (A → B → C) com a linha de base de C3 adiantada.

## 1. Problema

O Orin conversa bem e age mal. Um turno agêntico gasta muitas chamadas de ferramenta e entrega pouco, com qualquer modelo. As causas estão no kernel, não no provider.

### 1.1 O contexto entre turnos é destruído

`ChatApplication.history_for_turn` (`src/agentos/conversations/chat.py:516`) devolve apenas linhas de `conversation_messages`, cuja `CheckConstraint` admite somente os papéis `user` e `assistant` (`src/agentos/persistence/postgres/schema.py:549`). Nenhuma `tool_call` nem `tool_result` sobrevive ao fim do turno.

O único resíduo é `tool_ledger` (`src/agentos/conversations/chat.py:256`): até 20 registros, `arguments` truncado em 1000 caracteres na tabela e reduzido a 120 no prompt, `summary` a 512 e 120 respectivamente (`src/agentos/agentic/session.py:317`).

Consequência direta: em "reformule este orçamento", o segundo turno não sabe quais arquivos foram lidos, o que foi escrito, nem com que números. Ele redescobre tudo. É a origem principal do excesso de chamadas.

### 1.2 Não existe orçamento nem plano

`ChatWorker` monta `AgenticLimits` com `max_iterations` vindo de `AgentRuntimeSettingsStore`, cujo padrão é `None` (`src/agentos/agentic/settings.py:22`), e converte isso em `max_actions=None` (`src/agentos/workers/chat.py:842`), com `TURN_DEADLINE` de 3600 segundos (`src/agentos/workers/chat.py:111`). Não há ferramenta de plano, lista de tarefas ou contrato. O loop termina quando o modelo decide terminar.

### 1.3 A janela é travada em 60k independentemente do modelo

`_max_context_tokens_for` retorna `min(DEFAULT_MAX_CONTEXT_TOKENS, context_window - CONTEXT_WINDOW_RESERVE_TOKENS)` com `DEFAULT_MAX_CONTEXT_TOKENS = 60_000` (`src/agentos/workers/chat.py:94` e `:657`). Um modelo de 200k recebe 60k; um de 1M recebe 60k. `CONTEXT_COMPACTION_THRESHOLD = 0.82` (`src/agentos/agentic/runtime.py:17`) dispara compactação a aproximadamente 49k.

O marcador de corte inserido por `_request_messages` (`src/agentos/agentic/runtime.py:499`) instrui o modelo, em texto: "re-read files or re-run searches if you need their content". O sistema pede o desperdício que estamos tentando eliminar.

### 1.4 A compactação é cega

`_maybe_compact` (`src/agentos/agentic/runtime.py:402`) troca N unidades por um resumo em prosa livre, precedido de "use os arquivos e ferramentas para confirmar detalhes". Nada garante a sobrevivência de caminhos de arquivo, decisões tomadas ou números apurados.

### 1.5 Cinquenta ferramentas sempre publicadas

`AgentToolset.schemas` (`src/agentos/agentic/agent_tools.py:591`) publica todas as definições construídas em `_build_definitions` (`:273`): sistema de arquivos, terminal, web, doze ferramentas de navegador, memória, skills, MCP, plugins e subagentes. Não há disclosure progressivo. Para um modelo pequeno o espaço de decisão é grande demais para ser navegado de forma confiável.

## 2. Objetivo

Ao fim da Trilha A, para a mesma tarefa e o mesmo modelo:

1. o agente conserva entre turnos o que já descobriu e já fez;
2. o agente declara um plano antes de agir e é medido contra ele;
3. o agente vê, a cada momento, apenas as ferramentas relevantes à fase em que está;
4. o agente usa a janela real do modelo;
5. o turno tem orçamento por fase, não um limite global ausente.

## 3. Não-objetivos

- Otimização de cache de prompt e de custo por token: **Trilha B**.
- Contrato de conclusão com evidência obrigatória e subagentes estruturados: **Trilha C**.
- Qualquer alteração na experiência de instalação, atualização ou onboarding: fora de escopo, coberto pela auditoria `docs/audits/2026-08-28-auditoria-produto-arquitetura-e-prontidao-publica.md`.
- Mudança no contrato de reconciliação de efeitos (`EFFECT_RECONCILIATION_REQUIRED`). A garantia permanece invariável.

## 4. Linha de base mensurável (Trilha 0, executada antes de A1)

Sem linha de base, "200% mais assertivo" não é verificável. Antes de qualquer mudança de comportamento, instrumentamos o que hoje não é medido.

### 4.1 Contrato

Nova tabela `turn_quality_metrics`, uma linha por turno concluído:

| coluna | tipo | significado |
|---|---|---|
| `turn_id` | String(255), PK | turno |
| `conversation_id`, `user_id` | String(255) | escopo |
| `provider`, `model_id` | String(32) / String(512) | modelo do turno |
| `tool_calls` | Integer | total de invocações |
| `redundant_tool_calls` | Integer | invocações cuja assinatura `(nome, argumentos)` já havia sido executada **com sucesso** no mesmo turno |
| `iterations` | Integer | iterações do loop |
| `input_tokens`, `output_tokens` | Integer | soma reportada pelo provider |
| `cached_input_tokens` | Integer, nullable | tokens lidos de cache, quando o provider reporta |
| `outcome` | String(32) | `completed`, `failed`, `cancelled`, `waiting_user`, `reconciliation_required` |
| `error_code` | String(64), nullable | código terminal |
| `duration_ms` | Integer | do `running` ao terminal |
| `created_at` | DateTime(timezone=True) | |

`redundant_tool_calls` é derivado de um conjunto de assinaturas de sucesso mantido em memória durante o turno — o espelho de `_failed_signatures` (`src/agentos/agentic/runtime.py:127`), que hoje só rastreia falhas.

`cached_input_tokens` exige estender `ProviderUsage` para transportar o campo e lê-lo de `cache_read_input_tokens` (Anthropic) e `prompt_tokens_details.cached_tokens` (OpenAI-compatível). Quando o provider não reporta, permanece `NULL` — nunca zero, que significaria "medido e não houve cache".

### 4.2 Leitura

Um endpoint `GET /agent-runtime/quality` agrega por (provider, model_id, janela de tempo) e devolve: chamadas por turno concluído, fração redundante, tokens de entrada por turno concluído, fração vinda de cache, e taxa de conclusão. É a métrica contra a qual as trilhas A e B serão avaliadas.

### 4.3 Definição do alvo

O "200%" fica definido, e verificável, como a conjunção de três medidas sobre o mesmo conjunto de tarefas de referência e o mesmo modelo:

- **chamadas de ferramenta por tarefa concluída caem pelo menos 50%** (equivale a dobrar a eficiência por chamada);
- **taxa de conclusão sem intervenção sobe pelo menos 50% em termos relativos**;
- **`redundant_tool_calls / tool_calls` cai abaixo de 5%**, de qualquer valor que a linha de base revele.

O conjunto de tarefas de referência é definido em 4.4 e congelado antes da primeira mudança.

### 4.4 Conjunto de tarefas de referência

Doze tarefas fixas, versionadas em `tests/fixtures/agent_bench/`, executáveis contra um workspace semeado e um provider real escolhido pelo operador. Quatro categorias, três tarefas cada:

1. **Documento**: reformular um orçamento em `.xlsx`, extrair dados de um PDF para uma planilha, produzir um resumo comparativo de dois documentos.
2. **Script**: escrever e executar um script que transforma um CSV, corrigir um script que falha, empacotar uma rotina em arquivo reutilizável.
3. **Multi-turno**: uma tarefa de documento seguida de duas revisões que dependem do que foi feito antes. Esta categoria é a que mede diretamente A1.
4. **Exploração**: responder três perguntas sobre um projeto de código semeado, exigindo busca antes de resposta.

Cada tarefa declara critérios de aceite verificáveis por asserção (arquivo existe, contém valor, script sai com código 0). O bench não julga estilo.

## 5. Arquitetura

O kernel ganha três conceitos e mantém todo o resto:

```
                    ┌──────────────────────────────────────────┐
   turno anterior → │  TurnTranscript (durável, A1)            │
                    └──────────────────┬───────────────────────┘
                                       │ reidratação
                                       ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  AgenticTurnRuntime                                                 │
  │                                                                     │
  │   PhaseController (A2)  ──── fase ────►  ToolsetView (A2)           │
  │        │                                    │                       │
  │        │                              schemas da fase               │
  │        ▼                                    ▼                       │
  │   PhaseBudget (A5)                     provider.stream              │
  │        │                                    │                       │
  │        └──────────► TaskContract (A3, pinado) ◄────────────┐        │
  │                                             │              │        │
  │                     ContextManager (A4) ────┴──────────────┘        │
  │                       janela real + compactação estruturada         │
  └─────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                            TurnTranscript (persistido)
```

`PhaseController`, `TaskContract` e `TurnTranscript` são novos. `ToolsetView`, `PhaseBudget` e `ContextManager` são refatorações do que já existe dentro de `AgenticTurnRuntime` e `AgentToolset`, extraídas para poderem ser testadas isoladamente — `runtime.py` já tem 950 linhas e não deve absorver mais responsabilidade.

## 6. A1 — Transcript de turno durável

### 6.1 Decisão

Uma tabela nova, `conversation_turn_steps`, e **não** uma extensão de `conversation_messages`. Razões: a `CheckConstraint` de papéis existe para proteger a projeção que a interface renderiza; `content` é `String(16000)`, insuficiente para um resultado de ferramenta; e a numeração `sequence` por conversa é consumida pela paginação da UI. Misturar os dois contratos acopla a memória do agente à renderização do chat.

### 6.2 Esquema

Migration `0041_conversation_turn_steps`, `down_revision = "0040_execution_recovery_journal"`.

| coluna | tipo | nota |
|---|---|---|
| `id` | Integer, PK | |
| `step_id` | String(255), unique | `step:{turn_id}:{sequence}` |
| `conversation_id`, `turn_id`, `user_id` | String(255) | |
| `agent_id` | String(255) | `main` ou o id do subagente |
| `sequence` | Integer | ordem dentro do turno |
| `kind` | String(16) | `assistant`, `tool_call`, `tool_result`, `contract`, `summary` |
| `tool_name` | String(64), nullable | |
| `tool_call_id` | String(255), nullable | liga `tool_call` ao seu `tool_result` |
| `payload` | Text | JSON |
| `content_bytes` | Integer | tamanho original antes de qualquer truncamento |
| `truncated` | Boolean | verdadeiro quando `payload` não é íntegro |
| `created_at` | DateTime(timezone=True) | |

`UniqueConstraint("turn_id", "agent_id", "sequence")` e índice em `(conversation_id, turn_id, sequence)`.

### 6.3 Limite de tamanho

Cada `tool_result` é gravado até **32.768 caracteres**. Acima disso, grava o prefixo, marca `truncated=true` e registra `content_bytes` real. O valor é deliberadamente maior que os 12.000 do `private_result` do journal de efeitos, porque este registro serve à cognição do agente e aquele à recuperação. Um resultado truncado é reidratado com um rodapé explícito informando o tamanho original e o nome da ferramenta que o produziu.

### 6.4 Escrita

`AgenticTurnRuntime` já constrói exatamente as mensagens necessárias em `_assistant_tool_message` e `_tool_result_messages`. A escrita entra como um método novo do store, `record_step(turn, *, kind, ...)`, chamado nos mesmos pontos, dentro da transação que já grava a atividade. Uma falha de gravação **nunca** interrompe o turno: é registrada e o turno continua sem transcript, degradando ao comportamento atual.

### 6.5 Reidratação

`history_for_turn` passa a montar o histórico intercalando, em ordem de sequência: as mensagens `user`/`assistant` de `conversation_messages` e os passos de `conversation_turn_steps` do turno correspondente, projetados na forma do provider do turno **atual** (não do turno em que foram gravados — o usuário pode ter trocado de modelo).

A reidratação é **orçada**: no máximo `REHYDRATION_TOKEN_BUDGET` (padrão 40% da janela efetiva do turno) é gasto com passos de turnos anteriores, preenchido do mais recente para o mais antigo, em unidades atômicas `tool_call` + `tool_result` — a mesma invariante que `_group_tool_units` já protege (`src/agentos/agentic/runtime.py:576`). O que não couber é substituído por um resumo estruturado (ver A4), não descartado em silêncio.

### 6.6 Compatibilidade

Conversas anteriores à migration não têm passos. `history_for_turn` devolve exatamente o que devolve hoje para elas. Não há backfill: a informação não existe.

## 7. A2 — Fases determinísticas e toolset por fase

### 7.1 As fases

| fase | propósito | transição de saída |
|---|---|---|
| `orient` | entender o pedido e o estado do workspace | plano escrito, ou orçamento da fase esgotado |
| `plan` | escrever o contrato de tarefa | contrato válido gravado |
| `execute` | fazer o trabalho | critérios de aceite atendidos, ou orçamento esgotado |
| `verify` | conferir o próprio resultado | verificação registrada, ou orçamento esgotado |
| `respond` | responder à pessoa | fim do turno |

A transição é do runtime. O modelo **não** escolhe a fase; ele observa em qual está, através do bloco de fase do prompt e das ferramentas publicadas.

### 7.2 Salto rápido

Uma pergunta conversacional não deve atravessar cinco fases. `PhaseController` começa em `orient` com um orçamento de **uma** iteração; se essa iteração produz texto final sem pedir ferramenta, o turno vai direto para `respond` e termina. Nenhuma conversa fica mais lenta do que é hoje.

Se o turno pede ferramenta, ele entra no ciclo completo. Um turno que já carrega um contrato válido reidratado (turno de continuação) começa em `execute`, não em `orient`.

### 7.3 Toolset por fase

`AgentToolset` ganha um mapa fase → conjunto de nomes, e `schemas(phase)` filtra. `ToolDefinition` já carrega `kind` e `policy_tags` (`src/agentos/agentic/agent_tools.py:273`), o que dá quase toda a classificação de graça.

| fase | ferramentas publicadas |
|---|---|
| `orient` | `list_files`, `read_file`, `view_file`, `search_files`, `search_code`, `project_map`, `recall`, `ask_user`, `write_contract` |
| `plan` | `write_contract`, `ask_user`, `search_skills`, `use_skill` |
| `execute` | núcleo de arquivos e terminal, mais o toolkit pedido pelo contrato (ver 7.4) |
| `verify` | somente leitura: `read_file`, `list_files`, `search_files`, `run_command`, `view_file`, `browser_observe` |
| `respond` | nenhuma (`tool_choice="none"`) |

O teto de ferramentas publicadas por requisição é **12**. Quando um toolkit ultrapassa isso, o excedente entra pela ordem declarada no contrato.

### 7.4 Toolkits declarados

O contrato de tarefa (A3) declara os toolkits de que precisa. São cinco, nomeados e fechados: `files`, `terminal`, `web`, `browser`, `delegation`. `mcp` e `plugins` são um sexto e sétimo, publicados apenas quando o contrato os nomeia — hoje eles inflam todo request, e são exatamente as ferramentas que um modelo pequeno mais confunde.

Se o modelo pede uma ferramenta fora dos toolkits declarados, o runtime devolve um resultado `failed` com o código `TOOLKIT_NOT_DECLARED` e o texto explicando como estender o contrato com `write_contract`. Isso é uma correção de rota barata, não um fracasso do turno.

### 7.5 Prompt em camadas

`build_system_prompt` (`src/agentos/agentic/session.py:209`) hoje monta um bloco único. Passa a montar três:

1. **estático**: identidade, regras de trabalho, ambiente, workspace hint. Idêntico entre turnos do mesmo workspace e mesmo modelo — é o que a Trilha B vai cachear;
2. **de fase**: as instruções da fase atual, entre 5 e 15 linhas. Substitui os blocos condicionais de browser, skills, subagentes e PDF, que hoje ficam sempre presentes;
3. **volátil**: contrato de tarefa, árvore do workspace, memórias, contexto de hooks, data.

A separação já está desenhada para B1; aqui ela é feita porque a camada de fase é pré-requisito de A2. A ordem no request é estático → volátil → de fase, para que o bloco de fase seja o mais próximo da conversa.

## 8. A3 — Contrato de tarefa pinado

### 8.1 Forma

Uma ferramenta `write_contract` com schema fechado:

```json
{
  "objective": "string, uma frase",
  "deliverables": [{"path": "string", "description": "string"}],
  "constraints": ["string"],
  "acceptance": [{"id": "string", "check": "string", "how": "tool|inspection"}],
  "toolkits": ["files", "terminal", "web", "browser", "delegation", "mcp", "plugins"],
  "steps": ["string"]
}
```

`acceptance` é o que torna a fase `verify` possível: cada item é uma afirmação verificável, e `how` diz se ela se confirma rodando algo ou olhando algo.

### 8.2 Persistência e pin

O contrato é gravado como um passo `kind="contract"` do transcript (A1) e mantido em `TaskContract`, que o runtime insere no bloco volátil do prompt a cada iteração. Ele é **imune a trim e a compactação**: `_request_messages` e `_maybe_compact` nunca o consideram elegível, do mesmo modo que já protegem `_pinned_index` (`src/agentos/agentic/runtime.py:127`).

Um turno de continuação reidrata o contrato do turno anterior. O modelo pode reescrevê-lo com `write_contract` — a versão anterior fica no transcript, a nova passa a valer.

### 8.3 Validação

Um contrato sem `objective`, sem `acceptance` ou sem `toolkits` é rejeitado com mensagem explicando o campo faltante. Três rejeições seguidas na fase `plan` fazem o runtime **sintetizar** um contrato mínimo a partir do pedido do usuário (`objective` = a mensagem, `toolkits` = `files`, `acceptance` = um item genérico) e seguir para `execute`. Um modelo fraco que não consegue preencher o schema não pode travar o turno.

## 9. A4 — Janela real e compactação estruturada

### 9.1 Janela

`_max_context_tokens_for` (`src/agentos/workers/chat.py:657`) passa a retornar `max(MIN_MAX_CONTEXT_TOKENS, window - reserve)`, sem o teto de 60k. `reserve` deixa de ser a constante `CONTEXT_WINDOW_RESERVE_TOKENS = 12_000` e passa a ser `max(12_000, ceil(window * 0.10))`, limitado a 64.000: uma janela de 1M não precisa reservar 100k, mas 12k é pouco demais para um prompt com trinta ferramentas.

`DEFAULT_MAX_CONTEXT_TOKENS = 60_000` permanece, agora só para o caso em que o catálogo não conhece a janela — que é a situação de risco que a constante existe para cobrir.

O `num_ctx` do Ollama (`_num_ctx_for`, `src/agentos/workers/chat.py:672`) **não** acompanha essa liberação: continua limitado por `OLLAMA_FALLBACK_NUM_CTX` e pela janela do modelo, porque ali o custo é VRAM real na máquina da pessoa. O comentário existente sobre spill para RAM permanece válido e deve ser preservado.

### 9.2 Compactação estruturada

`_maybe_compact` (`src/agentos/agentic/runtime.py:402`) mantém a mecânica de agrupar unidades e chamar o provider, e muda o produto. O resumo passa a ser pedido em schema fechado e renderizado em seções fixas:

```
## Contexto compactado
### Arquivos tocados
- caminho — o que foi feito
### Decisões
- decisão — porquê
### Dados apurados
- rótulo: valor
### Pendências
- o que falta
```

Se o provider não devolve algo aproveitável, `_fallback_compaction_summary` (`src/agentos/agentic/runtime.py:474`) preenche as mesmas seções a partir dos passos do transcript, que são estruturados — hoje ele concatena 240 caracteres de cada mensagem, o que perde exatamente os números.

O cabeçalho atual, "use os arquivos e ferramentas para confirmar detalhes", é substituído por: "Este resumo é confiável para caminhos, decisões e valores. Releia um arquivo apenas se esperar que ele tenha mudado." A frase atual convida ao retrabalho que estamos medindo em `redundant_tool_calls`.

### 9.3 Marcador de corte

O marcador de `_request_messages` (`src/agentos/agentic/runtime.py:499`) deixa de dizer "re-read files or re-run searches" e passa a apontar o resumo estruturado: "N mensagens anteriores estão resumidas acima." Quando não há resumo (turno curto que estourou por um único resultado grande), o texto nomeia as ferramentas cujos resultados saíram, para que reler seja uma escolha informada e não um reflexo.

## 10. A5 — Orçamento por fase

`AgenticLimits` ganha `phase_budgets: Mapping[str, PhaseBudget]`, onde `PhaseBudget` é um par imutável `(iterations, actions)`. Padrões:

| fase | iterações | ações |
|---|---|---|
| `orient` | 3 | 8 |
| `plan` | 2 | 3 |
| `execute` | 20 | 60 |
| `verify` | 4 | 10 |
| `respond` | 1 | 0 |

Esgotar o orçamento de uma fase **avança** para a próxima; não falha o turno. `execute` esgotado leva o contrato para `verify` com o que existe, e `verify` esgotado leva a `respond` com uma declaração explícita do que não foi verificado.

`max_iterations` e `max_actions` globais permanecem como teto absoluto e continuam configuráveis. `None` continua significando sem teto global — os orçamentos de fase é que passam a impedir a deriva. `TURN_DEADLINE` de 3600s permanece.

Para um modelo sem `tools` no catálogo (`_model_calls_tools`, `src/agentos/workers/chat.py:581`), `PhaseController` opera em modo degenerado: `orient` → `respond`, exatamente o comportamento de hoje.

## 11. Erros e degradação

Toda peça nova falha para o comportamento atual, nunca para o turno:

| falha | comportamento |
|---|---|
| gravação de passo do transcript | registra e continua sem transcript |
| leitura do transcript na reidratação | histórico atual (só `user`/`assistant`) |
| contrato inválido três vezes | contrato sintetizado, segue para `execute` |
| toolkit não declarado pedido | resultado `failed` com `TOOLKIT_NOT_DECLARED` e instrução de correção |
| resumo de compactação vazio | fallback estruturado a partir do transcript |
| catálogo sem janela do modelo | `DEFAULT_MAX_CONTEXT_TOKENS` |
| modelo sem suporte a ferramentas | `orient` → `respond` |

A garantia de reconciliação de efeitos incertos não é tocada: `reconciliation_required` continua sendo consultado no mesmo ponto do loop, e uma fase nunca avança por cima de um checkpoint pausado.

## 12. Testes

Unitários novos, em `tests/unit/agentic/`:

- `test_turn_transcript.py` — gravação, ordem, truncamento em 32k com `content_bytes` correto, unidade `tool_call`+`tool_result` nunca órfã na reidratação, orçamento de reidratação respeitado, conversa sem passos devolve o histórico atual.
- `test_phase_controller.py` — salto rápido em turno conversacional; ciclo completo em turno com ferramenta; turno de continuação começa em `execute`; orçamento esgotado avança sem falhar; modelo sem ferramentas vai direto a `respond`.
- `test_phase_toolsets.py` — teto de 12 schemas por requisição; `verify` publica apenas leitura; `mcp`/`plugins` ausentes sem declaração; `TOOLKIT_NOT_DECLARED` para ferramenta fora do contrato.
- `test_task_contract.py` — validação de campos; imunidade a trim e a compactação; síntese após três rejeições; reescrita preserva a versão anterior no transcript.
- `test_context_window.py` (existente, estendido) — janela real sem teto de 60k; reserva proporcional; `num_ctx` do Ollama **não** liberado.
- `test_structured_compaction.py` — seções presentes; fallback preenche as mesmas seções; marcador de corte não instrui reexecução.
- `test_turn_quality_metrics.py` — `redundant_tool_calls` conta repetição bem-sucedida e não conta a primeira; `cached_input_tokens` fica `NULL` quando o provider não reporta.

Integração, em `tests/integration/`:

- um turno completo com transcript persistido, seguido de um segundo turno que **não** relê um arquivo já lido — a asserção direta de que A1 funciona.

O bench de 4.4 não roda em CI (precisa de provider real). É o script `scripts/agent_bench.py`, com resultado gravado em `docs/agent_memory/`.

## 13. Riscos

**Reidratação inflando o prompt.** Um turno de continuação passa a carregar trajetória que hoje não carrega. Mitigação: o orçamento de 40% da janela em 6.5, e a medição de `input_tokens` por turno já instrumentada na Trilha 0 — se subir sem que `tool_calls` caia proporcionalmente, o orçamento está errado e é um número, não uma reescrita.

**Fases engessando modelo forte.** O salto rápido (7.2) protege a conversa; o risco real é uma tarefa exploratória travada em `orient`. Mitigação: `orient` esgota em 3 iterações e avança; e `execute` tem 20.

**Contrato virando cerimônia.** Se `plan` gastar duas iterações em toda tarefa trivial, pioramos a latência. Mitigação: só entra no ciclo quem pediu ferramenta, e a síntese automática (8.3) evita insistência.

**Migration em base instalada.** `0041` é aditiva, sem backfill e sem alteração de tabela existente. O risco é o da própria atualização, coberto por P0-04/P0-06 da auditoria — que **não** são resolvidos aqui e continuam sendo pré-requisito de release pública.

## 14. Critérios de aceite da trilha

1. `turn_quality_metrics` grava uma linha por turno concluído e `GET /agent-runtime/quality` agrega, com linha de base do bench registrada **antes** de A1.
2. Um segundo turno sobre a mesma tarefa não repete leitura de arquivo já lido no primeiro (teste de integração).
3. Nenhuma requisição publica mais de 12 schemas de ferramenta.
4. Um modelo com janela de 200k recebe orçamento de contexto acima de 60k.
5. Um turno conversacional continua terminando em uma iteração.
6. Toda linha da tabela de degradação em §11 tem teste.
7. O bench de 4.4 mostra queda de pelo menos 50% em chamadas por tarefa concluída e `redundant_tool_calls / tool_calls` abaixo de 5%, no mesmo modelo da linha de base.

O critério 7 é o que autoriza declarar a Trilha A concluída. Os demais são condição para chegar até ele.

---

## 15. Desvios da implementação

Registrados na entrega, com o motivo. O desenho acima é a intenção; esta seção é o que foi construído onde os dois divergem.

### 15.1 As fases não são um funil (§7.1, §7.3)

**Previsto:** `orient` publicaria apenas ferramentas de leitura, e o trabalho começaria em `execute`.

**Construído:** `orient` publica também o núcleo de escrita (`write_file`, `edit_file`, `run_command`).

**Motivo:** com conjuntos disjuntos, "crie um arquivo" passaria de 2 para 4–5 chamadas de provider — uma regressão disfarçada de melhoria, e justamente no tipo de tarefa mais comum. A redução de ~50 para ~13 ferramentas continua valendo em `orient`; o que fica de fora é navegador, MCP, plugins e subagentes, que é de onde vinham as 50. O que dispara `plan` é o esgotamento do orçamento de `orient` sem conclusão — o momento em que o agente está demonstravelmente patinando.

### 15.2 O teto de 12 ferramentas por requisição não é um truncamento (§7.3)

**Previsto:** teto rígido de 12 schemas por requisição, com excedente entrando por ordem do contrato.

**Construído:** cada fase declara seu conjunto explicitamente (≤16 com toolkits básicos); um toolkit declarado soma sua família inteira. Não há truncamento.

**Motivo:** um contrato que declara `browser` precisa das 12 ferramentas de navegador. Cortar por prioridade removeria em silêncio a ferramenta de que a tarefa depende — trocaria um modo de falha ruidoso por um mudo e pior. O teto virou asserção de teste sobre os conjuntos base, não mecanismo de corte.

### 15.3 A fase de verificação é alcançada por esgotamento, não por toda alegação de conclusão (§7.1, §10)

**Previsto:** `execute` → `verify` quando os critérios de aceite fossem atendidos.

**Construído:** `verify` é alcançada quando `execute` esgota o orçamento. Quando o modelo conclui por conta própria, o turno termina como hoje.

**Motivo:** o texto final é transmitido por streaming à pessoa enquanto é gerado. Encaminhar para `verify` depois disso mostraria uma resposta "final", depois mais atividade, depois outra resposta. Tornar a verificação obrigatória exige mudar o protocolo de resposta — suprimir o stream até a verificação passar — que é exatamente o que **C1** precisa fazer. Fazer metade disso aqui produziria uma UX pior do que a atual.

O ganho preservado é real: um `execute` esgotado hoje termina em `ITERATION_LIMIT` sem nada; agora passa por uma verificação somente-leitura e uma resposta de fechamento.

### 15.4 O prompt em camadas está parcial (§7.5)

**Construído:** a camada de fase existe e é injetada por requisição. A separação entre bloco estático e bloco volátil **não** foi feita.

**Motivo:** essa separação existe para o cache (B1). Fazê-la aqui, sem os breakpoints de cache que a justificam, seria refatoração sem benefício mensurável. O prompt ganhou uma linha dizendo que nem toda ferramenta descrita está publicada em toda requisição, para o modelo não planejar em cima de uma ferramenta que não enxerga.

### 15.5 O limite do transcript é rede de segurança, não limite operativo (§6.3)

`MAX_TOOL_RESULT_CHARS = 12_000` já limita todo resultado de ferramenta antes de chegar ao transcript. Os 32.768 caracteres continuam valendo, mas na prática nenhum resultado os alcança.

### 15.6 O bench mede, não dirige (§4.4)

**Previsto:** um runner que executa as doze tarefas contra um provider real.

**Construído:** as doze tarefas versionadas, mais um script que lê `turn_quality_metrics` pela API e compara duas execuções, declarando se o alvo foi atingido.

**Motivo:** um driver HTTP escrito sem credencial não pode ser executado nem verificado, e um driver não verificado produz números em que ninguém deveria confiar. Enviar as tarefas pela interface, com o provider real, também é a única forma de os números descreverem o produto e não o arnês.

### 15.7 Correções de gate fora do escopo da trilha

O portão de release estava vermelho antes desta trilha: 9 erros de lint (`react-hooks/set-state-in-effect`) e 1 E2E falhando, ambos presentes em `v0.2.11`. Foram corrigidos aqui porque bloqueavam um release que pudesse ser honestamente chamado de verde — não porque pertençam à Trilha A. Isso endereça parcialmente o **P0-01** da auditoria: o gate agora passa; fazer a publicação *depender* dele continua pendente.
