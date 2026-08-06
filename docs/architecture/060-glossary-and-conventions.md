# RFC 060 — Glossário e convenções

**Estado:** Fundação normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](000-overview.md), [RFC 050 — Princípios de design](050-design-principles.md)

## Objetivo

Estabelecer a linguagem ubíqua e as convenções editoriais e contratuais do acervo arquitetural do AgentOS. RFCs e ADRs DEVEM usar estes termos com o sentido definido aqui ou declarar explicitamente uma especialização compatível.

## Responsabilidades

- definir entidades e conceitos centrais sem implementar seus contratos;
- padronizar nomes, identificadores, tempo, correlação e ownership;
- normatizar referências, eventos e pseudocódigo;
- reduzir ambiguidades entre contexto temporário, memória persistente e artefatos duráveis.

## Glossário

### Agent

Identidade persistente de um processo inteligente configurável, capaz de assumir Tasks dentro de Executions. Um Agent pode possuir modelo, instruções, Tools, Capabilities, Skills e escopos de Memory, mas não é a própria execução nem o worker que a processa.

### Execution

Unidade universal, identificável e observável de trabalho no AgentOS. Toda ação executável pertence a exatamente uma Execution principal, que possui ciclo de vida, ownership, correlação, estado e resultado explícitos. Subtrabalho pode criar Executions relacionadas sem perder causalidade.

### Task

Descrição do objetivo ou trabalho desejado. Task expressa intenção; Execution representa uma tentativa concreta de realizá-la. Uma Task pode originar mais de uma Execution por nova tentativa, delegação ou agendamento, conforme a RFC responsável.

### Event

Registro imutável de um fato relevante que já ocorreu. Event não é comando nem fonte implícita de autorização. Possui no mínimo identidade, instante, origem e correlação; quando vinculado a uma Execution, possui `execution_id`.

### Tool

Operação atômica, com responsabilidade única, contrato explícito e acesso controlado a Resources. Tool não chama Tool e não coordena fluxo composto.

### Capability

Programa composto que coordena uma ou mais Tools para oferecer uma capacidade de nível superior. Capability controla sequência e decisões de composição sem conhecer detalhes internos dos adapters das Tools.

### Resource

Capacidade operacional ou dispositivo lógico acessível por porta e política, como filesystem, terminal ou browser. Resource administra ciclo de vida, limites e isolamento do recurso; não representa por si só uma intenção de trabalho.

### Workspace

Limite lógico de projeto, colaboração, arquivos, recursos, Memory e Artifacts. Entidades pertencentes a esse limite carregam `workspace_id` quando aplicável. Workspace corresponde ao filesystem na analogia de sistema operacional, mas pode abranger metadados além de arquivos.

### Context

Estado temporário montado para uma Execution, análogo à RAM. Pode incluir mensagens selecionadas, resumos, referências, resultados e instruções dentro de limites definidos. É descartável e não é fonte persistente de verdade.

### Memory

Conhecimento persistente, recuperável e governado por ownership, escopo, proveniência e retenção. Memory não é cópia automática de Context nem histórico bruto; sua gravação é uma decisão observável.

### Artifact

Conteúdo durável produzido, recebido ou referenciado pelo sistema, armazenado fora do Context por uma abstração de Artifact Storage. Exemplos conceituais incluem relatório, arquivo, screenshot e resultado volumoso. Possui identidade, ownership, metadados e referência estável, sem implicar um backend de armazenamento específico.

### Provider

Porta uniforme para uma capacidade externa, especialmente modelos. Um adapter de Provider traduz tipos, streaming, erros, autenticação e recursos proprietários para contratos públicos; particularidades do fornecedor não vazam para o Runtime ou domínio.

### StructuredHandoff

Contrato mínimo, versionado, íntegro e expirável para transferir objetivo, critérios, constraints, budget e referências autorizadas entre Executions e Agents. A RFC 303 é sua fonte canônica; `HandoffRef` aponta para o contrato sem incorporar Context nem conceder acesso transitivo.

### TransactionalPersistence

Única porta atômica de escrita durável. Confirma `DomainChange` e entradas de outbox do mesmo fato em uma transação conceitual; fachadas de domínio como `ExecutionControl` validam regras, mas não criam uma segunda fronteira de commit.

## Distinções obrigatórias

