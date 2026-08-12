# Ollama como provider do Orin — desenho

**Data:** 2026-08-12
**Estado:** aprovado, pronto para plano de implementação

## Objetivo

Adicionar `ollama` como quinto provider do Orin, cobrindo tanto uma instância
local (`http://localhost:11434`) quanto o Ollama Cloud (`https://ollama.com`),
com catálogo de modelos, streaming e tool calling funcionando no loop agêntico.

## Contexto: o que um provider novo toca hoje

O Orin tem quatro providers (`openai`, `anthropic`, `openrouter`, `omniroute`).
A identidade de cada um aparece em oito lugares:

| Local | Papel |
|---|---|
| `frontend/src/api/providers.ts` (`PROVIDER_NAMES`) | lista da UI |
| `frontend/src/features/providers/ProviderSettingsPage.tsx` (`providerLabel`) | rótulo do painel |
| `src/agentos/api/gateway.py` (`_provider_name`) | allowlist da API |
| `src/agentos/provider_catalog/service.py` (`_provider`) | allowlist + normalizador de catálogo |
| `src/agentos/bootstrap/production.py` | registro do upstream de catálogo |
| `src/agentos/workers/chat.py` (`PROVIDER_BASE_URLS`, `_provider_transport`) | resolução de credencial e endpoint |
| `src/agentos/agentic/provider_stream.py` (`HTTPProviderStreamTransport.stream`) | ramo de transporte |
| `src/agentos/persistence/postgres/provider_configuration.py` (`_api_key`, `_base_url`) | regras de credencial |

Mais `src/agentos/provider_catalog/resolver_catalog.py`, que itera uma tupla fixa
de providers.

## Fatos do Ollama que sustentam as decisões

Verificados na documentação oficial em 2026-08-12:

- O endpoint OpenAI-compatível `/v1/chat/completions` existe e suporta streaming,
  tools e `stream_options.include_usage`, mas **não suporta `tool_choice`** e
  **não permite configurar o context length por request** (exige Modelfile).
- A API nativa `/api/chat` aceita `options.num_ctx`, mas responde em **NDJSON**,
  não em SSE — o `normalize_sse` atual só entende linhas `data:`.
- O contexto padrão do Ollama é **4096 tokens**; a própria Ollama recomenda ~64k
  para agentes. Um KV cache maior que a VRAM disponível é transferido para a RAM
  e derruba a velocidade de inferência em 20–50×.
- `GET /api/tags` lista modelos mas **não** traz context length nem capabilities.
  `POST /api/show` traz `capabilities` (`completion`/`tools`/`vision`/`thinking`)
  e `model_info` com uma chave `<arquitetura>.context_length`.
- Autenticação: nenhuma em localhost; `Authorization: Bearer <chave>` em
  `ollama.com`.
- `tools` no `/api/chat` usa exatamente a shape
  `{"type":"function","function":{name,description,parameters}}` — a mesma que
  `agentos/agentic/agent_tools.py` já produz.
- `tool_calls` na resposta **não têm campo `id`**, e `arguments` vem como objeto
  JSON, não como string.

Fontes: <https://docs.ollama.com/api/chat>,
<https://docs.ollama.com/api/openai-compatibility>,
<https://docs.ollama.com/api/authentication>, <https://docs.ollama.com/cloud>,
<https://docs.ollama.com/context-length>,
<https://docs.ollama.com/api-reference/show-model-details>.

## Decisões

| # | Decisão | Alternativa recusada |
|---|---|---|
| 1 | Um provider `ollama`, com modo Local/Cloud derivado do host | Dois providers (`ollama` + `ollama_cloud`), que duplicariam a entrada nas oito listas |
| 2 | API nativa `/api/chat` para os dois modos | OpenAI-compat, que travaria o contexto local em 4096 |
| 3 | Catálogo por `GET /api/tags` + `POST /api/show` por modelo | Só `/api/tags`, sem context window nem capabilities |
| 4 | `num_ctx = min(context window do modelo, budget do turno + reserva)` | Sempre o contexto máximo do modelo, com risco de estouro de VRAM |

## 1. Identidade e credencial

Uma única linha `(user_id, "ollama")` em `provider_configurations`, reusando a
coluna `base_url` que já existe para o OmniRoute. **O modo não é uma coluna
nova** — é derivado do host da `base_url`.

| Modo | `base_url` | `api_key` |
|---|---|---|
| Local | `http://localhost:11434` (editável, para servir uma máquina da LAN) | vazia, permitida |
| Cloud | `https://ollama.com` | obrigatória |

