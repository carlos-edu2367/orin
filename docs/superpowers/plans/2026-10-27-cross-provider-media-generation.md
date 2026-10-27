# Plano de Implementação: Geração de Mídia Cross-Provider e Correção OpenRouter

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir os endpoints de geração de imagem (`/api/v1/images` + fallback `/chat/completions`), áudio (`elevenlabs/*` vozes compatíveis) e vídeo (`/api/v1/videos` assíncrono) no OpenRouter, e implementar seleção de Provider + Modelo padrão por modalidade (`image`, `audio`, `video`) com execução cross-provider e rotação de chaves (`MultiKey`).

**Architecture:**
1. **`MediaGenerationClient` (`src/agentos/agentic/media_client.py`):** Suporte nativo ao `POST /api/v1/images` do OpenRouter (com fallback bidirecional para `/chat/completions`), vozes compatíveis por família em `/audio/speech` (`elevenlabs/*` -> `"george"`), suporte ao ciclo assíncrono `POST /api/v1/videos` -> status `"pending"` -> `GET /api/v1/videos/{id}/content`, e rotação de chaves via callback/pool em falhas 401/402/403/429.
2. **Persistência e Gateway (`media_model_selections`, `PostgresMediaModelSettingsStore`, `/v1/settings/media-models`):** Tabela e store duráveis para salvar o provider + modelo padrão de `image`, `audio` e `video` por usuário, expostos em `GET /v1/settings/media-models` e `PUT /v1/settings/media-models/{modality}`.
3. **Política Cross-Provider e Worker (`modalities.py`, `orin/media.py`, `workers/chat.py`):** `MediaModelTarget(provider, model_id)` e resolução priorizando: (1) modelo padrão configurado para a modalidade (autorizado automaticamente), (2) favoritos do provider ativo, (3) favoritos de qualquer outro provider habilitado. O worker instancia `MediaGenerationClient` com as credenciais e pool de chaves do provider dono do modelo resolvido.
4. **Frontend UX (`MediaModelsSetting.tsx`, `ProvidersSection.tsx`, `sections.ts`, `routes.tsx`):** Painel **"Geração de mídia"** com 3 seletores independentes (**Imagem**, **Áudio e voz**, **Vídeo**), busca instantânea por nome/ID e agrupamento de Favoritos + Todos os modelos compatíveis dos providers ativos.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy / Alembic, httpx, React 19, TypeScript, Vitest, Playwright.

---

### Task 1: Corrigir `MediaGenerationClient` para OpenRouter (`/images`, `/audio/speech` ElevenLabs e `/videos`) + Inferência de Modalidades

**Files:**
- Modify: `src/agentos/provider_catalog/modalities.py`
- Modify: `src/agentos/agentic/media_client.py`
- Modify: `tests/unit/agentic/test_media_generation_tools.py`

- [x] **Step 1 (RED): Escrever testes unitários falhando para:**
  - `qwen/qwen-image-2.1-pro` no OpenRouter chamando `POST /api/v1/images` e retornando a imagem (`b64_json` + `media_type`).
  - Fallback bidirecional no OpenRouter: se `/images` retornar 404 para `google/gemini-2.5-flash-image-preview`, faz fallback para `/chat/completions`; se `/chat/completions` retornar 404 (`cannot be used with the chat/completions endpoint. Use the /api/v1/images endpoint instead`), faz fallback para `/images`.
  - `elevenlabs/eleven-v3` no `/audio/speech` convertendo `voice="alloy"` para `"george"` (e fazendo retry se uma voz customizada der 400/404).
  - Vídeo no OpenRouter (`POST /api/v1/videos` -> `status: "pending"` -> `GET /api/v1/videos/{id}` -> `status: "completed"` -> `GET /api/v1/videos/{id}/content`).
  - `infer_output_modalities` reconhecendo `qwen/qwen-image-2.1-pro`, `bytedance-seed/seedream-4.5`, `elevenlabs/eleven-v3`, `elevenlabs/eleven-v4-turbo`.
