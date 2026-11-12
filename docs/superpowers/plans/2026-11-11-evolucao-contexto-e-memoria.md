# Evolução de Contexto da Conversa e Memória Geral do Orin

Data: 2026-11-11 · Status: **Implementado e validado**
Escopo aprovado: **Pacotes 1, 2, 3 e 4** · Política de consolidação: **Automático + Undo para memórias; aprovação explícita para `SKILL.md`**.

---

## 1. Motivação e Evidências no Código Atual

### 1.1 Contexto de Conversa e Compactação
1. **Corte do pedido inicial do usuário em `_compact_source` (`src/agentos/agentic/runtime.py:2130-2161`)**:
   - O método limita a entrada do resumidor a `min(48_000, max(4_000, 2 * self.limits.max_context_tokens))` caracteres e percorre `indices` **de trás para frente** (`reversed(sorted(indices))`).
   - Em turnos com ~25+ chamadas de ferramenta, as mensagens recentes consomem os 48k chars e truncam ou descartam o índice `0` (o prompt original do usuário), gravando `...[trecho truncado]...` no resumo persistido.
2. **Descarte das mensagens reais do usuário após fronteira de compactação (`src/agentos/conversations/chat.py:2547-2609`)**:
   - Embora `conversation_messages` preserve todas as mensagens `role == 'user'` intactas no banco, `history_for_turn` descarta toda linha com `sequence <= through` (`rows = [row for row in rows if int(row["sequence"]) > through]`) e injeta apenas `compaction_message(summary)`. Se o resumo perdeu ou parafraseou mal uma restrição, ela desaparece da sessão.
3. **Orçamento de saída insuficiente no resumidor (`src/agentos/agentic/runtime.py:2175` e `src/agentos/agentic/compaction.py:27-38`)**:
   - `max_output_tokens` está limitado a `min(1_600, max(256, self.limits.max_context_tokens // 4))`, mas `COMPACTION_INSTRUCTION` exige primeiro um bloco `<analysis>...</analysis>` (descartado depois) seguido das 9 seções markdown. O `<analysis>` consome parte substancial dos 1.600 tokens e comprime as seções úteis.
4. **Leituras repetidas acumuladas antes do limiar de microcompactação**:
   - Leituras repetidas do mesmo arquivo/trecho (`read_file`) ou leituras anteriores a um `edit_file`/`write_file` no mesmo arquivo permanecem inteiras em `messages` até que o limiar global dispare `_microcompact`.
5. **`ProjectMap` estático (`src/agentos/retrieval/service.py:161-171`)**:
   - Ordena apenas por `most_imported` global e lista até 12 símbolos por arquivo, sem considerar os arquivos abertos na sessão (`ConversationReadStates`) nem os identificadores mencionados na tarefa atual.

### 1.2 Memória Geral de Longo Prazo
1. **Sobrescrita silenciosa por falso positivo em `_supersede` (`src/agentos/persistence/postgres/agent_memory.py:50, 195-241`)**:
   - Qualquer nova memória do mesmo `kind` e `scope` com Jaccard léxico `>= 0.5` (`_CONTRADICTION_OVERLAP = 0.5`) marca a anterior como `superseded_by`. Dois fatos complementares sobre o mesmo projeto/componente frequentemente compartilham >=50% dos termos e anulam um ao outro.
2. **Busca puramente léxica sem IDF, sem semântica e sem reforço de uso (`src/agentos/persistence/postgres/agent_memory.py:243-299, 382-395`)**:
   - `relevant()` e `search()` ranqueiam até 500 linhas apenas por `len(wanted & haystack)` (interseção exata de palavras), sem BM25, sem embeddings e sem atualizar `hit_count`/`last_used_at` fora de `inside_joke` no modo Friend.
3. **Truncamento de memórias técnicas longas na injeção (`src/agentos/agentic/session.py:668-687` + `VOLATILE_REMINDER_CHARS`)**:
   - Memórias operacionais/arquiteturais extensas entram inteiras no bloco volátil e podem ser cortadas no meio da frase quando o lembrete volátil atinge o teto.
