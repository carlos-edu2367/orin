# RFC 104 — Pipeline de contexto

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](101-runtime.md), [RFC 102 — Ciclo de vida da Execution](102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](103-event-system.md)

## Objetivo

Definir o `ContextManager` e o pipeline que monta, limita, sanitiza, registra e atualiza o Context temporário de cada turno de uma `Execution`. O pipeline seleciona informações autorizadas e relevantes dentro de orçamento explícito, preservando proveniência e isolamento sem confundir Context com Memory permanente.

## Fora de escopo

- política de criação, consolidação e retenção de Memory;
- backend de Artifact Storage, filesystem, banco ou índice de busca;
- algoritmo concreto de embeddings, ranking, tokenização ou sumarização;
- template proprietário de Provider ou formato final de prompt;
- janela de contexto específica de um modelo;
- armazenamento automático de todo Context após o fim da `Execution`.

## Responsabilidades e não responsabilidades

O `ContextManager` DEVE:

- receber uma solicitação vinculada a uma `Execution`, Agent, usuário e Workspace;
- coletar candidatos somente por portas públicas e autorização explícita;
- compor task, instruções, resumo, mensagens, Memory recuperada, arquivos por referência, decisões, eventos e resultados de Tools;
- sanitizar e classificar conteúdo antes da inclusão;
- atribuir prioridade, estimar consumo e respeitar orçamento de tokens;
- compactar, truncar ou substituir conteúdo por referência segundo política determinística;
- registrar um manifesto de inclusão, exclusão, transformação, versão e proveniência;
- atualizar o Context a cada turno sem enviar automaticamente todo o histórico;
- descartar estado efêmero quando não for mais necessário.

O `ContextManager` NÃO DEVE:

- persistir conhecimento como Memory por consequência da inclusão no Context;
- copiar histórico bruto ilimitado;
- resolver referência sem revalidar ownership e permissão;
- acessar storage, banco, Provider ou filesystem concreto;
- executar Agent, Tool ou Capability;
- decidir sozinho gravar Memory ou Artifact;
- incluir segredo, credencial ou conteúdo não autorizado;
- garantir reprodutibilidade de resposta do modelo, apenas da composição de entrada.

## Context não é Memory

Context é RAM temporária da `Execution`: uma seleção descartável montada para um turno. Memory é conhecimento persistente, com ownership, proveniência e retenção próprios. As regras são obrigatórias:

- recuperar Memory cria um item de Context referenciado; não transfere ownership nem duplica a Memory;
- remover um item do Context não apaga a Memory ou Artifact de origem;
- resultado ou mensagem no Context não vira Memory automaticamente;
- gravar Memory exige decisão explícita, porta própria, autorização e Event `MemorySaved`;
- o manifesto do Context pode ser durável para auditoria, mas não é o conteúdo persistente de Memory.

## Arquitetura

```text
Runtime / Execution
        │ ContextAssemblyRequest
        ▼
   ContextManager
        ├── PolicyResolver
        ├── CandidateSources (portas)
        ├── Authorization / Isolation
        ├── Sanitizer
        ├── Relevance + Priority
        ├── TokenBudget
        ├── Compactor
        └── ManifestRecorder
                 │
                 ▼
        ContextSnapshot + Manifest
```

Fontes entregam candidatos ou referências; não controlam inclusão final. O `ContextManager` não conhece implementação de Memory, Artifact, Event archive, filesystem ou Provider.

## Modelo de dados

```text
ContextAssemblyRequest {
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  turn: PositiveInteger
  task: TaskSnapshot
  model_requirements: ModelRequirements
  budget: ContextBudget
  prior_manifest_ref: ContextManifestRef | null
  correlation_id: CorrelationId
}

ContextBudget {
  maximum_input_tokens: PositiveInteger
  reserved_output_tokens: NonNegativeInteger
  reserved_control_tokens: NonNegativeInteger
  per_category_limits: CategoryBudget[]
  overflow_policy: OverflowPolicy
}
```

