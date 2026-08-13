# Plano de entrega — Navegador seguro e visual para agentes

**Origem:** auditoria `docs/audits/2026-08-13-browser-agent-readiness.md`
**Objetivo:** disponibilizar navegação e interação de browser aos agentes, com isolamento, autorização pelo backend, aprovação humana quando necessário e feedback visual no chat.

## Resultado de produto

Durante uma ação de browser, a conversa mostra um cartão vivo: agente, host sanitizado, ação atual e estado. Ao terminar uma navegação ou uma ação relevante, o cartão mostra uma miniatura de screenshot autorizada, título/URL sem query ou credenciais e um resumo textual. O usuário pode abrir a captura em modal, acompanhar a sequência de ações e aprovar/rejeitar uma ação classificada como externa. O modelo recebe apenas observações e referências de elementos; nunca cookies, credenciais ou screenshot em base64 via evento.

```text
Agente -> Browser gateway autorizado -> fila BROWSER -> Browser Worker isolado
       <- job/event/artifact refs     <- Playwright + policy de conexão
Chat/SSE <- projeção pública mínima <- artifact proxy autenticado <- screenshots
```

## Decisões de implementação

1. **Manter o domínio existente e remover o atalho conversacional direto.** `agentos.browser` continua dono dos contratos. `ConversationBrowser` deixa de instanciar Playwright e passa a ser um cliente da porta de jobs.
2. **Um processo `BROWSER` separado.** Criar entrypoint `agentos.workers.browser` e iniciar por launcher/supervisor/Compose como pool próprio. Ele recebe jobs já autorizados, não importa banco/ORM/Redis/Runtime/API e não decide ownership.
3. **Autorização no serviço confiável.** Gateway/serviço valida usuário, workspace, execution, perfil, lease, versão e capabilities antes do job. O worker valida novamente o grant mínimo e o fencing token.
4. **Profiles são explícitos e opt-in.** Por padrão, sessão efêmera e sem login. Um perfil persistente só é conectado pelo usuário; storage state usa secret reference, possui escopo, expiração e revogação.
5. **Ações são tipadas, não JavaScript livre.** Expor `navigate`, `observe`, `click`, `fill`, `press`, `select`, `check`, `upload`, `download`, `screenshot` e `close`. Preferir referências efêmeras de elemento (`ref`) provenientes de `observe`; limitar CSS/text locators como fallback auditável. `evaluate` continua negado.
6. **Confirmação antes do efeito quando a política exigir.** Login/OTP, envio de formulário, compra, remoção, alteração de conta, upload, download e qualquer seletor marcado como submit/destructive entram em `waiting_user`. O backend recebe a decisão autenticada e emite um grant único, com TTL curto.
7. **Rede realmente contida.** Declarar política por perfil; usar isolamento de rede/proxy ou mecanismo equivalente que resolva e conecte apenas ao IP validado. Revalidar cada hop, bloquear loopback/RFC1918/link-local/metadata e schemes/portas não permitidos. Permitir subresources somente após a mesma política e com quotas.
8. **Capturas são artefatos privados.** Browser Worker envia bytes apenas ao Artifact Output; a API fornece URL temporária autenticada pelo dono do conversation/workspace. O SSE leva `artifact_id`, media type, dimensões e metadados sanitizados, nunca bytes, cookies ou URL com query.

## Fases e critérios de aceite

### 1. Fundamento operacional

- Declarar versão fixa/compatível de Playwright e provisionar Chromium em ambiente de desenvolvimento e imagem de worker; falhar em readiness com código claro se a engine estiver ausente.
- Criar configuração de Browser Worker: concorrência, timeout, páginas por sessão, DOM/screenshot/download/upload bytes, hosts/portas e retenção de artifact.
- Criar `agentos.workers.browser`, healthcheck e supervisor/launcher/Compose para os pools AGENT e BROWSER separados.
- Remover `PlaywrightBrowserAdapter` do caminho do `ChatWorker`; somente o novo entrypoint pode importá-lo.

**Aceite:** iniciar Orin informa `Browser worker ready`; matar o worker falha somente jobs de browser e não interrompe turns sem browser; scan de imports prova que API/runtime/chat worker não importa Playwright.

### 2. Despacho durável, tenancy e lifecycle

