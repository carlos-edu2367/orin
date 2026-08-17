# RAG Local — Memória Semântica de Código

Data: 2026-08-17
Status: aprovado, pronto para plano de implementação

## Problema

A única busca de conteúdo que o agente tem hoje é `search_files`
([`agent_tools.py:889`](../../../src/agentos/agentic/agent_tools.py)), um wrapper
sobre `ConversationWorkspace.search()`
([`workspace.py:187`](../../../src/agentos/agentic/workspace.py)): expressão
regular, linha a linha, com filtro de glob e teto de resultados.

Isso resolve "encontre a string exata" e falha em "entenda onde fica o fluxo de
autorização de ferramentas". O agente precisa acertar o identificador antes de
poder procurar por ele, o que o obriga a adivinhar nomes ou a listar e ler
arquivos até encontrar. Em um projeto do tamanho do Orin isso queima contexto e
turnos.

## Decisão central: somar, não substituir

`search_files` **permanece exatamente como está**. Busca semântica não substitui
regex — ela cobre o caso oposto.

- Regex é insubstituível quando o identificador exato é conhecido: procurar
  `MemoryCommitRequest` devolve as ocorrências reais, todas elas. Uma busca
  vetorial devolveria "coisas parecidas com commit de memória" e poderia perder
  uma.
- Semântico é insubstituível quando o nome é desconhecido: "onde fica o controle
  de permissão de ferramenta?" não tem uma string para procurar.

A implementação adiciona `search_code` ao lado de `search_files`, com descrições
que tornam a escolha óbvia para o modelo.

## Decisão: sem ChromaDB

A proposta original citava ChromaDB. Foi descartada.

ChromaDB arrasta `onnxruntime`, `tokenizers` e `pulsar-client` — dezenas a
centenas de megabytes e vários hidden-imports para resolver no build congelado
do PyInstaller — para entregar o que aqui é essencialmente uma tabela mais um
produto escalar. O Orin já embarca SQLite, e o SQLite embarcado tem **FTS5 com
`bm25()`** (verificado: versão 3.50.4), o que entrega o lado léxico da busca
híbrida sem nenhuma dependência nova.

A única dependência adicionada é `numpy`.

## Escolhas confirmadas

| Decisão | Escolha | Motivo |
|---|---|---|
| Escopo do índice | Por pasta de projeto vinculada | O valor só se acumula se o índice sobreviver à conversa |
| Fonte de embeddings | Ollama padrão, provider remoto opt-in, léxico como fallback | Local-first sem engordar o release; nunca falha duro |
| Chunking | Splitter heurístico por linguagem | Zero dependências; tree-sitter fica para depois, atrás da mesma interface |
| Banco de vetores | SQLite sidecar + numpy | Sem ChromaDB, sem extensão binária |
| Execução da indexação | Thread de background dedicada | Índice reconstruível não precisa de dispatch durável |

## Arquitetura

Módulo novo `src/agentos/retrieval/`, seguindo o padrão do repositório
(`models.py` / `ports.py` / adaptadores / `service.py`):

```
retrieval/
  models.py        Chunk, SearchHit, IndexStatus, EmbedderIdentity
  ports.py         EmbeddingPort, ChunkStore, Chunker
  chunking.py      splitter heurístico
  symbols.py       extração de símbolo e de imports por linguagem
  store.py         SQLite sidecar (schema, upsert, consultas)
  indexer.py       varredura incremental por hash
  service.py       RetrievalService.search() — busca híbrida
  embeddings/
    ollama.py      padrão
    remote.py      provider HTTP (opt-in nas Settings)
    lexical.py     fallback sem vetores
```

### Isolamento

`RetrievalService` não conhece `ConversationWorkspace` nem a camada de
ferramentas. Recebe uma raiz, um `Chunker`, um `EmbeddingPort` e um
`ChunkStore`. Cada peça é testável sozinha:

- o chunker é função pura — texto entra, chunks saem;
- o store é I/O sem lógica de ranking;
- o serviço é ranking sem I/O de disco.

### Identidade e localização do índice

O índice vive em `orin_paths().data / "retrieval" / <workspace_id>.db`, um
SQLite **separado** do `orin.db`.

A chave é o `workspace_id`. Ele já existe e já é estável entre conversas:
`PostgresProjectStore.create` grava `workspace_id = f"workspace:{project_id}"`
([`projects/store.py`](../../../src/agentos/projects/store.py)), e a tabela
`workspace_roots` liga esse id à pasta local escolhida pelo usuário
([`local_workspace/store.py`](../../../src/agentos/local_workspace/store.py)).
Nenhuma identidade nova precisa ser inventada.

O arquivo é separado do banco de domínio porque o índice é dado derivado e
reconstruível, cresce rápido, e não deve entrar nas migrações Alembic nem
inflar o WAL do `orin.db`. Apagar o arquivo é uma operação segura e completa.

