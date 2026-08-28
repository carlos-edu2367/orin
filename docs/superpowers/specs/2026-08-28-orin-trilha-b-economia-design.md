# Orin — Trilha B: economia de tokens do runtime agêntico

Data: 2026-08-28
Base: `main`, `v0.2.12`
Escopo: `agentos.agentic.runtime`, `agentos.agentic.provider_stream`, `agentos.agentic.session`
Depende de: Trilha A (medição em `turn_quality_metrics`, fases, transcript)

## 1. O problema, medido

### 1.1 Toda entrada de cache é invalidada pela iteração seguinte

`_age_tool_results` (`src/agentos/agentic/runtime.py:797`) é chamado **depois** de cada requisição (`:383`) e comprime, no lugar, o resultado de ferramenta que acabou de ser enviado. Na iteração seguinte, aquela mensagem — que está no meio do prefixo — mudou de bytes.

Medição direta do loop, com seis chamadas de ferramenta e resultados de 3.000 caracteres:

| iteração | mensagens | prefixo idêntico ao da requisição anterior | divergiu |
|---:|---:|---:|:--|
| 2 | 4 | 2 de 2 | não |
| 3 | 6 | 3 de 4 | **sim** |
| 4 | 8 | 5 de 6 | **sim** |
| 5 | 10 | 7 de 8 | **sim** |
| 6 | 12 | 9 de 10 | **sim** |
| 7 | 14 | 11 de 12 | **sim** |

O padrão é invariável: o prefixo compartilhado é sempre `len(anterior) − 1`. `HTTPProviderStreamTransport._with_cached_tail` (`src/agentos/agentic/provider_stream.py:361`) marca o breakpoint na **última** mensagem, exatamente a que será comprimida em seguida. A entrada gravada nunca serve.

### 1.2 A compressão custa mais do que economiza

Um token lido de cache custa cerca de um décimo de um token de input comum. Comprimir um resultado de 12.000 caracteres para 400 economiza ~2.900 tokens a preço cheio, e ao fazê-lo invalida um prefixo que, numa tarefa de dez passos, já passa de 30.000 tokens — que teriam custado 10%.

A otimização se paga negativa. Não é uma questão de ajuste de parâmetro: qualquer mutação retroativa do prefixo tem esse efeito.

### 1.3 O bloco cacheado do system prompt é o volátil

`_anthropic_request` marca `cache_control` apenas no **primeiro** item de system, com o comentário de que ele é "byte-identical across every iteration of a turn (and most turns of a conversation)". A primeira metade é verdadeira; a segunda não. `build_system_prompt` (`src/agentos/agentic/session.py:209`) embute no mesmo bloco:

- `workspace_tree` — até 60 entradas, muda a cada arquivo criado;
- `tool_ledger` — 20 registros, muda a cada ferramenta chamada;
- `skill_catalog` — recuperado por similaridade com a tarefa do turno;
- `hook_context`, e a data corrente.

Entre dois turnos da mesma conversa esse bloco praticamente nunca se repete. O único breakpoint de system, mais o de tools que vem depois dele, morrem juntos.

### 1.4 Repetição bem-sucedida não é barrada

`_failed_signatures` (`:127`) impede repetir uma chamada que **falhou**. Repetir `read_file` do mesmo arquivo cinco vezes com sucesso é livre — e a Trilha A instrumentou isso justamente como `redundant_tool_calls`.

## 2. Objetivo

1. O prefixo de mensagens de um turno só cresce; nada anterior muda de bytes.
2. O bloco de system marcado como cacheável é genuinamente estável entre turnos.
3. Os breakpoints ficam onde há prefixo estável para reaproveitar.
4. Uma leitura repetida idêntica, sem escrita no meio, não é reexecutada.

Medido por `cached_fraction` e `redundant_fraction` em `GET /v1/runtime/quality`, contra a linha de base da Trilha A.

## 3. Não-objetivos

