# Sessão web, OpenRouter e Home Imersiva Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a configuração autenticada do OpenRouter funcionar no browser e substituir a home atual por uma experiência imersiva, acessível e com transição curta para a execution.

**Architecture:** A sessão opaca continua sendo emitida fora do React e o browser recebe apenas o cookie `HttpOnly` e o CSRF bootstrapado pelo host. O `ApiClient` consome esse bootstrap e as páginas tratam sessão, CSRF e erro de provider como estados distintos. A home é dividida em cenário decorativo semântico-zero e composer conversacional funcional; o composer controla a transição e só navega depois do recibo de conversa.

**Tech Stack:** Python 3.13/FastAPI/Pydantic/SQLAlchemy; React/TypeScript/Vite; Motion; Vitest; Playwright/Axe.

## Global Constraints

- O host autenticado deve emitir `agentos_session` como cookie `HttpOnly`, `Secure` em HTTPS, `SameSite=Lax` ou mais restritivo, e injetar `<meta name="csrf-token" content="…">` no HTML da sessão; este projeto não cria login, IdP ou formulário de PAT.
- Não disponibilizar PAT, API key, CSRF, `task_ref`, `agent_id`, payload de catálogo ou segredo no DOM, em localStorage/sessionStorage, URL, logs ou analytics.
- Mutação por sessão sempre inclui `X-CSRF-Token`, `Origin` e `Idempotency-Key`; repetir a mesma intenção reutiliza a mesma chave de idempotência.
- O catálogo e refresh do OpenRouter continuam exclusivamente server-side; identidade de modelo é `(provider, model_id)`.
- O envio da home navega somente após `POST /v1/conversations` responder 201 com `execution_id`.
- A referência visual orienta atmosfera e hierarquia, não uma imagem raster de fundo. Ornamentos são CSS/SVG, decorativos e `aria-hidden`.
- Respeitar `prefers-reduced-motion`; a alternativa reduzida não pode depender de WebGL ou animação contínua.
- Não fazer commit, push ou PR neste trabalho sem uma solicitação explícita do usuário.

---

## File Structure

| Arquivo | Responsabilidade |
| --- | --- |
| `docs/backend/frontend-integration.md` | Contrato exato de sessão emitida pelo host, CSRF bootstrapado e comportamento de expiração. |
| `frontend/index.html` | Ponto documentado de injeção da meta CSRF pelo host, sem valor de fallback. |
| `frontend/src/api/browserSession.ts` (novo) | Leitura validada do bootstrap CSRF e estado público da sessão no documento. |
| `frontend/src/api/client.ts` | Criação do cliente web a partir do bootstrap, sem alterar clientes PAT injetados em testes/automação. |
| `frontend/src/api/errors.ts` | Classificação segura de erro de autenticação, autorização/CSRF e provider. |
| `frontend/src/features/providers/ProviderSettingsPage.tsx` | Estado de sessão no formulário, preservação em memória da chave em erro e mensagens específicas. |
| `frontend/src/features/conversations/ConversationComposer.tsx` | Composer em coluna, seleção autorizada, intenção de envio, foco e transição de falha/sucesso. |
| `frontend/src/app/Home.tsx` | Estrutura da home, botão de configurações e cenário decorativo acessível. |
| `frontend/src/styles/index.css` | Layout, camadas visuais, responsividade e animações reduzíveis. |
| `tests/unit/api/test_api_asgi.py` | Prova HTTP do contrato cookie + CSRF nas rotas provider/conversation. |
| `frontend/tests/unit/apiClient.test.ts` | Bootstrap CSRF e cabeçalhos do cliente browser. |
| `frontend/tests/unit/HomeNetwork.test.tsx` (novo) | Estados do composer, transição e navegação sem IDs técnicos. |
| `frontend/tests/unit/ProviderSettingsPage.test.tsx` (novo) | Chave somente escrita, falhas de sessão/CSRF/provider e recuperação do formulário. |
| `frontend/tests/e2e/provider-settings.spec.ts` | Cenário browser que verifica cabeçalho CSRF, chave e mensagens de erro. |
| `frontend/tests/e2e/execution-controls.spec.ts` | Jornada home → conversa → execution, com o novo markup. |
| `frontend/tests/e2e/a11y.spec.ts` | Foco, aria-live e auditoria da home/configurações. |
| `frontend/tests/visual/execution-page.spec.ts` | Snapshots desktop/mobile da home em repouso e envio reduzido. |
| `.env.example`, `README.md`, `docs/frontend/{UX_UI_SPEC,BACKEND_DISCOVERY}.md` | Operação local, limites de autenticação e comportamento UX atualizados. |

