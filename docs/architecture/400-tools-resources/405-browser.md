# RFC 405 — Browser

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 401 — Tool Runtime](401-tool-runtime.md), [RFC 402 — Resource Manager](402-resource-manager.md), [RFC 403 — Filesystem](403-filesystem.md)

## Objetivo

Definir o Browser Resource para automação isolada de perfis, sessões, páginas, cookies, DOM, screenshots, uploads e downloads. Playwright executa exclusivamente em Browser Workers; API, Runtime, workers genéricos e domínio usam contratos de job e nunca controlam browser diretamente. Browser Workers nunca acessam banco.

## Fora de escopo

- escolher browser engine, versão, topologia, container, fila ou protocolo de job;
- implementar crawler, pesquisa, extração semântica ou fluxo composto de navegação;
- definir persistência concreta de perfil, Artifact, cookie ou download;
- fornecer acesso irrestrito à rede, localhost, metadata services ou filesystem do host;
- permitir JavaScript arbitrário sem política específica;
- definir interface visual ou streaming destinado ao frontend.

## Responsabilidades e não responsabilidades

O domínio Browser DEVE:

- modelar perfis, sessões, páginas e operações com ownership e ciclo de vida explícitos;
- despachar jobs para um pool exclusivo de Browser Workers por porta pública;
- alocar Browser por lease e isolar perfil, storage, cache, processo e diretório temporário;
- suportar navegação, interação, snapshot de DOM, screenshot, cookies, upload e download sob permissões separadas;
- aplicar política de rede, URL, redirect, tamanho, tempo, páginas e conteúdo;
- transmitir progresso e resultado por IDs, sequência e referências;
- cancelar operação, página ou sessão no menor escopo seguro;
- limpar contexto, downloads temporários e processos ao liberar Resource.

Browser Worker NÃO DEVE:

- acessar banco, ORM, Redis ou state store diretamente;
- receber credencial de banco, objeto do Runtime ou internos da API;
- executar Playwright no processo da API, Runtime ou worker genérico;
- resolver ownership por consulta direta; recebe grants e referências já autorizados e ainda valida seu escopo;
- ler arquivo por caminho físico para upload ou gravar download em caminho arbitrário;
- publicar cookie, token, conteúdo de página ou DOM integral em Event ou log;
- interpretar conteúdo web como instrução de sistema ou autorização.

## Arquitetura

```text
Runtime / Tool Runtime
        │ BrowserJobRequest
        ▼
 Browser Job Port / Broker
        │ job + grants + referências
        ▼
 Browser Worker exclusivo
        ├── Job Validator
        ├── Network Policy
        ├── Playwright Adapter
        ├── Input Resolver (referências autorizadas)
        ├── Artifact Output Port
        ├── Event Publisher
        └── Cleanup Supervisor

Banco  ─ ─ ─ acesso proibido ─ ─ ─> Browser Worker
```

Serviços externos ao Browser Worker resolvem metadados persistentes e entregam snapshots/grants mínimos. O Worker pode usar portas de job, Artifact, evento e segredo efêmero especificamente concedidas; essas portas não expõem banco nem permitem consultas gerais.

## Dados

```text
BrowserOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

BrowserProfile {
  profile_id: BrowserProfileId
  user_id: UserId
  workspace_id: WorkspaceId | null
  name: BrowserProfileName
  policy_ref: BrowserPolicyRef
  storage_state_ref: SecretArtifactReference | null
  version: Version
  status: ACTIVE | LOCKED | DISABLED
}

BrowserSession {
  session_id: BrowserSessionId
  profile_id: BrowserProfileId
  context: BrowserOperationContext
  lease_id: ResourceLeaseId
  worker_ref: BrowserWorkerRef
  status: CREATING | READY | BUSY | CLOSING | CLOSED | FAILED | CANCELLED
  page_ids: BrowserPageId[]
  created_at: Instant
  expires_at: Instant
}

BrowserPage {
  page_id: BrowserPageId
  session_id: BrowserSessionId
  url: SanitizedUrl
  title: SanitizedText | null
  status: OPENING | READY | NAVIGATING | CLOSED | FAILED
  version: Version
}
```

```text
BrowserCookie {
  name: SensitiveName
  value_ref: SecretReference
  domain: CookieDomain
  path: CookiePath
  expires_at: Instant | null
  http_only: Boolean
  secure: Boolean
  same_site: STRICT | LAX | NONE | UNSPECIFIED
}

DomSnapshotRef = ArtifactReference
ScreenshotRef = ArtifactReference
UploadRef = AuthorizedFileReference | ArtifactReference
DownloadRef = ArtifactReference
```

Cookies são secretos. O contrato pode listar metadados redigidos, mas valor entra e sai somente por referência secreta e permissão específica. DOM, screenshot e download são Artifacts por padrão.