### Schema do sidecar

| tabela | conteúdo |
|---|---|
| `files` | `path` PK, `content_hash`, `size_bytes`, `mtime_ns`, `language`, `indexed_at` |
| `chunks` | `chunk_id` PK, `path`, `start_line`, `end_line`, `symbol`, `kind`, `text` |
| `chunks_fts` | tabela virtual FTS5 sobre `text` e `symbol` → BM25 nativo |
| `vectors` | `chunk_id` PK, `embedding` BLOB (float32 empacotado) |
| `imports` | `path`, `target` — o grafo usado no reranking |
| `index_meta` | `schema_version`, `embedder_id`, `model`, `dim`, `last_scan_at` |

`index_meta` previne o modo de falha mais traiçoeiro desse tipo de sistema: se o
embedder ou o modelo mudar, os vetores gravados viram lixo silencioso — ainda
retornam resultados, apenas errados. Ao abrir o índice, se `embedder_id`,
`model` ou `dim` não baterem com o embedder ativo, a tabela `vectors` é truncada
e reconstruída. `chunks` e `chunks_fts` sobrevivem, então a busca léxica
continua funcionando durante a reconstrução.

### Sobre a dependência `numpy`

Com cerca de 30 mil chunks de 768 dimensões, o produto escalar em Python puro
leva segundos por busca; com `numpy`, milissegundos.

A alternativa considerada foi `sqlite-vec`: uma extensão binária por plataforma,
que além disso exige `enable_load_extension` habilitado no `sqlite3` do CPython
— atrito real em um build congelado. `numpy` é uma wheel única e comportada sob
PyInstaller. É a única dependência nova deste trabalho.

## Fluxo de indexação

### Gatilhos

Três, todos incrementais:

1. **Ao vincular a pasta** (`set_root`): primeira varredura completa, em
   background.
2. **Depois de uma ferramenta que muta**: `write_file`, `edit_file` e
   `run_command` já chamam `file_snapshot()` / `changed_files()`
   ([`workspace.py:239`](../../../src/agentos/agentic/workspace.py)) para montar
   a lista de artefatos da resposta. Essa lista já é calculada de graça e vira a
   fila de reindexação — o gatilho mais barato e mais preciso disponível no
   código atual.
3. **Antes de um `search_code`**, se `last_scan_at` for mais velho que 60
   segundos: cobre edições feitas fora do Orin, com o usuário no editor dele.

### Detecção de mudança

`mtime_ns` primeiro: arquivo com mtime igual é pulado sem ser lido. Mtime
diferente dispara leitura e comparação de `content_hash` (blake2b dos bytes); o
reembedding só acontece se o conteúdo mudou de fato. Arquivo que sumiu tem
chunks e vetores removidos em cascata.

### Execução

Uma thread de background dedicada ao serviço, com uma fila e um único worker.

O dispatch durável de `workers/` (que tem um `WorkerPool.MAINTENANCE`)
**não** será usado. Aquela máquina existe para trabalho que precisa sobreviver a
um crash e ser arrendado exatamente uma vez. Um índice reconstruível não precisa
dessa garantia: se o processo morre no meio, a próxima varredura refaz o que
faltou. Usar a fila durável aqui seria pagar complexidade e migração de schema
por uma garantia sem valor neste caso.

### Exclusões

- O `.gitignore` do projeto, respeitado de verdade — é o sinal que o usuário já
  escreveu sobre o que não importa.
- Denylist fixa: `.git`, `node_modules`, `.venv`, `dist`, `build`,
  `__pycache__`, `*.lock`, e qualquer arquivo que não decodifique como UTF-8.
- Teto de bytes por arquivo, reusando o `MAX_SEARCH_FILE_BYTES` que o `search`
  atual já aplica.
- **Denylist de segredos**: `.env*`, `*.pem`, `*.key`, `id_rsa*`, `*.pfx`.
- As mesmas guardas de sandbox de `resolve()` e do `search` atual: symlink
  resolvido e re-checado contra a raiz, para a varredura não escapar da pasta
  vinculada.

A denylist de segredos é aplicada **antes** do chunker, nunca como filtro de
resultado. No modo Ollama isso é higiene; no modo provider remoto é requisito —
o conteúdo indexado sai da máquina, e um `.env` embutido em um chunk vira uma
chave de API dentro de um corpo de requisição HTTP.

## Fluxo de busca

Para `search_code("onde o Orin decide se uma ferramenta pode rodar?")`:

1. **Embeda a query** com o `EmbeddingPort` ativo.
2. **Dois conjuntos de candidatos**: top-50 por similaridade de cosseno (numpy
   sobre a matriz de vetores) e top-50 por `bm25()` do FTS5.
3. **Fusão por Reciprocal Rank Fusion**: `score = Σ 1/(60 + rank)`. RRF porque
   escores de cosseno e de BM25 não são comparáveis em escala; RRF usa apenas a
   posição, cabe em uma linha e é robusto.
