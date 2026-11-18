# Orin 0.13: orquestração e multiagentes (relatório consolidado)

Data: 18/11/2026. Versão de referência do código: 0.12.31.
Escopo: somente levantamento. Nenhum código foi alterado. A decisão do que entra na 0.13 fica com o Carlos.

## 1. Conclusões principais

1. **INS-022 (revisão cruzada) vale a pena, mas não como regra cega de "família diferente".** O único estudo controlado de revisão de código entre Claude e Codex que encontrei mostra efeito assimétrico: Claude revisando Codex subiu a taxa de acerto de 71,6% para 89,7%, e Codex revisando Claude baixou de 91,4% para 82,8%. A regra que a evidência sustenta é: revisor independente, igual ou mais forte que o autor, de outra família quando houver uma à altura, com poder de executar testes e achados que exigem prova.
2. **INS-023 (expurgo atômico) já está em boa parte implementado.** A troca de chunks por arquivo já é transacional e por `content_hash`, e as memórias já são filtradas por `superseded_by IS NULL` no SQL, antes do ranking. O "Frankenstein" que ainda pode acontecer vem de outros lugares: contradição sem vínculo explícito, índice de código defasado em até 60 s, arquivos ilegíveis que mantêm chunks velhos e conflito entre memória e arquivos de instrução.
3. **Esforço é o item mais barato e com efeito imediato.** Hoje o chat não tem seletor de esforço, o campo de esforço por turno existe mas nada o grava, subagentes não herdam o esforço do pai e a API da Anthropic ainda recebe o formato legado (`budget_tokens`).
4. **O maior buraco de orquestração é o verificador.** O Orin tem um subagente `verifier` definido, mas ele nunca é acionado sozinho: a fase VERIFY é feita pelo mesmo agente e pelo mesmo modelo que implementou.
5. **O Orin tem uma vantagem que quase nenhum harness tem:** três CLIs de assinatura de famílias diferentes (Claude, Codex, Antigravity) no mesmo runtime. Nos concorrentes, revisão por outra família é configuração manual do usuário. No Orin pode ser comportamento padrão.
6. **Faltam quatro peças estruturais** para chamar de orquestração completa: isolamento de escrita por subagente, quadro de tarefas compartilhado, agentes em background que sobrevivem a reinício e pipelines declarativos.

## 2. Método e grau de confiança

- **Código do Orin:** mapeado por três subagentes e conferido por mim nos pontos centrais (verifier, gate de verificação, memória, índice, esforço). As referências `arquivo:linha` abaixo foram lidas por mim, salvo quando marcado "(relato de subagente)".
- **Pesquisa externa:** três subagentes leram cerca de 90 fontes (documentação oficial, READMEs, código-fonte e artigos). Os relatórios completos estão em `.orin/tool-outputs/check_subagent-7aca209b9bf2.txt` (harnesses de código), `check_subagent-4ca1ad7aa505.txt` (frameworks e evidência empírica) e `check_subagent-a40e5fd6b995.txt` (memória e indexação).
- **Conferido por mim direto na fonte:** Claude Code (subagentes, agent teams, model-config, workflows, `/goal`), Codex (subagentes, `/review` e `review_model`), opencode (agentes), Roo (Orchestrator), PAL MCP (README), LangGraph (persistência e tolerância a falhas), OpenAI Agents SDK (handoffs), beads, mem0 (migração para ADD-only), API de esforço da Anthropic, os artigos da Anthropic e os papers 2404.13076, 2506.07962, 2503.13657 e 2607.21656.
- **Limite:** a ferramenta de leitura web devolve um resumo feito por modelo, não o HTML bruto. Nomes de campos e números podem ter erro de transcrição. O que não foi aberto está marcado "(não verificado)".
- **Estimativas de tamanho (P/M/G)** são ordem de grandeza minha, sem medição.

## 3. Estado atual do Orin

### 3.1 Orquestração

O que já existe: subagentes (`explore`, `plan`, `general`, `fork`, `verifier` e agentes custom em `.md`), agentes em background com `CheckSubagent`, times com `SendMessage` e caixa de correio, ferramentas de tarefa, plan mode, fases ORIENT→PLAN→EXECUTE→VERIFY→RESPOND, hooks (`SubagentStart/Stop`, `TeammateIdle`, `Stop`), worktree manual, cron e wakeup, uso registrado por agente e árvore de agentes com `parent_agent_id`. Os relatórios de subagente já vão para arquivo (`.orin/agent-outputs/`) e voltam por referência, que é o padrão recomendado pela Anthropic.

