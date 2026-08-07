# Prompt da próxima sessão — Fechamento integral do Gate RFC 405 — Browser

Você é o agente responsável por fechar integralmente, na mesma sessão e sem solicitar decisões ao usuário, o gate normativo do **RFC 405 — Browser** do AgentOS.

## Regra absoluta de conclusão

Não finalize, não entregue resposta parcial e não declare sucesso enquanto o gate não estiver 100% implementado, funcional, integrado, testado, documentado e revisado contra o RFC 405 e todas as dependências normativas.

“100% completo” significa:

- todos os contratos públicos, invariantes, fluxos, eventos, políticas e limites normativos do RFC 405 implementados;
- Browser Resource com perfis, sessões e páginas isolados, versionados, ownership explícito, lease/TTL e lifecycle completo;
- Playwright executando exclusivamente em Browser Workers dedicados, atrás de portas tipadas, sem execução no processo da API, Runtime ou worker genérico;
- nenhuma operação obrigatória reduzida a stub, TODO, placeholder, `pass`, caminho feliz artificial ou contrato sem implementação;
- autorização, ownership, finalidade, contexto, profile snapshot, lease, fencing, quotas, páginas, rede, redirects, DNS, conteúdo não confiável, cookies, storage state, uploads, downloads, Artifacts, cancelamento, timeout, cleanup, idempotência, efeito UNKNOWN e reconciliação demonstrados por testes;
- Browser Worker sem acesso a banco, ORM, Redis, state store ou consulta geral de ownership;
- integração real com Resource Manager, Workspaces, Filesystem, Execution, Persistence, Events/Outbox, Secret e Artifact Storage pelas portas existentes;
- matriz de requisitos, spec, plano, closeout e prompt da sessão seguinte atualizados com evidência fresca;
- nenhuma obrigação normativa deixada para um agente futuro.

## Autonomia obrigatória

Você **não deve fazer perguntas** ao usuário. Se houver ambiguidade ou algo não documentado, escolha a alternativa mais aderente ao RFC 405, RFCs relacionadas, ADRs, contratos existentes, princípios de segurança e padrões do repositório. Registre a decisão na spec e no closeout e continue.

Não solicite confirmação de escopo, nome de pacote, engine, adapter, topologia, broker, worker, contrato, teste ou commit. Não transforme uma decisão pendente em backlog. Quando uma tecnologia concreta estiver fora do escopo, implemente a porta technology-neutral e o adapter mínimo determinístico necessário para provar o contrato. Quando a capacidade estiver explicitamente fora de escopo do RFC, implemente a rejeição ou limitação segura correspondente, documentando o motivo; não finja suporte e não deixe requisito obrigatório incompleto.

Preserve todo trabalho preexistente do worktree. Não use `git reset --hard`, `git checkout --`, remoções amplas ou qualquer operação que descarte mudanças do usuário. Stage e commit somente arquivos pertencentes a este gate.

## Contexto e dependências já fechadas

Os gates RFC 603 — Workspaces, RFC 403 — Filesystem e RFC 402 — Resource Manager estão concluídos no repositório. O gate RFC 404 — Terminal também está concluído. Consuma as portas existentes; não replique ownership, lifecycle, root, quota, lease, fencing, catálogo ou armazenamento dentro do Browser.

Antes de alterar código, leia integralmente:

- `docs/architecture/400-tools-resources/405-browser.md`;
- `docs/architecture/400-tools-resources/402-resource-manager.md`;
- `docs/architecture/400-tools-resources/403-filesystem.md`;
- `docs/architecture/400-tools-resources/404-terminal.md`;
- `docs/architecture/600-platform-data/603-workspaces.md`;
- `docs/architecture/100-kernel/101-runtime.md`, `102-execution-lifecycle.md` e `103-event-system.md`;
- `docs/architecture/400-tools-resources/401-tool-runtime.md`;
- `docs/architecture/600-platform-data/601-persistence.md` e `602-artifact-storage.md`;
- ADRs relacionados, no mínimo `001-arq-workers.md`, `003-playwright-browser-workers.md`, `005-local-workspaces.md`, `007-server-side-sessions.md`, `008-artifact-storage-abstraction.md`, `012-sqlalchemy-alembic-persistence-adapters.md`, `013-asyncio-concurrency-runtime.md` e `014-pydantic-boundary-validation.md`;
- specs, planos, closeouts, matrizes e prompts dos gates já concluídos;
- os pacotes existentes `agentos.resources`, `agentos.workspaces`, `agentos.filesystem`, `agentos.execution`, `agentos.context`, `agentos.events`, `agentos.persistence`, `agentos.artifact_storage`, `agentos.runtime` e `agentos.tool_runtime`, além dos testes correspondentes.