- Persistir job, estado, owner, execution, session/page refs, fence, deadline, idempotency, effect state e artifact refs; não persistir DOM, cookie, URL query ou segredo.
- Adaptar `BrowserService` para uma porta de broker/queue, inspeção/stream/cancelamento e reconciliação após worker crash.
- Integrar leases do Resource Manager e perfis/sessões/páginas reais; fechar em cancelamento, expiração, término do turn e shutdown.
- Adicionar endpoints internos/autorizados para inspeção, aprovação e artifact proxy, todos filtrados por usuário e workspace.

**Aceite:** testes cobrem ownership cruzado, perfil cruzado, fence antigo, idempotência, timeout, cancelamento, resultado tardio, crash/restart e limpeza de contexto/temp/download.

### 3. Automação útil e segura

- Implementar operações Playwright tipadas, com observação que produz árvore de elementos interativos limitada e refs de vida curta.
- Após navegação/interação, capturar DOM observável e screenshot conforme política; sanitizar texto e URL antes de retornar ao modelo e eventos.
- Implementar upload exclusivamente por `AuthorizedFileReference`; download em staging, validação de tipo/tamanho e commit como artifact.
- Classificar efeito da ação. Ações read-only podem rodar sem aprovação; ações externas só executam com grant de aprovação, não recebem retry automático e reportam `UNKNOWN` quando apropriado.

**Aceite:** fixture local isolada prova navigate/click/fill/press/select/check/upload/download/screenshot, bloqueio de selector inválido, saída de host, redirect, DNS rebinding, payload malicioso, limite de bytes e não-retry de submit.

### 4. Ferramentas do agente e política

- Substituir `browse_page` por schemas granulares do gateway de browser, expostos apenas quando o Browser Worker estiver saudável e a policy do usuário/workspace permitir.
- Fazer `observe` e `screenshot` read-only; serializar ações que mudam a mesma página e conservar versão/ref esperada.
- Incluir no system prompt a regra de que conteúdo da web é dado não confiável, que não se deve preencher/confirmar dados sensíveis sem o usuário e que formulários podem solicitar aprovação.
- Registrar ledger/auditoria com host sanitizado, ação, ref, decision id, outcome e effect state.

**Aceite:** uma subagente recebe o mesmo conjunto autorizado via sessão confiável; prompt/tool args não conseguem elevar capability nem escolher profile alheio.

### 5. Resposta visual no chat

- Acrescentar eventos públicos `browser.started`, `browser.navigated`, `browser.action_requested`, `browser.action_finished`, `browser.capture_ready` e `browser.failed`, com sequência e payload mínimo.
- Expandir o parser de atividades e criar `BrowserActivityCard` reutilizando a timeline existente. Estados: abrindo, navegando, observando, aguardando aprovação, agindo, capturando, concluído, bloqueado, falhou e cancelado.
- Mostrar host/título sanitizados e miniatura de screenshot via artifact proxy; modal acessível com loading/error, texto alternativo e resumo DOM. Não forçar auto-scroll nem expor a captura a outro usuário.
- Criar `BrowserApprovalCard` com explicação, alvo e ação; enviar decisão autenticada por API. A decisão expirada não executa nada.
- Aplicar retenção/expiração, limite de capturas por job e redaction configurável antes de servir a miniatura.

**Aceite:** e2e com SSE simulado verifica transições, reidratação após reload, deduplicação por `event_id`, modal/teclado, estado de aprovação, captura indisponível/expirada e isolamento entre conversas.

### 6. Validação de release

- Executar unitários do domínio, worker e agent tools; integração com Redis/PostgreSQL; fixture Chromium real; testes frontend/build/e2e; `git diff --check`.
- Fazer teste manual: página JavaScript pública permitida, formulário que requer aprovação, bloqueio de destino privado, cancelamento no meio da navegação, crash do Browser Worker e reabertura do chat.
- Atualizar README/runbook com requisitos, instalação de engine, healthchecks, limites, profiles, consentimento e como desabilitar a capability.

**Aceite final:** uma conversa e uma subagente podem navegar, observar, interagir e mostrar captura com autorização e isolamento comprovados; os controles P0/P1 possuem teste automatizado e o launcher declara a saúde do pool Browser.

## Sequência recomendada

Implementar as fases 1–2 antes de expor qualquer nova ferramenta. Em seguida, concluir fases 3–4 em uma feature flag desabilitada por padrão. Só liberar a flag depois de fase 5 e da validação completa da fase 6. Isso evita publicar uma ferramenta de clique antes de existir isolamento, aprovação e retorno visual verificável.