## Interfaces

```ts
// frontend/src/api/browserSession.ts
export type BrowserSessionBootstrap =
  | { status: 'ready'; csrfToken: string }
  | { status: 'missing_csrf' }

export function readBrowserSessionBootstrap(documentRef?: Document): BrowserSessionBootstrap
```

```ts
// frontend/src/features/conversations/ConversationComposer.tsx
export type ConversationComposerProps = {
  client: ApiClient
  onCreated: (executionId: string) => void
  onSubmittingChange?: (submitting: boolean) => void
}
```

```http
# Contrato de hosting, não uma rota nova do gateway
Set-Cookie: agentos_session=<opaque>; HttpOnly; Secure; SameSite=Lax; Path=/
<meta name="csrf-token" content="<opaque-per-session-token>">
```

### Task 1: Fixar e provar o contrato de sessão web antes de alterar a UI

**Files:**
- Modify: `docs/backend/frontend-integration.md`, `frontend/index.html`, `tests/unit/api/test_api_asgi.py`
- Test: `tests/unit/api/test_api_asgi.py`

**Consumes:** `InMemorySecurityService.add_session(session_id, principal, csrf_token)` e `gateway.principal_for(..., mutable=True)`.

**Produces:** contrato verificável de cookie + meta CSRF que o host deve injetar e testes que separam 401 de 403 nas rotas relevantes.

- [ ] **Step 1: Escrever os testes RED do caminho de sessão.**

  Em `tests/unit/api/test_api_asgi.py`, criar `FakeProviderConfiguration`/`FakeConversationApplication` com contadores e adicionar três casos para `PUT /v1/providers/openrouter`:

  ```python
  def test_openrouter_write_with_cookie_and_csrf_reaches_configuration_port() -> None:
      security.add_session("sid-1", principal, csrf_token="csrf-1")
      client.cookies.set("agentos_session", "sid-1")
      response = client.put(
          "/v1/providers/openrouter",
          headers={"X-CSRF-Token": "csrf-1", "Origin": "http://127.0.0.1:4173", "Idempotency-Key": "provider-1"},
          json={"api_key": "key-writes-only", "enabled": True},
      )
      assert response.status_code == 200
  ```

  Repetir para cookie ausente (`401`, categoria `AUTHENTICATION`, contador zero) e CSRF ausente/errado (`403`, categoria `AUTHORIZATION`, contador zero). Fazer a mesma prova de sucesso para `POST /v1/conversations`.

- [ ] **Step 2: Executar RED.**

  Run: `python -m pytest -q tests/unit/api/test_api_asgi.py -k "cookie or openrouter or conversation"`

  Expected: os casos novos falham até que a configuração dos fakes inclua as portas provider/conversation e os cabeçalhos corretos; os casos de negação devem confirmar que nenhuma porta foi chamada.

- [ ] **Step 3: Atualizar o contrato de hosting sem criar bypass.**

  Em `docs/backend/frontend-integration.md`, substituir a descrição antiga de modelo no `PUT` por `api_key` e `enabled`; documentar exatamente cookie, meta CSRF, `Origin`, expiração e que o host/IdP emite a sessão. Em `frontend/index.html`, incluir somente o marcador não secreto:

  ```html
  <!-- O host autenticado substitui/insere content antes de servir este documento. -->
  <meta name="csrf-token" content="">
  ```

  Não adicionar rota de login, token de desenvolvimento, cookie não-HttpOnly nem qualquer fallback em memória no `gateway.py`/ASGI.

- [ ] **Step 4: Executar GREEN.**

  Run: `python -m pytest -q tests/unit/api/test_api_asgi.py`

  Expected: sucesso cookie+CSRF chama a porta uma vez; 401/403 não chamam a porta e nenhuma resposta contém chave, cookie, CSRF ou `task_ref`.