- [x] **Step 2 (GREEN): Implementar em `src/agentos/provider_catalog/modalities.py` e `src/agentos/agentic/media_client.py`:**
  - Atualizar `_IMAGE_MODEL_RE`, `_AUDIO_MODEL_RE`, `_VIDEO_MODEL_RE` em `modalities.py`.
  - Em `MediaGenerationClient.generate_image`: implementar `_generate_image_via_openrouter_images` (`POST {base_url}/images`) com mapeamento de `size` para `aspect_ratio` e fallback bidirecional com `_generate_image_via_chat`.
  - Em `MediaGenerationClient.generate_audio`: detectar modelos `elevenlabs` / `eleven-` e usar `"george"` quando `voice` é `"alloy"` ou vazio, além de fallback de voz em erro 400/404/422.
  - Em `MediaGenerationClient.generate_video`: suportar `POST {base_url}/videos` no OpenRouter, status `"pending"`, aborto imediato em `"failed"`/`"error"` e download em `GET {base_url}/videos/{job_id}/content`.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/agentic/test_media_generation_tools.py`.**

---

### Task 2: Persistência (`media_model_selections`), Migration `0053` e Endpoints `/v1/settings/media-models`

**Files:**
- Modify: `src/agentos/persistence/postgres/schema.py`
- Create: `src/agentos/persistence/postgres/migrations/versions/0053_media_model_selections.py`
- Modify: `src/agentos/reading/store.py`
- Modify: `src/agentos/api/contracts.py`
- Modify: `src/agentos/api/gateway.py`
- Modify: `src/agentos/bootstrap/production.py`
- Modify: `tests/unit/persistence/test_postgres_schema.py`
- Modify: `tests/unit/persistence/test_w3_schema.py`
- Modify: `tests/integration/api/test_profile_isolation.py`
- Create: `tests/unit/api/test_media_model_routes.py`

- [x] **Step 1 (RED): Escrever testes em `tests/unit/api/test_media_model_routes.py` e atualizar `test_postgres_schema.py`, `test_w3_schema.py` e `test_profile_isolation.py`:**
  - `GET /v1/settings/media-models` retorna `image`, `audio`, `video` em modo `"automatic"` inicialmente.
  - `PUT /v1/settings/media-models/image` salva `{"provider": "openrouter", "model_id": "qwen/qwen-image-2.1-pro"}` e retorna `mode: "manual"`.
  - `PUT /v1/settings/media-models/audio` salva `{"provider": "openrouter", "model_id": "elevenlabs/eleven-v3"}`.
  - `PUT /v1/settings/media-models/image` com `{"provider": None, "model_id": None}` limpa a seleção e volta para `mode: "automatic"`.
  - Validações adversárias: modalidade inválida (`404` ou `422`), `provider` presente sem `model_id` (`422`), `provider` desconhecido (`422`).
- [x] **Step 2 (GREEN): Implementar tabela, migration, store e rotas:**
  - Adicionar `media_model_selections` em `schema.py` e criar migration `0053_media_model_selections.py`.
  - Adicionar `MediaModelSelection` e `PostgresMediaModelSettingsStore` em `src/agentos/reading/store.py`.
  - Injetar `media_model_settings` em `ApiServices` (`gateway.py`) e `production.py`.
  - Criar rotas `GET /v1/settings/media-models` e `PUT /v1/settings/media-models/{modality}`.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/api/test_media_model_routes.py tests/unit/persistence/test_postgres_schema.py tests/unit/persistence/test_w3_schema.py tests/integration/api/test_profile_isolation.py`.**

---

### Task 3: Política Cross-Provider (`MediaModelTarget`), Roteamento de Provider nas Tools e Rotação de Chaves (`MultiKey`)

**Files:**
- Modify: `src/agentos/provider_catalog/modalities.py`
- Modify: `src/agentos/agentic/tools/orin/media.py`
- Modify: `src/agentos/workers/chat.py`
- Modify: `tests/unit/agentic/test_media_generation_tools.py`
- Modify: `tests/unit/workers/test_chat_provider_transport.py`