```text
ContextCandidate {
  candidate_id: ContextCandidateId
  kind: ContextItemKind
  content: InlineContent | ContentReference
  ownership: OwnershipScope
  provenance: Provenance
  classification: DataClassification
  relevance: RelevanceScore
  priority: ContextPriority
  estimated_tokens: NonNegativeInteger
  created_at: Instant | null
  source_version: Version | null
  integrity_ref: IntegrityRef | null
}

ContextItemKind =
  SYSTEM_INSTRUCTION | AGENT_INSTRUCTION | TASK | SUMMARY | MESSAGE |
  MEMORY_REFERENCE | FILE_REFERENCE | DECISION | EVENT | TOOL_RESULT |
  CONTROL_STATE

ContextPriority = REQUIRED | HIGH | NORMAL | LOW
```

```text
ContextSnapshot {
  execution_id: ExecutionId
  turn: PositiveInteger
  items: ContextItem[]
  token_accounting: TokenAccounting
  manifest_ref: ContextManifestRef
  assembled_at: Instant
}

ContextManifest {
  manifest_id: ContextManifestId
  execution_id: ExecutionId
  turn: PositiveInteger
  policy_version: Version
  tokenizer_profile: TokenizerProfile
  source_cutoff_at: Instant
  included: IncludedItemRecord[]
  excluded: ExcludedItemRecord[]
  transformations: ContextTransformation[]
  token_accounting: TokenAccounting
  previous_manifest_id: ContextManifestId | null
  created_at: Instant
}
```

`ContextManifest` registra referências, versões, hashes de integridade quando aplicáveis, ordem, estimativas e razões de exclusão. Conteúdo sensível não é duplicado no manifesto.

## Contratos públicos

```text
interface ContextManager {
  assemble(request: ContextAssemblyRequest) -> ContextSnapshot
  apply_turn(request: ContextTurnUpdate) -> ContextSnapshot
  finalize(execution_id: ExecutionId, disposition: ContextDisposition) -> Unit

  pre: ownership e Agent da solicitação correspondem à Execution
  post: snapshot cabe no maximum_input_tokens efetivo
  post: todo item incluído possui autorização e proveniência
}
```

```text
ContextTurnUpdate {
  execution_id: ExecutionId
  expected_turn: PositiveInteger
  previous_manifest_ref: ContextManifestRef
  model_message: ModelMessage | null
  tool_results: ToolResultReference[]
  new_messages: MessageReference[]
  decisions: DecisionReference[]
  observed_events: EventReference[]
  control_state: ControlState
  usage: UsageDelta
}
```

```text
interface ContextSource {
  collect(query: AuthorizedContextQuery) -> ContextCandidate[]

  pre: query declara user, workspace, Agent, Execution, finalidade e limites
  post: candidatos preservam ownership, classificação e proveniência da fonte
}

interface ContextManifestRecorder {
  record(manifest: ContextManifest) -> ContextManifestRef
  load(reference: ContextManifestRef, ownership: OwnershipScope) -> ContextManifest
}
```

O recorder é uma porta de auditoria e recuperação; ele não transforma Context em fonte de verdade de Memory.

## Fontes e composição

O pipeline PODE considerar:

| Fonte | Regra de inclusão |
| --- | --- |
| Task | snapshot ou referência imutável da intenção corrente; obrigatório |
| instruções de sistema/Agent | versões autorizadas e compatíveis; obrigatórias quando aplicáveis |
| resumo | representação derivada com proveniência e cobertura declaradas |
| mensagens | subconjunto relevante, incluindo dependências conversacionais necessárias |
| Memory | resultados recuperados por escopo, finalidade e autorização; sempre com referência |
| arquivos/Artifacts | metadados, trechos mínimos ou referência; conteúdo integral somente se necessário e permitido |
| decisões | decisões estruturadas relevantes, com autor e proveniência |
| eventos | fatos selecionados, não feed bruto de toda a `Execution` |
| Tool results | resultados necessários ao próximo passo, preferindo referência para conteúdo volumoso |
| estado de controle | limites, iteração, ação pendente e instruções necessárias para execução correta |

