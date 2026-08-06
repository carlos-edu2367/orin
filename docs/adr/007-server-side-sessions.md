# ADR 007 — Sessões server-side com cookie opaco e CSRF

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

A interface web precisa manter uma identidade autenticada sem expor privilégio reutilizável a JavaScript, logs, URLs ou armazenamento do navegador. A sessão deve ser revogável, expirar, carregar versão de credencial e permitir que o servidor invalide conexões ou decisões quando policy e ownership mudarem. Cookies, porém, são enviados automaticamente pelo navegador e exigem proteção contra requisições cross-site mutáveis.

O AgentOS já usa Redis como coordenação efêmera. Uma sessão pode desaparecer durante failover, expiração ou perda total desse store, mas a perda não pode ser interpretada como autorização nem permitir que o cookie passe a conter estado confiável por conta própria.

## Decisão

Usar **sessões server-side em Redis**. O navegador recebe somente um identificador opaco de sessão em cookie `HttpOnly`, `Secure` e com atributos de escopo restritos. Identidade, versão de credencial, expiração, revogação, escopos e binding operacional permanecem no store server-side; o cookie nunca contém token de acesso, `user_id`, permissão, segredo ou estado de domínio.

Toda requisição mutável autenticada por cookie exige token CSRF válido e vinculado à sessão, além de validação de origem conforme policy. A borda resolve a sessão, revalida expiração, revogação e versão antes de criar o principal; autenticação não concede acesso por si só, pois cada recurso continua sujeito à autorização por ownership, Workspace, finalidade e policy. Logout e incidente revogam a sessão server-side; streams e handles vivos são encerrados ou expiram dentro do prazo contratual.

Redis é o adapter inicial de sessão, não a fonte de verdade de usuários, políticas, ownership ou auditoria. Indisponibilidade, perda ou inconsistência do estado de sessão falha fechada para a autenticação baseada em cookie; uma sessão pode ser invalidada e o usuário precisa autenticar novamente.

## Consequências

### Benefícios

- Mantém material de autenticação sensível fora do JavaScript e reduz exposição por XSS de tokens persistentes.
- Permite revogação individual, expiração curta, invalidação por versão e contenção de incidente sem depender do navegador cooperar.
- Separa autenticação de autorização por recurso e preserva a revalidação de tenancy em cada operação.
- Reutiliza coordenação Redis já adotada, com TTL, namespace e descarte explícito.

### Custos e falhas aceitas

- Redis passa a ser dependência de disponibilidade para a sessão web; perda, evicção, failover ou partição invalidam sessões e elevam novos logins.
- A operação exige cookies corretos, CSRF, rotação de IDs, TTL, limpeza, rate limit, monitoramento de sessões e prevenção de fixation.
- Um cookie roubado ainda pode ser usado até revogação, expiração ou controles adicionais; `HttpOnly` reduz, mas não elimina, risco de endpoint, navegador ou rede comprometidos.
- O sistema não promete sessão infinita, consistência instantânea entre caches nem continuidade de stream após revogação, reinício ou falha de Redis.

### O que esta decisão não resolve

Esta decisão não escolhe mecanismo de login, MFA, OIDC, passkeys, identidade de serviço ou autorização colaborativa. Ela não substitui TLS, proteção XSS, rate limit, auditoria, PATs, autorização por recurso ou o lifecycle durável de credenciais.

## Alternativas consideradas

- **JWT ou token autocontido como sessão primária no navegador:** rejeitada porque complica revogação, rotação e redução de claims expostos, e favorece tratar o cliente como autoridade de sessão.
- **Cookie que contenha `user_id` e scopes confiáveis:** rejeitada porque transforma dado do cliente em fonte de autorização e dificulta revogação.
- **Sessão armazenada apenas no processo da API:** rejeitada porque não sobrevive a reinício, não compartilha estado entre instâncias e não oferece operação consistente.
- **Aceitar operação mutável sem CSRF:** rejeitada porque cookies são enviados automaticamente e não distinguem intenção legítima de requisição cross-site.

## Relações com RFCs

- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define sessão server-side, cookie, CSRF, revogação, auditoria e falha fechada.
- [RFC 701 — API e SSE](../architecture/700-api-security/701-api-sse.md) define a borda autenticada, streams e reautorização.
- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) separa credenciais e estado durável de coordenação efêmera.
- [RFC 801 — Workers e filas](../architecture/800-operations/801-workers.md) define recuperação quando sinais efêmeros desaparecem.
- [ADR 009 — Redis para coordenação efêmera](009-redis-for-ephemeral-coordination.md) limita Redis a estado descartável e reconstruível.
