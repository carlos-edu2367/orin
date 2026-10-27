# Especificação de Design: Geração de Mídia Cross-Provider e Correção dos Endpoints OpenRouter

**Data:** 2026-10-27  
**Escopo:** Architectural (`media_client`, `modalities`, `persistence`, `api/gateway`, `workers/chat`, `frontend/providers & settings`)

---

## 1. Contexto e Problemas Atuais

1. **Erro HTTP 404 ao gerar imagens no OpenRouter com modelos dedicados (`qwen/qwen-image-2.1-pro`, `flux`, `seedream`, etc.):**
   - Atualmente, `MediaGenerationClient.generate_image` (`src/agentos/agentic/media_client.py`) contém um desvio fixo:
     ```python
     if self.provider == "openrouter":
         return self._generate_image_via_chat(client, model=model, prompt=clean_prompt)
     ```
   - Isso funciona apenas para LLMs multimodais que geram imagem via `/chat/completions` (como `google/gemini-2.5-flash-image-preview`), mas falha com `HTTP 404: <model> is an image generation model and cannot be used with the chat/completions endpoint. Use the /api/v1/images endpoint instead.` em todos os modelos nativos de imagem do OpenRouter.
   - Além disso, o endpoint de vídeo do OpenRouter (`POST /api/v1/videos`) retorna status inicial `"pending"` e disponibiliza o MP4 final em `GET /api/v1/videos/{job_id}/content`, enquanto modelos de áudio ElevenLabs no OpenRouter (`elevenlabs/eleven-v3`, `elevenlabs/eleven-v4`) precisam de tratamento adequado de voz padrão (já que `"alloy"` é uma voz específica da OpenAI).

2. **Acoplamento forçado ao provider do turno (`SameProviderMediaModelPolicy`):**
   - Hoje, se o usuário está conversando com um modelo da **NVIDIA NIM** (ou Anthropic, Ollama, OpenCode), ele **não consegue** gerar imagens, áudios ou vídeos usando modelos do **OpenRouter** ou **OpenAI** no mesmo chat, pois `SameProviderMediaModelPolicy` filtra apenas favoritos do `turn["provider"]` e `_media_client_for_turn` usa exclusivamente a credencial do `turn["provider"]`.

---

## 2. Objetivos da Solução

1. **Corrigir e blindar a geração de Imagem, Áudio e Vídeo no `MediaGenerationClient`:**
   - Suporte nativo ao endpoint `POST {base_url}/images` do OpenRouter (com fallback bidirecional automático entre `/images`, `/images/generations` e `/chat/completions`).
   - Suporte a vozes compatíveis por família de modelo em `/audio/speech` (ex.: voz default `"george"` para modelos `elevenlabs/*` quando o agente passa o default `"alloy"`, com fallback automático em caso de erro 400/404 de voz).
   - Suporte completo ao ciclo assíncrono de vídeo do OpenRouter (`POST /videos`, status `"pending"`, polling em `/videos/{id}` e download em `/videos/{id}/content`, além do `/videos/generations` padrão OpenAI).
2. **Permitir definir Provider + Modelo Padrão independente para cada tipo de mídia (`image`, `audio`, `video`):**
   - O usuário pode escolher um modelo padrão para **Imagem** (ex.: `openrouter:qwen/qwen-image-2.1-pro`), **Áudio** (ex.: `openrouter:elevenlabs/eleven-v3`) e **Vídeo** (ex.: `openrouter:google/veo-3.1`).
   - O modelo escolhido como padrão fica **automaticamente autorizado** para uso pelo agente, mesmo que o usuário não o tenha marcado manualmente como favorito antes.
   - Além do modelo padrão, outros modelos favoritados em **qualquer provider habilitado** também ficam autorizados caso o usuário peça explicitamente outro modelo durante a conversa.
   - Na hora de gerar a mídia, o worker instancia o cliente HTTP usando **as credenciais, `base_url` e rotação de chaves (`MultiKey`) do provider dono daquele modelo de mídia**, independentemente de qual provider está conduzindo o chat.

---

## 3. Arquitetura Técnica

### 3.1. Persistência (`media_model_selections`)
Nova tabela em `src/agentos/persistence/postgres/schema.py`:
- `id`: `Integer`, PK
- `user_id`: `String(255)`, `nullable=False`
- `modality`: `String(32)`, `nullable=False` (`CheckConstraint("modality IN ('image', 'audio', 'video')")`)
- `provider`: `String(64)`, `nullable=False`
- `model_id`: `String(512)`, `nullable=False`
- `updated_at`: `DateTime(timezone=True)`, `nullable=False`
- `UniqueConstraint("user_id", "modality", name="uq_media_model_selections_user_modality")`