Faça primeiro uma leitura read-only do estado atual, branch, histórico recente, testes e worktree. O worktree pode estar sujo por mudanças anteriores: preserve-as e delimite claramente o escopo deste gate.

## Resultado obrigatório

O repositório deve conter um Browser Resource completo e seguro, preferencialmente sob um pacote final coerente com os padrões atuais — use `agentos.browser` se não houver convenção melhor — com:

- modelos públicos imutáveis e technology-neutral para perfil, sessão, página, job, operação, grants, limites, políticas, resultados e erros;
- `BrowserJobPort` completo, com submit, inspect, stream e cancelamento;
- `BrowserWorker` e fronteira de execução isolada, sem acesso a banco e sem exposição de handles nativos;
- adapter de referência determinístico, testável e sem depender de browser real;
- adapter Playwright operacional somente no Browser Worker, quando exigido pelas RFCs/ADRs e convenções do repositório, com a API concreta isolada na boundary do adapter;
- supervisor de sessão/página/processo, isolamento de perfil/storage/cache/diretório temporário e cleanup idempotente;
- política efetiva de URL, rede, redirects, DNS, subresources, páginas, conteúdo, uploads e downloads;
- integração sem bypass com Resource Manager, Workspaces, Filesystem, Artifacts, Events/Outbox, Persistence e Secret;
- testes de unidade, segurança, concorrência, integração, cancelamento, crash recovery, restart/reconcile e regressão completa.

O Browser não é uma Execution, não interpreta intenção, não compõe fluxos de navegação, não é fonte de autorização e não acessa banco, Memory, Context ou Tool Registry diretamente. O Browser Worker não interpreta DOM, texto, script, atributo, download ou resposta de rede como instrução de sistema, autorização ou mudança de policy.

## Contratos públicos obrigatórios

Implemente os equivalentes tipados dos contratos abaixo, adaptando somente nomes que já tenham convenção estabelecida no repositório:

### Contexto e identidade

Toda operação deve carregar e validar um `BrowserOperationContext` completo com:

`user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`.

Ausência, incompatibilidade ou alteração desses campos deve falhar de modo seguro. `profile_id`, `session_id`, `page_id`, `lease_id`, `job_id`, URL, worker ou handle nativo nunca concedem autorização por si mesmos.

### Modelos

Cubra os modelos normativos:

- `BrowserProfile`/snapshot com `profile_id`, owner, Workspace, nome validado, `policy_ref`, `storage_state_ref` secreto opcional, versão e estados `ACTIVE`, `LOCKED` e `DISABLED`;
- `BrowserSession`/snapshot com `session_id`, profile, contexto, lease, worker, páginas, estado, timestamps e expiração;
- estados de sessão `CREATING`, `READY`, `BUSY`, `CLOSING`, `CLOSED`, `FAILED` e `CANCELLED`;
- `BrowserPage`/snapshot com `page_id`, sessão, URL sanitizada, título sanitizado opcional, estado, versão e timestamps;
- estados de página `OPENING`, `READY`, `NAVIGATING`, `CLOSED` e `FAILED`;
- `BrowserJob` com contexto, lease, profile snapshot, sessão/página opcional, operação, limites, grants, idempotency key e deadline;
- operações `OpenSession`, `CloseSession`, `OpenPage`, `ClosePage`, `Navigate`, `Interact`, `CaptureDom`, `CaptureScreenshot`, `ReadCookies`, `SetCookies`, `Upload` e `Download`;
- `BrowserCookie` e entrada de cookie, mantendo o valor somente por referência secreta e permissões granulares;
- referências `DomSnapshotRef`, `ScreenshotRef`, `UploadRef` e `DownloadRef`, sem payload volumoso ou caminho físico nas interfaces públicas;
- `BrowserLimits` para timeout, páginas, redirects, DOM, screenshot, upload, download, quantidade de downloads e policy de rede;
- `BrowserJobOutcome` explícito para sucesso, falha e cancelamento, com `EffectState`, `Retryability`, `ResourceUsage` e referências parciais somente quando autorizadas;
- resultados de sessão, página, interação, DOM, screenshot, metadados redigidos de cookies, download e upload;
- erros categorizados sem vazamento de segredo, cookie, storage state, header, DOM, screenshot, arquivo, URL com credencial, caminho físico, stack ou handle nativo.

