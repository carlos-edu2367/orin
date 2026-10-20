# Design Spec: OpenCode Local Bridge (Harness Compartilhado) & Correção de Duplicação de Respostas na UI

**Data:** 2026-10-20  
**Escopo:** `Architectural` (Bridge OpenCode + Harness Orin) + `Bounded` (Correção de duplicação de respostas de perguntas na UI e limpeza do repositório)

---

## 1. Contexto e Problema

### 1.1. Duplicação de resposta às perguntas do agente na UI (fixa no final do chat)
Ao responder um card `UserQuestionCard` (`ask_user`), a resposta do usuário aparece duplicada no chat e uma das cópias permanece fixa no final da thread mesmo após o agente iniciar o turno seguinte:
1. Em `ChatPage.tsx` (`submitQuestionAnswers`), o texto de `answer.note` e o `response` final não passam por `.trim()`, enquanto o backend (`PostgresChatStore.create`) aplica `message = message.strip()` antes de persistir em `conversation_messages`.
2. Quando há espaço ou quebra de linha ao final da observação (ex.: pressionar `Enter` no `<textarea>`), `pendingUserMessage.content` (não-normalizado) diverge de `item.content` (normalizado no banco).
3. Na projeção `messages` (`useMemo` em `ChatPage.tsx`), a verificação `alreadyDurable` faz comparação estrita (`item.content === pendingUserMessage.content`), avaliando como `false`, e `setPendingUserMessage(null)` nunca é chamado após `await loadSnapshot(false)`.
4. Como consequência, `messages` retorna `[...base, pendingUserMessage]`: a mensagem persistida aparece na posição correta de `base` e o `pendingUserMessage` fica preso eternamente na última posição do array (`fixo no final do chat`), abaixo até do novo turno do assistente.
5. Adicionalmente, `UserQuestionCard.tsx` não mantém estado interno de submissão (`isSubmitting`) e `ActivityStream.tsx` não repassa `submitting`, permitindo duplo clique acidental enquanto a requisição está em voo.

### 1.2. OpenCode Free Tier Bridge compartilhando o Harness do Orin
Modelos gratuitos do OpenCode (`big-pickle`, `mimo-v2.6-flash-free` e demais `*-free`) retornam `403 FreeTierError ("OpenCode's free tier can only be used from within OpenCode")` quando chamados fora do cliente oficial.
Por outro lado, o próprio binário local do OpenCode (`opencode serve`) publica uma API HTTP oficial documentada (`GET /config/providers`, `POST /session`, `POST /session/{sessionID}/message`) que executa legitimamente o OpenCode local e responde em `big-pickle` e `mimo-v2.6-flash-free`.

Restrições técnicas verificadas do `opencode serve`:
- Não expõe rota `/v1/chat/completions` crua; a conversa ocorre via `POST /session` e `POST /session/{id}/message`.
- Se todas as tools internas do OpenCode forem desabilitadas (`read: false`), o próprio `opencode serve` recebe `403 FreeTierError` do `opencode.ai`. Porém, mantendo **apenas** `read: true` e desabilitando todas as demais tools internas (`bash: false`, `edit: false`, `write: false`, `glob: false`, `grep: false`, `task: false`, `webfetch: false`, `websearch: false`, `todowrite: false`, `skill: false`, `question: false`, `apply_patch: false`), o `opencode serve` passa no gate do tier gratuito sem poder executar comandos ou alterar arquivos por fora do Orin.
- Para compartilhar o **harness do Orin** (`AgentRuntime`, tools nativas do Orin como `read_file`, `edit_file`, `write_file`, `run_command`, `ask_user`, `run_subagent`, sistema de permissões, checkpoints e timeline da UI), a Bridge deve delegar apenas a inferência ao `opencode serve` e traduzir chamadas de ferramentas para o protocolo `NormalizedStreamItem` (`TEXT_DELTA`, `TOOL_CALL`, `USAGE`, `FINISH`) do Orin.

---

## 2. Arquitetura Proposta

### 2.1. Correção da UI (`ChatPage.tsx` e `UserQuestionCard.tsx`)
1. **Normalização no envio (`ChatPage.tsx`):**
   - Em `submitQuestionAnswers`, aplicar `.trim()` em `answer.note` e no `response` final antes de definir `pendingUserMessage` e chamar `sendChatMessage`.
   - Em `handleRequestPlanChanges`, aplicar `.trim()` em `feedback`.