Novo módulo `src/agentos/provider_catalog/ollama.py` com
`normalize_ollama_base_url`, espelhando `normalize_omniroute_base_url` mas **sem**
exigir sufixo `/v1` (a API nativa vive em `/api/*`):

- aceita apenas `http`/`https` com netloc;
- rejeita credenciais embutidas, query e fragment;
- remove barra final e um sufixo `/v1` ou `/api` que o usuário tenha colado.

Uma função `is_ollama_cloud(base_url) -> bool` decide o modo comparando o host
com `ollama.com` (incluindo subdomínios), e é a única fonte dessa distinção.

Em `provider_configuration.py`, as comparações literais com `"omniroute"` em
`_api_key` e `_base_url` passam a consultar dois frozensets nomeados no módulo:

```python
PROVIDERS_WITH_BASE_URL = frozenset({"omniroute", "ollama"})
PROVIDERS_WITH_OPTIONAL_KEY = frozenset({"omniroute", "ollama"})
```

com a regra adicional: `ollama` só dispensa a chave quando `is_ollama_cloud` é
falso. Um salvamento em modo Cloud sem chave falha com a mesma
`ValueError("provider API key is required")` já usada hoje.

## 2. Transporte: `/api/chat` nativo

### Refatoração de suporte

`HTTPProviderStreamTransport.stream` hoje tem dois ramos inline e cerca de 70
linhas; um terceiro a tornaria intratável. A montagem de request é extraída em
três métodos privados — `_anthropic_request`, `_openai_request`,
`_ollama_request` — cada um devolvendo `(endpoint, headers, payload)`. `stream`
fica como despacho por `self.provider` mais o trecho compartilhado de HTTP,
projeção de rate limit e normalização. Nenhum comportamento existente muda; os
testes atuais de payload de Anthropic e OpenAI devem passar sem alteração.

### Payload

`POST {base_url}/api/chat`:

```json
{
  "model": "…",
  "messages": [...],
  "stream": true,
  "tools": [...],
  "options": { "num_ctx": 32768, "num_predict": 4096 }
}
```

- **`tools` passa sem conversão** — a shape produzida por `agent_tools.py` já é
  a que o Ollama espera.
- **`tool_choice: "none"`** (enviado pelo runtime na iteração final, `runtime.py`)
  → **`tools` é omitido do payload**. É mais estrito que a dica atual e não
  depende do `tool_choice` que o Ollama não suporta. Qualquer outro valor de
  `tool_choice` é ignorado.
- `max_output_tokens` → `options.num_predict` apenas quando há cap; sem cap o
  campo é omitido, mantendo a política atual de não cortar respostas longas.
- Mensagens `role: "system"` permanecem inline no array `messages`.
- Header `authorization: Bearer <chave>` apenas quando há chave; ausente no modo
  local.

### `num_ctx`

```
num_ctx = min(context_window, max_context_tokens + CONTEXT_WINDOW_RESERVE_TOKENS)
```

`max_context_tokens` e `CONTEXT_WINDOW_RESERVE_TOKENS` já existem em
`workers/chat.py`. O worker computa o valor em `_provider_transport` (ele já
consulta o catálogo em `_max_context_tokens_for`) e o injeta no transporte por
um argumento de construtor `num_ctx: int | None`; o transporte não fala com o
banco.

Quando o modelo não tem `context_window` no catálogo (catálogo ainda não
atualizado), o fallback é **16.384**, e não o default de 60k do worker: pedir 60k
de KV cache para um modelo desconhecido é exatamente o cenário de estouro de
VRAM. Overshoot não quebra correção — o Ollama limita `num_ctx` ao máximo
treinado do modelo — mas custa VRAM.

`num_ctx` é omitido quando o transporte recebe `None`, o que mantém os outros
providers intocados.

### `normalize_ndjson`

Função nova em `provider_stream.py`, ao lado de `normalize_sse`, com a mesma
assinatura de saída (`Iterator[NormalizedStreamItem]`). Cada linha é um objeto
JSON completo:

| Entrada | Saída |
|---|---|
| `message.content` não-vazio | `TEXT` |
| `message.tool_calls[]` | um `TOOL_CALL` por chamada, `arguments_delta = json.dumps(arguments)` |
| `done: true` | `USAGE` (de `prompt_eval_count`/`eval_count`, measurement `CONFIRMED`) e depois `FINISH` |
| `{"error": "…"}` | `ERROR` via `_safe_error`, que degrada para `UNKNOWN` sanitizado |
| linha malformada | `ERROR` com código `INVALID_NDJSON`, espelhando `INVALID_SSE_JSON` |

