# ADR 008 — Artifact Storage como porta substituível

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS produz e recebe uploads, downloads, screenshots, DOM, logs e resultados que podem ser grandes, sensíveis, não confiáveis e sujeitos a retenção. Guardar seus bytes em Events, Context, logs ou no banco transacional comprometeria desempenho, limites de token, recuperação e confidencialidade. Ao mesmo tempo, tornar bucket, path, URL assinada ou credencial a identidade pública do arquivo acoplaria todas as camadas ao backend e abriria caminhos de acesso fora da policy.

Conteúdo e metadata têm falhas distintas: bytes podem estar indisponíveis, incompletos, órfãos ou corrompidos enquanto a identidade, ownership, estado, quota e auditoria permanecem duráveis. O sistema precisa tratar staging, checksum, publicação, leitura, cancelamento e limpeza como protocolos recuperáveis, não como uma única gravação infalível.

## Decisão

Adotar **`ArtifactStorage` como porta substituível para bytes**, sob controle de um `ArtifactManager` proprietário de autorização, namespace, quota, metadata, referência e lifecycle. PostgreSQL registra metadata transacional, vínculo de ownership, estado e referência interna; o adapter materializa staging, escrita, selagem, leitura, verificação e remoção de objetos opacos. Nenhuma API pública expõe bucket, path, URL permanente, credencial ou handle nativo.

Um Artifact nasce em `STAGING`, recebe bytes em stream limitado e só se torna `AVAILABLE` quando tamanho, checksum calculado por componente confiável, política, quota e metadata são confirmados por protocolo idempotente. `ArtifactReference` é opaca, versionada, limitada por finalidade e reautorizada na resolução; não contém bytes nem transfere ownership. Conteúdo corrompido, staging incompleto, órfão ou em quarentena nunca é servido como Artifact válido.

O adapter inicial pode usar filesystem local, mas filesystem, object storage, armazenamento remoto ou outro backend só entram por capabilities declaradas da mesma porta. Falha incerta exige inspeção, `verify` ou reconciliação; não há publish, delete ou retry destrutivo cego.

## Consequências

### Benefícios

- Separa conteúdo volumoso da autoridade transacional e evita vazar backend para Runtime, API, Browser, Tools e clientes.
- Suporta uploads, downloads, screenshots, logs e resultados com a mesma semântica de ownership, integridade, quota e retenção.
- Permite trocar backend e adicionar capacidades como multipart, leitura por faixa ou tiering sem alterar referências públicas.
- Preserva recuperação após falhas parciais ao distinguir `STAGING`, `AVAILABLE`, quarentena, remoção e objetos órfãos.

### Custos e falhas aceitas

- Exige protocolos de staging, checksum, quota, metadata, referências, retenção e reconciliação, além de monitoramento de capacidade e órfãos.
- Backend pode indisponibilizar bytes, perder staging, retornar erro ambíguo, atrasar delete ou divergir de checksum; consumidores recebem erro explícito, não conteúdo alternativo.
- Publicação não é transação distribuída com o backend: falhas entre selagem e metadata podem produzir trabalho pendente de inspeção.
- Integridade não significa conteúdo seguro ou benigno; política de media type, classificação, malware e DLP continuam necessárias.

### O que esta decisão não resolve

Esta decisão não escolhe fornecedor, região, CDN, interface HTTP de upload, preview, antivírus, deduplicação física, content-addressing ou política de compartilhamento. Ela não substitui Filesystem Resource, Workspace, Memory, banco transacional ou autorização por recurso.

## Alternativas consideradas

- **Armazenar bytes no PostgreSQL:** rejeitada como padrão por aumentar volume, custos e contenção no store que preserva estado transacional.
- **Expor paths ou URLs do backend como identidade:** rejeitada porque acopla consumidores, enfraquece reautorização e permite persistir detalhes de acesso.
- **Acoplar Artifact ao filesystem local permanentemente:** rejeitada porque impede substituibilidade, separação de responsabilidades e evolução operacional.
- **Publicar antes da verificação de checksum:** rejeitada porque transformaria conteúdo parcial ou corrompido em resultado observável.

## Relações com RFCs

- [RFC 602 — Artifact Storage](../architecture/600-platform-data/602-artifact-storage.md) define a porta, lifecycle, integridade, referências, quotas e cleanup.
- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) define metadata durável, outbox e recuperação.
- [RFC 603 — Workspaces](../architecture/600-platform-data/603-workspaces.md) define ownership e limites de projeto para Artifacts.
- [RFC 405 — Browser](../architecture/400-tools-resources/405-browser.md) produz DOM, screenshots e downloads por referências autorizadas.
- [RFC 403 — Filesystem](../architecture/400-tools-resources/403-filesystem.md) separa acesso a arquivos de conteúdo durável publicado.
- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define classificação, autorização e isolamento por tenancy.