4. **Lacunas de continuidade entre sessões e aprendizado procedural**:
   - `ReadProjectConversation` (`src/agentos/agentic/tools/orin/projects.py:590-623`) exige conhecer `conversation_id` previamente e lê apenas as últimas `limit` mensagens, sem busca textual/semântica entre conversas passadas.
   - `transcript_excerpt` (`src/agentos/memory/consolidation.py:152-177`) filtra exclusivamente `role == "user"`, impedindo que causas-raiz e fluxos validados durante o turno virem memórias episódicas ou propostas de `SKILL.md`.

---

## 2. Referências Open Source Adotadas no Desenho

- **Codex CLI (`codex-rs/core/src/compact.rs`)**: retenção literal de mensagens reais do usuário (`COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000`) em ordem cronológica junto ao resumo de compactação; reaproveitamento de contexto/estado operacional atualizado.
- **OpenHands SDK (`openhands/sdk/context/condenser`)**: preservação simultânea de **Head (`keep_first`) + Tail** na condensação, resumindo apenas o miolo intermediário sobre um histórico append-only imutável.
- **Cline (`ContextManager` / Auto-Compact)**: deduplicação não-destrutiva de leituras antigas do mesmo arquivo na janela ativa antes da compactação por resumo.
- **Aider (`aider/repomap.py`)**: `RepoMap` dinâmico com **Personalized PageRank** (`50x` arquivos ativos no chat, `10x` identificadores mencionados ou compostos `>= 8` chars, `0.1x` símbolos privados/ubíquos) e ajuste ao orçamento de tokens via busca binária (`ok_err = 0.15`).
- **Letta / Letta Code (`MemFS`, Core/Recall/Archival, `/dream`)**: separação entre **Core Memory** (curta e sempre visível no prompt), **Archival Memory** (notas longas acessadas sob demanda) e **Recall Memory** (busca direta no histórico de conversas passadas).
- **Mem0 (Algoritmo Abril/2026) & Graphiti (`getzep/graphiti`)**: separação estrita entre duplicata e contradição real (evitando sobrescritas destrutivas acidentais) combinada com recuperação híbrida multi-sinal (**BM25 + vetores + escopo + recência/força de uso**).
- **LangMem & Gemini CLI (`packages/core/src/services/memoryService.ts`)**: distinção entre memória **Semântica**, **Episódica** e **Procedural**, validando patches/propostas de `SKILL.md` em dry-run antes de pedir aprovação do usuário.

---

## 3. Especificação Técnica por Pacote

### Pacote 1 — Blindagem de Compactação e Contexto Vivo

#### 1.1 `_compact_source` com Head + User Spine + Tail (`src/agentos/agentic/runtime.py`)
- **Orçamento de entrada proporcional à janela**:
  - Substituir o teto fixo de `48_000` chars por `budget_chars = min(240_000, max(32_000, int((self.limits.context_window_tokens or self.limits.max_context_tokens) * 1.6)))`.
  - Se o provedor retornar erro de janela (`_is_context_length_error`) durante a própria chamada de resumo, reduzir o miolo de `tool_result` pela metade e tentar novamente uma vez.
- **Particionamento determinístico da fonte do resumo**:
  1. **Head (`20%` do orçamento, piso `8_000` chars)**: primeiras mensagens elegíveis a partir do primeiro `role == "user"`, garantindo que o objetivo original e o contexto inicial nunca sejam cortados.
  2. **User Spine (`25%` do orçamento)**: todas as mensagens reais `role == "user"` dentro de `compact_indices` (ignorando `<system-reminder>`, tool results e resumos anteriores).
  3. **Tail (`55%` do orçamento)**: mensagens elegíveis mais recentes antes de `keep_units`, comprimindo mensagens de ferramenta com `_compress_tool_message` em formato **head + tail** (primeiros 60% + últimos 40% com contagem de linhas/chars omitidos, preservando códigos de saída e tracebacks no fim do output).
  4. Reordenar todos os itens selecionados por índice crescente e inserir `[... N passos intermediários de ferramenta omitidos ...]` onde houver lacuna entre Head e Tail.