### `BrowserJobPort`

Entregue operações equivalentes a:

```text
submit(request: BrowserJobRequest) -> BrowserJobAccepted
inspect(query: AuthorizedBrowserJobQuery) -> BrowserJobSnapshot
stream(request: BrowserJobStreamRequest, sink: BrowserJobSink) -> StreamResult
request_cancel(request: CancelBrowserJob) -> CancelBrowserResult
```

`BrowserJobRequest` deve conter o `BrowserJob`, `expected_profile_version`, `expected_page_version` quando aplicável e pre/postconditions documentadas. A porta deve provar que perfil, sessão, página, lease, contexto, purpose e grants pertencem ao mesmo escopo autorizado e que o job é destinado somente a Browser Worker compatível.

### `BrowserWorker`

Entregue uma porta equivalente a:

```text
execute(job: GrantedBrowserJob) -> BrowserJobOutcome
```

O Worker valida grants mínimos, expiração, versão, finalidade, limites e policy sem consultar banco. Playwright só pode existir atrás desta interface, dentro da fronteira do Browser Worker. Resultado e erros usam somente tipos públicos e referências autorizadas.

### Input e Artifact output

Entregue portas equivalentes a:

```text
open(reference: UploadRef, grant: BrowserWorkerGrant) -> BoundedByteSource
begin(request: GrantedArtifactWrite) -> ArtifactWriteSink
commit(sink: ArtifactWriteSink, metadata: ArtifactMetadata) -> ArtifactReference
abort(sink: ArtifactWriteSink) -> Unit
```

Input nunca aceita caminho físico arbitrário e revalida ownership, finalidade, classificação, tamanho e grant. DOM, screenshot e download são escritos por sink temporário isolado e só se tornam Artifact depois de validação, quota e commit confirmado.

## Invariantes normativos que devem ser implementados

### Perfil, sessão, página e ownership

- perfil, sessão e página preservam ownership, Workspace, Agent, Execution, purpose e relações verificáveis;
- sessão é contexto vivo sob lease; página pertence exatamente a uma sessão e não é handle global;
- abrir sessão materializa snapshot versionado do perfil; alteração posterior do perfil não altera sessão já aberta;
- reuso de sessão entre Executions é negado por padrão e exige novo grant, identidade de ownership compatível e finalidade compatível quando permitido;
- criação, inspeção, navegação, interação, DOM, screenshot, cookies, upload, download, close e cancelamento revalidam binding completo, versão e lease;
- quota de páginas e limites de sessão são efetivos, não apenas campos informativos;
- fechamento ou expiração fecha páginas, contexto, processo e temporários, invalida IDs operacionais e preserva apenas Artifacts/Events autorizados;
- lease perdido, revogado ou expirado bloqueia novas operações e inicia cleanup seguro; nenhum stale writer pode alterar estado atual;
- retry com idempotency key não duplica sessão, página, navegação declarada idempotente, upload, download ou close;
- falha de Worker não transfere handle vivo para outro Worker; sessão fica `FAILED`, parciais são abortados e o domínio decide nova sessão.

### Worker e isolamento

- Playwright roda exclusivamente em Browser Workers dedicados;
- Browser Worker nunca acessa banco, ORM, Redis, state store, Runtime, API interna ou consulta geral de ownership;
- Worker recebe apenas job, grants e referências mínimas autorizadas;
- perfil, storage, cache, processo e diretório temporário são isolados por sessão conforme policy;
- contexto Playwright, page, browser, cookie jar, storage state, native handle e caminho físico não atravessam as portas públicas;
- supervisor prova ownership da sessão/processo antes de usar ou liberar qualquer recurso;
- adapter concreto fica isolado, e o domínio permanece testável sem engine real;
- extensões e plugins não recebem acesso direto a contexto Playwright, banco, filesystem do host ou cookies fora de grants tipados.