2. **Deduplicação resiliente e limpeza de `pendingUserMessage` (`ChatPage.tsx`):**
   - Na comparação `alreadyDurable` em `messages` (`useMemo`), comparar `item.content.trim() === pendingUserMessage.content.trim()`.
   - Limpar `setPendingUserMessage(null)` quando o snapshot durável já contiver a mensagem do usuário, evitando que um `pendingUserMessage` antigo reapareça após um `rewindTurn`.
   - Adicionar guarda de concorrência (`submittingQuestionRef`) em `submitQuestionAnswers`.
3. **Bloqueio de duplo envio no `UserQuestionCard.tsx`:**
   - Adicionar estado local `localSubmitting` em `UserQuestionCard` combinado com a prop `submitting` (`const busy = submitting || localSubmitting`), desabilitando o botão e exibindo `"Enviando…"` durante toda a promise `onSubmit`.

### 2.2. OpenCode Local Bridge integrada ao Provider `opencode`
Seguindo o mesmo padrão arquitetural já consolidado no provider `ollama` (`Local` vs `Cloud` derivado de `base_url`):

1. **Modos de Operação por `base_url` (`src/agentos/provider_catalog/opencode.py`):**
   - `DEFAULT_OPENCODE_LOCAL_BASE_URL = "http://127.0.0.1:18446"` (Modo Bridge Local — sem necessidade de API key).
   - `OPENCODE_CLOUD_BASE_URL = "https://opencode.ai/zen/v1"` (Modo Cloud Zen — com API key).
   - `normalize_opencode_base_url(value)` valida o esquema HTTP(S), bloqueia credenciais/query/fragment na URL e normaliza o sufixo.
   - `is_opencode_cloud(base_url)` identifica se o host aponta para `opencode.ai` (Cloud) ou para um servidor `opencode serve` local.
   - `PROVIDERS_WITH_BASE_URL` e `PROVIDERS_WITH_OPTIONAL_KEY` passam a incluir `"opencode"` (exigindo `api_key` apenas quando `is_opencode_cloud(base_url)` for `True`, exatamente como `ollama`).

2. **Gerenciador de Processo Local `opencode serve` (`OpenCodeBridgeProcessManager` em `src/agentos/provider_catalog/opencode.py`):**
   - Quando `base_url` aponta para loopback (`127.0.0.1` ou `localhost`) e o endpoint ainda não está respondendo em `GET /config/providers`:
     - Localiza o executável `opencode` (`opencode.cmd` / `opencode.exe` / `opencode` no PATH ou em `%APPDATA%\npm`).
     - Inicia `opencode serve --hostname 127.0.0.1 --port <port>` em um diretório de trabalho isolado e vazio (`orin_paths().data / "opencode-bridge"`), sem janela de console (`CREATE_NO_WINDOW` no Windows).
     - Aguarda readiness via polling em `GET {base_url}/config/providers` (timeout configurável).
     - Registra cleanup seguro para encerrar apenas o processo filho criado pelo próprio Orin.

3. **Descoberta de Catálogo e Teste de Conexão (`OpenCodeModelCatalogClient`):**
   - **Modo Local (`not is_opencode_cloud(base_url)`):**
     - Garante que o `opencode serve` está ativo (`ensure_opencode_serve(base_url)`).
     - Consulta `GET {base_url}/config/providers`, localiza o provider `"opencode"` e transforma o dicionário `models` na lista de modelos do catálogo do Orin (incluindo `big-pickle`, `mimo-v2.6-flash-free`, `nemotron-3.5-super-vl-free`, etc., marcando suporte a `tools`, `context_length` e modalidades).
   - **Modo Cloud (`is_opencode_cloud(base_url)`):**
     - Mantém a chamada `GET {base_url}/models` com `Authorization: Bearer {api_key}`.
   - **`test_connection` em `PostgresProviderConfigurationAdapter`:**
     - Suporta `provider == "opencode"`, retornando `{"connected": True, "models_available": len(models), "base_url": base_url}`.