Store `PostgresMediaModelSettingsStore` em `src/agentos/reading/store.py` (ou `src/agentos/media/store.py`):
- `get_all(user_id: str) -> dict[str, MediaModelSelection]`
- `get(user_id: str, modality: str) -> MediaModelSelection | None`
- `set(user_id: str, modality: str, provider: str, model_id: str) -> None`
- `clear(user_id: str, modality: str) -> None`

### 3.2. Endpoints da API Gateway
- `GET /v1/settings/media-models`
  - Ação RBAC: `settings.vision_model.read` (mesmo escopo de leitura de preferências de modelo do usuário).
  - Resposta:
    ```json
    {
      "image": { "provider": "openrouter", "model_id": "qwen/qwen-image-2.1-pro", "mode": "manual" },
      "audio": { "provider": "openrouter", "model_id": "elevenlabs/eleven-v3", "mode": "manual" },
      "video": { "provider": null, "model_id": null, "mode": "automatic" }
    }
    ```
- `PUT /v1/settings/media-models/{modality}` (`modality` em `image | audio | video`)
  - Ação RBAC: `settings.vision_model.configure`.
  - Payload: `{"provider": "openrouter", "model_id": "qwen/qwen-image-2.1-pro"}` ou `{"provider": null, "model_id": null}` (modo `automatic`).
  - Valida que `provider` e `model_id` são ambos informados ou ambos `null`, e que `provider` pertence aos providers suportados.

### 3.3. Política Cross-Provider (`CrossProviderMediaModelPolicy`)
Em `src/agentos/provider_catalog/modalities.py`:
- Novo dataclass `MediaModelTarget(provider: str, model_id: str)` com formatação `f"{provider}:{model_id}"`.
- `SameProviderMediaModelPolicy` é estendido (mantendo compatibilidade retroativa com testes existentes) para suportar múltiplos providers habilitados e `defaults_by_modality: Mapping[str, MediaModelTarget]`:
  - **Ordem de prioridade por modalidade (`image`, `audio`, `video`):**
    1. **Default configurado pelo usuário** (`defaults_by_modality[modality]`), desde que o `provider` esteja habilitado para o usuário.
    2. **Favoritos do mesmo provider do turno** (`turn_provider`).
    3. **Favoritos de outros providers habilitados** (em ordem determinística).
  - `resolve_target(modality: str, requested_model: str | None = None) -> MediaModelTarget`:
    - Se `requested_model` não for passado: retorna o primeiro target da lista de prioridade (que será o default configurado pelo usuário, ou o primeiro favorito disponível no modo `Automático`).
    - Se `requested_model` for passado (ex.: `"openrouter:qwen/qwen-image-2.1-pro"`, `"qwen/qwen-image-2.1-pro"`, ou `"qwen-image-2.1-pro"`): resolve contra os targets permitidos (o default configurado + favoritos dos providers habilitados) e retorna `MediaModelTarget(provider, model_id)`.

### 3.4. Roteamento de Provider + Rotação de Chaves na Execução das Tools
Em `src/agentos/workers/chat.py` e `src/agentos/agentic/tools/orin/media.py`:
- `_media_policy_for_turn(turn)`:
  - Consulta todos os providers habilitados (`provider_configurations.c.enabled == True`) do `user_id`.
  - Consulta as seleções padrão do usuário em `media_model_selections` (filtrando para providers que estejam habilitados).
  - Consulta todos os modelos favoritados (`provider_model_favorites`) nos providers habilitados.
  - Constrói a política com `defaults_by_modality` + `favorite_models` de todos os providers habilitados.
- `_media_client_for_turn(turn)`:
  - Passa a fornecer um construtor parametrizado pelo `provider` alvo (`build(target_provider: str | None = None) -> MediaGenerationClient | None`).
  - Quando a tool (`generate_image`, `generate_audio`, `generate_video`) resolve `target = self._media_policy.resolve_target(modality, model)`, ela solicita o cliente para `target.provider`!
  - Suporte a rotação de chaves (`MultiKey`): se o `target.provider` possui múltiplas chaves ativas no pool e a requisição falha com erro de chave/crédito (HTTP 401/402/403 ou 429 de quota), marca a chave como `exhausted` ou `cooldown` no `PostgresProviderApiKeyAdapter` e tenta automaticamente a próxima chave disponível do pool.