#### 1.2 Orçamento de Saída do Resumidor (`src/agentos/agentic/runtime.py` e `src/agentos/agentic/compaction.py`)
- Em `_request_compaction_summary` (`runtime.py:2175`), ajustar `max_output_tokens` para:
  `min(6_000, max(2_048, (self.limits.max_output_tokens or 8_192) // 2))`.
- Em `COMPACTION_INSTRUCTION` (`compaction.py:27-38`), instruir que `<analysis>` tenha no máximo ~350 palavras para reservar o orçamento principal às 9 seções markdown.

#### 1.3 Retenção Literal das Mensagens do Usuário (`src/agentos/conversations/chat.py` e `src/agentos/agentic/runtime.py`)
- **Constante**: `COMPACT_RETAINED_USER_TOKENS = 16_000` (~64.000 chars).
- **Helper compartilhado** `build_retained_user_requests_section(user_messages, *, max_tokens=16_000)` em `src/agentos/agentic/compaction.py`:
  - Filtra mensagens reais da pessoa (`role == "user"`, excluindo `<system-reminder>`, expansões internas de comando, anexos binários e resumos anteriores).
  - Seleciona da mais recente para a mais antiga até `max_tokens`; se uma mensagem individual exceder `8_000` chars (ex.: log colado), preserva `4_000` chars iniciais + `4_000` chars finais com marcador explícito.
  - Restaura a ordem cronológica (`[ Turno #k ] ...`) sob o cabeçalho `### Verbatim User Requests (authoritative)` acoplado ao `compaction_message`, sem quebrar a alternância `user`/`assistant` dos provedores.
- **Aplicação dupla (sem migração de schema)**:
  1. Intra-turno em `AgenticTurnRuntime._run_compaction` (`runtime.py:2065-2090`): extrai as mensagens `user` reais de `compact_indices` antes de dobrar `messages`.
  2. Entre turnos em `ChatStore.history_for_turn` (`conversations/chat.py:2547-2609`): quando `compacted` é `True`, consulta as linhas `role == 'user'` com `sequence <= through` (já persistidas em `conversation_messages`) e anexa a seção literal ao `compaction_message` caso o resumo salvo não a contenha ou tenha truncado mensagens anteriores.

#### 1.4 Deduplicação de Leituras Repetidas na Janela Ativa (`src/agentos/agentic/runtime.py`)
- Anotar resultados de `read_file` com metadados internos `{"_read_key": (norm_path, offset, limit), "_read_full": is_full_read, "_read_sha": sha256}` (chaves iniciadas por `_` já são filtradas antes do envio ao provedor).
- Antes de cada chamada ao modelo (`_dedupe_stale_reads(messages)`):
  - Se houver duas leituras com o mesmo `_read_key` (ou uma leitura integral posterior cobrindo um trecho parcial anterior) antes de `last_call`, substituir o conteúdo da leitura antiga por:
    `[duplicate read elided: newer read of <path> appears later in this conversation]`.
  - Se `edit_file` ou `write_file` tiver modificado `norm_path` com sucesso posteriormente (e não pertencer à unidade protegida `_pinned_index`), marcar leituras brutas anteriores muito grandes (`> MICROCOMPACT_MIN_CHARS`) como obsoletas, mantendo intactas as últimas `2` interações sobre o arquivo.

---

### Pacote 2 — Motor de Memória Híbrida, Correção de `_supersede` e Core vs. Archival