Lacunas:

| # | Lacuna | Evidência |
|---|---|---|
| L1 | VERIFY é feito pelo mesmo agente e modelo que implementou. O `verifier` nunca é acionado automaticamente. | `src/agentos/agentic/runtime.py:1526-1546` |
| L2 | O `verifier` não tem modelo, provedor nem esforço próprios; herda os do pai. | `src/agentos/agentic/agent_definitions.py:225-240` |
| L3 | Subagentes ficam fora do gate de verificação. | `runtime.py:3509` (relato de subagente) |
| L4 | A ferramenta `Agent` só aceita `model_id`. Para rodar um filho em outro CLI é preciso um arquivo de agente com provedor fixado. | `session.py:2417-2497` |
| L5 | Sem isolamento de escrita. O campo `isolation` é lido e ignorado, todos escrevem no mesmo `workspace.root`, e `write_claims.py` só atribui autoria. | `agent_definitions.py`, `src/agentos/agentic/write_claims.py` |
| L6 | Sem quadro de tarefas compartilhado. Cada subagente começa com a lista de tarefas vazia. | relato de subagente |
| L7 | Estado e histórico de agentes em background só em memória. Após reinício, os órfãos são marcados como falhos, sem retomada. | `src/agentos/agentic/background_agents.py` |
| L8 | Campos de definição inertes: `memory`, `isolation`, `mcp_servers`, `color`. | `agent_definitions.py` |
| L9 | Sem teto de tokens ou custo por subagente. Os limites são 20 ações, 16.384 tokens de saída, profundidade 3 (teto 6) e 16 simultâneos por turno. | `session.py:176-177` e relato de subagente |
| L10 | Sem pipeline declarativo. Só existem as fases dentro do turno de um agente. | `src/agentos/agentic/phases.py` |
| L11 | Paralelismo fraco em turnos de CLI: as chamadas de ferramenta são serializadas e dependem de `run_in_background`. | relato de subagente |
| L12 | O hook do tipo agente roda um `explore`, não o `verifier`. | `session.py:5517-5534` |
| L13 | Os RFCs `docs/architecture/200-agents/202-orchestrator.md` e `203-multi-agent.md` divergem do runtime real. | leitura dos documentos |

Observação desta própria sessão, que serve de evidência de uso: três subagentes de pesquisa rodaram cerca de 25 minutos em background sem nenhum sinal de progresso. O `CheckSubagent` espera no máximo 60 s e só responde "running", e o Carlos perguntou se um deles tinha travado. Falta visibilidade de progresso (última atividade, número de chamadas, tokens gastos).

### 3.2 Memória e índice

O que já existe: tabela `agent_memories` com `superseded_by`, índice híbrido (FTS5 BM25 + vetores, fusão RRF) com bônus de projeto, recência e uso, consolidação opcional ("auto dream") e índice de código por projeto com troca atômica por arquivo.

Lacunas:

| # | Lacuna | Evidência |
|---|---|---|
| M1 | Não há detecção de contradição na gravação. A supersessão depende de o modelo passar `replaces_memory_id` ou da consolidação opcional (no máximo uma vez a cada 24 h). | `src/agentos/persistence/postgres/agent_memory.py:187-325`, `src/agentos/memory/consolidation.py` |
| M2 | Quase-duplicata (Jaccard 0,88) sobrescreve o texto e perde o histórico. | `agent_memory.py:187-325` |
| M3 | Buscar memória por id devolve memórias superadas. | `agent_memory.py` (`get_memory`) |
| M4 | `forget` é DELETE físico, sem tombstone. | `agent_memory.py` |
| M5 | `_all()` corta em 500 linhas. | `agent_memory.py:770` |
| M6 | Sem validade temporal (`valid_from`, `invalid_at`) nem proveniência (origem, confiança). | esquema |
| M7 | Sem memória por agente. Não existe escopo `agent`, e `MemoryScopeReference` é metadado inerte. Subagentes compartilham a memória de usuário e projeto do pai. | `src/agentos/agents/models.py:84` |
| M8 | O índice de código pode responder defasado por até 60 s mais o tempo de varredura. Não há checagem de frescor na consulta. | `src/agentos/retrieval/service.py:114-137` |
| M9 | Arquivo que fica ilegível ou deixa de ser UTF-8 mantém os chunks antigos no índice. | `src/agentos/retrieval/indexer.py:145-176` |
| M10 | Conflito entre memória e arquivos de instrução não é tratado. Exemplo real desta sessão: o `ORIN.md` manda usar `claude-sonnet-4-6` em subagentes e uma memória salva diz `claude-sonnet-5-5`. | arquivos do usuário |