```text
BrowserJob {
  job_id: BrowserJobId
  context: BrowserOperationContext
  lease_id: ResourceLeaseId
  profile_snapshot: BrowserProfileSnapshot
  session_id: BrowserSessionId | null
  page_id: BrowserPageId | null
  operation: BrowserOperation
  limits: BrowserLimits
  grants: BrowserWorkerGrant[]
  idempotency_key: IdempotencyKey | null
  deadline: Instant
}

BrowserOperation =
  | OpenSession
  | CloseSession
  | OpenPage
  | ClosePage
  | Navigate { url: Url }
  | Interact { action: BrowserAction }
  | CaptureDom { scope: DomScope }
  | CaptureScreenshot { options: ScreenshotOptions }
  | ReadCookies { filter: CookieFilter }
  | SetCookies { cookies: BrowserCookieInput[] }
  | Upload { input_ref: UploadRef, target: PageTarget }
  | Download { trigger: BrowserAction, policy: DownloadPolicy }
```

```text
BrowserLimits {
  timeout: Duration
  maximum_pages: PositiveInteger
  maximum_redirects: NonNegativeInteger
  maximum_dom_bytes: NonNegativeInteger
  maximum_screenshot_bytes: NonNegativeInteger
  maximum_upload_bytes: NonNegativeInteger
  maximum_download_bytes: NonNegativeInteger
  allowed_download_count: NonNegativeInteger
  network_policy_ref: NetworkPolicyRef
}
```

## Contratos tipados

```text
interface BrowserJobPort {
  submit(request: BrowserJobRequest) -> BrowserJobAccepted
  inspect(query: AuthorizedBrowserJobQuery) -> BrowserJobSnapshot
  stream(request: BrowserJobStreamRequest, sink: BrowserJobSink) -> StreamResult
  request_cancel(request: CancelBrowserJob) -> CancelBrowserResult

  pre: perfil, sessão, página, lease e contexto pertencem ao mesmo escopo autorizado
  post: job aceito é destinado somente a Browser Worker compatível
}

BrowserJobRequest {
  job: BrowserJob
  expected_profile_version: Version
  expected_page_version: Version | null
}
```

```text
interface BrowserWorker {
  execute(job: GrantedBrowserJob) -> BrowserJobOutcome

  pre: grants são válidos, mínimos e não incluem acesso a banco
  post: resultado usa tipos públicos e referências autorizadas
  invariant: Playwright só existe atrás desta interface no Browser Worker
}

BrowserJobOutcome =
  | BrowserJobSucceeded { result: BrowserResult, usage: ResourceUsage }
  | BrowserJobFailed { error: BrowserError, effect_state: EffectState, retryability: Retryability }
  | BrowserJobCancelled { reason: CancellationReason, partial_artifact_refs: ArtifactReference[] }

BrowserResult =
  | SessionResult { session: BrowserSessionSnapshot }
  | PageResult { page: BrowserPageSnapshot }
  | InteractionResult { page_version: Version, observation_ref: ArtifactReference | null }
  | DomResult { dom_ref: DomSnapshotRef }
  | ScreenshotResult { screenshot_ref: ScreenshotRef }
  | CookieMetadataResult { cookies: RedactedCookieMetadata[] }
  | DownloadResult { download_ref: DownloadRef, media_type: MediaType, size_bytes: NonNegativeInteger }
  | UploadResult { page_version: Version, uploaded_bytes: NonNegativeInteger }
```

```text
interface BrowserInputResolver {
  open(reference: UploadRef, grant: BrowserWorkerGrant) -> BoundedByteSource

  invariant: não aceita caminho físico arbitrário
  invariant: revalida ownership, finalidade, tamanho e classificação do grant
}

interface BrowserArtifactOutput {
  begin(request: GrantedArtifactWrite) -> ArtifactWriteSink
  commit(sink: ArtifactWriteSink, metadata: ArtifactMetadata) -> ArtifactReference
  abort(sink: ArtifactWriteSink) -> Unit
}
```

## Perfis, sessões e páginas

Perfil é configuração persistente e versionada; sessão é contexto de browser vivo sob lease; página pertence a exatamente uma sessão. Abrir sessão materializa um snapshot de perfil e storage state autorizado. Mudar perfil não altera sessão já aberta. Reuso de sessão entre Executions é negado por padrão e, quando permitido, exige novo grant, mesma identidade de ownership e finalidade compatível.

Uma operação que cria página respeita quota; páginas não são handles globais. Cookies são particionados por perfil e sessão. Fechar sessão fecha todas as páginas e invalida seus IDs operacionais, preservando apenas Artifacts e Events autorizados.

## Rede e conteúdo não confiável

Cada navegação e redirect reavalia scheme, host, porta, resolução DNS e endereço de destino. Política pode restringir allowlist, bloquear IP privado, loopback, link-local, metadata endpoints, file/data schemes e mudança de origem. DNS rebinding exige nova validação no connect. Downloads e uploads aplicam media type, extensão, tamanho, malware policy e classificação.