- Contrato de conclusão com evidência e subagentes estruturados: **Trilha C**.
- Reduzir o número de ferramentas publicadas: já feito na Trilha A (§7.3), que fechou o item B5 original.
- Cache em providers que não o expõem. Ollama e a maioria dos gateways OpenAI-compatíveis não aceitam `cache_control`; para eles a Trilha B entrega prefixo estável e dedup, não cache.

## 4. B2 — A lista de mensagens passa a ser append-only

### 4.1 Decisão

`_age_tool_results` deixa de ser chamado por iteração. O encolhimento de resultados antigos passa a acontecer **dentro de `_maybe_compact`**, que já reescreve o prefixo inteiro e cujo cache miss é inevitável e esperado.

Entre duas compactações, `messages` só recebe append. O prefixo da requisição N é prefixo exato da requisição N+1.

### 4.2 O que substitui a economia perdida

Nada, deliberadamente. Os tokens que a compressão economizava passam a ser cobrados como leitura de cache, mais baratos do que a compressão economizava. Quando o volume realmente cresce, `_maybe_compact` dispara e resolve de uma vez — comprimindo resultados antigos *e* resumindo, num único ponto de invalidação.

O limiar de compactação (`CONTEXT_COMPACTION_THRESHOLD = 0.82`) não muda. O que muda é que ele passa a ser o **único** momento em que o prefixo é reescrito.

### 4.3 Risco

Uma tarefa muito longa carrega mais tokens por requisição entre compactações. Mitigação: são tokens cacheados, e `input_tokens_per_completed_turn` já é medido — se subir sem `cached_fraction` acompanhar, o limiar está errado e é um número, não uma reescrita.

Para um provider sem cache o efeito é adverso: mais tokens a preço cheio. Por isso o encolhimento por iteração é preservado, sob a bandeira `prefix_caching`, quando o provider do turno não suporta cache (§7).

## 5. B1 — Prompt de sistema em duas camadas

`build_system_prompt` passa a devolver `(estável, volátil)`.

**Estável** — idêntico entre turnos do mesmo workspace e mesmo modelo: identidade, regras de trabalho, blocos de referência (PDF, navegador, skills, subagentes, `ask_user`), dica de workspace, ambiente, nomes das ferramentas.

**Volátil** — `workspace_tree`, `tool_ledger`, memórias, catálogo de skills recuperado, contexto de hooks, data corrente.

O runtime envia os dois como itens de system distintos, nessa ordem. Só o estável recebe `cache_control`.

`AgenticTurnRuntime.system_prompt` continua aceitando uma string única (todo teste e todo chamador que não optou pela separação), tratada como inteiramente estável — o comportamento de hoje.

## 6. B3 — Breakpoints onde há prefixo estável

Anthropic admite quatro. A alocação passa a ser:

1. fim do bloco de system **estável**;
2. última ferramenta (mantido);
3. fim da penúltima unidade de mensagens — o ponto estável mais recente;
4. cauda (mantido).

O terceiro é o que passa a valer alguma coisa: com prefixo append-only, ele garante um acerto mesmo quando a cauda do turno anterior já saiu da janela de cache.

## 7. Providers sem cache

`HTTPProviderStreamTransport` já conhece o provider do turno. `supports_prefix_caching` passa a ser explícito: verdadeiro para `anthropic`, falso para `ollama`, e para os demais segue a leitura de `cached_input_tokens` que a Trilha A já instrumentou — um provider que reportou cache alguma vez é tratado como suportado.

Quando falso, o runtime mantém `_age_tool_results` por iteração: sem cache, encolher é puro ganho.

## 8. B4 — Repetição bem-sucedida com ponteiro

Uma chamada cuja assinatura `(nome, argumentos)` já teve sucesso neste turno não é reexecutada. Devolve o conteúdo anterior com uma nota dizendo que veio da chamada anterior.

**Só quando as duas condições valem:**

1. a ferramenta é `read_only`;
2. nenhuma ferramenta capaz de escrever rodou entre as duas chamadas.