### 3.3 Esforço

| # | Lacuna | Evidência |
|---|---|---|
| E1 | Não há seletor de esforço no chat. O comando `/effort` responde que o Orin não ajusta esforço por sessão. | `frontend/src/features/commands/moreCommands.ts:40` |
| E2 | `turn["effort"]` é lido, mas nada o grava. Esforço só vem do arquivo de agente custom, da configuração global ou de `orin code --effort`. | `src/agentos/agentic/session.py:6229-6252` |
| E3 | Subagentes não herdam o esforço do pai e não há padrão por tipo de agente. | `session.py:2384-2392`, `:3703`, `:3758` |
| E4 | A API da Anthropic recebe `thinking={"type":"enabled","budget_tokens":N}`. A documentação atual chama `budget_tokens` de legado e põe o esforço em `output_config.effort` com thinking adaptativo. O que acontece ao mandar o formato legado para os modelos novos: não verificado. | `src/agentos/agentic/effort.py:71`, `:74` |
| E5 | Provedores no estilo OpenAI colapsam `xhigh` e `max` em `high`. | `effort.py:14` |
| E6 | Suporte a raciocínio é binário por regex. Não há lista de níveis por modelo no catálogo de API. | `effort.py:16-48` |
| E7 | Descarte silencioso: o Codex ignora nível fora de `supported_efforts` e o Antigravity omite `--effort` quando o nível já vem no nome do modelo. | `src/agentos/providers/codex_cli/command.py:528-536`, `src/agentos/providers/antigravity_cli/command.py:1216-1246` |
| E8 | Sem acompanhamento proativo de cota de assinatura (5 h e semanal) e sem fallback entre assinaturas. O fallback só existe dentro do mesmo provedor e zera o esforço quando o novo modelo não aceita. | `runtime.py:734-735` |
| E9 | Sem ajuste automático de esforço (só há escalada de tokens de saída). | relato de subagente |

## 4. Avaliação das três ideias

### 4.1 INS-022: revisão cruzada no verifier

**A premissa tem base.** Modelos avaliadores favorecem as próprias gerações (arXiv 2404.13076). Sem feedback externo, modelos têm dificuldade de se autocorrigir (2310.01798, relato de subagente).

**A premissa tem limites que mudam o desenho:**

- **Efeito assimétrico.** arXiv 2607.21656, 116 tarefas, revisor sem executar testes:

  | Autor | Revisor | Antes | Depois |
  |---|---|---|---|
  | Codex | Claude | 71,6% | 89,7% |
  | Codex | Codex | 71,6% | 84,5% |
  | Claude | Codex | 91,4% | 82,8% |
  | Claude | Claude | 91,4% | 91,4% |

  É um preprint de workshop, com amostra pequena e um benchmark só. Mesmo assim mostra que trocar de família pode piorar quando o revisor é mais fraco, e que revisão pela mesma família também pode ajudar.
- **Erros correlacionados entre famílias.** Em mais de 350 modelos, quando dois erram, concordam em 60% dos casos em um dos conjuntos, e modelos maiores erram de forma correlacionada mesmo com provedores distintos (2506.07962). Outra família reduz o ponto cego, não o elimina.
- **Revisores alucinam bugs** (2407.00215, relato de subagente). Um revisor que reescreve o código sem prova pode estragar código correto.