#### 2.1 Correção de `_supersede` (`src/agentos/persistence/postgres/agent_memory.py`)
- **Remover** a regra `len(wanted & other) / len(wanted | other) >= 0.5` em `_supersede` (`agent_memory.py:235`).
- **Nova política em duas camadas**:
  1. **No caminho síncrono `save()` (determinístico e rápido)**:
     - Parâmetro opcional explícito `supersedes_id: str | None = None` em `save()` e na ferramenta `Remember` (`replaces_memory_id`), permitindo substituição cirúrgica quando o agente sabe qual memória está atualizando.
     - Deduplicação de quase-idênticos: se `Jaccard >= 0.88` no mesmo `(scope_type, project_id, kind)` **sem** inversão de polaridade (`não`/`nunca`/`jamais`/`evite` vs afirmativa), atualizar a linha existente (`updated_at`, `confidence`, união de `tags`) em vez de criar duplicata.
     - Fatos com overlap intermediário (`< 0.88`) **nunca** sobrescrevem automaticamente um ao outro no `save()`; ambos permanecem ativos e disputam espaço por relevância + recência.
  2. **Na consolidação assíncrona (`auto-dream`)**:
     - Agrupar pares do mesmo escopo com alta similaridade (`cosine >= 0.78` ou `BM25` alto) e permitir que o consolidator emita `{action: "supersede", old_id, new_id}` **somente** quando houver contradição factual direta comprovada, encadeando `superseded_by` de forma auditável.

#### 2.2 Índice Híbrido Derivado `MemoryHybridIndex` (`src/agentos/memory/hybrid_index.py`)
- **Princípio arquitetural**: `agent_memories` continua sendo a única fonte da verdade (compatível com SQLite local e Postgres em servidor, zero dependência de `pgvector`).
- **Armazenamento derivado**: arquivo SQLite WAL `<ORIN_DATA_DIR>/retrieval/memories_<sha256(user_id)[:16]>.db` (mesmo padrão de `SqliteChunkStore` em `src/agentos/retrieval/store.py`):
  - `memories_meta(memory_id PRIMARY KEY, scope_type, project_id, kind, updated_at_iso, content_hash)`
  - `memories_fts USING fts5(memory_id UNINDEXED, fact, tags, tokenize = 'unicode61')`
  - `memory_vectors(memory_id PRIMARY KEY, embedding BLOB)` + `index_meta` (reconcilia identidade do `EmbeddingPort` igual a `SqliteChunkStore._reconcile_identity`).
- **Sincronização incremental**:
  - Antes de `relevant()` / `search()`, reconciliar diffs rápidos (`memory_id`, `updated_at`) contra `agent_memories` (tipicamente dezenas/centenas de linhas, < 5 ms para FTS5; embeddings gerados em background ou inline com timeout curto e fallback gracioso para BM25 se o embedder estiver indisponível).
- **Fórmula de Ranking (`relevant` e `search`)**:
  - Candidatos via `reciprocal_rank_fusion([bm25_ranking, vector_ranking], k=60)`.
  - Bônus aditivos calibrados na escala RRF:
    - `+0.008` se `scope_type == "project"` do projeto ativo;
    - `+0.004 * exp(-age_days / 90.0)` (recência suave — desempata fatos atualizados sem esconder memórias antigas importantes);
    - `+0.002 * min(3.0, log1p(hit_count))` (força por uso comprovado);
    - multiplicado por `(0.7 + 0.3 * confidence)`.
- **Contabilização de uso universal**:
  - Registrar `hit_count = hit_count + 1` e `last_used_at = now` para memórias efetivamente injetadas por `relevant()` ou retornadas por `Recall(query=...)` (com throttle por `(conversation_id, memory_id)` em memória para não inflar `hit_count` a cada turno da mesma conversa).

#### 2.3 Separação Core Memory vs. Archival Pointer (`src/agentos/agentic/session.py` e `src/agentos/agentic/tools/orin/memory.py`)
- **Posicionamento e orçamento no prompt**:
  - Mover `## What you remember about this user` para **antes** da listagem longa de arquivos do workspace no `volatile_prompt` (ou reservar cota dedicada de `4_500` chars que não sofre corte pelo tail de `VOLATILE_REMINDER_CHARS`).