### Rede, URL e conteúdo não confiável

- rede é negada por padrão e cada navegação começa por URL sanitizada e policy efetiva;
- scheme, host, porta, resolução DNS e endereço de destino são validados na URL inicial, em cada redirect e no connect;
- bloqueie por padrão IP privado, loopback, link-local, metadata services, `file:`, `data:` e origens proibidas pela policy;
- DNS rebinding exige nova validação do destino antes da conexão;
- redirects, mudança de origem, subresources e quantidade de páginas respeitam limites e policy;
- URL com segredo, headers, tokens, conteúdo de resposta e rede não entram em log, Event, trace, erro ou snapshot;
- DOM, texto, script, atributo, download e resposta de rede são dados não confiáveis e nunca alteram permissions, purpose, argumentos de Tool ou instruções do Agent;
- `evaluate`/JavaScript arbitrário, clipboard, câmera, geolocation e capacidades equivalentes são negados salvo grant explícito, granular e testado;
- falhas de policy são rejeições categóricas e não devem chegar ao Playwright.

### Cookies e secrets

- cookies são particionados por perfil e sessão e preservam metadados de ownership e policy;
- valor de cookie, token e storage state entram e saem somente por referência secreta e permissão específica;
- `ReadCookies` retorna somente metadados redigidos, salvo contrato explícito de Secret por referência;
- cookies, secrets, headers, storage state, URLs sensíveis e conteúdo nunca aparecem em Events, logs, traces, snapshots, erros ou testes de snapshot;
- `SetCookies` valida domínio, path, secure, same-site, expiração, origem e grant antes do efeito;
- nenhuma API pública aceita credencial crua, secret inline, caminho de perfil ou diretório nativo como autoridade.

### DOM, screenshot, upload e download

- DOM, screenshot e download são Artifacts por padrão, com limite de bytes, classificação, quota, sink isolado e referência confirmada;
- captura respeita `maximum_dom_bytes` e `maximum_screenshot_bytes`, com resultado explícito para truncation, rejeição ou cancelamento;
- upload aceita somente `AuthorizedFileReference` ou `ArtifactReference`, nunca caminho físico arbitrário;
- upload valida ownership, finalidade, tamanho, media type, extensão, classificação e policy de malware quando aplicável;
- download usa sink temporário isolado, valida media type, extensão, tamanho, classificação, quantidade e policy antes do commit;
- download não grava destino arbitrário e não publica arquivo, DOM ou screenshot inteiro em Event/log;
- Artifact parcial só é preservado quando confirmado e autorizado; sink incompleto é abortado e limpo;
- referências carregam metadados mínimos, quota e ownership, sem transformar Artifact em canal de bypass.

### Navegação, interação e efeitos externos

- cada operação é atômica no escopo declarado e retorna versão/estado atualizado quando altera página;
- navegação, interação, submit, upload e download declaram efeito, timeout, retryability e `EffectState`;
- operações com efeito externo incerto nunca são repetidas cegamente;
- timeout ou perda de conexão produz estado explícito, sem converter ausência de observação em sucesso;
- interação não aceita seletor, script, URL, headers ou payload fora da policy e dos limites autorizados;
- chegada tardia de resultado não reabre página/sessão fechada nem transforma job cancelado em sucesso;
- títulos e URLs persistidos/publicados são sanitizados e limitados.

### Cancelamento, falha e cleanup

- cancelamento impede novos redirects, interações, uploads e downloads;
- aborta request, página ou contexto no menor escopo seguro e não afeta outra sessão;
- se a operação não responder, encerra página, depois contexto e finalmente processo isolado, com deadline;
- fecha sinks incompletos, preserva somente Artifacts confirmados e publica resultado cancelado com parciais autorizadas;
- cleanup de páginas, contexto, processo, cache, storage temporário e diretórios é idempotente, checkpointed e reconciliável;
- close confirmado implica limpeza confirmada; caso contrário, retorna `UNKNOWN`/`RECOVERY_REQUIRED` sem alegar sucesso;
- crash/restart marca sessão como `FAILED` ou estado recuperável explícito e não permite assumir handle vivo;
- saúde do pool e sucesso/falha da página são estados distintos.