**O que os outros fazem:** nenhum harness impõe família diferente por padrão. O que existe é configuração: `review_model` no Codex, plugin `oracle` do Amp com modelo próprio, `plan-reviewer` em GPT com planejador em Claude no oh-my-openagent, consenso de Gemini, GPT e Opus em paralelo no Cline, `consensus` e `codereview` no PAL MCP. O Claude Code tem `/code-review ultra`, em que cada achado é reproduzido de forma independente antes de ser reportado.

**Desenho recomendado:**

1. **Acionar o verificador de verdade.** Na fase VERIFY, quando a mudança passa de um limiar (arquivos tocados, linhas, risco), o runtime lança o `verifier` como subagente independente em vez de pedir ao mesmo agente que se confira.
2. **Política de modelo do verificador**, configurável: `cross_family` (padrão proposto), `same_model`, `explicit`. No modo `cross_family`, o runtime escolhe entre as famílias disponíveis uma que seja de nível igual ou superior ao implementador. Se não houver, cai para a mesma família com esforço alto. Isso exige uma tabela de famílias e de nível por modelo.
3. **Revisor não edita.** Ele já é somente leitura. O achado precisa de prova: teste que falha, comando com saída, ou `arquivo:linha` com a razão. O implementador confirma cada achado antes de mudar o código. Isso ataca o caso em que o revisor piora o resultado (inferência minha sobre o mecanismo, o abstract não explica a assimetria).
4. **Revisor com execução.** O `verifier` do Orin já tem `Bash` e `PowerShell`, o que o estudo não testou.
5. **Medir por par.** Registrar por par autor×revisor: achados confirmados, falsos positivos e custo. Depois de algumas semanas a política padrão passa a ser decidida com dados do próprio Orin.
6. **Cota.** Verificação cruzada gasta a cota de outra assinatura. Precisa respeitar o item de cota da seção 4.3.

Pré-requisitos no código: L1, L2, L4 e a herança de esforço (E3). Tamanho: M.

### 4.2 INS-023: expurgo atômico

**O que a proposta pede e já existe:**

- Troca atômica por arquivo: `replace_file` apaga chunks, FTS, vetores e referências de símbolo e insere os novos em uma única transação (`src/agentos/retrieval/store.py:144-164`).
- Detecção por `content_hash` (blake2b) após filtro de mtime e tamanho (`indexer.py:145-176`).
- Arquivos removidos são esquecidos na varredura (`indexer.py:42-88`).
- Filtro `superseded_by IS NULL` no SQL antes do ranking (`agent_memory.py:741`, `:757`), e o índice híbrido poda os ids que saíram (`src/agentos/memory/hybrid_index.py:239-353`).
- `replaces_memory_id` na ferramenta `Remember` (`src/agentos/agentic/tools/orin/memory.py`).

**O que ainda permite mistura de velho com novo:** M1, M2, M3, M8, M9 e M10 da seção 3.2.

**Referências externas:**

- **Graphiti/Zep:** não apaga, invalida. Cada fato tem `valid_at`, `invalid_at` e `expired_at`. A contradição é decidida por LLM que devolve só índices, e a invalidação em si é determinística.
- **mem0 v3:** virou somente inclusão ("ADD-only"). A própria documentação diz que o fato novo fica ao lado do velho e que o ranking resolve. É o contraexemplo: o Frankenstein empurrado para a busca.
- **CrewAI:** consolidação na gravação quando há registro similar acima de 0,85, com LLM decidindo manter, atualizar, apagar ou inserir.
- **ACE (2510.04618):** reescrever a memória inteira com LLM destrói conhecimento (18.282 tokens viraram 122 em um passo). A saída é gravar deltas e mesclar sem LLM.
- **Roo, claude-context, LangChain Indexing, LlamaIndex:** apagam e reinserem por arquivo sem transação entre as lojas. O Orin já está à frente aqui por manter tudo em um SQLite só.
- **Cursor e Continue:** cache de embedding por hash do chunk, para não recalcular trechos inalterados.

**Recorte recomendado (INS-023 revisto):**

