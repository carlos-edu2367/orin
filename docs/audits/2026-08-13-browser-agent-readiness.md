# Auditoria de prontidão — Navegador para agentes

**Data:** 2026-08-13
**Escopo:** capacidade de navegar, observar, clicar, preencher campos, enviar formulários, capturar tela e comunicar visualmente a atividade ao dono da conversa.
**Método:** leitura do runtime, worker, domínio `agentos.browser`, frontend/SSE, dependências e launcher local; execução de `pytest` focado. Não foi feita automação contra um site externo.

## Conclusão

O repositório possui uma boa base de contratos para Browser Resource, porém **a capacidade não está pronta para ser disponibilizada aos agentes**. Hoje ela só pode aparecer como `browse_page` se Playwright estiver instalado; neste ambiente ele não está. Quando aparece, abre uma URL HTTPS e devolve apenas texto do DOM renderizado. Não há ferramenta de clique, preenchimento, seleção, pressionamento de teclas, upload, download, autenticação de perfil, aprovação de ação sensível ou resposta visual para a pessoa.

O próximo passo correto é integrar o domínio existente por uma fila/pool `BROWSER` dedicado, e não ampliar o atalho atual no `chat` worker. Isso preserva o ADR 004 e evita que um processo que possui banco, provider e workspace também detenha um navegador com conteúdo não confiável.

## Atualização de implementação — 2026-08-13

Foi entregue uma capacidade operacional para o perfil local. `conversation_browser_for()` agora instancia `IsolatedConversationBrowser`, que cria um processo Chromium dedicado por turno e se comunica somente por pipe. O processo filho não recebe banco, Redis, workspace, provider ou Runtime. O agente possui `browse_page`, `browser_observe`, `browser_click`, `browser_fill`, `browser_press`, `browser_select`, `browser_check` e `browser_screenshot`; cada observação salva uma PNG privada no workspace e a interface apresenta um cartão visual que abre a captura autenticada.

O provisionamento está em `scripts/install-browser.ps1` e é chamado pelo launcher local, salvo quando `AGENTOS_BROWSER_ENABLED=false`. A validação real abriu `https://example.com` no processo isolado e capturou a tela. Submit de formulário, senha, JavaScript arbitrário, cookies, upload/download, clipboard, câmera e geolocalização continuam bloqueados: eles dependem do fluxo de approval/profile e do broker durável descritos no plano.

## O que existe e foi confirmado

| Área | Evidência | Situação |
| --- | --- | --- |
| Contratos e controles | `src/agentos/browser/` modela jobs, grants, limites, contexto, SSRF, artefatos e lifecycle. | Base útil, testada em memória. |
| Motor real opcional | `PlaywrightBrowserAdapter` faz `goto`, DOM, screenshot e metadados de cookies. | Não é dependência instalada nem há browser binário provisionado. |
| Runtime conversacional | `ChatWorker` cria `ConversationBrowser` diretamente; `AgentToolset` expõe somente `browse_page`. | Leitura renderizada somente. |
| Interação | O enum contém `INTERACT`, mas o adapter retorna `INTERACTION` sem executar ação; `ConversationBrowser` concede apenas `NAVIGATE` e `READ_DOM`. | Indisponível. |
| Eventos e interface | SSE já mostra `tool.started`/`tool.finished` em `ActivityCard`. | Apenas cartão textual genérico; não há estado de página nem miniatura. |
| Pool separado | `WorkerPool.BROWSER` e `WorkKind.BROWSER_ACTION` existem como modelos. `docker-compose.yml`, launcher e `WorkerSettings` iniciam apenas o worker de chat. | Não conectado. |

## Achados priorizados

### P0 — bloqueiam a disponibilização

