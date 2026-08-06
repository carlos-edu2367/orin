# ADR 005 — Workspaces locais isolados na primeira versão

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS precisa oferecer um espaço persistente de projeto para Tools, terminal, agentes e recursos sem aceitar que o chamador escolha uma raiz física. Arquivos do Workspace podem conter dados sensíveis, entradas não confiáveis, links e processos concorrentes; uma conveniência de caminho local não pode permitir traversal, symlink/junction escape, corrida de troca de raiz ou acesso entre projetos.

No lançamento, a necessidade é uma implantação local simples, com custos e operação menores que uma camada de volumes ou storage compartilhado. Ainda assim, Workspaces já precisam sobreviver a reinícios, ter lifecycle, quotas, locks, limpeza recuperável e uma identidade lógica independente do diretório físico para que a arquitetura possa evoluir.

## Decisão

Adotar **roots locais isoladas por Workspace** como implementação inicial do Workspace Resource. O `WorkspaceManager` cria e mantém a identidade e ownership duráveis antes de provisionar uma raiz. Somente o resolvedor autorizado mapeia `workspace_id` para `root_ref` opaca e identidade canonicalizada; clientes, Agents, Events, Providers e logs não recebem paths físicos.

Operações sobre Workspace existente usam contexto com `user_id`, `workspace_id`, `agent_id`, `execution_id`, correlação e finalidade. A criação usa o `CreateWorkspaceContext` de bootstrap, que carrega `user_id` e demais contexto operacional, mas admite `workspace_id` prévio nulo para que o servidor aloque a nova identidade; essa exceção não autoriza referenciar, alterar ou acessar Workspace existente. Filesystem, terminal e Browser trabalham por referências e paths relativos validados sob a raiz concedida. A implementação impede `..`, drives, UNC, device namespaces, symlinks, junctions, reparse points e trocas de raiz que escapem do Workspace. Estados administrativos, quotas, leases, locks e limpeza preservam versão durável; um lock efêmero apenas coordena, nunca prova ownership.

O layout local é um adapter inicial, não contrato público. Backup, replicação, volume remoto ou provisioning em containers poderão substituir a materialização da raiz sem mudar a identidade do Workspace nem a autorização por operação.

## Consequências

### Benefícios

- Entrega uma base operacional simples para desenvolvimento e lançamento inicial, sem exigir storage distribuído ou montagem remota.
- Dá a cada projeto uma fronteira física e lógica explícita, útil para quota, auditoria, limpeza e concessão de recursos.
- Mantém Tools e Resources independentes de paths do host, favorecendo migração futura de backend.
- Faz o isolamento por usuário e Workspace existir mesmo no modo single-user.

### Custos e falhas aceitas

- A capacidade fica limitada ao disco, disponibilidade, backup e recuperação da máquina ou volume local; não há alta disponibilidade, replicação multi-host nem mobilidade automática.
- Crash, queda de energia, corrupção, permissões do host, antivírus, handles abertos e falha durante provisionamento ou remoção podem deixar roots órfãs, incompletas ou em `DELETING` até reconciliação.
- Caminhos locais exigem canonicalização por acesso e defesas específicas da plataforma contra links e condições de corrida; checar somente strings é insuficiente.
- Quotas, locks e limpeza administrativa adicionam accounting, monitoramento de espaço, checkpoints e procedimentos de restauração testados.

### O que esta decisão não resolve

Esta decisão não define VCS, sincronização, colaboração, editor, backup externo, compartilhamento entre usuários ou billing. Ela não converte filesystem em Artifact Storage, não permite montar diretórios arbitrários e não oferece garantia de disponibilidade distribuída.

## Alternativas consideradas

- **Filesystem compartilhado ou object storage como Workspace desde o início:** adiado; adiciona custo, latência, semântica de lock e operação antes da necessidade inicial, mas pode implementar a resolução de raiz futura.
- **Um diretório global para todos os projetos:** rejeitado porque enfraquece quotas, lifecycle, isolamento e recuperação por Workspace.
- **Aceitar um path fornecido pelo usuário ou pela Tool:** rejeitado por traversal, acesso indevido ao host e confusão entre identidade lógica e localização física.
- **Usar apenas lock Redis para proteger a raiz:** rejeitado porque expiração ou partição não preservam ownership, versão ou limpeza segura.

## Relações com RFCs

- [RFC 603 — Workspaces](../architecture/600-platform-data/603-workspaces.md) define identidade, roots opacas, lifecycle, quota e limpeza recuperável.
- [RFC 403 — Filesystem](../architecture/400-tools-resources/403-filesystem.md) define canonicalização e acesso de arquivo autorizado.
- [RFC 404 — Terminal](../architecture/400-tools-resources/404-terminal.md) vincula processos e diretórios de trabalho ao Workspace.
- [RFC 402 — Resource Manager](../architecture/400-tools-resources/402-resource-manager.md) regula leases e uso de recursos.
- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) mantém ownership e estados administrativos duráveis.
- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) exige isolamento por usuário e Workspace.