- [ ] **Step 5: Commit (somente se autorizado).**

  ```bash
  git add docs/backend/frontend-integration.md frontend/index.html tests/unit/api/test_api_asgi.py
  git commit -m "docs: define authenticated browser session contract"
  ```

### Task 2: Tornar o cliente browser consciente do bootstrap e os erros acionáveis

**Files:**
- Create: `frontend/src/api/browserSession.ts`
- Modify: `frontend/src/api/client.ts`, `frontend/src/api/errors.ts`, `frontend/tests/unit/apiClient.test.ts`
- Test: `frontend/tests/unit/apiClient.test.ts`

**Consumes:** meta `csrf-token` do host e `ApiClient` atual.

**Produces:** `readBrowserSessionBootstrap()` e um `ApiClient` browser que só anexa CSRF não vazio; consumidores podem bloquear a mutação antes de uma chamada fadada a 403.

- [ ] **Step 1: Escrever testes RED para a leitura do bootstrap.**

  Adicionar testes que inserem/removem a meta no `document.head` e esperam:

  ```ts
  expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'ready', csrfToken: 'csrf-test' })
  expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'missing_csrf' })
  ```

  Criar um `ApiClient` com `createBrowserApiClient()` e um `fetchImpl` espião; em mutação com meta válida, exigir `X-CSRF-Token: csrf-test`; com meta vazia, exigir ausência desse cabeçalho.

- [ ] **Step 2: Executar RED.**

  Run: `npm test -- --run tests/unit/apiClient.test.ts`

  Expected: falha porque não existe `browserSession.ts` e o cliente não expõe bootstrap validado.

- [ ] **Step 3: Implementar leitura mínima e taxonomia de erro.**

  Implementar leitura de uma única meta, aceitando valor trimmed entre 1 e 255 caracteres; não persistir nem registrar o valor. Alterar `createBrowserApiClient()` para usar somente o estado `ready`. Em `errors.ts`, criar predicados puros `isAuthenticationError(error)` e `isCsrfAuthorizationError(error)` baseados em status/categoria/código sanitizado, sem inferir segredos.

- [ ] **Step 4: Executar GREEN e regressão de transporte.**

  Run: `npm test -- --run tests/unit/apiClient.test.ts`

  Expected: todos os cabeçalhos esperados são enviados, clientes com Bearer injetado continuam independentes da meta, e nenhum teste inspeciona/imprime o valor além da asserção local.

- [ ] **Step 5: Commit (somente se autorizado).**

  ```bash
  git add frontend/src/api/browserSession.ts frontend/src/api/client.ts frontend/src/api/errors.ts frontend/tests/unit/apiClient.test.ts
  git commit -m "feat(frontend): consume browser csrf bootstrap"
  ```

### Task 3: Corrigir o fluxo OpenRouter e sua recuperação de erro