Detalhes que exigem cuidado:

- **IDs de tool call.** O Ollama não envia `id`. O normalizador sintetiza
  `tool-call:{n}` com um **contador de escopo do stream**, incrementado a cada
  tool call encontrada. Usar o índice dentro do chunk faria duas chamadas
  distintas colidirem no mesmo id, e o runtime as fundiria numa só.
- **Finish reason.** Se houve qualquer tool call no stream, o `FINISH` é
  `TOOL_CALLS`; caso contrário aplica-se o mapa de `done_reason` do `_finish`
  existente (`stop` → `STOP`, `length` → `LENGTH`).
- **`message.thinking` é descartado.** Não existe canal de raciocínio no
  `NormalizedStreamItem`, e o payload não envia `think`. Fora de escopo.

`project_rate_limit_headers` é reusado sem alteração: o modo local não emite
esses headers e a projeção simplesmente devolve tudo `None`.

## 3. Catálogo

### Correção do contrato `ProviderCatalogUpstream`

O Protocol em `provider_catalog/ports.py` declara hoje apenas
`fetch(self, api_key: str)`. O `OmniRouteCatalogClient` só encaixa nele porque
seu `base_url` tem valor default — e é por isso que `ProviderModelCatalogService.
refresh` precisa ramificar por nome de provider só para decidir se passa o kwarg.
O Protocol passa a declarar o parâmetro que dois dos três clientes já usam:

```python
class ProviderCatalogUpstream(Protocol):
    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]: ...
```

`OpenRouterModelCatalogClient` ganha o parâmetro e o ignora. Com isso a chamada em
`refresh` vira uma linha só, **sem ramo por provider**:

```python
raw_models = upstream.fetch(api_key, base_url=str(credential.get("base_url") or ""))
```

Isso remove um desvio em vez de acrescentar outro; o frozenset de providers
continua necessário apenas para a regra de chave vazia.

### `OllamaCatalogClient`

Novo em `src/agentos/provider_catalog/ollama.py`:

1. `GET {base}/api/tags` para listar;
2. `POST {base}/api/show {"model": <nome>}` por modelo, para ler
   `capabilities` e `model_info`.

Normalizador `_normalize_ollama` novo em `provider_catalog/service.py`:

| Campo do `ProviderModelRecord` | Origem |
|---|---|
| `model_id`, `display_name` | `model` / `name` do `/api/tags` |
| `context_window` | a chave de `model_info` cujo nome termina em `.context_length` (o prefixo varia por arquitetura, então a busca é por sufixo) |
| `capabilities` | array `capabilities` do `/api/show` |
| `input_modalities` | `["text"]`, mais `"image"` quando há `vision` |
| `output_modalities` | `["text"]` |
| `pricing` | `None` |
| `route_kind` | `"model"` |

Um `/api/show` que falhe **degrada apenas aquele modelo** (`context_window=None`,
`capabilities=()`) em vez de derrubar o refresh inteiro. Uma falha de conexão no
`/api/tags` vira `RuntimeError("Ollama connection failed")` sanitizado, como o
`OmniRouteCatalogClient`, e o serviço a converte em `ProviderCatalogUnavailable`.

Em `ProviderModelCatalogService.refresh`, o caso especial do kwarg `base_url`
desaparece com a correção do Protocol acima; a tolerância a chave vazia passa a
consultar o frozenset nomeado.

Registro em `bootstrap/production.py`:

```python
{"openrouter": OpenRouterModelCatalogClient(),
 "omniroute": OmniRouteCatalogClient(),
 "ollama": OllamaCatalogClient()}
```

## 4. Worker, gateway e frontend

**`workers/chat.py`**

- `PROVIDER_BASE_URLS["ollama"] = "http://localhost:11434"`.
- `_provider_transport` generaliza o caso especial do OmniRoute: a normalização
  de `base_url` e o `allow_empty` do `_credential_value` passam a ser escolhidos
  pelos frozensets, e o `num_ctx` calculado é passado ao transporte.

**`api/gateway.py`**

- `_provider_name` aceita `ollama`.
- Em `configure_provider`, a checagem `provider_name != "omniroute"` para exigir
  `api_key` consulta o frozenset; a regra fina Local/Cloud fica no adaptador, que
  é quem conhece a `base_url`.