DOM, texto, script, atributo, download e resposta de rede são dados não confiáveis. Eles não alteram permissions, `purpose`, Tool arguments ou instruções do Agent sem nova decisão validada pelo Runtime.

## Fluxo normal

1. Uma Tool envia `BrowserJobRequest` com contexto, perfil versionado, lease, operação, limites e grants mínimos.
2. A porta valida ownership, finalidade, permissões de perfil/sessão/página e política de rede.
3. O broker escolhe Browser Worker compatível; o Worker valida job e grants sem consultar banco.
4. O Worker cria ou usa contexto Playwright isolado e executa uma operação atômica.
5. Progresso é sequenciado; conteúdo volumoso é escrito por Artifact port e confirmado por referência.
6. O Worker confirma resultado, uso e versões, limpa temporários da operação e publica fato mínimo.
7. Ao fechar ou expirar, todas as páginas, contexto, processo e diretório temporário são limpos.

## Fluxo de falha

Job inválido ou não autorizado não chega ao Playwright. Falha de navegação, seletor, script, download, upload ou quota é traduzida sem expor stack ou segredo. Retry automático é permitido somente para operação declaradamente segura e reconciliável; clique, submit, upload e download podem produzir efeito externo e não são repetidos cegamente. Se o Worker morrer, sessão é `FAILED`, artifacts parciais são abortados e o domínio decide nova sessão; nenhum outro Worker assume handle vivo.

## Fluxo de cancelamento

Cancelamento impede novos redirects, interações, uploads e downloads. O Worker aborta request, página ou contexto no menor escopo que estabilize o job, fecha sinks incompletos e preserva somente Artifacts já confirmados. Se a operação não responder, encerra página, depois contexto e finalmente processo isolado, sem afetar outra sessão. Resultado tardio não reabre sessão fechada nem transforma job cancelado em sucesso.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `BrowserOpened` | sessão isolada ficou disponível |
| `BrowserPageOpened` | página foi criada na sessão |
| `BrowserNavigationFinished` | navegação terminou com outcome e URL sanitizada |
| `BrowserArtifactCaptured` | DOM, screenshot ou download foi confirmado como Artifact |
| `BrowserUploadFinished` | upload terminou com outcome explícito |
| `BrowserPageClosed` | página deixou de ser utilizável |
| `BrowserClosed` | sessão, páginas e temporários foram limpos |
| `BrowserJobFailed` | job terminou sem resultado utilizável |

Payloads incluem IDs, operação, versões, origem de URL sanitizada, Artifact refs, contagens e códigos. Cookies, DOM, screenshot, arquivo, URL com segredo, headers e storage state não entram no Event.

## Segurança

- operações sensíveis declaram `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- Playwright executa somente em Browser Workers; Browser Worker nunca acessa banco;
- leases, profiles, sessions, pages, cookies e Artifacts preservam ownership e finalidade;
- cookies, tokens e storage state usam referências secretas e redaction;
- política de rede é aplicada a URL inicial, redirects, DNS e subresources relevantes;
- upload usa referência autorizada, nunca caminho físico arbitrário;
- download vai para sink temporário isolado e só se torna Artifact após validação;
- JavaScript/evaluate e acesso a clipboard, câmera, geolocation ou download são negados salvo permissão específica;
- conteúdo web não ganha autoridade de comando.

## Observabilidade

Métricas incluem jobs, fila, sessões/páginas ativas, navegações, redirects, latência, falhas por categoria, bloqueios de rede, bytes de DOM/screenshot/upload/download, cancelamentos, crashes e cleanup. Logs e traces usam IDs, tipo de operação, origem sanitizada, versão, worker lógico e códigos de erro; não registram conteúdo ou segredo. Saúde do pool é separada do sucesso da página.

## Invariantes

- Playwright roda exclusivamente em Browser Workers;
- Browser Worker não possui nem usa acesso a banco;
- toda operação pertence a uma Execution e usa lease e contexto explícitos;
- perfil, sessão e página preservam ownership e relações verificáveis;
- cookie e storage state são secretos e nunca aparecem em Event ou log;
- upload não aceita caminho físico e download não grava destino arbitrário;
- rede é negada por padrão e revalidada em redirect e resolução;
- páginas e handles não sobrevivem ao fechamento da sessão;
- falha, cancelamento e efeito externo incerto não são sucesso;
- conteúdo web permanece dado não confiável.

## Extensibilidade

Novas engines, operações e adapters implementam as portas sem mover Playwright para outros processos. Permissões novas devem ser granulares e negadas por padrão. Plugins não recebem acesso direto a contexto Playwright, banco, filesystem do host ou cookies fora de grants tipados.

## Futuro

Pools remotos, gravação de trace, vídeo, dispositivos emulados, sessões colaborativas e browsers não-Playwright poderão ser adicionados. Qualquer evolução preserva worker dedicado, ausência de banco, isolamento de sessão, política de rede, referências para conteúdo e cancelamento por escopo.