**Files:**
- Create: `frontend/tests/unit/ProviderSettingsPage.test.tsx`
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx`, `frontend/tests/e2e/provider-settings.spec.ts`
- Test: `frontend/tests/unit/ProviderSettingsPage.test.tsx`, `frontend/tests/e2e/provider-settings.spec.ts`

**Consumes:** `BrowserSessionBootstrap`, `ApiError`, `configureProvider`, `refreshProviderModels` e catálogo já autorizado.

**Produces:** painel OpenRouter que não tenta salvar sem CSRF, diferencia sessão/CSRF/provider e só limpa a chave após confirmação de sucesso.

- [ ] **Step 1: Escrever RED para os quatro resultados do formulário.**

  Cobrir com `ApiClient({ fetchImpl })`:

  ```ts
  // sucesso: PUT contém api_key/enabled e o input vira ''
  // 401: mostra "Sua sessão expirou. Entre novamente para salvar a chave." e mantém o input
  // 403 CSRF: mostra "Atualize a página para renovar sua sessão." e mantém o input
  // 503 provider: mostra falha do provider + correlation ID, sem renderizar a chave
  ```

  Acrescentar caso de bootstrap `missing_csrf`: botão Salvar desabilitado, explicação em `role="status"`, e nenhuma chamada `PUT`. Atualizar a spec Playwright para inserir a meta antes de navegar e afirmar o cabeçalho `X-CSRF-Token` no request interceptado.

- [ ] **Step 2: Executar RED.**

  Run: `npm test -- --run tests/unit/ProviderSettingsPage.test.tsx && npm run test:e2e -- provider-settings.spec.ts`

  Expected: os testes falham porque o painel sempre limpa a chave, agrupa 401/403 e ainda envia a mutação sem bootstrap.

- [ ] **Step 3: Implementar a menor mudança por estado.**

  Passar o estado do bootstrap ao painel. Antes de `configureProvider`, recusar `missing_csrf`; no `catch`, usar os predicados de `errors.ts`; limpar `apiKey` somente após resposta de configure aceita, revogação ou desmontagem. Manter `apiKey` somente no estado local do componente em erros e nunca colocá-la em error, URL, catalog ou log. Após `refreshProviderModels` aceito, atualizar o catálogo e manter resposta sanitizada.

- [ ] **Step 4: Executar GREEN e prova de segredo.**

  Run: `npm test -- --run tests/unit/ProviderSettingsPage.test.tsx && npm run test:e2e -- provider-settings.spec.ts`

  Expected: o sucesso limpa a chave; erros preservam-na somente enquanto a página está montada; HTML/alertas nunca mostram a chave; o request contém CSRF e Idempotency-Key.

- [ ] **Step 5: Commit (somente se autorizado).**

  ```bash
  git add frontend/src/features/providers/ProviderSettingsPage.tsx frontend/tests/unit/ProviderSettingsPage.test.tsx frontend/tests/e2e/provider-settings.spec.ts
  git commit -m "fix(providers): handle authenticated OpenRouter setup"
  ```

### Task 4: Estruturar a home imersiva e o cenário decorativo acessível

**Files:**
- Modify: `frontend/src/app/Home.tsx`, `frontend/src/styles/index.css`
- Create: `frontend/tests/unit/HomeNetwork.test.tsx`
- Test: `frontend/tests/unit/HomeNetwork.test.tsx`, `frontend/tests/visual/execution-page.spec.ts`

**Consumes:** `Home`, `ConversationComposer`, Motion e os tokens visuais existentes (`#0b0d10`, `#c8ff6a`, `#f4f0e9`).

**Produces:** uma home full-viewport com topbar, cenário orbital e área central para o composer, sem texto decorativo duplicado no leitor de tela.

- [ ] **Step 1: Escrever RED para a hierarquia da página.**

  Em `HomeNetwork.test.tsx`, renderizar a rota `/` com cliente falso e exigir marca, botão/link acessível `Configurações de providers`, único `h1`, textarea `Mensagem`, e cenário `aria-hidden` que não cria botão/canvas. Verificar que `Agent ID` e `Referência de tarefa` não existem.

- [ ] **Step 2: Executar RED.**

  Run: `npm test -- --run tests/unit/HomeNetwork.test.tsx`

  Expected: falha porque a home ainda tem hero em duas colunas, links de fixture e não contém a nova semântica de configurações.

- [ ] **Step 3: Implementar markup de cenário em `Home.tsx`.**

  Remover links de fixture e o contexto visual antigo. Inserir uma camada decorativa composta por `div`s para névoa, rede, pontos e duas órbitas, todas sob `aria-hidden="true"`; colocar marca, botão para `/providers`, título curto e `ConversationComposer` em uma única região funcional. Usar `motion` apenas no container decorativo; não adicionar Three.js/canvas à home.

- [ ] **Step 4: Criar a base CSS responsiva.**

  Trocar regras `.home-hero` por camadas full-viewport: fundo fixo, conteúdo central com `max-width: 944px`, z-index explícito, gradientes de baixa opacidade e breakpoints de 800px/480px. Usar `@media (prefers-reduced-motion: reduce)` para desligar animações de órbita e preservar uma composição estática.

- [ ] **Step 5: Executar GREEN e criar baseline visual.**

  Run: `npm test -- --run tests/unit/HomeNetwork.test.tsx && npm run test:visual -- --update-snapshots`

  Expected: hierarquia passa; `home-win32.png` é deliberadamente atualizado para a composição imersiva sob movimento reduzido.