- Nova rota `POST /v1/providers/ollama/test`, espelhando a do OmniRoute e batendo
  em `/api/tags`, devolvendo `{connected, models_available, base_url}`. A rota
  `/v1/providers/omniroute/test` fica intocada, então nada existente quebra.
  `PostgresProviderConfigurationAdapter.test_connection` passa a despachar por
  provider em vez de rejeitar tudo que não for OmniRoute.

**Frontend**

- `PROVIDER_NAMES` e `providerLabel` ganham `ollama`.
- Novo componente `OllamaSetup` no painel do provider, bem menor que o
  `OmniRouteSetup`: um controle segmentado Local/Cloud, URL pré-preenchida por
  modo mas editável, campo de chave visível apenas no modo Cloud, botões
  "Testar conexão" e "Salvar e ativar", com refresh automático do catálogo após
  salvar — o mesmo padrão do `onSave` do OmniRoute.
- `ModelPicker`: modelos cujas `capabilities` não incluem `tools` aparecem
  **marcados e rebaixados na ordenação**, não escondidos. O loop do Orin é movido
  a ferramentas, então um modelo sem `tools` não funciona nele; mas fazer um
  modelo recém-baixado sumir sem explicação é pior do que mostrá-lo com aviso.

**`provider_catalog/resolver_catalog.py`**

A tupla fixa de `list_models` passa a incluir `ollama` **e** `omniroute`. O
OmniRoute nunca esteve nessa lista — não é um bug ativo, porque o caminho de chat
usa `provider`/`model_id` do turno sem passar pelo resolver, mas é uma lacuna
real e a correção é de uma palavra.

## Fora de escopo

- **Instalador e gerenciamento de runtime.** O OmniRoute tem `install` e
  start/stop porque é um pacote npm; o Ollama se instala por app nativo, e o
  equivalente aqui seria um link, não um comando.
- **Canal de raciocínio (`think` / `message.thinking`).** Exigiria um tipo novo
  de evento no `NormalizedStreamItem` e tratamento no runtime e na UI.
- **Embeddings** (`/api/embed`) e gerenciamento de modelos (`/api/pull`,
  `/api/delete`).
- **Preço por token no modo Cloud.** A API não publica pricing; `pricing` fica
  `None`, como no OmniRoute.

## Testes

**Unitários**

- `tests/unit/agentic/test_provider_stream_payload.py`: payload do Ollama
  (`options.num_ctx`, `num_predict` presente e ausente, `tools` omitido quando
  `tool_choice` é `none`, header de auth presente no Cloud e ausente no local);
  `normalize_ndjson` (texto, tool calls recebendo ids distintos, usage do chunk
  final, finish com e sem tool call, linha de erro, linha malformada).
- `tests/unit/provider_catalog/test_service.py`: `_normalize_ollama` (descoberta
  da chave `*.context_length`, capabilities, `vision` virando modalidade
  `image`, `/api/show` que falha degradando só aquele modelo).
- `tests/unit/provider_catalog/`: `OpenRouterModelCatalogClient` continua
  funcionando após ganhar o parâmetro `base_url` que ignora, e `refresh` passa a
  chamar todo upstream com o mesmo kwarg.
- Novo `tests/unit/provider_catalog/test_ollama_client.py`:
  `normalize_ollama_base_url` (rejeita credenciais e query, remove `/v1` e
  `/api`), `is_ollama_cloud`, merge de `tags` + `show`, falha sanitizada.
- `tests/unit/workers/test_chat.py`: resolução de `base_url` e `allow_empty` para
  Ollama local e Cloud; cálculo de `num_ctx`, incluindo o fallback de 16.384.
- `tests/unit/api/test_api_asgi.py`: allowlist aceita `ollama`; chave exigida no
  host Cloud e opcional no local.
- Frontend: marcador de modelo sem `tools` no `ModelPicker`; toggle Local/Cloud
  do painel mostrando e escondendo o campo de chave.

**Integração**

- Estender o padrão de `tests/integration/agentic/test_provider_tool_loop.py`
  com um fake NDJSON, cobrindo um turno completo com tool call e resultado.

## Nota de sequenciamento

Em 2026-08-12 o working tree tem alterações não commitadas em
`src/agentos/agentic/provider_stream.py` (cache de prompt do Anthropic) e
`src/agentos/workers/chat.py` (`_max_context_tokens_for`, do qual o cálculo de
`num_ctx` depende). A implementação do Ollama constrói **em cima** desse
trabalho; ele precisa estar commitado ou pelo menos estável antes que a
refatoração de `stream()` comece, para que os dois conjuntos de mudanças não
colidam no mesmo método.