1. **Frescor na consulta.** Para os arquivos dos K primeiros resultados, comparar mtime e tamanho com o que está indexado. Se divergir, reindexar aquele arquivo antes de responder ou descartar o resultado. Fecha a janela de 60 s onde ela importa. Tamanho: P.
2. **Arquivo ilegível ou não UTF-8 sai do índice** em vez de manter chunks antigos. Tamanho: P.
3. **Detecção de conflito na gravação de memória.** Buscar as 5 ativas mais parecidas no mesmo escopo. Regra determinística primeiro (hash igual, similaridade muito alta). LLM só na faixa ambígua, devolvendo decisão com índices (incluir, substituir, conflito, nada), nunca texto novo. Tamanho: M.
4. **Histórico de memória** (`memory_events`) para a quase-duplicata não apagar o texto anterior, e tombstone no `forget`. Tamanho: P a M.
5. **Busca por id redireciona para a sucessora** ou marca a memória como superada. Tamanho: P.
6. **Validade e proveniência:** `valid_from`, `invalid_at`, origem (usuário, agente, ferramenta, web, arquivo) e confiança. Memória do usuário só é superada por outra do usuário. Memória derivada de saída de ferramenta ou web fica em quarentena e não entra na consolidação (padrão do OpenClaw). Tamanho: M.
7. **Lint de instruções:** rotina que cruza memórias ativas com `CLAUDE.md`, `ORIN.md` e `AGENTS.md` e aponta contradições para o usuário resolver. Tamanho: M.

Não recomendo trocar a identidade dos chunks nem migrar para sqlite-vec agora: o ganho é pequeno frente ao que já existe, e a atomicidade entre tabela comum e `vec0` não foi verificada.

### 4.3 Esforço dos modelos

**Referências externas:**

- **Claude Code:** esforço salvo por modelo ("a escala é calibrada por modelo"), `effort` no frontmatter do subagente e da skill, parâmetro por invocação, `ultrathink` para elevar um turno só, `opusplan` (modelo forte no plano, modelo médio na execução). Ordem de resolução documentada.
- **Codex:** `model_reasoning_effort` por agente, `agents.default_subagent_reasoning_effort`, `plan_mode_reasoning_effort`, níveis `low|medium|high|xhigh|max|ultra` conforme o modelo.
- **API da Anthropic:** `output_config.effort`. `low` é o nível indicado para subagentes simples. Para código em agente: Fable 5.2 começa em `high`, Sonnet 5.5 em `medium` e sobe para `high` em tarefas longas, Opus 5.5 tem padrão `medium`. `xhigh` e `max` só onde houver ganho medido.
- **opencode e Cursor:** esforço no próprio identificador do modelo (`modelo#high`, `modelo[effort=high]`).
- **Factory:** o pai informa a complexidade da tarefa (`light|medium|heavy`) e o usuário mapeia cada faixa a um modelo.
- **Codex (memórias):** `min_rate_limit_remaining_percent` impede que trabalho de fundo gaste cota quando ela está baixa.

**Recorte recomendado:**

1. **Seletor de esforço no compositor**, por modelo, mostrando só os níveis que aquele modelo aceita, com o valor lembrado por modelo. Ligar o campo `turn["effort"]`, que já existe. Tamanho: M.
2. **Esforço de subagente:** parâmetro `effort` por chamada na ferramenta `Agent`, padrão por tipo (por exemplo `explore` baixo, `plan` alto, `verifier` alto, `general` herda) e herança do pai quando o modelo é o mesmo. Tamanho: P a M.
3. **Atualizar o caminho da API da Anthropic** para `output_config.effort` com thinking adaptativo nos modelos novos, mantendo `budget_tokens` só onde for exigido. Tamanho: P a M.
4. **Catálogo de níveis por modelo** no lugar do suporte binário, e fim do colapso `xhigh`/`max` → `high` onde o provedor aceita os níveis altos. Tamanho: M.
5. **Avisar em vez de descartar em silêncio** quando o CLI ignora o nível pedido (E7). Tamanho: P.
6. **Cota de assinatura:** acompanhar o consumo das janelas de 5 h e semanal, mostrar na interface e aplicar política (reduzir esforço, trocar de modelo, cadeia de fallback entre assinaturas). É o que mais pesa para "modelos de assinatura". A viabilidade de ler a cota de cada CLI de forma proativa não foi verificada. Tamanho: G.
7. **Elevar um turno só** por palavra-chave ou botão, e esforço próprio para o plan mode. Tamanho: P.
8. **Escalada por falha:** depois de N reprovações do verificador, subir o esforço ou o modelo. Proposta minha, sem referência direta atual (o lead/worker do Goose foi removido do projeto). Tamanho: M.