Eventos, mensagens e resultados não são confiáveis apenas por origem textual. Conteúdo de usuário, Tool, arquivo, web, Memory ou Provider permanece dado e não pode substituir instruções de maior autoridade.

## Orçamento e prioridades

O orçamento efetivo separa entrada, saída esperada e controle. `maximum_input_tokens` nunca pode consumir reservas necessárias à saída ou ao protocolo de ação. A contabilização usa perfil de tokenizer versionado ou estimativa conservadora declarada.

Ordem normativa de preservação, sujeita a autorização:

1. estado de controle necessário para segurança e contrato;
2. instruções de sistema e Agent vigentes;
3. Task e critérios de conclusão;
4. dependências imediatas da ação ou turno corrente;
5. mensagens recentes e resumo que mantêm coerência;
6. decisões e resultados de Tool diretamente relevantes;
7. Memory, arquivos e eventos recuperados por relevância;
8. contexto suplementar de baixa prioridade.

Prioridade não contorna isolamento nem sanidade. Um item `REQUIRED` que seja proibido não entra; a montagem falha explicitamente se a execução não puder continuar sem ele.

Dentro da mesma prioridade, a seleção considera relevância, dependência causal, recência quando semanticamente útil, custo em tokens e diversidade de fontes. Recência por si só não torna um item verdadeiro ou autorizado.

## Pipeline normativo

1. **Fixar escopo.** Validar `execution_id`, `user_id`, `workspace_id`, `agent_id`, turno, limites e versões.
2. **Resolver política.** Fixar versões de instruções, política, tokenizer, classificação e orçamento para o turno.
3. **Coletar candidatos.** Consultar portas com limites por fonte e cutoff temporal; não carregar históricos completos por padrão.
4. **Autorizar e isolar.** Rejeitar relações que cruzem usuário, Workspace ou Agent sem contrato explícito.
5. **Sanitizar.** Detectar segredo, conteúdo não confiável, formato inválido, referência quebrada, tamanho excessivo e instrução injetada em dados.
6. **Normalizar proveniência.** Associar fonte, versão, autoria, instante, referência e transformação anterior.
7. **Classificar e pontuar.** Aplicar categoria, prioridade, relevância e dependências.
8. **Compactar.** Substituir conteúdo volumoso por referência, resumir grupos e selecionar trechos preservando proveniência.
9. **Alocar orçamento.** Incluir itens por prioridade e limites, contabilizar tokens e reservar margem definida.
10. **Validar snapshot.** Confirmar obrigatórios, ordem, sanidade, compatibilidade do modelo e orçamento.
11. **Registrar manifesto.** Registrar incluídos, excluídos e transformações antes da invocação de Provider.
12. **Entregar snapshot.** Retornar Context temporário ao Runtime; fontes não recebem autoridade sobre o loop.

## Compactação e degradação por excesso

Enviar automaticamente todo o histórico é PROIBIDO. Quando candidatos excederem o orçamento, o pipeline degrada nesta ordem:

1. remover duplicatas e representações dominadas;
2. substituir conteúdo volumoso por referências e metadados essenciais;
3. descartar itens `LOW` e depois `NORMAL` de baixa relevância;
4. selecionar trechos relevantes de arquivos, mensagens e resultados;
5. condensar sequências antigas em resumo versionado com cobertura e proveniência;
6. reduzir a quantidade de Memory e Events recuperados, preservando diversidade necessária;
7. encurtar itens `HIGH` somente por transformação segura e rastreável;
8. falhar com `ContextBudgetExceeded` se itens `REQUIRED` autorizados ainda não couberem.

O pipeline NÃO DEVE truncar silenciosamente instruções, Task, identificadores de referência ou argumentos estruturados de ação. Toda exclusão ou transformação registra razão no manifesto. Aumentar orçamento é decisão externa sujeita aos limites da `Execution`, não efeito automático ilimitado.