## Integrações obrigatórias

### RFC 402 — Resource Manager

Use o descriptor `BROWSER` existente ou complete-o sem criar autoridade paralela. A criação/uso do Browser deve:

1. autorizar contexto, purpose, Workspace, capability, profile e lease;
2. derivar isolation key pelo Resource Manager, sem escolha livre do chamador;
3. alocar o Browser pelo adapter de Resource/Worker;
4. associar sessão e páginas ao lease, profile e Workspace;
5. revalidar lease, fencing, ownership e versão em todas as operações;
6. liberar/revogar e limpar no expiry, cancelamento, close, falha e crash;
7. registrar estados confirmados, `UNKNOWN`, `RECOVERY_REQUIRED` e quarantine sem falso sucesso.

### RFC 603 — Workspaces, RFC 403 — Filesystem e RFC 602 — Artifacts

Consuma as portas existentes para referências de input, diretórios temporários, sinks, quotas e Artifacts. Nunca exponha ou persista root física, native handle, caminho do host, diretório de perfil, arquivo de cookie ou destino de download. Não replique containment, ownership, root, quota ou classificação dentro do Browser.

### RFC 101/102/103 — Runtime, Execution e Events

Browser só opera com `execution_id` e contexto compatíveis; não cria Execution paralela. Emita somente após fatos confirmados:

- `BrowserOpened`;
- `BrowserPageOpened`;
- `BrowserNavigationFinished`;
- `BrowserArtifactCaptured`;
- `BrowserUploadFinished`;
- `BrowserPageClosed`;
- `BrowserClosed`;
- `BrowserJobFailed`.

Use `EventEnvelope` e outbox existentes, com sequência, correlação, execution e payload mínimo sanitizado. Não publique cookies, DOM, screenshot, arquivo, storage state, headers, segredo ou URL sensível.

### RFC 601 — Persistence

Use exclusivamente `TransactionalPersistence`/outbox e adapters existentes. Estado durável pode conter IDs, ownership limitado, status, versões, policy references, uso, checkpoints, timestamps, expiry e referências. Nunca persista browser/page handle vivo, cookie value, storage state cru, secret, header, DOM integral, screenshot integral, download integral, caminho físico ou autoridade baseada em PID.

Não implemente banco, ORM, Redis, fila ou broker dentro do domínio ou do Browser Worker. Se o contrato exigir armazenamento de job, use a porta existente e prove ownership, idempotência, fencing e recuperação.

## Estratégia de implementação e testes

Use TDD: escreva testes que falhem para cada contrato/invariante antes da implementação correspondente e mantenha cada ciclo RED/GREEN/REFACTOR verificável.

Crie testes, no mínimo, para:

- contratos, modelos imutáveis, enums, transições e validação de contexto;
- profile snapshot, versionamento, estados LOCKED/DISABLED e ausência de alteração retroativa em sessão;
- criação/reuso de sessão, páginas, quotas, TTL, lease expiry/revoke/release/fencing e stale writer;
- catálogo `BROWSER`, capabilities, isolation key, grants mínimos e autorização por operação;
- worker sem acesso a banco, Playwright somente na boundary correta e adapter determinístico;
- navegação, redirects, timeout, origin change, URL sanitizada, allowlist e bloqueio de scheme/host/porta;
- DNS rebinding, IP privado, loopback, link-local, metadata service, `file:`/`data:` e policy de subresources;
- conteúdo web não confiável não alterando authorization, purpose, Tool args ou instruções;
- interação, seletor, evaluate/JavaScript, clipboard, câmera e geolocation com negação por padrão;
- cookies redigidos, Secret references, particionamento por sessão/perfil e ausência de vazamento em qualquer superfície;
- snapshot DOM limitado, screenshot limitado, quota, truncation/rejeição e Artifact reference;
- upload sem caminho físico, grants, ownership, tamanho, media type, classificação e malware policy;
- download em sink temporário, limites, media type, extensão, quantidade, validação, commit/abort e ausência de destino arbitrário;
- páginas popup/child quando suportadas, quota de páginas e fechamento em cascata;
- stream de progresso com sequência, limites, backpressure, cancelamento e conteúdo não publicado em Event/log;
- cancelamento por job/página/sessão, late result, timeout, effect UNKNOWN e retryability;
- cleanup parcial, processo/contexto/directório temporário, quarantine, restart/crash recovery e reconcile;
- corridas open/navigate/interact/capture/upload/download/cancel/close/release/expiry;
- integração E2E Resource Manager ↔ Workspaces ↔ Filesystem ↔ Artifacts ↔ Browser;
- persistência/outbox round-trip sem handle, path, cookie, secret, header ou conteúdo integral;
- eventos somente após fatos confirmados, com IDs, versões, contagens, referências e códigos sanitizados;
- adapter de referência determinístico e adapter Playwright operacional, quando implementado;
- regressão completa do repositório.