## 5. O que os melhores harnesses fazem e o Orin não

| Padrão | Quem faz | Situação no Orin |
|---|---|---|
| Verificador independente como gate de parada | Claude Code (hook `agent` no `Stop`, `TaskCompleted` com exit 2), AG2 no estudo MAST (só o verificador encerra), superpowers (revisão em duas etapas) | Não. VERIFY é auto-revisão (L1). |
| Isolamento de escrita por worktree | Claude Code (`isolation: worktree`, limpeza automática), Codex (`--worktree`), Gas Town, claude-squad | Não. Campo inerte (L5). |
| Fila de merge serializada | Gas Town (Refinery, lote com bisect), container-use (branch por agente) | Não. |
| Quadro de tarefas com dependências e claim atômico | Claude Code agent teams (file lock), beads (`bd ready --claim`), Cline (SQLite) | Não (L6). |
| Workflow como código ou grafo | Claude Code (scripts JS com `agent`, `pipeline`, `parallel`, replay de resultados salvos), OpenHands (`map_agents`, `reduce_agent`), Kiro (tarefas em ondas), Goose (subrecipes YAML), LangGraph | Não (L10). |
| Execução durável e retomada | LangGraph (checkpoint por passo), Temporal, Cline (teams em SQLite, sessão retomável), Claude Code (`SendMessage` com id do agente) | Parcial. Background só em memória (L7). |
| Objetivo persistente com avaliador pequeno | Claude Code e Codex (`/goal`, teto em tokens no Codex) | Parcial. Há hook `Stop` com até 5 continuações (`runtime.py:128`). |
| Saída estruturada de subagente com validação | Claude Code workflows (`schema`, 5 tentativas), Goose (`json_schema`) | Não verificado no Orin. |
| Detecção de travamento e replanejamento | Magentic (ledger de progresso, `max_stall_count`), Gas Town (Witness, estados Stalled e Zombie) | Não. |
| Teto de custo por subagente | Devin (ACU por filha), Codex (`max_goal_token_budget`) | Não (L9). |
| Modelo e esforço por papel, com ordem de resolução | Claude Code, Codex, Factory, opencode, Cursor | Parcial. Só por arquivo de agente (L4, E3). |
| Memória por agente | Claude Code (`memory: user|project|local`, índice de 200 linhas injetado), CrewAI (escopo em árvore com visão somente leitura) | Não (M7). |
| Consenso multimodelo | PAL (`consensus`, cada modelo cego aos outros), LLM Council (ranking anônimo), Cline | Não. |
| Orquestrador sem ferramentas de execução | Roo (Orchestrator não lê, não escreve, só delega) | Não. |
| Lista de features em JSON com `passes`, uma por sessão | Anthropic (harness de longa duração) | Não. |

Lições de custo e de falha que devem balizar o desenho:

- Multiagente gasta cerca de 15 vezes os tokens de um chat, e o ganho aparece em tarefa paralelizável. Código tem menos paralelismo que pesquisa (Anthropic).
- No MAST (1.642 traces, 14 modos de falha), as falhas se dividem em desenho do sistema (cerca de 44%), desalinhamento entre agentes (cerca de 32%) e verificação (cerca de 24%). Mudar a topologia rendeu mais do que melhorar o prompt (relato de subagente para os percentuais).
- Sem verificação centralizada os erros se propagam, e o efeito de multiagente vai de +80,8% em tarefa decomponível a −70,0% em planejamento sequencial (2512.08296, relato de subagente).
- Anthropic, Microsoft, Pydantic AI e smolagents dizem a mesma coisa: fluxo determinístico antes de agente, sempre que a tarefa permitir.

## 6. Candidatos para a 0.13

Agrupados em pacotes. A ordem dentro de cada pacote é a de dependência.

### Pacote A: verificação independente (núcleo da INS-022)