| Conceitos | Distinção normativa |
| --- | --- |
| Task × Execution | Task descreve o objetivo; Execution é uma tentativa concreta e observável. |
| Agent × Worker | Agent é identidade de processo inteligente; Worker é unidade operacional que processa trabalho. |
| Tool × Capability | Tool é atômica; Capability coordena Tools. |
| Context × Memory | Context é temporário; Memory é persistente. |
| Memory × Artifact | Memory guarda conhecimento recuperável; Artifact guarda conteúdo durável endereçável. |
| Workspace × Resource | Workspace define ownership e isolamento de projeto; Resource oferece capacidade operacional. |
| Event × Command | Event relata fato passado; Command solicita uma ação que pode falhar ou ser recusada. |
| Provider × Model | Provider é a porta/adaptação; Model é uma opção resolvida pelo catálogo por atributos públicos. |

## Convenções de nomenclatura

- Documentos usam `NNN-nome-em-kebab-case.md`.
- Tipos, entidades, estados semânticos e eventos usam nomes em inglês para estabilidade contratual.
- Tipos e eventos usam `PascalCase`: `Execution`, `ArtifactReference`, `ExecutionStarted`.
- Campos, parâmetros e identificadores usam `snake_case`: `execution_id`, `occurred_at`, `workspace_id`.
- Constantes e valores de estado usam `UPPER_SNAKE_CASE`: `WAITING_USER`, `COMPLETED`.
- Portas recebem nomes pelo papel, não pela tecnologia: `ArtifactStorage`, não `S3Storage`; o adapter concreto pode indicar tecnologia.
- Coleções usam substantivos no plural; operações usam verbo explícito e evitam abreviações ambíguas.
- O nome de Event DEVE indicar fato no passado e NÃO DEVE usar formas de comando como `StartExecution`.

## Identificadores

- Todo identificador é opaco, imutável no ciclo de vida da entidade e único no escopo definido por sua RFC.
- Campos de referência terminam em `_id`: `user_id`, `workspace_id`, `agent_id`, `execution_id`, `event_id`.
- Contratos NÃO DEVEM inferir tipo, tempo, tenancy ou localização decompondo o texto de um ID.
- `user_id` identifica ownership de usuário mesmo no lançamento single-user.
- `workspace_id` é obrigatório para entidades de projeto quando aplicável; ausência só é válida para entidades explicitamente globais ou estritamente pertencentes ao usuário.
- IDs recebidos de sistemas externos ficam em campos de namespace explícito e não substituem a identidade interna.

## Tempo

- Instantes usam UTC e representação RFC 3339/ISO 8601 com offset explícito; a forma textual canônica termina em `Z` quando UTC.
- Campos de instante terminam em `_at`, por exemplo `created_at`, `occurred_at` e `finished_at`.
- Durações usam unidade explícita no nome ou um tipo `Duration`; números sem unidade são proibidos em contratos.
- Ordenação causal NÃO DEVE ser inferida apenas de relógio de parede. A RFC de eventos definirá garantias de ordenação sem contrariar esta regra.
- Tempo apresentado ao usuário pode ser localizado na borda; persistência e contratos internos permanecem em UTC.

## Correlação e causalidade

- `correlation_id` agrupa operações e eventos pertencentes ao mesmo fluxo lógico de ponta a ponta.
- `causation_id` PODE apontar para o comando ou evento que causou diretamente o fato, quando o fluxo exigir cadeia causal.
- `execution_id` identifica a Execution e é obrigatório em todo evento a ela vinculado; não substitui `correlation_id`.
- Delegações e novas tentativas preservam correlação e recebem identidades próprias.
- Logs, métricas e traces DEVEM permitir associação por IDs sem usar conteúdo sensível como chave.

## Ownership e tenancy

- Toda entidade persistente pertencente a uma pessoa DEVE carregar `user_id`.
- Toda entidade vinculada a projeto DEVE carregar `workspace_id` quando aplicável.
- Ownership é validado em cada fronteira de acesso; conhecer um ID não concede autorização.
- Relações entre entidades NÃO DEVEM cruzar `user_id` ou `workspace_id` sem contrato e autorização explícitos.
- Resources, Memory, Artifacts e eventos herdam ou declaram ownership; adapters não podem removê-lo.
- O modo single-user inicial é uma restrição de lançamento, não permissão para omitir chaves ou controles de isolamento.