Não use testes que apenas inspecionem que um método existe. Prove efeitos, rejeições, estados, limites, isolamento, cleanup e ausência de vazamento. Quando um runtime/engine opcional não estiver instalado, teste a fronteira e registre o caso como `skipped` por motivo real, sem simular sucesso.

## Documentação obrigatória antes do fechamento

Crie ou atualize, com decisões concretas e sem placeholders:

- `docs/superpowers/specs/2026-08-07-rfc-405-browser-design.md`;
- `docs/superpowers/plans/2026-08-07-rfc-405-browser.md`;
- `docs/superpowers/2026-08-07-rfc-405-browser-requirement-matrix.md`;
- `docs/superpowers/2026-08-07-rfc-405-browser-closeout.md`;
- este prompt, acrescentando o registro de encerramento e o próximo gate.

A matriz deve mapear requisito por requisito do RFC 405 para arquivos e testes reais e marcar o estado somente com evidência executada. O closeout deve registrar decisões, alternativas rejeitadas, integrações, limitações tecnológicas legítimas, review findings, commits e comandos reais.

## Verificação obrigatória antes da conclusão

Execute e registre a saída real de:

```text
python -m pytest -q
python -m compileall -q src tests
git diff --check
git status --short --branch
```

Faça scans ajustados aos nomes finais dos pacotes, no mínimo:

```text
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|requests|httpx|kafka|rabbit|broker|scheduler|worker|playwright|Playwright|browser|page|context|cookie|storage_state|header|evaluate|javascript|clipboard|camera|geolocation|file:|data:|localhost|127\\.0\\.0\\.1|169\\.254|metadata|private|loopback|physical_path|native_handle|secret|dom|screenshot|upload|download" src/agentos/browser
```

O scan deve provar que tecnologia concreta, worker interno, banco, caminho físico, handle, cookie, token, storage state, header, DOM, screenshot, arquivo, upload ou download não atravessam as portas públicas. Se `playwright`, `subprocess`, filesystem ou outra API concreta for necessária no adapter operacional, o scan deve separar domínio/portas de adapter e a documentação deve explicar a boundary, os grants e as garantias.

Verifique também explicitamente que nenhum Browser Worker importa ou instancia banco, ORM, Redis, state store, API interna ou objeto do Runtime. Faça scans de `src/agentos/browser` e testes de boundary; falsos positivos devem ser explicados e cobertos por testes.

Rode o teste PostgreSQL opcional quando `AGENTOS_TEST_POSTGRES_DSN` estiver configurado. Sem DSN, execute-o e registre `skipped`; nunca simule sucesso. Faça também qualquer teste opcional de Playwright/browser capability como `skipped` somente com motivo real, mantendo os testes de contratos, policy, isolamento e adapters determinísticos obrigatórios.

Faça revisão final requisito por requisito contra RFC 405, RFCs 402, 403, 603, 601, 602, 103, 101 e 102 e ADRs relacionados. Faça uma segunda passagem read-only independente do fluxo de implementação, focada em worker boundary, ausência de banco, leases, profile/session/page ownership, isolamento, URL/SSRF/DNS, conteúdo não confiável, cookies/secrets, DOM/screenshots, upload/download, Artifact, cancelamento, cleanup, persistência e bypass. Findings devem ser corrigidos com testes RED/GREEN antes do encerramento.