## Proveniência e referências

```text
Provenance {
  source_kind: SourceKind
  source_ref: SourceReference
  source_version: Version | null
  authored_by: ActorRef | null
  observed_at: Instant | null
  retrieved_at: Instant
  transformation_chain: TransformationRef[]
}
```

Resumo, trecho, redaction e normalização adicionam transformação à cadeia sem substituir a origem. Referências são opacas e resolvidas por porta com autorização; não codificam caminho físico, tenant ou tecnologia. Se uma referência expirar ou mudar de versão, a montagem registra exclusão ou falha conforme sua necessidade, nunca usa conteúdo de outro escopo como fallback.

## Sanidade e conteúdo não confiável

O pipeline DEVE:

- validar tipo, tamanho, encoding conceitual e integridade declarada;
- tratar instruções presentes em páginas, arquivos, Tool results, mensagens e Memory como dados de sua categoria;
- delimitar fonte e papel de cada item;
- remover ou isolar segredos e dados acima da classificação permitida;
- impedir que referência resolva fora do ownership solicitado;
- registrar redaction sem incluir o valor removido;
- rejeitar estrutura ambígua que possa virar pedido de Tool sem nova validação pelo Runtime.

Sanitização reduz risco, mas não afirma veracidade. Conflitos de fatos permanecem identificados por proveniência e podem exigir decisão do Agent ou usuário.

## Isolamento

- toda consulta a fonte inclui `user_id` e `workspace_id` aplicável;
- itens pertencentes a Agent diferente só entram por contrato explícito de compartilhamento e autorização;
- Execution filha não herda todo o Context da mãe; recebe referências e handoff mínimo;
- caches conceituais são particionados por ownership, política e classificação;
- resultado de Tool ou Artifact preserva escopo original;
- modo single-user não permite omitir chaves nem validação;
- conhecer `candidate_id`, `memory_id` ou referência não concede acesso.

## Reprodutibilidade

Reprodutibilidade significa poder explicar e, enquanto fontes e versões forem retidas, remontar a seleção equivalente do turno. O manifesto DEVE registrar:

- versão de política, instruções e tokenizer;
- cutoff e versões das fontes;
- candidatos incluídos e excluídos por referência;
- ordem final, estimativa/contagem de tokens e reservas;
- transformações, parâmetros contratuais e hashes quando aplicáveis;
- relação com o manifesto do turno anterior.

Dados mutáveis devem ser capturados por snapshot, referência versionada ou hash. Indisponibilidade posterior é registrada como limitação de reprodução. Mesmo Context idêntico não garante resposta idêntica do Provider.

## Atualização por turno

Ao fim de cada turno, `apply_turn`:

1. valida `expected_turn` e o manifesto anterior;
2. registra novas mensagens, decisões, eventos e referências de resultado como candidatos;
3. contabiliza uso sem duplicar resultado já aplicado;
4. identifica conteúdo resolvido, obsoleto ou resumível;
5. remonta o próximo Context pelo pipeline completo;
6. encadeia novo manifesto ao anterior;
7. entrega snapshot somente após validação.

Atualização é incremental na origem dos candidatos, mas a decisão de inclusão é refeita; nenhum item permanece apenas porque estava no turno anterior. Tool results volumosos entram por referência e resumos antigos podem ser substituídos por versão nova com cadeia de transformação.

## Fluxo normal

O Runtime solicita Context com escopo e orçamento. O `ContextManager` coleta, autoriza, sanitiza, prioriza, compacta, registra manifesto e entrega snapshot. Após Provider ou Tool, o Runtime envia atualização do turno. Ao concluir ou suspender a `Execution`, o Context efêmero é descartado ou preservado somente como referências/checkpoint necessários; Memory não é alterada implicitamente.

## Fluxo de falha

