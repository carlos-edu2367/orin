# Relatório — Task 3: agentes e orquestração

## Status

Concluída. Foram criadas somente as três RFCs solicitadas e este relatório, sem implementação de backend.

## Arquivos

- `docs/architecture/200-agents/201-agent.md`
- `docs/architecture/200-agents/202-orchestrator.md`
- `docs/architecture/200-agents/203-multi-agent.md`
- `.superpowers/sdd/2026-08-05-agentos-backend-architecture-documentation/task-3-report.md`

## Verificações

- Fundações 000, 050 e 060 e RFCs 101–104 do Kernel foram lidas antes da redação.
- As três RFCs contêm objetivo, responsabilidades, dados conceituais, contratos tipados não executáveis, eventos no passado, fluxos normal/falha/cancelamento, segurança, observabilidade, invariantes, extensibilidade, futuro e fora de escopo.
- Agent foi definido como identidade persistente independente de chat, Context, Worker e Execution; conversa ou terminal de Execution não remove nem arquiva Agent.
- Configuração de Agent cobre owner, Workspace, modelo por perfil público, prompt versionado, avatar/cor, Tools, Capabilities, Skills e Memory privada.
- Orchestrator cria e coordena Executions por portas do Kernel, sem invocar LLM, Provider ou adapters concretos; despacho contém somente identidade e referências mínimas.
- Estados permanecem os da RFC 102. Trabalho agendado/dependente só vira Execution em `QUEUED` quando elegível; espera multi-agent usa checkpoint e `PAUSED -> QUEUED` ou nova Execution de continuação.
- Eventos seguem envelope, entrega duplicável, correlação, sequência e ownership da RFC 103; nomes relatam fatos no passado.
- Context compartilhado segue a RFC 104: referências versionadas, reautorizadas e mínimas; histórico bruto e herança indiscriminada foram proibidos.
- Delegações, mensagens com processamento e trabalho assíncrono possuem Executions próprias, idempotência, deadlines, autorização, ownership, propagação de falha e auditoria.
- `user_id` é obrigatório e `workspace_id` é aplicado a entidades de projeto, inclusive no lançamento single-user.
- Links entre RFCs são relativos. Não foram definidos endpoints, schemas ORM, filas concretas ou código executável.

## Interpretações

- `PlannedWork` representa intenção, não trabalho em andamento. Essa distinção preserva tanto “tudo que realiza trabalho é Execution” quanto a semântica de `QUEUED` como tentativa já elegível.
- Espera multi-agent não ganhou estado novo. Uma espera longa suspende em limite seguro e retoma por `QUEUED`, ou produz uma Execution posterior de continuação.
- Mensagem que solicita entrega ou processamento é uma Execution atribuída ao destinatário; consulta puramente informacional e leitura de estado não criam trabalho.
- Arquivamento é terminal apenas no ciclo administrativo do Agent e preserva identidade, auditoria e referências; não equivale a exclusão física.

## Preocupações

- O primeiro Agent administrativo de uma instalação depende de um contrato futuro de bootstrap. As RFCs proíbem usar esse ponto como caminho lateral para criações de produto: toda criação de Agent visível ao domínio permanece uma Execution auditável.
- Políticas detalhadas de Memory, Skills, Workspaces, Scheduler e Artifact Storage pertencem às RFCs futuras; estes documentos usam somente referências e invariantes já definidos, sem presumir implementação.