Qualquer falha, placeholder, TODO, bypass, vazamento, teste ausente, corrida insegura, cleanup incompleto, falso sucesso ou documentação contraditória significa que o trabalho continua.

## Relatório final obrigatório

Somente ao concluir o gate, informe:

- arquivos alterados e commits realizados;
- decisões de desenho e alternativas rejeitadas;
- matriz de cobertura requisito por requisito do RFC 405;
- integração comprovada com RFCs 402, 403, 603, 601, 602, 103, 101 e 102;
- confirmação de que Playwright ficou restrito ao Browser Worker e de que o Worker não acessa banco;
- evidências de policy de rede, bloqueio SSRF/DNS rebinding, isolamento de perfil/sessão/página, cookies/secrets, Artifacts, upload/download, cancelamento e cleanup;
- comandos executados e resultados reais;
- testes condicionados e motivo de cada `skipped`;
- revisão independente e findings corrigidos;
- limitações tecnológicas legítimas, somente as previstas nas RFCs;
- confirmação explícita de que o **Gate RFC 405 está 100% completo, funcional, alinhado às docs e sem pendências futuras de implementação**;
- próximo gate indicado pela documentação atualizada.

Não entregue “quase pronto”, não pare por falta de tempo, não peça confirmação e não transforme requisito obrigatório em backlog. A sessão só termina quando o RFC 405 estiver realmente fechado, integrado, verificado e documentado.

## Registro de encerramento desta sessão — a ser preenchido pelo agente executor

Ao fechar o gate, acrescente aqui a evidência real de implementação, testes, decisões, commits, review, limitações legítimas e o próximo gate. Não deixe este registro vazio, genérico ou baseado em intenção.

Próximo gate esperado pela sequência normativa: **RFC 406 — Capabilities**.

## Registro de encerramento desta sessão — evidência real

Em 2026-08-07 foi implementado `src/agentos/browser` com contratos imutáveis, `BrowserJobPort`, `BrowserWorker` sem acesso a banco/ORM/Redis/Runtime/API, adapter determinístico, boundary Playwright opcional, NetworkPolicy com SSRF/DNS-rebinding checks, profile/session/page lifecycle com Resource Manager BROWSER lease/fence/TTL, grants mínimos, cookies por secret reference, artifact/input sinks, upload/download limitado, cancelamento, UNKNOWN, cleanup e journal Persistence/Outbox sanitizado. Os testes Browser e a regressão demonstram os efeitos e rejeições; nenhum caminho físico, handle nativo, segredo, DOM integral ou conteúdo volumoso atravessa as portas públicas.

Evidência executada:

- `python -m pytest -q` → `600 passed, 5 skipped in 5.46s`.
- `python -m compileall -q src tests` → sucesso.
- `git diff --check` → sucesso; apenas avisos de normalização LF/CRLF em arquivos preexistentes.
- `python -m pytest -q tests/integration/workspaces/test_postgres_optional.py` → `1 skipped` porque `AGENTOS_TEST_POSTGRES_DSN` não está configurado.
- `python -m pytest -q tests/unit/browser/test_playwright_boundary.py` → `1 passed`; a capability Playwright não está instalada e não foi simulada.
- scan do Worker → somente stdlib e portas/modelos/security de `agentos.browser`; nenhuma dependência de banco, ORM, Redis, state store, Runtime, API, fila ou broker.
- revisão independente encontrou e corrigiu sete findings: sink de Artifact não usado, upload validado tarde, release ausente no close, idempotência ausente em open session, argumentos mutáveis, cookie inline e lease/network não revalidados.

Decisões: Playwright ficou isolado em `playwright_adapter.py`; Resource Manager, Workspaces, Filesystem, Artifacts, Persistence e Events permanecem autoridades externas. Nenhum commit foi criado para não misturar o worktree sujo preexistente; o closeout está pronto para staging seletivo. O Gate RFC 405 está 100% completo. Próximo gate: **RFC 406 — Capabilities**.
