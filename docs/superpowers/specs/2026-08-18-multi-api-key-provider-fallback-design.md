# Múltiplas API keys por provider com fallback automático — desenho

**Data:** 2026-08-18
**Estado:** aprovado, pronto para plano de implementação

## Objetivo

Permitir que o usuário cadastre mais de uma API key por provider (ex.: duas
contas free tier + uma paga do mesmo serviço), definindo uma ordem de
prioridade. Quando uma chamada ao provider falha por um erro atribuível à key
(autenticação, quota/rate limit, rede), o sistema deve automaticamente tentar
a próxima key disponível na lista, sem exigir intervenção do usuário.

## Contexto: como funciona hoje

- `provider_configurations` (`src/agentos/persistence/postgres/schema.py:384-402`)
  tem uma `UniqueConstraint(user_id, provider)` — estruturalmente só permite
  uma key por provider por usuário, guardada em `api_key_ciphertext`.
- `PostgresProviderConfigurationAdapter.configure()`
  (`src/agentos/persistence/postgres/provider_configuration.py`) faz upsert
  escopado por `(user_id, provider)`.
- `chat.py::_transport_for` (`src/agentos/workers/chat.py:393-407`) busca essa
  única credencial e constrói um único `HTTPProviderStreamTransport`.
- `AgenticTurnRuntime.run` (`src/agentos/agentic/runtime.py:130-236`) já tem
  retry (`AgenticLimits.max_provider_retries`, default 1), mas repete a
  **mesma** key/transport — não existe troca de credencial nem de provider.
- A UI (`frontend/src/features/providers/ProviderDetail.tsx`) assume um único
  campo `apiKey: string` por provider (`frontend/src/api/providers.ts:23-27`).
- Cifra por valor já existe e é reutilizável como está:
  `ProviderSecretCipher` (`src/agentos/persistence/provider_secrets.py`),
  formato `enc:v1:<fernet-token>`.
- Existe um modelo de fallback *de modelo/provider* em
  `src/agentos/providers/models.py` (`FallbackMode`, `FallbackRequest`,
  `ModelResolver.resolve_fallback`), mas está desconectado do caminho de
  execução real — não é reaproveitado aqui; esta feature é fallback *de key*
  dentro do mesmo provider, implementada no caminho que de fato roda
  (`runtime.py` / `chat.py`).

## Decisões

| # | Decisão | Alternativa recusada |
|---|---|---|
| 1 | Nova tabela `provider_api_keys`, uma linha por key | Múltiplas linhas em `provider_configurations` (mistura config de provider com credencial) ou lista em JSON (perde granularidade de update/decrypt por key) |
| 2 | Ordem da lista = prioridade; primeira posição = key principal | Flag `is_primary` independente da ordem |
| 3 | Key com erro vai para cooldown temporário (tempo configurável por provider, default 60s) | Desativar key até ação manual do usuário |
| 4 | Fallback só troca de key antes do primeiro token da resposta | Fallback também no meio de um stream já iniciado |
| 5 | Só erros de autenticação (401/403), quota/rate limit (429) e rede/timeout disparam troca de key | Qualquer erro HTTP não-2xx dispara troca |
| 6 | Se todas as keys estiverem em cooldown, tenta a principal mesmo assim | Falhar localmente sem chamar o provider |
| 7 | Toda nova requisição recomeça pela key principal (não fica "grudada" na última que funcionou) | Round-robin entre as keys disponíveis |

## 1. Modelo de dados

Nova tabela (schema Postgres e espelho SQLite):

```
provider_api_keys
  id                  PK
  user_id             string(255), not null
  provider            string(32), not null
  label               string(255), nullable   -- apelido do usuário
  api_key_ciphertext  string(8192), not null  -- "enc:v1:...", via ProviderSecretCipher
  secret_ref          string(255), not null
  position            integer, not null       -- 0 = principal; ordem de fallback
  status              string(16), not null    -- 'active' | 'cooldown'
  cooldown_until      datetime(tz), nullable  -- null quando status='active'
  created_at          datetime(tz), not null
  updated_at          datetime(tz), not null

  UniqueConstraint(user_id, provider, position)
```

`provider_configurations` perde as colunas `api_key` e `api_key_ciphertext` e
ganha `key_cooldown_seconds` (integer, not null, default 60) — tempo de
cooldown configurável por provider.