A segunda condição é o que torna isso correto: reler um arquivo depois de editá-lo é legítimo e precisa acontecer. Um contador de escritas por turno, comparado com o valor no momento da leitura original, decide.

`run_command` não é `read_only` e portanto nunca é deduplicado, mesmo quando o comando é uma leitura — o runtime não tem como saber.

## 9. Erros e degradação

| falha | comportamento |
|---|---|
| provider sem cache | encolhimento por iteração preservado |
| prompt de camada única | tratado como inteiramente estável, como hoje |
| dedup com dúvida sobre escrita | executa de novo; nunca serve um resultado possivelmente velho |
| compactação falha | igual à Trilha A: fallback estruturado |

A garantia de reconciliação de efeitos permanece invariável. O dedup nunca se aplica a ferramenta capaz de escrever, portanto não pode suprimir um efeito externo.

## 10. Testes

- `test_prefix_stability.py` — o prefixo da requisição N é prefixo exato da N+1 ao longo de dez iterações; após uma compactação, diverge uma vez e volta a ser estável; com provider sem cache, o encolhimento por iteração continua acontecendo.
- `test_cache_breakpoints.py` — quatro breakpoints no máximo; o system estável marcado e o volátil não; nenhum breakpoint numa mensagem que ainda vai mudar.
- `test_layered_prompt.py` — o bloco estável é byte-idêntico entre dois turnos que diferem em árvore de workspace, ledger e memórias; o volátil difere.
- `test_successful_call_dedup.py` — segunda leitura idêntica não invoca a ferramenta; uma escrita no meio faz a terceira invocar de novo; `run_command` nunca é deduplicado; o resultado servido diz que veio da chamada anterior.

## 11. Critérios de aceite

1. Ao longo de dez iterações sem compactação, cada requisição tem a anterior como prefixo exato.
2. `cached_fraction` deixa de ser nula e é maior que zero num turno multi-iteração com Anthropic.
3. O bloco de system cacheado é idêntico entre dois turnos da mesma conversa que criaram arquivos.
4. Uma leitura idêntica repetida sem escrita no meio não chega à ferramenta.
5. Um provider sem suporte a cache mantém exatamente o comportamento de hoje.
6. `redundant_fraction` medido cai em relação à linha de base da Trilha A.

---

## 12. Desvios e resultados medidos

### 12.1 B5 já estava feito

O tiering de MCP, plugins, navegador e subagentes foi entregue na Trilha A, pelos conjuntos de ferramentas por fase. Verificado: em `orient`, `browse_page`, `ask_agents` e `fetch_url` estão fora; `browse_page` só aparece quando o contrato declara `browser`. Nenhum código novo foi necessário.

### 12.2 O que foi medido

**Prefixo reaproveitável entre iterações** (seis chamadas de ferramenta, resultados de 3.000 caracteres):

| provider | antes | depois |
|---|---:|---:|
| anthropic | ~0% | **100%** |
| ollama | 5% | 5% (inalterado, por desenho) |

**Bloco de system cacheável entre turnos da mesma conversa** (turnos que diferem em árvore de workspace, ledger e memórias):

| | tokens | cacheável entre turnos |
|---|---:|---:|
| antes | ~1.584 | 0% |
| depois | ~1.517 estáveis + ~67–98 voláteis | **96%** |

### 12.3 O que continua sem número

`cached_fraction` real, cobrado por um provider. Como na Trilha A, produzir esse número exige credencial e é passo do operador: `scripts/agent_bench.py --record depois --compare baseline`. As medições acima são do prefixo e do prompt, não da fatura.

### 12.4 `run_command` fica de fora do dedup

Um comando que apenas lê (`cat`, `ls`, `git status`) é o caso mais comum de repetição desperdiçada, e não é deduplicado: `run_command` não é `read_only` e o runtime não tem como saber se um comando escreve. Classificar comandos por padrão seria adivinhação com consequência de correção. Fica como está.
