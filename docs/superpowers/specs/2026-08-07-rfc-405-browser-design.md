# RFC 405 Browser — Design de Fechamento

**Data:** 2026-08-07
**Status:** implementado e verificado pelo Gate RFC 405
**Próximo gate:** RFC 406 — Capabilities

## Objetivo

Entregar um Browser Resource seguro e technology-neutral que consuma Resource Manager, Workspaces, Filesystem, Artifact Storage, Events/Outbox e Persistence somente por portas. O domínio não executa Playwright nem concede autorização por IDs; um Browser Worker dedicado recebe jobs autorizados, valida grants e usa apenas adapters de engine isolados.

## Decisões

1. `agentos.browser` é o pacote público final. Modelos são `dataclass(frozen=True, slots=True)` e referências de conteúdo não carregam bytes, paths, handles ou segredos.
2. `BrowserService` é a autoridade de profile/session/page/job, ownership, lease, fencing, quota, TTL, idempotência e cleanup. Ele mantém um estado em memória para o adapter determinístico; a `BrowserPersistenceJournal` grava apenas snapshots sanitizados na `TransactionalPersistence` existente.
3. `ReferenceBrowserAdapter` simula um engine sem rede real, processo ou filesystem. Ele prova navegação, redirects, DOM, screenshot, cookie metadata, upload/download, cancelamento e efeitos `UNKNOWN`.
4. `BrowserWorker` recebe `GrantedBrowserJob`, `BrowserInputResolver`, `BrowserArtifactOutput` e `SecretReferencePort`. Não importa nem instancia banco, ORM, Redis, state store, API, Runtime ou Resource Manager. A boundary opcional `PlaywrightBrowserAdapter` é um protocolo separado; nenhum tipo Playwright atravessa as portas.
5. A política de rede nega por padrão, rejeita `file:`/`data:`, loopback, link-local, RFC1918, metadata service e portas não permitidas, e revalida destino inicial, redirect e resolução DNS. DNS é injetado por porta para testes de rebinding.
6. DOM, screenshot e download passam por `BrowserArtifactOutput`; staging incompleto é abortado. Upload só usa `AuthorizedFileReference`/`ArtifactReference`; caminhos físicos não existem nos contratos.
7. Eventos são mínimos e só emitidos após confirmação do fato. A journal usa a `TransactionalPersistence` existente e a outbox quando fornecida; nenhuma API pública do Browser publica payload volumoso.
8. Cookies usam `SecretReference`; `ReadCookies` retorna metadados redigidos. `SetCookies` exige grant granular e valida domínio, path, secure, same-site, expiração e origem.

## Limites e estados

Profiles são versionados e podem estar `ACTIVE`, `LOCKED` ou `DISABLED`. Sessions são vivas sob lease, vinculadas ao contexto completo e passam por `CREATING → READY/BUSY → CLOSING → CLOSED`; falha/crash termina em `FAILED` e cancelamento em `CANCELLED`. Pages pertencem a uma única session e invalidam operações após `CLOSED`/`FAILED`. Todas as operações retornam `BrowserJobOutcome`, `EffectState`, `Retryability`, uso e referências autorizadas.

## Fluxo

```text
BrowserJobRequest
  -> BrowserService: contexto/profile/session/page/lease/version/grant
  -> Resource Manager: descriptor BROWSER e lease/fence
  -> BrowserWorker: GrantedBrowserJob mínimo
  -> adapter de referência ou engine isolada
  -> ArtifactOutput/InputResolver/SecretReferencePort
  -> outcome + snapshot + evento sanitizado
```

O serviço valida preconditions e idempotência antes de dispatch. O worker não faz consulta de ownership; valida somente o grant e o snapshot recebido. Resultado tardio não altera job cancelado ou sessão fechada. Cleanup cascata páginas, temporários, staging e processo lógico; falha de cleanup é `UNKNOWN`/`RECOVERY_REQUIRED`.

## Segurança

Conteúdo web é dado não confiável e nunca altera purpose, grants, argumentos de Tool ou policy. `evaluate`, JavaScript arbitrário, clipboard, câmera e geolocation exigem permissões separadas e são negados pelo default. URLs, títulos, erros, eventos e persistence passam por sanitização e limites. Reuso entre executions é negado salvo grant explícito, binding compatível e nova fence.

## Testes

Os testes cobrem contratos imutáveis, contexto, transitions, profile snapshot, quotas, lease expiry/revoke/fence, worker boundary, rede/SSRF/DNS rebinding, conteúdo não confiável, cookies/secrets, artifacts, upload/download, stream/backpressure, cancelamento, `UNKNOWN`, cleanup/reconcile, concorrência e journal/outbox. O adapter Playwright real é uma boundary opcional: quando a dependência não estiver instalada, o teste é `skipped` com motivo; o comportamento normativo continua coberto pelo adapter determinístico.

## Alternativas rejeitadas

- Playwright na API/Runtime ou worker genérico: viola ADR 004 e mistura handles/processos não confiáveis com o Kernel.
- Banco/ORM no Browser Worker: viola RFC 405/601 e transforma automação em autoridade de ownership.
- Paths físicos em upload/download: viola RFC 403/603/602 e permite bypass de containment.
- Payload integral em Events/snapshots: viola classificação, outbox e limites de observabilidade.
- Rede liberada por default ou validação somente lexical: não protege contra SSRF, redirect ou DNS rebinding.