**Migração de dados:** para cada linha existente em `provider_configurations`
com `api_key_ciphertext` (ou `api_key` legado) preenchido, cria uma linha
correspondente em `provider_api_keys` com `position=0`, `label=null`,
`status='active'`, reaproveitando o mesmo `secret_ref`. A leitura/reescrita de
credencial legada em texto plano (`chat.py:457-469`, "upgrading a
pre-encryption row in place") passa a operar sobre `provider_api_keys` em vez
de `provider_configurations`.

Status e `cooldown_until` ficam persistidos no banco, não em memória: o
cooldown sobrevive a reinícios do app e evita inconsistência entre chamadas.

## 2. Motor de fallback (backend)

Novo componente `ProviderKeyPool` (junto de
`src/agentos/persistence/postgres/provider_configuration.py`):

```python
def next_key(user_id, provider) -> ProviderApiKey | None:
    # keys com status='active' OR cooldown_until <= now(), ordenadas por position
    # se nenhuma disponível: retorna a de position=0 mesmo assim (decisão #6)

def mark_cooldown(key_id, cooldown_seconds) -> None:
    # status='cooldown', cooldown_until = now() + cooldown_seconds

def mark_active(key_id) -> None:
    # limpa cooldown quando a key volta a funcionar
```

`chat.py::_transport_for` deixa de buscar 1 credencial fixa e passa a montar
o transporte a partir da key retornada por `next_key` no início do turno
(decisão #7: sempre parte da principal).

Em `AgenticTurnRuntime.run` (`src/agentos/agentic/runtime.py:130-236`), ao
capturar uma falha classificada como erro de autenticação, rate limit ou
rede/timeout (reaproveitando a categorização já existente em
`_safe_error`/`StreamKind.ERROR`/`StreamKind.RATE_LIMIT`,
`provider_stream.py:118-145`) **antes de `StreamKind.CONTENT` ter sido
emitido nesse attempt**:

1. `mark_cooldown(key_atual.id, provider_config.key_cooldown_seconds)`
2. pede a próxima key ao pool (`next_key`, pulando as em cooldown)
3. reconstrói o `HTTPProviderStreamTransport` com a nova key
4. tenta de novo

O loop tenta no máximo uma vez por key disponível (bound natural = número de
keys cadastradas, sem risco de loop infinito). Se todas as tentativas
falharem, erro final igual ao `PROVIDER_RETRY_EXHAUSTED`/
`PROVIDER_STREAM_FAILED` de hoje.

Falha **depois** do primeiro token (`StreamKind.CONTENT` já emitido nesse
attempt) não troca de key — sobe o erro como já acontece hoje (decisão #4,
evita respostas parciais misturadas).

Erros não classificados como retryable (ex.: 400, content filter) continuam
subindo direto, sem consumir tentativa nem trocar de key (decisão #5).

Quando uma key usada com sucesso estava em `cooldown` (ex.: era a única
disponível, decisão #6, e funcionou), `mark_active` limpa o cooldown dela.

## 3. API / contratos (backend)

Substitui a configuração de key única por um sub-recurso:

```
GET    /v1/providers/{provider}/keys
       -> [{ id, label, position, status, cooldownUntil }]   -- nunca a key em claro

POST   /v1/providers/{provider}/keys
       body: { apiKey, label? }
       -> adiciona no final da lista (position = len atual)

PATCH  /v1/providers/{provider}/keys/{id}
       body: { label? }              -- renomear; trocar a key em si é remove+adiciona

DELETE /v1/providers/{provider}/keys/{id}
       -> remove e recompacta as positions restantes

PUT    /v1/providers/{provider}/keys/order
       body: { orderedIds: [id, id, id] }   -- primeiro = principal
```

`PUT /v1/providers/{provider}` (config existente) ganha o campo opcional
`keyCooldownSeconds`.

O fluxo atual de "colar uma key e salvar" continua funcionando: o backend cria
automaticamente a primeira linha em `provider_api_keys` — não quebra quem só
quer uma key.

Providers com key opcional (Ollama local, OmniRoute) continuam podendo operar
com zero keys cadastradas — comportamento inalterado.

Defesa em profundidade existente (`gateway.py:1486`, filtro de campos
`api_key`/`secret`/`token`/`password`/`credential`) se aplica também às
respostas do novo sub-recurso.

## 4. UI (frontend)

Em `ProviderDetail.tsx`, o campo único "Chave de API" vira lista ordenável:

- Cada item: apelido (editável inline), valor mascarado, badge de status —
  "Ativa" ou "Em cooldown até HH:MM:SS" (derivado de `cooldownUntil`) — e
  botão remover.
- Arrastar para reordenar; primeiro item tem selo "Principal".
- Botão "Adicionar chave" abre campo (apelido opcional + valor).
- Campo "Tempo de cooldown (s)" ligado a `keyCooldownSeconds` (default 60).

`useProviderState.ts` e `frontend/src/api/providers.ts` ganham
`listProviderKeys`, `addProviderKey`, `renameProviderKey`, `removeProviderKey`,
`reorderProviderKeys`, espelhando o sub-recurso `/keys`.

## 5. Fora de escopo

- Fallback entre **providers** diferentes (ex.: cair de Anthropic para
  OpenAI) — o modelo de fallback de modelo/provider já esboçado em
  `src/agentos/providers/models.py` fica como está, desconectado; não é
  acionado por esta feature.
- Round-robin ou balanceamento de carga entre keys — toda requisição nova
  recomeça pela principal (decisão #7).
- Notificação proativa ao usuário quando uma key entra em cooldown (a UI
  mostra o status ao abrir a tela, mas não há push/toast).

## 6. Testes

- Migração: dado um usuário com key legada em `provider_configurations`,
  confirmar que vira uma linha `position=0` em `provider_api_keys` e que o
  fluxo de "upgrade de credencial em texto plano" passa a operar na tabela
  nova.
- `ProviderKeyPool.next_key`: ordena por `position`, pula `cooldown` não
  vencido, cai para a principal quando todas estão em cooldown.
- `AgenticTurnRuntime`: erro 401/403/429/timeout antes do primeiro token troca
  de key e reconstrói o transporte; erro 400 não troca; erro depois do
  primeiro token não troca.
- API: CRUD completo do sub-recurso `/keys` (criar, renomear, remover,
  reordenar), incluindo que a key em claro nunca aparece em nenhuma resposta.
- UI: adicionar/remover/reordenar keys, exibição do badge de cooldown.