- [x] **Step 1 (RED): Escrever testes unitários falhando para:**
  - Conversa ativa em `nvidia` (`meta/llama-3.1-70b-instruct`), com modelo padrão de imagem configurado como `openrouter:qwen/qwen-image-2.1-pro` (mesmo sem estar nos favoritos!) e modelo de áudio `openrouter:elevenlabs/eleven-v3`:
    - As tools `generate_image` e `generate_audio` são publicadas para o agente.
    - Quando `generate_image(prompt="...")` é chamada sem especificar `model`, resolve `MediaModelTarget(provider="openrouter", model_id="qwen/qwen-image-2.1-pro")` e usa a credencial/base_url do `openrouter`.
    - Quando o agente passa explicitamente outro favorito de qualquer provider habilitado (ex.: `openai:gpt-image-1` ou `gpt-image-1`), resolve `(provider="openai", model_id="gpt-image-1")` e usa a credencial da `openai`.
  - Rotação de chaves no provider de mídia: se a primeira chave do `openrouter` retornar HTTP 402 (sem créditos), marca a chave como `exhausted` e repete a geração com a segunda chave ativa do `openrouter`.
- [x] **Step 2 (GREEN): Implementar resolução cross-provider e cliente multi-key:**
  - Adicionar `MediaModelTarget` e suporte a `defaults_by_modality` + múltiplos providers habilitados em `SameProviderMediaModelPolicy` (`modalities.py`), com método `resolve_target(modality, requested_model=None) -> MediaModelTarget`.
  - Atualizar `MediaTools` (`src/agentos/agentic/tools/orin/media.py`) para resolver `target = self._media_policy.resolve_target(...)`, obter o cliente para `target.provider` via `self._resolve_media_client(target.provider)` e gravar `"provider": target.provider` no payload da tool.
  - Atualizar `ChatWorker._media_policy_for_turn` e `ChatWorker._media_client_for_turn` em `src/agentos/workers/chat.py` para carregar providers habilitados, `media_model_selections` e todos os favoritos de providers habilitados, além de rotacionar chaves do `PostgresProviderApiKeyAdapter` do `target.provider` em falhas 401/402/403/429.
- [x] **Step 3 (VERIFY): Rodar `pytest tests/unit/agentic/test_media_generation_tools.py tests/unit/workers/test_chat_provider_transport.py`.**

---

### Task 4: Frontend UX (`MediaModelsSetting.tsx`, Integrações & Rota `/settings/media`)

**Files:**
- Modify: `frontend/src/api/providers.ts`
- Create: `frontend/src/features/providers/MediaModelsSetting.tsx`
- Modify: `frontend/src/features/providers/ProvidersSection.tsx`
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx`
- Modify: `frontend/src/features/settings/sections.ts`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/styles/index.css`
- Create: `frontend/tests/unit/MediaModelsSetting.test.tsx`
- Modify: `frontend/tests/e2e/provider-settings.spec.ts`

- [x] **Step 1 (RED): Escrever testes unitários (`frontend/tests/unit/MediaModelsSetting.test.tsx`) e E2E (`frontend/tests/e2e/provider-settings.spec.ts`):**
  - Renderiza os 3 seletores (**Imagem**, **Áudio e voz**, **Vídeo**), listando modelos compatíveis de todos os providers com **Favoritos** no topo.
  - Permite filtrar modelos por texto (ex.: buscar `"qwen"` ou `"eleven"`) e selecionar `qwen/qwen-image-2.1-pro (OpenRouter)` para Imagem e `elevenlabs/eleven-v3 (OpenRouter)` para Áudio, enviando `PUT /v1/settings/media-models/image` e `PUT /v1/settings/media-models/audio`.
  - Permite voltar qualquer modalidade para `Automático`.
- [x] **Step 2 (GREEN): Implementar API client, componente `MediaModelsSetting.tsx`, rotas e estilos:**
  - Adicionar `getMediaModelSettings` e `setMediaModelSetting` em `frontend/src/api/providers.ts`.
  - Criar `MediaModelsSetting.tsx` com busca por modalidade, agrupamento `Favoritos` / `Todos os modelos compatíveis` e estados de carregamento/erro acessíveis.
  - Incluir `<MediaModelsSetting />` em `ProvidersSection.tsx`, `ProviderSettingsPage.tsx` e criar rota `/settings/media` em `sections.ts` e `routes.tsx`.
- [x] **Step 3 (VERIFY): Rodar testes unitários (`npm test` em `frontend`), detector `impeccable detect --json` e validação visual/E2E no navegador via Playwright.**