4. **Reranking por grafo de imports**: bônus fixo para chunks em arquivos que
   importam, ou são importados por, os arquivos do top-5 corrente. É este passo
   que transforma "achou texto parecido" em "achou o módulo certo do fluxo".
5. **Retorno citável**: `path:start-end`, símbolo e o texto do chunk, sempre com
   números de linha reais, para que o agente possa abrir e conferir.

No modo fallback léxico, os passos 1 e 2-vetorial desaparecem; sobram BM25 e o
reranking por grafo. A resposta **declara explicitamente** que está em modo
léxico: o agente precisa saber que a busca foi mais fraca, senão confia demais
em um resultado pior.

## Superfície exposta ao modelo

| ferramenta | descrição ao modelo |
|---|---|
| `search_files` (existente) | Busca por expressão regular. Use quando você sabe o texto exato: um nome de símbolo, uma string literal, um TODO. |
| `search_code` (nova) | Busca por significado no projeto indexado. Use quando você não sabe onde algo está, ou quer entender como um fluxo funciona. Retorna definições inteiras com path:linha. |
| `project_map` (nova) | Os arquivos mais conectados no grafo de imports, com os símbolos de topo. A arquitetura em uma chamada. |

`search_code` é `read_only=True`, categoria `filesystem`. Os `policy_tags`
acompanham o embedder configurado, não a ferramenta: sem tag de rede quando o
embedder é o Ollama local, com `("network",)` quando o modo provider remoto está
ativo — porque nesse caso a ferramenta de fato fala com um serviço externo.

`project_map` sai quase de graça do que o índice já guarda na tabela `imports`.

## Erros e degradação

O princípio: **`search_code` nunca falha duro**. Cada modo de falha tem um degrau
abaixo dele.

| Situação | Comportamento |
|---|---|
| Nenhuma pasta vinculada | Erro claro: "este projeto não tem pasta vinculada; use `search_files`" |
| Ollama fora do ar ou modelo ausente | Cai para léxico, avisa no retorno, registra uma vez por sessão |
| Índice ainda em construção | Responde com o que já foi indexado, informando o progresso |
| Embedder mudou | Vetores truncados; léxico atende enquanto reconstrói |
| Índice corrompido | Apaga o arquivo e reindexa; não tenta reparar |
| Projeto acima de 50 mil chunks | Indexa até o teto, priorizando profundidade menor e recência maior, e declara o corte |

## Testes

- **Chunker** — puro, sem I/O. Casos por linguagem; arquivo sem nenhuma
  definição; arquivo de uma linha; definição maior que a janela máxima.
- **Store** — SQLite em `:memory:`. Upsert idempotente; cascata ao remover
  arquivo; invalidação por troca de `embedder_id`.
- **Indexer** — `tmp_path` com arquivos reais. Mtime igual não reembeda;
  conteúdo alterado reembeda; arquivo removido limpa; `.gitignore` e a denylist
  de segredos são respeitados; symlink apontando para fora da raiz é ignorado.
- **Service** — `EmbeddingPort` falso e determinístico (vetor derivado de hash).
  Cobre RRF, o bônus do grafo, e a degradação para léxico quando o embedder
  levanta exceção.
- **Ferramenta** — `search_code` entra em
  `tests/unit/agentic/test_agent_tools.py`, no mesmo formato que o teste
  existente de `search_files`.

Nenhum teste depende de Ollama estar rodando.

## Fora de escopo

Ficam de fora deste trabalho, deliberadamente:

- seleção semântica de skills e plugins;
- RAG sobre os documentos que o módulo `reading/` já extrai (PDF, docx, xlsx,
  pptx);
- memória semântica de conversas, substituindo o relevance scan descrito em
  `persistence/postgres/agent_memory.py`;
- geração automática de um resumo de arquitetura na primeira indexação.

O `EmbeddingPort` é desenhado sem nenhuma referência a código ou a arquivos,
exatamente para que esses consumidores possam plugar nele depois sem reescrita.
É a preparação que custa zero hoje.

## Trabalho futuro registrado

1. **tree-sitter** substituindo o chunker heurístico, atrás da interface
   `Chunker` já definida aqui. Chunks por limite sintático real, em qualquer
   linguagem. Custo: `tree-sitter` mais o pacote de gramáticas (~30–50MB de
   wheels) e mais uma peça para o PyInstaller resolver. Como fica atrás da
   interface, é uma implementação nova e não uma reescrita.
2. **Embedder ONNX embarcado no pacote** (bge-small ou MiniLM via
   `onnxruntime`, ~120–150MB), como quarta implementação de `EmbeddingPort`.
   Elimina a exigência de o usuário ter Ollama instalado e torna o modo
   totalmente offline funcional no primeiro clique, ao custo de engordar o
   release.