- [ ] **Step 6: Commit (somente se autorizado).**

  ```bash
  git add frontend/src/app/Home.tsx frontend/src/styles/index.css frontend/tests/unit/HomeNetwork.test.tsx frontend/tests/visual/execution-page.spec.ts frontend/tests/visual/execution-page.spec.ts-snapshots/home-win32.png
  git commit -m "feat(home): add immersive agent desk landing screen"
  ```

### Task 5: Recriar o composer central e a transição para a execution

**Files:**
- Modify: `frontend/src/features/conversations/ConversationComposer.tsx`, `frontend/src/app/Home.tsx`, `frontend/src/styles/index.css`, `frontend/tests/unit/HomeNetwork.test.tsx`, `frontend/tests/e2e/execution-controls.spec.ts`
- Test: `frontend/tests/unit/HomeNetwork.test.tsx`, `frontend/tests/e2e/execution-controls.spec.ts`

**Consumes:** endpoint `POST /v1/conversations`, catálogo autorizado e interface `onSubmittingChange`.

**Produces:** composer de mensagem como foco principal, seletores compactos de provider/modelo, fade de 220 ms e recuperação sem perda de intenção.

- [ ] **Step 1: Escrever RED para criação, transição e falha.**

  Adicionar testes que verificam:

  ```ts
  await user.type(screen.getByLabelText('Mensagem'), 'Organize os dados')
  await user.click(screen.getByRole('button', { name: 'Enviar mensagem' }))
  expect(screen.getByTestId('home-submit-state')).toHaveAttribute('data-submitting', 'true')
  expect(await screen.findByText('Execution conectada')).toBeInTheDocument()
  ```

  Para erro 503, esperar retorno de `data-submitting="false"`, textarea com a mensagem original, foco no textarea e alerta. Verificar que provider/modelo são selects rotulados abaixo do textarea, favoritos funcionam como atalhos e catálogo vazio contém link para `/providers`.

- [ ] **Step 2: Executar RED.**

  Run: `npm test -- --run tests/unit/HomeNetwork.test.tsx && npm run test:e2e -- execution-controls.spec.ts`

  Expected: falha porque o composer atual usa grid de campos, não expõe estado de transição e somente marca `failed`.

- [ ] **Step 3: Implementar estado e callback sem navegação otimista.**

  Em `ConversationComposer`, usar `submitting` como fonte única: invocar `onSubmittingChange(true)` antes da requisição; desabilitar textarea, selects e envio; chamar `onCreated(receipt.execution_id)` apenas após parse 201. No `catch`, chamar `onSubmittingChange(false)`, manter `message`, focar textarea com `useRef` e renderizar alerta localizado. Em `Home`, aplicar a classe/atributo `data-submitting` no conteúdo e manter a navegação atual somente no callback `onCreated`.

- [ ] **Step 4: Implementar controles e motion.**

  Aplicar ao composer uma textarea grande, botão circular com nome acessível `Enviar mensagem`, e dois selects compactos em sequência após ela. Quando `data-submitting=true`, aplicar uma transição de opacidade de 220 ms ao conteúdo secundário e uma única animação de escala/opacidade no núcleo decorativo. A regra reduzida deve trocar apenas opacidade e continuar confirmando a mesma navegação.

- [ ] **Step 5: Executar GREEN.**

  Run: `npm test -- --run tests/unit/HomeNetwork.test.tsx && npm run test:e2e -- execution-controls.spec.ts`

  Expected: envio gera um único POST idempotente, sucesso navega após recibo, erro restaura todos os controles e os testes não encontram IDs técnicos/segredos no DOM.

- [ ] **Step 6: Commit (somente se autorizado).**

  ```bash
  git add frontend/src/features/conversations/ConversationComposer.tsx frontend/src/app/Home.tsx frontend/src/styles/index.css frontend/tests/unit/HomeNetwork.test.tsx frontend/tests/e2e/execution-controls.spec.ts
  git commit -m "feat(home): animate conversation handoff to execution"
  ```

### Task 6: Completar acessibilidade, documentação e verificação de release

**Files:**
- Modify: `frontend/tests/e2e/a11y.spec.ts`, `frontend/tests/e2e/reduced-motion.spec.ts`, `frontend/tests/visual/execution-page.spec.ts`, `.env.example`, `README.md`, `docs/frontend/UX_UI_SPEC.md`, `docs/frontend/BACKEND_DISCOVERY.md`
- Test: suítes backend e frontend abaixo