- **Regra Core vs. Archival**:
  - **Core (`kind == "preference"` ou `len(fact) <= 300`)**: renderizado integralmente (`- [mem_id] <fact>`).
  - **Archival (`len(fact) > 300`)**: renderiza resumo limpo até o fim da primeira sentença / `260` chars sem cortar palavra ao meio:
    `- [mem_id · architecture] <preview>... (memória completa de N chars disponível via Recall(memory_id="mem_id"))`.
- **Extensão da ferramenta `Recall` (`src/agentos/agentic/tools/orin/memory.py:63`)**:
  - Aceitar `query: str | None = None` e `memory_id: str | None = None` (além de `scope` opcional). Quando `memory_id` for informado, busca o registro exato pertencente ao `user_id` e devolve o texto integral, tags, escopo e data de atualização.
  - Aumentar coluna/limite de `fact` para `4_000` chars em nova migração Alembic compatível com SQLite/Postgres (`String(4000)` / `Text`), mantendo validação contra secrets e prompt injection.

---

### Pacote 3 — `ProjectMap` Dinâmico com Personalized PageRank

#### 3.1 Evolução do `SqliteChunkStore` (`src/agentos/retrieval/store.py` e `src/agentos/retrieval/indexer.py`)
- Subir `SCHEMA_VERSION` de `"1"` para `"2"` em `src/agentos/retrieval/store.py:20` (reconstrução automática e transparente em background, zero impacto no banco principal).
- Adicionar tabela leve `symbol_refs(path TEXT, symbol TEXT, count INTEGER, PRIMARY KEY (path, symbol))` preenchida durante o chunking/indexação já existente (contando ocorrências de identificadores definidos nos chunks do projeto).
- Método `dependency_edges()` em `SqliteChunkStore`: retorna arestas ponderadas `(src_path, dst_path, base_weight, matched_symbols)` combinando `imports(path, target)` e `symbol_refs` contra símbolos exportados (`chunks.symbol`), aplicando os multiplicadores inspirados no Aider:
  - `sqrt(count)` para frequência de referência;
  - `10x` para identificadores `snake_case` / `CamelCase` com `len >= 8`;
  - `0.1x` para símbolos iniciados por `_` ou definidos em `> 5` arquivos.

#### 3.2 Personalized PageRank em NumPy (`src/agentos/retrieval/pagerank.py`)
- Implementação enxuta usando exclusivamente `numpy` (já instalado e importado em `store.py`):
  - Grafo esparso em arrays `src_idx`, `dst_idx`, `weights`, `alpha = 0.85`, `max_iter = 20`, `tol = 1e-6` (< 5 ms para repositórios de até 10k arquivos).
  - **Vetor de personalização `p` por arquivo**:
    - `+2.0` se `path` estiver nos arquivos ativos da conversa (`ConversationReadStates`: lidos/editados recentemente);
    - `+1.5` se `path` ou seu basename aparecer explicitamente na `query`/mensagem atual;
    - `+1.0` se o arquivo definir identificador presente em `query_identifiers` (multiplicando também por `10x` as arestas desse identificador e por `50x` as arestas saindo dos arquivos ativos na conversa).
  - **Cold start (nenhum arquivo lido e `query` vazia)**: vetor `p` uniforme -> produz ranking global de centralidade estrutural (estritamente superior à contagem simples de imports diretos, pois captura importância transitiva).

#### 3.3 Orçamento de Tokens por Busca Binária (`src/agentos/retrieval/service.py` e `tools/orin/search_code.py`)
- Assinatura evoluída:
  `RetrievalService.project_map(*, limit: int = 20, focus_paths: Sequence[str] = (), query: str = "", max_tokens: int = 1_200)`
- Renderiza os arquivos mais bem ranqueados com seus símbolos principais e linha inicial (`symbol:start_line`), usando busca binária (`ok_err = 0.15`) para encaixar o máximo de contexto relevante dentro de `max_tokens`.
- Na ferramenta `ProjectMap` (`search_code.py:42, 117`):
  - Adicionar parâmetros opcionais `query: str | None = None` e `max_tokens: int = 1200` no schema da ferramenta.
  - Injetar automaticamente `focus_paths` a partir de `self._read_file_state` da sessão atual.