| Item | O que é | Fecha | Tam. |
|---|---|---|---|
| A1 | Parâmetros `provider`, `model` e `effort` por chamada na ferramenta `Agent` | L4, E3 | P–M |
| A2 | `verifier` acionado automaticamente no gate de VERIFY, por limiar de mudança | L1, L3 | M |
| A3 | Política de modelo do verificador (`cross_family`, `same_model`, `explicit`) com tabela de famílias e de nível, e fallback | L2 | M |
| A4 | Formato de achado com prova e severidade; implementador confirma antes de corrigir | — | P |
| A5 | Métricas por par autor×revisor | — | P–M |
| A6 | Hook do tipo agente passa a usar o `verifier` | L12 | P |

### Pacote B: escrita paralela segura

| Item | O que é | Fecha | Tam. |
|---|---|---|---|
| B1 | `isolation: worktree` funcional por subagente, com limpeza quando não há mudança | L5, L8 | M–G |
| B2 | Integração serializada das mudanças de volta (um merge por vez, com checagem) | L5 | M |
| B3 | Quadro de tarefas compartilhado em SQLite, com dependências e claim atômico | L6 | M |
| B4 | Agentes em background persistidos, com retomada após reinício | L7 | M–G |
| B5 | Progresso visível de agente em background (última atividade, chamadas, tokens, estado travado) | seção 3.1 | P–M |

### Pacote C: pipelines

| Item | O que é | Fecha | Tam. |
|---|---|---|---|
| C1 | Definição declarativa de pipeline: etapas, agente e modelo por etapa, gates (comando com saída 0, esquema de saída, verificador), paralelismo com teto, novas tentativas | L10 | G |
| C2 | Diário de execução para repetir só o que falhou | L10 | M |
| C3 | Saída estruturada de subagente com validação por esquema | — | P–M |
| C4 | Teto de tokens, custo e tempo por subagente e por pipeline; contador de travamento com replanejamento | L9 | M |
| C5 | Pipelines prontos: plano→implementa→verifica→corrige; revisão com vários modelos e consenso; correção de bug (teste que reproduz→correção→verificação); pesquisa (exploradores em paralelo→redutor) | — | M cada |
| C6 | `/goal` com avaliador pequeno e teto em tokens | — | M |

### Pacote D: memória por agente e conflitos (INS-023 revisto)

| Item | O que é | Fecha | Tam. |
|---|---|---|---|
| D1 | Frescor na consulta do índice de código | M8 | P |
| D2 | Arquivo ilegível sai do índice | M9 | P |
| D3 | Detecção de conflito na gravação | M1 | M |
| D4 | Histórico de memória e tombstone | M2, M4 | P–M |
| D5 | Busca por id respeita supersessão; fim do corte de 500 | M3, M5 | P |
| D6 | Escopo `agent` (por nome de agente e projeto), com resumo injetado no prompt do subagente e limite de tamanho | M7, L8 | M |
| D7 | Subagente propõe, um único escritor promove; resumo congelado no início da sessão | M7 | M |
| D8 | Validade, proveniência e quarentena | M6 | M |
| D9 | Lint de instruções contra memória | M10 | M |

Pontos de desenho para D6 e D7, tirados das fontes: escopo padrão `project` (um agente por papel com escopo de usuário mistura conhecimento de todos os projetos); só aprender de resultado verificado (Voyager, ACE); gravar deltas, nunca reescrever tudo; congelar o resumo no início da sessão para não invalidar o cache de prompt (Hermes).

### Pacote E: esforço

Os oito itens da seção 4.3.

### Itens avulsos

- Ativar `mcp_servers` e `color` nas definições (L8). P.
- Paralelismo em turnos de CLI (L11). Tamanho não estimado, depende de cada CLI.
- Ferramenta de consenso multimodelo para decisões de plano e arquitetura. M.
- Modo orquestrador restrito (só delega). P–M.
- Atualizar os RFCs 202 e 203 para o runtime real (L13). P.
- Faixa de complexidade mapeada a modelo (padrão Factory). M.

## 7. Dependências e uma ordem possível

- A1 é base para A2, A3, C1 e para o esforço de subagente.
- O pacote E (itens 1 a 5) é pequeno, independente e melhora o verificador, que deve rodar com esforço alto.
- B1 e B2 vêm antes de qualquer pipeline que escreva em paralelo (C1, C5).
- B4 e C2 resolvem o mesmo problema (retomar) em camadas diferentes e convém desenhar juntos.
- O pacote D é independente dos demais, exceto D6, que usa o campo `memory` das definições.