### 3.5. Correções no `MediaGenerationClient` (`src/agentos/agentic/media_client.py`)
1. **`generate_image`:**
   - Para `openrouter`:
     - Se o modelo é um LLM multimodal de chat (ex.: contém `gemini` + `image`, `gpt-5-image`, `gpt-4o`), tenta primeiro `_generate_image_via_chat` (`POST {base_url}/chat/completions`); se receber 400/404/405, faz fallback automático para `_generate_image_via_openrouter_images` (`POST {base_url}/images`).
     - Para todos os demais modelos no OpenRouter (`qwen/qwen-image-2.1-pro`, `flux`, `seedream`, `ideogram`, `recraft`, etc.): chama primeiro `POST {base_url}/images` com `{"model": model, "prompt": clean_prompt, "aspect_ratio": ...}`; se receber 400/404/405, faz fallback automático para `_generate_image_via_chat`.
   - Respeita `media_type` (`image/png` -> `png`, `image/jpeg` -> `jpg`, `image/webp` -> `webp`, `image/svg+xml` -> `svg`) retornado em `data[0]`.
2. **`generate_audio`:**
   - Detecta modelos ElevenLabs (`elevenlabs/` ou `eleven-` no `model_id`). Se `voice` for `"alloy"` (default genérico da tool) ou vazio, substitui automaticamente por `"george"`.
   - Se `POST {base_url}/audio/speech` retornar 400/404/422 e a voz enviada não for `"george"` (para ElevenLabs) ou `"alloy"`, faz uma tentativa de fallback antes de lançar `MediaGenerationError`.
3. **`generate_video`:**
   - No `openrouter`: tenta `POST {base_url}/videos` (com fallback para `/videos/generations` se 404/405).
   - Reconhece `"pending"` além de `{"queued", "processing", "running", "in_progress"}` como status de job em andamento.
   - Faz polling em `GET {base_url}/videos/{job_id}` (ou `/videos/generations/{job_id}`). Se `status in {"failed", "error", "cancelled", "canceled"}`, aborta imediatamente com o erro detalhado.
   - Quando `status in {"completed", "succeeded"}`: se o payload já contiver `data`/`url`/`b64_json`, extrai diretamente; caso contrário (padrão OpenRouter `/api/v1/videos`), baixa o vídeo binário em `GET {base_url}/videos/{job_id}/content`.

---

## 4. Design de UX (`MediaModelsSetting.tsx`)

1. **Superfície e Localização:**
   - Novo componente `MediaModelsSetting` (`frontend/src/features/providers/MediaModelsSetting.tsx`).
   - Exibido em **Providers / Integrações** (`ProvidersSection.tsx` e `ProviderSettingsPage.tsx`) logo abaixo de `Leitura visual`.
   - Nova entrada no menu lateral de Configurações (`sections.ts`) dentro do grupo **Inteligência**:
     - `Providers` (`/settings/providers`)
     - `Leitura visual` (`/settings/vision`)
     - `Geração de mídia` (`/settings/media`)
2. **Interação e Hierarquia Visual (seguindo `DESIGN.md`):**
   - Painel com cabeçalho claro (**"Geração de mídia"**) explicando que os modelos escolhidos aqui são usados por padrão em qualquer conversa (mesmo quando o chat usa outro provider, como NVIDIA ou Anthropic).
   - 3 cartões de modalidade (**Imagem**, **Áudio e voz**, **Vídeo**):
     - Cada cartão possui:
       - Cabeçalho com título da modalidade + badge indicando o estado atual (`Automático` ou `<Provider> · <Modelo>`).
       - Campo de filtro rápido por texto (`Buscar modelo de imagem...`, etc.) para encontrar facilmente modelos como `qwen 2.1` ou `eleven v3` em catálogos grandes.
       - `<select>` acessível com:
         - Opção `Automático (usar favoritos dos providers ativos)`
         - Grupo `Favoritos` (modelos já favoritados daquela modalidade em qualquer provider habilitado)
         - Grupo `Todos os modelos compatíveis` (demais modelos daquela modalidade nos providers habilitados — selecionar qualquer um já salva e autoriza automaticamente).
       - Feedback imediato de salvamento/erro com `aria-live="polite"`.