## Termos normativos

As palavras abaixo têm força normativa quando grafadas em maiúsculas:

- **DEVE:** requisito obrigatório.
- **NÃO DEVE:** proibição obrigatória.
- **PODE:** opção válida, sem obrigação.
- **RECOMENDADO:** escolha preferida; desvio exige justificativa local.

Textos descritivos em minúsculas não adquirem força normativa por coincidência vocabular.

## Referências a RFCs e ADRs

- RFCs definem contratos, responsabilidades, invariantes e evolução de subsistemas.
- ADRs registram contexto, decisão estrutural e consequências; não substituem contratos de RFC.
- A primeira referência usa `RFC NNN — Título` ou `ADR NNN — Título` com link relativo; referências seguintes podem usar apenas `RFC NNN` ou `ADR NNN`.
- Todos os destinos do índice concluído DEVEM existir. Uma relação marcada como futura descreve evolução de escopo, não ausência do documento nem contrato presumido.
- Numeração representa ordem conceitual de dependência, não versão nem prioridade.
- Alterações incompatíveis exigem registrar impacto nas RFCs relacionadas e, quando forem decisão estrutural, no ADR correspondente.

## Convenções de eventos

Eventos são fatos no passado e usam `PascalCase`, por exemplo `AgentCreated`, `ToolFinished` e `MemorySaved`. O envelope conceitual mínimo é:

```text
EventEnvelope<TPayload> {
  event_id: EventId
  event_type: EventType
  occurred_at: Instant
  source: EventSource
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  user_id: UserId
  workspace_id: WorkspaceId | null
  execution_id: ExecutionId | null
  payload: TPayload
}
```

O exemplo explica o contrato mínimo; não define linguagem, serialização ou schema de persistência. Regras obrigatórias:

- `event_id`, `occurred_at`, `source` e `correlation_id` estão presentes em todo Event;
- `execution_id` está presente e não nulo quando o fato pertence a uma Execution;
- `user_id` e `workspace_id` seguem as regras de ownership aplicáveis ao fato;
- `payload` contém dados do fato, não objetos proprietários de Provider ou adapter;
- segredo, credencial e conteúdo sensível não são publicados sem política explícita;
- nome de evento não promete sucesso antes de o fato ocorrer.

A RFC 103 especializa entrega, ordenação, idempotência, versionamento e retenção sem alterar essas propriedades mínimas.

## Convenções de pseudocódigo

Pseudocódigo existe somente para explicar contratos. Ele DEVE:

- ser tipado e independente de linguagem e framework;
- nomear entradas, saídas, erros e efeitos observáveis;
- declarar pré-condições, pós-condições e invariantes quando relevantes;
- usar interfaces públicas, sem instanciar adapters ou tecnologias;
- indicar nulabilidade e coleções explicitamente;
- evitar corpo executável, algoritmo incidental, endpoint, schema ORM ou configuração.

Exemplo de alias para contrato cuja fonte canônica pertence a outra RFC:

```text
alias ExecutionControl = RFC102.ExecutionControl
```

O alias não abrevia nem redefine a assinatura da fonte canônica e não determina mecanismo de autorização, fila, persistência ou implementação.

## Invariantes editoriais

- Termos do glossário mantêm capitalização e significado consistentes.
- Documentos distinguem estado atual, contrato normativo e extensão futura.
- Nenhuma RFC presume conteúdo indefinido de uma extensão futura; relações entre documentos entregues apontam para contratos existentes.
- Exemplos ilustram contratos e não criam dependência tecnológica.
- Eventos, IDs, tempo, correlação e ownership seguem este documento.
- Context jamais é descrito como Memory, e Tool jamais é descrita como Capability.

## Extensibilidade e futuro

RFCs especializadas PODEM introduzir novos tipos de referência, escopos de ownership, categorias de Event e interfaces, desde que preservem estas convenções ou atualizem esta fundação de forma explícita. Novos termos centrais devem ser adicionados ao glossário antes de serem usados com significados incompatíveis no acervo.

## Fora de escopo

- escolher formato binário ou textual de IDs;
- definir schema serializado final do envelope de eventos;
- escolher bibliotecas de tempo, tracing ou geração de identificadores;
- definir tabelas, endpoints, classes de produção ou configuração executável;
- detalhar retenção, ordenação e entrega específicas de cada subsistema.