Uma ordem que respeita isso: E (1–5) e A1 → A2 a A6 → B → C → D em paralelo com B e C. É sugestão. A escolha é do Carlos.

## 8. Riscos

- **Custo.** Verificador cruzado e pipelines multiplicam tokens e consomem cota de mais de uma assinatura. Sem o item de cota (4.3, item 6) e sem tetos (C4), a 0.13 pode esgotar as janelas de uso mais rápido.
- **Revisor pior que o autor.** Mitigado pela regra de nível, pelo achado com prova e pela medição por par.
- **Falso positivo de revisor** gerando retrabalho. Mesma mitigação.
- **Worktree no Windows.** Caminhos longos, arquivos travados e dependências não versionadas (`node_modules`, `.venv`) precisam de tratamento. O Claude Code usa `.worktreeinclude` para copiar arquivos ignorados. Não testei no ambiente do Orin.
- **Memória envenenada.** Memória por agente aumenta a superfície. Proveniência e quarentena (D8) devem vir junto com D6, não depois.
- **Complexidade.** Pipelines declarativos são o item mais caro. Começar com os pipelines prontos escritos sobre A e B, e só depois abrir a definição ao usuário, reduz o risco.

## 9. Decisões que dependem do Carlos

1. Verificador cruzado ligado por padrão ou opcional na primeira entrega?
2. Qual limiar aciona o verificador automático (toda mudança, ou só acima de N arquivos ou linhas)?
3. Pipelines: formato declarativo (YAML ou Markdown) ou script, como o Claude Code?
4. Memória por agente: começa só para `verifier` e `explore`, ou para todo agente custom?
5. Cota de assinatura entra na 0.13 ou fica para depois?
6. Quais pacotes entram e em que ordem.

## 10. Relação com as metas

Este planejamento não avança nenhuma das metas do Q4/2026 nem as de novembro. Todas elas estão em outros projetos (Educly, pós-vendas, ClickUp interno) e vencem no fim de dezembro. O tempo investido na 0.13 concorre com elas.

## 11. Fontes principais

Conferidas por mim:

- Claude Code: https://code.claude.com/docs/en/sub-agents · /agent-teams · /model-config · /workflows.md · /goal.md
- Codex: https://learn.chatgpt.com/docs/agent-configuration/subagents · https://learn.chatgpt.com/docs/code-review.md
- opencode: https://opencode.ai/docs/agents/
- Roo Code: https://roocodeinc.github.io/Roo-Code/features/boomerang-tasks/
- Aider: https://aider.chat/docs/usage/modes.html
- PAL MCP: https://raw.githubusercontent.com/BeehiveInnovations/pal-mcp-server/main/README.md
- LangGraph: https://docs.langchain.com/oss/python/langgraph/persistence · /fault-tolerance
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/handoffs/
- beads: https://raw.githubusercontent.com/steveyegge/beads/main/README.md
- mem0: https://docs.mem0.ai/core-concepts/memory-operations/add
- Graphiti: documentação em help.getzep.com/graphiti (visão geral; endereço exato não guardado)
- Anthropic: https://www.anthropic.com/research/building-effective-agents · /effective-harnesses-for-long-running-agents · /multi-agent-research-system · documentação de esforço em platform.claude.com (endereço exato não guardado)
- Papers: https://arxiv.org/abs/2607.21656 · https://arxiv.org/abs/2404.13076 · https://arxiv.org/abs/2506.07962 · https://arxiv.org/abs/2503.13657

Lidas só pelos subagentes (ver os três relatórios completos citados na seção 2): oh-my-openagent, Kilo, Cline, Amp, Goose, Gemini CLI, OpenHands, Kiro, Cursor, Factory, pi, Zed/ACP, Microsoft Agent Framework, CrewAI, Google ADK, Pydantic AI, Mastra, Temporal, Gas Town, claude-squad, container-use, spec-kit, OpenSpec, superpowers, HumanLayer, Ralph, LLM Council, Devin, LangChain Indexing, LlamaIndex, Letta, Continue, claude-context, sqlite-vec, LangMem, ACE, ExpeL, Voyager, Hermes, OpenClaw e os demais papers citados.
