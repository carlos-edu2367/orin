# Provider NVIDIA NIM e Auto-Rotação Inteligente de API Keys — Especificação de Design

**Data:** 2026-10-25  
**Estado:** Aguardando aprovação do Carlos  
**Escopo:** Arquitetural / Multi-componente (`superpowers:brainstorming`)

---

## 1. Objetivo

Adicionar o provider **NVIDIA (`nvidia`)** ao Orin, suportando tanto a **NVIDIA NIM Cloud** (`https://integrate.api.nvidia.com/v1`, onde operam as chaves `nvapi-...` gratuitas de desenvolvedor/trial do `build.nvidia.com` e as chaves pagas/enterprise) quanto instâncias **Self-Hosted NIM** (`http://localhost:8000/v1` ou endpoint corporativo customizado).

Em conjunto, evoluir o motor de pool e auto-rotação de API keys do Orin para:
1. **Diferenciar falhas temporárias (`cooldown`) de esgotamento de créditos/uso (`exhausted`)**:
   - Rate limit por minuto (`HTTP 429` sem erro de saldo zerado) coloca a chave em `cooldown` temporário (respeitando `Retry-After` ou `key_cooldown_seconds`) e rotaciona imediatamente para a próxima chave.
   - Esgotamento de tokens/créditos ("out of usage" / `HTTP 402 Payment Required` / `insufficient_quota` / `credits exhausted` / `401`/`403` de chave expirada ou sem saldo) marca a chave como `exhausted`, rotacionando imediatamente para a próxima chave (ex.: Chave Grátis 1 $\rightarrow$ Chave Grátis 2 $\rightarrow$ Chave Paga) e evitando que requisições futuras percam tempo tentando chaves já zeradas.
   - Chaves em `cooldown` ou `exhausted` podem ser reativadas manualmente na UI (botão **"Reativar"**) ou automaticamente caso sejam usadas como último recurso e voltem a responder com sucesso.
2. **Estender a auto-rotação para todo o ecossistema**:
   - Stream principal de chat/code (`ChatWorker._provider_transport`)
   - Subagentes e leitura visual (`ChatWorker._transport_for`)
   - Geração de mídia (`MediaGenerationClient`)
   - Atualização de catálogo de modelos (`ProviderModelCatalogService.refresh`)

---

## 2. Abordagens Consideradas

| Abordagem | Descrição | Trade-offs | Decisão |
|---|---|---|---|
| **A. Unificada (Recomendada)** | Criar o módulo `agentos.provider_catalog.nvidia` seguindo o padrão dual-mode (Cloud + Self-Hosted) de `ollama`/`opencode`, e evoluir o `PostgresProviderApiKeyAdapter` + `MultiKeyProviderStreamTransport` com o estado `'exhausted'`, detecção de `HTTP 402` / payload de quota + erro inicial SSE, e rotação transversal (chat, subagentes, visão, catálogo). | Reutiliza a tabela `provider_api_keys` existente sem necessidade de migração destrutiva de schema (`status` já é `String(16)`). Beneficia todos os providers além da NVIDIA. | **Escolhida** |
| **B. Provider NVIDIA isolado** | Implementar lógica de múltiplas keys e detecção de `out of usage` apenas dentro de um cliente específico da NVIDIA. | Duplicaria lógica de criptografia, persistência e fallback que já existe em `provider_api_keys` e `MultiKeyProviderStreamTransport`. | Descartada |
| **C. Cooldown único sem estado `exhausted`** | Tratar `402 Payment Required` / falta de créditos com o mesmo `key_cooldown_seconds` (60s) do rate limit `429`. | A cada 60 segundos o Orin voltaria a tentar a key gratuita sem saldo na primeira tentativa de cada mensagem, adicionando latência desnecessária. | Descartada |

---

## 3. Arquitetura e Componentes