---

### Pacote 4 — `SearchConversations` (`Recall Memory`) e `auto-dream` Episódico + Skills com Aprovação

#### 4.1 Busca no Histórico de Conversas (`SearchConversations` + upgrade em `ReadProjectConversation`)
- **Backend (`src/agentos/conversations/chat.py`)**:
  - Novo método `ChatStore.search_conversations(user_id: str, *, query: str, project_id: str | None = None, scope: str = "project", mode: str | None = None, limit: int = 8) -> list[dict[str, object]]`:
    - `scope="project"` (padrão quando a conversa pertence a um projeto, com fallback/opção `scope="all"` para buscar em todas as conversas do mesmo `user_id`).
    - Pesquisa em `conversations.title`, `conversation_messages.content` (excluindo mensagens bloqueadas) e `conversation_compact_boundaries.summary`.
    - Ranqueia por cobertura de termos da query + densidade + recência (`updated_at`), retornando: `conversation_id`, `title`, `mode`, `project_id`, `updated_at`, `matched_sequence` e `snippet` sanitizado (`±180` chars).
  - Estender `ChatStore.read_project_conversation` (`projects.py:590`) com `around_sequence: int | None = None`: quando fornecido, retorna a janela `[around_sequence - limit//2, around_sequence + limit//2]` em vez de apenas as últimas mensagens da conversa.
- **Ferramenta (`src/agentos/agentic/tools/orin/projects.py` e `public_names.py`)**:
  - Nova `ToolSpec(key="search_conversations", read_only=True, ...)` com nome público `SearchConversations` (deferida em `tool_deferral.py` junto com `ReadProjectConversation`).
  - Atualizar a dica em `build_system_prompt` (`session.py:701-710`) informando `SearchConversations` para localizar discussões além das 12 sessões recentes.

#### 4.2 `auto-dream` com Memórias Episódicas (Automático + Undo) e Propostas de `SKILL.md` (Com Aprovação)
- **Clean Turn Digest (`src/agentos/memory/consolidation.py`)**:
  - Substituir o filtro cego de `transcript_excerpt` por `build_verified_consolidation_digest(history, tool_ledger, *, had_untrusted_external_input: bool)`:
    - Sempre inclui mensagens `role == "user"` não-tainted.
    - Inclui resumo estruturado de ações locais verificadas (`edit_file`, `write_file`, comandos de teste/build executados e se passaram) e a conclusão final do assistente **somente** de turnos sem taint web/MCP não-confiável (ou isolando estritamente metadados locais e aplicando `_SECRET` + `_PROMPT_INJECTION_RE` + `validate_memory_content` na saída).
    - Exige gatilho mínimo de substância (ex.: `>= 5` turnos acumulados ou sessão técnica encerrada com resolução de erro + verificação).
- **Saída JSON estruturada do `MemoryConsolidator`**:
  ```json
  {
    "memories": [
      {
        "fact": "...",
        "kind": "preference | fact | architecture | operational | episodic",
        "scope": "user | project",
        "tags": ["..."]
      }
    ],
    "supersedes": [
      {"old_memory_id": "mem_...", "new_fact_index": 0, "reason": "..."}
    ],
    "skill_proposals": [
      {
        "action": "create | edit",
        "target_skill_id": null,
        "name": "...",
        "description": "...",
        "when_to_use": ["..."],
        "when_not_to_use": ["..."],
        "requires_tools": ["..."],
        "instructions": "## Workflow\n...\n\n## Validation\n..."
      }
    ]
  }
  ```