**Consumes:** comportamento final de sessão, ProviderSettings e home.

**Produces:** documentação sem requisito obsoleto de modelo, testes de acessibilidade/movimento e matriz de evidências de release.

- [ ] **Step 1: Escrever RED de acessibilidade e movimento.**

  Em `a11y.spec.ts`, adicionar meta CSRF no documento antes de abrir settings, verificar `role="alert"` e foco após erro da home; rodar axe na home nova e no painel OpenRouter. Em `reduced-motion.spec.ts`, navegar para `/`, enviar uma conversa interceptada e verificar `data-submitting` sem canvas, animation/pulse e sem quebra de navegação.

- [ ] **Step 2: Executar RED.**

  Run: `npm run test:e2e -- a11y.spec.ts reduced-motion.spec.ts`

  Expected: os novos assertions falham até que os rótulos, live regions e regras de reduced motion da Tasks 3–5 estejam completos.

- [ ] **Step 3: Atualizar documentação operacional.**

  Em `.env.example` e `README.md`, remover `*_MODEL` como requisito para salvar credencial e explicar que as seleções vêm do catálogo por usuário. Em `UX_UI_SPEC.md`, substituir a referência ao composer futuro por fluxo mensagem/provider/modelo e transition confirmada. Em `BACKEND_DISCOVERY.md`, registrar explicitamente o pré-requisito do host de sessão e a ausência deliberada de login/PAT no browser.

- [ ] **Step 4: Executar GREEN focalizado.**

  Run: `npm run test:e2e -- a11y.spec.ts reduced-motion.spec.ts && npm run test:visual`

  Expected: nenhuma violação axe crítica/grave, navegação continua sob reduced motion e snapshots correspondem ao novo design.

- [ ] **Step 5: Executar a matriz final completa.**

  Run:

  ```powershell
  python -m pytest -q tests/unit/api/test_api_asgi.py
  python -m pytest -q tests/integration/api/test_provider_configuration_postgres_optional.py tests/integration/api/test_frontend_contracts.py
  npm test
  npm run test:e2e
  npm run test:visual
  npm run lint
  npm run build
  ```

  Expected: todas as suítes passam. Se `AGENTOS_TEST_POSTGRES_DSN` não estiver configurada, documentar que as integrações Postgres foram puladas; não declarar a release pronta sem a execução delas num ambiente com banco.

- [ ] **Step 6: Revisar segurança manualmente.**

  Procurar na árvore modificada por `api_key`, `csrf-token`, `task_ref` e `agent_id`; confirmar que aparecem apenas em contratos/testes autorizados e nunca são renderizados ou logados pelo browser.

  Run: `rg -n "api_key|csrf-token|task_ref|agent_id" frontend/src src/agentos/api docs`

  Expected: nenhum valor de segredo, token de teste ou identificador técnico é introduzido como UI normal.

- [ ] **Step 7: Commit (somente se autorizado).**

  ```bash
  git add frontend/tests/e2e/a11y.spec.ts frontend/tests/e2e/reduced-motion.spec.ts frontend/tests/visual/execution-page.spec.ts .env.example README.md docs/frontend/UX_UI_SPEC.md docs/frontend/BACKEND_DISCOVERY.md
  git commit -m "docs: document authenticated provider and home flow"
  ```

## Plan Self-Review

- Cobertura da especificação: Tasks 1–3 tratam autenticação, CSRF, OpenRouter e recuperação; Tasks 4–5 tratam a home e a transição; Task 6 confirma acessibilidade, documentação e verificação.
- Segurança: o plano não cria emissão de PAT, bypass in-memory, token em storage ou catálogo client-side; a emissão da sessão permanece explicitamente no host confiável.
- Escopo: login/IdP é pré-requisito externo e não é implementado como trabalho oculto neste plano; isso evita prometer que o frontend possa autenticar sem autoridade de identidade.
- Consistência: todos os caminhos de sucesso usam `execution_id` do recibo, e todas as mutações por sessão requerem o mesmo bootstrap CSRF documentado.