### 3.1. Borda do Provider NVIDIA (`src/agentos/provider_catalog/nvidia.py`)
- **Constantes e Normalização de URL**:
  - `NVIDIA_CLOUD_BASE_URL = "https://integrate.api.nvidia.com/v1"`
  - `DEFAULT_NVIDIA_LOCAL_BASE_URL = "http://localhost:8000/v1"`
  - `DEFAULT_NVIDIA_BASE_URL = NVIDIA_CLOUD_BASE_URL`
  - `normalize_nvidia_base_url(value: str) -> str`:
    - Valida scheme (`http` ou `https`) e `netloc` não vazio.
    - Rejeita URLs com `username`, `password`, `query` ou `fragment` (proteção contra credential injection e SSRF).
    - Se o host pertencer ao domínio `nvidia.com` (`integrate.api.nvidia.com`, `*.api.nvidia.com`, `build.nvidia.com`), exige obrigatoriamente `https://`.
    - Remove sufixos acidentais (`/models`, `/chat/completions`) e garante que o caminho base aponte para `/v1` quando nenhum caminho específico for fornecido.
  - `is_nvidia_cloud(base_url: str) -> bool`:
    - Retorna `True` quando o hostname normalizado é `nvidia.com` ou termina com `.nvidia.com` (ou quando nenhuma `base_url` customizada foi definida).
- **Cliente de Catálogo (`NvidiaModelCatalogClient`)**:
  - Consulta `GET {base_url}/models` com `Authorization: Bearer {api_key}` (quando `api_key` estiver presente; obrigatória no modo Cloud, opcional no modo Self-Hosted).
  - Em caso de `HTTP 401`, `402`, `403` ou `429`, lança `NvidiaAuthenticationError` (com metadados de `status_code` e se é `exhausted`) para permitir tanto diagnóstico claro no `test_connection` quanto rotação de key no `refresh`.
- **Normalização de Modelos (`_normalize_nvidia` em `service.py`)**:
  - Lê `id`, `name`/`display_name`, `context_length` ou `max_model_len` (padrão vLLM/NIM).
  - Infere `input_modalities`: `("text", "image")` para modelos VLM/vision da NVIDIA (ex.: famílias contendo `vision`, `vlm`, `vl`, `neva`, `cosmos`, `paligemma`, `pixtral`, `gemma-3`) ou quando `input_modalities` explícito incluir `"image"`.
  - Infere `capabilities`: adiciona `"tools"` e, para modelos de raciocínio (`deepseek-r1`, `qwq`, `nemotron` reasoning, etc.), `"reasoning"`.

### 3.2. Detecção de "Out of Usage" vs Rate Limit (`src/agentos/agentic/provider_stream.py`)
- **`ProviderKeyRejected` enriquecido**:
  ```python
  class ProviderKeyRejected(Exception):
      status_code: int | None = None
      retry_after_seconds: float | None = None
      exhausted: bool = False
  ```
- **Classificação HTTP e de Payload antes do 1º token**:
  - `HTTP 402 Payment Required`: sempre `ProviderKeyRejected(exhausted=True)`.
  - `HTTP 401 Unauthorized` / `HTTP 403 Forbidden`: sempre `ProviderKeyRejected(exhausted=True)` (credencial inválida, revogada ou sem permissão/créditos).
  - `HTTP 429 Too Many Requests`:
    - Inspeciona o corpo da resposta (de forma segura e limitada): se contiver indicadores de quota/saldo esgotado (`insufficient_quota`, `billing_hard_limit_reached`, `credits exhausted`, `out of usage`, `exceeded your current quota`, `not enough credits`, `balance`), classifica como `exhausted=True`.
    - Caso contrário (rate limit de RPM/TPM, ex.: 40 RPM do free tier da NVIDIA), classifica como `exhausted=False` e extrai `retry_after_seconds` do header `Retry-After` / `x-ratelimit-reset`.
  - `HTTP 400` / `HTTP 422`:
    - Continua não rotacionando para erros de validação de request comum, **exceto** quando a mensagem de erro do provider indicar explicitamente esgotamento de créditos/saldo/quota da conta (`credit balance is too low`, `insufficient_quota`, `out of credits`, `out of usage`), caso em que levanta `ProviderKeyRejected(exhausted=True)`.
  - **Primeiro evento SSE com erro de quota/rate-limit**:
    - Em `HTTPProviderStreamTransport.stream`, se o provider responder `HTTP 200` mas o primeiro item emitido por `normalize_sse` (antes de qualquer `TEXT`, `THINKING` ou `TOOL_CALL`) for `StreamKind.ERROR` com categoria `RATE_LIMITED`, `QUOTA_EXCEEDED` ou `AUTHENTICATION`, converte em `ProviderKeyRejected` para acionar a rotação de key antes que qualquer token chegue ao usuário.