4. **Bridge de Inferência e Tool-Calling no Harness do Orin (`HTTPProviderStreamTransport`):**
   - Em `HTTPProviderStreamTransport.stream(request)`, quando `self.provider == "opencode"` e `not is_opencode_cloud(self.base_url)`:
     - Garante que o `opencode serve` local está rodando em `self.base_url`.
     - Cria uma sessão efêmera via `POST {self.base_url}/session` (isolando cada chamada de inferência para que o histórico canônico e compactado do `AgentRuntime` do Orin seja a única fonte de verdade).
     - Constrói o prompt do sistema combinando:
       - As mensagens `role == "system"` do Orin;
       - As declarações das tools disponíveis no turno do Orin (`tools` do `AgentRuntime`) e o contrato de invocação estruturada `<orin_tool_call>{"name": "...", "arguments": {...}}</orin_tool_call>`, instruindo o modelo a usar exclusivamente esse formato para acionar ferramentas e a nunca invocar ferramentas internas do OpenCode.
     - Serializa o histórico de mensagens (`user`, `assistant`, `tool_calls` anteriores e resultados `role == "tool"`) em `parts: [{"type": "text", "text": ...}]`.
     - Envia `POST {self.base_url}/session/{session_id}/message` com:
       - `model: {"providerID": "opencode", "modelID": self.model}`
       - `system`: prompt de sistema + protocolo de tools do Orin
       - `tools`: `{"read": True, "bash": False, "edit": False, "glob": False, "grep": False, "skill": False, "task": False, "todowrite": False, "webfetch": False, "websearch": False, "write": False, "question": False, "apply_patch": False}` (mantendo `read: True` para satisfazer o gate do tier gratuito e bloqueando qualquer execução/escrita no OpenCode).
     - Processa a resposta JSON do `opencode serve`:
       - Se `info.error` estiver presente, levanta `ProviderKeyRejected` com o status correspondente (`401`/`403`/`429`) ou erro de transporte.
       - Extrai blocos `<orin_tool_call>...</orin_tool_call>` do texto retornado pelas `parts` (e, como fallback defensivo, caso o modelo invoque a tool `read` nativa do OpenCode em uma part `type == "tool"`, converte-a para uma chamada `read_file` do Orin se `read_file` estiver nas tools do turno).
       - Emite `NormalizedStreamItem(StreamKind.TEXT_DELTA, ...)` para o texto visível ao usuário.
       - Emite `NormalizedStreamItem(StreamKind.TOOL_CALL, ...)` para cada tool call extraída, permitindo que o `AgentRuntime` do Orin execute a ferramenta com todas as políticas de segurança, permissões, checkpoints e eventos de UI do Orin.
       - Emite `NormalizedStreamItem(StreamKind.USAGE, ...)` com `prompt_tokens` e `completion_tokens` vindos de `info.tokens`.
       - Emite `NormalizedStreamItem(StreamKind.FINISH, ..., finish_reason="tool_calls" if tool_calls else "stop")`.

5. **Interface de Configuração no Front-end (`OpenCodeSetup.tsx`, `ProviderDetail.tsx`, `useProviderState.ts`):**
   - Criar `OpenCodeSetup.tsx` (seguindo a mesma ergonomia visual de `OllamaSetup.tsx`) com seletor de modo:
     - **Bridge Local (Free & Harness Orin):** URL padrão `http://127.0.0.1:18446`, sem exigir chave de API, com botões **"Testar conexão"** (que sobe/valida o `opencode serve` local e exibe a contagem de modelos disponíveis) e **"Salvar e ativar"**.
     - **OpenCode Zen (Cloud API Key):** URL `https://opencode.ai/zen/v1` + campo de Chave de API.
   - Atualizar `useProviderState.ts` e `providers.ts` para suportar `opencodeMode` (`'local' | 'cloud'`), `baseUrl` e `testOpenCodeConnection`.

---

## 3. Segurança, Mentalidade Adversária e Casos de Borda
- **Prevenção de SSRF / Comandos Arbitrários:** `normalize_opencode_base_url` aceita apenas esquemas `http`/`https`, rejeita credenciais embutidas, query strings e fragmentos; o auto-start do `opencode serve` só é disparado quando o host é estritamente loopback (`127.0.0.1`, `localhost`, `::1`) e usa lista de argumentos fixa sem `shell=True`.
- **Isolamento do `opencode serve`:** Todas as tools de escrita/execução do OpenCode (`bash`, `edit`, `write`, `apply_patch`, `webfetch`, `websearch`, `task`) são enviadas como `False` em cada requisição, e o processo roda em diretório vazio isolado (`data/opencode-bridge`). Toda leitura/escrita/comando real passa pelo harness e pelo motor de permissões do Orin.
- **Parsing Adversário de `<orin_tool_call>`:** Inputs malformados, JSON truncado ou nomes de ferramentas que não pertencem à lista de tools autorizadas no turno são ignorados como tool call ou tratados com segurança sem quebrar o stream.
- **Resiliência na UI:** Duplo clique em `UserQuestionCard`, notas com múltiplos `\n` ou espaços finais, e limpeza de `pendingUserMessage` após snapshot são cobertos por testes unitários e validação no navegador via Playwright.