1. **Dependência e engine ausentes.** `pyproject.toml` não declara `playwright`; a verificação no ambiente retornou `playwright=None`. Mesmo com o pacote Python, Chromium/Firefox/WebKit precisam ser instalados de modo reproduzível na imagem/instalação.
2. **O caminho em execução viola o isolamento definido.** `ChatWorker._runtime_for()` constrói `ConversationBrowser`, que instancia `PlaywrightBrowserAdapter` no worker genérico. O ADR 004 e a RFC 405 exigem Browser Worker/pool dedicado, sem banco, ORM, Redis, Runtime ou API.
3. **Não há automação interativa.** O schema de ferramenta aceita apenas `browse_page(url)`. O adapter não implementa click/fill/press/select/check/upload/download; o seu fallback genérico em `INTERACT` não altera a página.
4. **Páginas JavaScript não funcionam de forma confiável.** A ferramenta afirma atender páginas que constroem conteúdo em JavaScript, mas `NetworkPolicy.allow_subresources` é `False` e o route handler aborta CSS, scripts, fontes e XHR. O documento pode abrir, mas a aplicação não recebe os recursos para construir sua interface.
5. **Não existe resposta visual para o usuário.** Screenshots atuais ficam em `MemoryArtifactOutput`, duram somente o turno e não possuem URL/endpoint autorizado para a interface. O stream não carrega uma referência de captura renderizável.

### P1 — segurança, produto e operação

1. **A política de rede real não fixa a conexão ao IP validado.** Há validação de DNS antes de `goto` e no route handler, mas o browser resolve/conecta depois da validação. É preciso uma política de conexão/proxy que impeça DNS rebinding entre validação e socket, além de revalidar redirect e subresource.
2. **Não há autorização específica da capacidade.** O `AllowList` genérico é capaz de esconder ferramentas, mas não existe grant persistido por usuário/workspace/perfil nem uma decisão para credenciais, cookies, upload, download e formulários com efeito externo.
3. **Não há aprovação humana para efeitos externos.** Clique, submit, upload e download não podem ter retry cego. A UI precisa permitir aprovar ou recusar a ação proposta, com alvo, resumo e efeito esperado, antes da execução quando a política exigir.
4. **Perfis e login não estão disponíveis ao agente.** A conversa cria sessão efêmera por turno. Não há fluxo para um usuário conectar um perfil, guardar storage state como segredo, mostrar estado de autenticação sem expor cookies, ou revogar esse acesso.
5. **Cancelamento e recuperação não são operacionais.** O serviço em memória oferece contratos de cancelamento, mas não há despacho durável, worker separado, health/readiness, reconciliação real de crash ou observabilidade do pool.
6. **Testes de engine e produto faltam.** Os testes atuais verificam contratos e um adapter falso; não há teste com Chromium provisionado, site fixture de interação, aprovação, redaction, artifact proxy, SSE ou componente visual.

### P2 — experiência e observabilidade

1. `BrowserStarted`, `BrowserNavigated` e `BrowserArtifact` existem no enum de eventos, mas o runtime usa somente `tool.*`; o usuário não vê host atual, ação, resultado ou captura.
2. `ActivityCard` agrupa `web` como “Consultou páginas”; ela não diferencia navegação, digitação, clique, espera, captura, solicitação de aprovação e falha de política.
3. Não há limites de taxa de capturas, retenção, expiração de URLs, redaction visual nem acessibilidade para a imagem (alt text e alternativa textual).

## Riscos que não devem ser aceitos na implementação

- Executar Playwright na API, no Runtime ou no `ChatWorker` por conveniência.
- Permitir `evaluate`/JavaScript arbitrário, clipboard, câmera ou geolocalização como extensão de click/fill.
- Passar paths físicos, cookies, headers, tokens, DOM integral ou screenshot base64 no SSE.
- Confiar em prompt, ferramenta ou frontend para conceder autorização de browser.
- Retentar automaticamente ações com efeito externo ou continuar uma sessão após crash do worker.
- Liberar subresources sem uma política de conexão verificável por host, porta e IP.

## Evidência de validação

```text
.venv\\Scripts\\python.exe -m pytest -q tests\\unit\\browser tests\\unit\\agentic\\test_browser_tools.py
47 passed in 0.52s

.venv\\Scripts\\python.exe -c "import importlib.util; print(importlib.util.find_spec('playwright'))"
None
```

Os 47 testes demonstram a base de contrato, não a prontidão end-to-end: não iniciam um browser real, não exercitam interação nem validam a resposta visual ao usuário.