Falhas possíveis incluem referência indisponível, ownership divergente, item obrigatório proibido, tokenizer incompatível, transformação inválida e orçamento insuficiente. O pipeline:

- exclui fonte opcional com razão auditável quando política permitir;
- usa representação anterior somente se versionada, íntegra, autorizada e declarada como fallback;
- não cruza escopo para preencher lacuna;
- falha explicitamente quando Task, instrução ou controle obrigatório não puder ser montado;
- não entrega snapshot parcial como se fosse completo;
- preserva correlação e categoria de erro sem conteúdo sensível.

## Fluxo de cancelamento

Coleta ou compactação longa verifica sinal cooperativo por porta do Runtime. Ao cancelar:

- não inicia novas consultas de fonte;
- interrompe transformações em limite seguro;
- não publica manifesto incompleto como snapshot utilizável;
- descarta material temporário e referências não confirmadas;
- preserva somente metadados mínimos necessários à auditoria da tentativa;
- devolve `ContextAssemblyCancelled` ao Runtime, que governa a transição da `Execution`.

Pausa pode preservar um manifesto confirmado em checkpoint; retomada revalida autorização, versões e expiração das fontes.

## Eventos

Eventos de domínio permanecem mínimos:

| Event | Fato |
| --- | --- |
| `ContextAssembled` | snapshot válido e manifesto foram confirmados para um turno |
| `ContextCompacted` | transformação de compactação foi confirmada |
| `ContextAssemblyFailed` | montagem terminou sem snapshot utilizável |

O payload usa `execution_id`, turno, `manifest_ref`, contagens, categorias e razões sanitizadas; não inclui Context completo. `MemorySaved`, `DecisionCreated` e eventos de Artifact pertencem às portas responsáveis, ainda que suas referências virem candidatos.

## Segurança

- autorização precede leitura de conteúdo e é revalidada em resolução de referência;
- princípio de menor informação rege cada turno e cada fonte;
- segredos são proibidos em snapshot, manifesto, log e Event, salvo mecanismo explícito fora do Context para referência segura;
- sanitização e delimitação protegem contra prompt injection originada em dados;
- classificações restringem Provider/modelo elegível e consumidores;
- resumos não podem desclassificar nem remover ownership da fonte;
- checkpoints e manifests não contêm handles vivos ou credenciais.

## Observabilidade

Métricas incluem tokens candidatos/incluídos/excluídos por categoria, taxa de compactação, itens redacted, referências quebradas, falhas de autorização, latência por etapa, cache autorizado, budget excedido e tamanho por turno. Logs e traces usam IDs, versões e razões categóricas; não registram prompts completos ou conteúdo privado por padrão.

## Extensibilidade

Novas fontes implementam `ContextSource` e declaram ownership, classificação, proveniência, limites e cancelamento. Novos rankers, compactadores e tokenizers são estratégias versionadas atrás de portas. Extensões não podem introduzir acesso direto a adapters, alterar hierarquia de autoridade ou gravar Memory implicitamente.

## Invariantes

- Context é temporário, limitado e específico de uma `Execution` e turno;
- Memory é permanente e separada; inclusão não é gravação;
- todo item possui ownership, classificação e proveniência;
- todo snapshot cabe no orçamento efetivo e preserva reservas;
- histórico completo nunca é enviado automaticamente;
- compactação e exclusão são rastreáveis;
- referências são preferidas para conteúdo volumoso e sempre reautorizadas;
- dados não confiáveis não ganham autoridade de instrução;
- isolamento de usuário, Workspace e Agent é preservado em todas as fontes;
- manifesto permite explicar a composição sem duplicar segredo;
- Runtime, não ContextManager, governa estados e efeitos da `Execution`.

## Futuro

Context compartilhado, blackboards, handoffs multi-agent, ranking aprendido e compactação multimodal poderão especializar fontes e políticas. Essas extensões devem manter escopo mínimo, proveniência, manifesto, orçamento e separação entre Context, Memory e Artifact.