### 3.3. Pool de Keys e Persistência (`src/agentos/persistence/postgres/provider_api_keys.py`)
- **Novos métodos no `PostgresProviderApiKeyAdapter`**:
  - `mark_exhausted(key_id: int) -> None`: define `status = "exhausted"`, `cooldown_until = None`, `updated_at = now`.
  - `activate_key(command: dict[str, object]) -> dict[str, object]`: valida escopo `(user_id, provider, key_id)` e restaura `status = "active"`, `cooldown_until = None`, `updated_at = now`.
- **Ordem de seleção em `next_available_key(user_id, provider, *, exclude=frozenset())`**:
  1. Filtra keys do `(user_id, provider)` ordenadas por `position` cujo `id` não esteja em `exclude`.
  2. **Tier 1 (Disponíveis)**: keys com `status == "active"` ou (`status == "cooldown"` e `cooldown_until <= now`).
  3. **Tier 2 (Em Cooldown temporário)**: keys com `status == "cooldown"` e `cooldown_until > now`.
  4. **Tier 3 (Exhausted / Último recurso)**: keys com `status == "exhausted"`.
  - Retorna a primeira key do primeiro tier não-vazio. Se essa key completar a operação com sucesso, `mark_active(key.id)` é chamado automaticamente.
- **Proteção ao salvar configuração (`PostgresProviderConfigurationAdapter.configure`)**:
  - Se `api_key` vier vazia em `configure()`, mas o provider já possuir keys salvas em `provider_api_keys` (adicionadas via lista de keys), o adapter **não** apaga a key `position=0` e **não** falha exigindo key nova — apenas atualiza `enabled` e `base_url`.

### 3.4. Auto-Rotação em Todo o Ecossistema
1. **`MultiKeyProviderStreamTransport` (`src/agentos/agentic/provider_key_fallback.py`)**:
   - Ao capturar `ProviderKeyRejected` antes do 1º token:
     - Se `rejected.exhausted` for `True`: chama `self._key_pool.mark_exhausted(credential.id)`.
     - Se `rejected.exhausted` for `False`: calcula `cooldown = max(1, min(3600, int(math.ceil(rejected.retry_after_seconds))))` se `retry_after_seconds` estiver presente, senão usa `self._cooldown_seconds`, e chama `self._key_pool.mark_cooldown(credential.id, cooldown)`.
     - Busca `next_available_key(..., exclude=frozenset(tried))` e tenta a próxima key imediatamente.
2. **Subagentes e Leitura Visual (`ChatWorker._transport_for` em `src/agentos/workers/chat.py`)**:
   - Passa a envolver o `HTTPProviderStreamTransport` em `MultiKeyProviderStreamTransport` quando houver keys no pool (em vez de usar apenas uma key estática).
3. **Refresh do Catálogo (`ProviderModelCatalogService.refresh` e `PostgresProviderCatalogRepository`)**:
   - O repositório expõe um iterador/método de tentativa com rotação (`fetch_with_key_rotation` ou lista de credenciais do pool + callbacks `mark_cooldown`/`mark_exhausted`/`mark_active`), de modo que `refresh()` tente as keys disponíveis em ordem caso a principal esteja sem créditos ou em rate limit.

### 3.5. API e Frontend UI
- **Novos endpoints no `gateway.py`**:
  - `POST /v1/providers/nvidia/test`: testa conexão com a NVIDIA Cloud ou Self-Hosted NIM (usando a `api_key` enviada ou, se vazia e já configurada, a próxima key disponível do pool).
  - `POST /v1/providers/{provider}/keys/{key_id}:activate`: reativa manualmente uma key em `cooldown` ou `exhausted`.