- **Política de gravação (conforme definido pelo usuário)**:
  1. **Memórias (`fact`, `preference`, `architecture`, `operational`, `episodic`)**:
     - Gravadas automaticamente em `agent_memories` (`source="consolidation"`, `confidence=0.75`).
     - Emitem `AgentActivityEventType.MEMORY_LEARNED` (`Aprendi: ...`) com o botão **Undo / Desfazer** já suportado pela API e UI (`session.py:486-493`).
  2. **Propostas de `SKILL.md` (`skill_proposals`)**:
     - Recebem no prompt a lista compacta das skills já existentes (`id`, `name`, `description`) para evitar duplicatas.
     - Passam por **dry-run obrigatório** via validação de `_skill_command(..., require_complete=True)` (`skills_admin.py:131-157`): se faltarem seções `Workflow`/`Validation`, se citarem segredos (`_SECRET`) ou ferramentas inexistentes, são descartadas silenciosamente.
     - **Nunca ativam sozinhas**: ficam salvas como propostas pendentes (`skill_proposals` em disco/banco com `status="pending"`) e disparam card/item de revisão na área de Skills / conversa com ações **Aprovar** (chama `SkillLibrary.create`/`update`), **Editar** ou **Descartar**.

---

## 4. Sequenciamento de Implementação e Estratégia de Testes

| Fase | Pacote | Arquivos Principais | Risco / Banco | Critério de Aceite e Testes |
| :--- | :--- | :--- | :--- | :--- |
| **Fase 1** | Pacote 1 (Compactação e Contexto) | `src/agentos/agentic/compaction.py`, `src/agentos/agentic/runtime.py`, `src/agentos/conversations/chat.py`, `src/agentos/agentic/native_turn.py` | Baixo · **Zero migração** | Testes unitários provando: (a) turno com 40 tool outputs grandes preserva o prompt inicial do usuário em `_compact_source`; (b) `history_for_turn` pós-boundary mantém `Verbatim User Requests` até 16k tokens; (c) `<analysis>` grande não corta as 9 seções; (d) leituras repetidas do mesmo arquivo são elididas antes do resumo. |
| **Fase 2** | Pacote 2 (Memória Híbrida + Core/Archival) | `src/agentos/persistence/postgres/agent_memory.py`, `src/agentos/memory/hybrid_index.py`, `src/agentos/agentic/session.py`, `src/agentos/agentic/tools/orin/memory.py`, migração Alembic `fact` 4000 chars + `episodic` | Médio · 1 migração aditiva segura | Testes unitários e de integração provando: (a) dois fatos complementares do mesmo projeto (`Jaccard = 0.55`) **não** sobrescrevem um ao outro; (b) busca semântica/BM25 encontra memória sem palavras idênticas e degrada para FTS5 sem embedder; (c) `hit_count`/`last_used_at` atualizam; (d) memórias > 300 chars entram como preview limpo + `Recall(memory_id=...)` devolve texto integral. |
| **Fase 3** | Pacote 3 (`ProjectMap` PageRank) | `src/agentos/retrieval/pagerank.py`, `src/agentos/retrieval/store.py`, `src/agentos/retrieval/service.py`, `src/agentos/agentic/tools/orin/search_code.py` | Baixo · `SCHEMA_VERSION="2"` reconstruível | Testes unitários em repositório sintético provando: (a) Personalized PageRank com `focus_paths` promove vizinhos diretos/transitivos do subsistema ativo acima de utilitários genéricos; (b) cold start sem `focus_paths` funciona; (c) busca binária respeita `max_tokens` (±15%). |
| **Fase 4** | Pacote 4 (`SearchConversations` + `auto-dream` Episódico/Skills) | `src/agentos/conversations/chat.py`, `src/agentos/agentic/tools/orin/projects.py`, `src/agentos/agentic/tools/public_names.py`, `src/agentos/memory/consolidation.py`, rotas/UI de aprovação de proposta de Skill | Médio · Isolamento de perfil (`test_profile_isolation.py`) | Testes unitários e de integração provando: (a) `SearchConversations` respeita isolamento por `user_id`/`project_id` e `ReadProjectConversation(around_sequence=...)` abre o trecho exato; (b) `auto-dream` extrai episódio técnico com card Undo e bloqueia qualquer conteúdo `tainted`/secret; (c) proposta de `SKILL.md` fica `pending` até aprovação explícita e rotas novas passam em `test_profile_isolation.py`. |