- **Frontend (`frontend/src/`)**:
  - `PROVIDER_NAMES`: `['openai', 'anthropic', 'openrouter', 'nvidia', 'opencode', 'omniroute', 'ollama']`.
  - `NvidiaSetup.tsx`:
    - Modo **NVIDIA Cloud (`integrate.api.nvidia.com`)** vs **Self-Hosted NIM (`localhost:8000`)**.
    - Explicação direta sobre como combinar múltiplas API keys (`nvapi-...` gratuitas e pagas) com fallback automático.
    - Botões de **Testar conexão**, **Salvar** e **Revogar acesso**.
  - `ProviderKeyList.tsx`:
    - Exibe status `"Sem créditos / Esgotada"` quando `status === 'exhausted'` e `"Em cooldown até ..."` quando `status === 'cooldown'`.
    - Exibe botão **"Reativar"** ao lado de chaves em `cooldown` ou `exhausted`.

---

## 4. Segurança, Tratamento de Erros e Postura Adversarial

1. **Sanitização de Credenciais**:
   - Nenhuma chave `nvapi-...` é exposta em respostas HTTP, logs ou mensagens de erro (`_provider_public`, `_provider_key_public` e `ProviderError` continuam filtrando qualquer token sensível).
2. **Prevenção de SSRF e Vazamento de Key**:
   - `normalize_nvidia_base_url` exige `https://` para qualquer host `*.nvidia.com`, impedindo que uma chave `nvapi-...` da nuvem seja transmitida em texto claro via `http://`.
   - Proíbe credenciais na URL (`user:pass@host`), query strings e fragmentos.
3. **Limites de Cooldown e Loop**:
   - `MultiKeyProviderStreamTransport` mantém o conjunto `tried: set[int]` por tentativa de stream: cada key cadastrada é tentada no máximo **uma vez** por chamada, garantindo término finito mesmo se todas as keys estiverem esgotadas.
   - `retry_after_seconds` vindo de header externo é limitado ao intervalo `[1, 3600]` segundos, impedindo que um header malformado ou malicioso trave a key por anos ou gere `timedelta` inválido.
4. **Integridade de Stream Parcial**:
   - Se o stream já emitiu qualquer token ao usuário (`yielded == True`) e cair no meio, a rotação **não** é disparada no meio da frase para não concatenar respostas de dois modelos/chamadas diferentes.

---

## 5. Estratégia de Testes

- **Unitários (Backend)**:
  - `tests/unit/provider_catalog/test_nvidia.py`: normalização de URL (Cloud vs Self-Hosted, rejeição de HTTP para `nvidia.com`, rejeição de credenciais na URL), `NvidiaModelCatalogClient`, normalização de modelos VLM/reasoning/context window.
  - `tests/unit/agentic/test_provider_key_fallback.py`: diferenciação entre `429` (`mark_cooldown` + respeito ao `Retry-After`) e `402`/`401`/erro de saldo (`mark_exhausted`), erro no 1º evento SSE, prioridade de seleção (`active` -> `cooldown` -> `exhausted`).
  - `tests/unit/persistence/test_provider_api_keys.py`: `mark_exhausted`, `activate_key`, prioridade em `next_available_key`, preservação de keys ao atualizar config do provider.
  - `tests/unit/api/test_nvidia_provider_api.py`: endpoints `/v1/providers/nvidia`, `/v1/providers/nvidia/test`, `/v1/providers/nvidia/keys/{id}:activate`.
- **Unitários e E2E Visual (Frontend)**:
  - Testes Vitest para `NvidiaSetup`, `ProviderKeyList` (status `exhausted` e botão `Reativar`) e `providers.ts`.
  - Verificação visual real no navegador via Playwright (desktop e mobile, estados não configurado, Cloud, Self-Hosted, lista de keys com key ativa, em cooldown e esgotada).
