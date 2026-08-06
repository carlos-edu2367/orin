# Prompt da próxima sessão — Pipeline de Contexto do AgentOS

Você vai implementar o próximo subsistema do backend do AgentOS: o `ContextManager` e o pipeline normativo de Contexto da RFC 104.

Leia integralmente antes de editar:

- `C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\101-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\102-execution-lifecycle.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\301-memory.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\303-context-sharing.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\501-provider-api.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\502-model-catalog.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md`

Inspecione também o que já foi implementado:

- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\runtime\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\execution\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\runtime\`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\2026-08-06-runtime-next-session-prompt.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-runtime-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-runtime.md`

## Contexto da decisão

O Runtime da RFC 101 já possui contrato público, loop de uma `Execution`, limites, cancelamento cooperativo, pausa, falha, resultado e checkpoints por referência. Ele usa um `ContextManager` Protocol simplificado e fakes de teste.

O próximo subsistema deve ser o `ContextManager` da RFC 104 porque ele é a próxima fronteira do Kernel diretamente consumida pelo Runtime. O objetivo desta sessão é substituir o fake conceitual por um pipeline de domínio determinístico, sem criar fontes tecnológicas concretas.

## Objetivo

Implementar exclusivamente o domínio backend do Pipeline de Contexto: contratos públicos, montagem e atualização de Contexto temporário, orçamento, prioridades, proveniência, isolamento, sanitização, referências, manifesto, falhas, cancelamento cooperativo e descarte.

O `ContextManager` deve montar um snapshot autorizado para um turno de uma `Execution`, registrar um manifesto explicável e entregar ao Runtime somente referências e dados públicos mínimos. Contexto nunca pode virar Memory automaticamente.

## Escopo obrigatório

- Definir e implementar a porta pública `ContextManager`:
  - `assemble(request: ContextAssemblyRequest) -> ContextSnapshot`;
  - `apply_turn(request: ContextTurnUpdate) -> ContextSnapshot`;
  - `finalize(execution_id, disposition) -> None`.
- Definir tipos públicos para:
  - `ContextAssemblyRequest`;
  - `ContextBudget`;
  - `ContextCandidate`;
  - `ContextItem` e `ContextItemKind`;
  - `ContextSnapshot`;
  - `ContextManifest`;
  - `ContextTurnUpdate`;
  - `TokenAccounting`;
  - `Provenance`;
  - prioridade, classificação, overflow e disposição;
  - erros categóricos do Contexto e retryability.
- Definir portas Protocol mínimas para:
  - `ContextSource` somente para coleta autorizada de candidatos;
  - `ContextManifestRecorder` para registrar/carregar manifesto por referência;
  - relógio e política de Contexto quando necessários.
- Implementar o pipeline normativo da RFC 104:
  1. validar `execution_id`, `user_id`, `workspace_id`, `agent_id`, turno, correlação, finalidade e ownership;
  2. fixar orçamento, reservas, classificação, versões de política e cutoff;
  3. coletar candidatos por fontes públicas com escopo completo;
  4. revalidar ownership, classificação, proveniência e integridade;
  5. sanitizar conteúdo, referências, estrutura e instruções não confiáveis;
  6. normalizar proveniência e cadeia de transformações;
  7. ordenar por prioridade, dependência, relevância, custo, recência válida e diversidade;
  8. alocar orçamento preservando reservas de saída e controle;
  9. compactar, substituir conteúdo volumoso por referência ou excluir itens opcionais conforme a política;
  10. falhar explicitamente quando item `REQUIRED` autorizado não couber ou não puder ser validado;
  11. registrar manifesto com incluídos, excluídos e transformações;
  12. retornar snapshot temporário somente depois de todas as pós-condições;
  13. atualizar Context por turno sem carregar automaticamente todo o histórico;
  14. finalizar e descartar estado efêmero sem gravar Memory implicitamente.
- Integrar o contrato do `ContextManager` ao Runtime existente sem importar fonte, Provider, Memory, Artifact ou persistência concreta.
- Preservar `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose` em toda solicitação sensível a fontes e recorder.
- Garantir que snapshots e manifestos usem referências para conteúdo volumoso e nunca carreguem segredos, credenciais, tokens, prompts completos ou payloads proprietários.
- Garantir que uma referência nunca conceda autorização por si só: toda resolução deve revalidar ownership e classificação.
- Garantir que conteúdo vindo de usuário, arquivo, web, Memory, Tool, Event ou Provider permaneça dado não confiável e não eleve sua autoridade para instruções.

## Fronteiras obrigatórias

O Context Manager pode compor candidatos recebidos por portas, mas não implementa fontes reais. Nesta sessão não criar:

- Memory real ou adapter de Memory;
- Artifact Storage, filesystem ou índice de busca;
- Provider, Model Catalog ou tokenizer específico de fornecedor;
- Event Bus, publicador de outbox ou persistência concreta;
- FastAPI, endpoints, SSE, workers, ARQ, Redis, PostgreSQL, ORM ou Alembic;
- Tool Runtime, Tool, Capability, Browser ou Resource;
- embeddings, ranking aprendido, sumarização generativa ou compactador proprietário.

As implementações de fontes, recorder, clock e políticas devem ser fakes/stubs somente dentro dos testes. A implementação de produção deve ser uma composição de domínio sobre portas e estratégias públicas injetadas.

## Contrato de dados e segurança

O Contexto de produção pode conter somente:

- itens estruturados mínimos e referências opacas;
- classificação e ownership já validados;
- proveniência, versão e integridade;
- contagem/estimativa de tokens;
- razões categóricas de exclusão, transformação ou falha.

O manifesto não deve duplicar conteúdo sensível. Redaction registra apenas que uma remoção ocorreu, sua categoria e a referência necessária para auditoria autorizada; nunca registra o valor removido.

O pipeline não pode truncar silenciosamente Task, instruções de sistema/Agent, identificadores, referências ou argumentos estruturados. Se um requisito obrigatório não couber, deve retornar erro categórico de budget, sem entregar snapshot parcial como se fosse completo.

## Testes obrigatórios

Cubra pelo menos:

- montagem final simples contendo Task e itens obrigatórios;
- propagação completa de ownership, correlação e finalidade para cada `ContextSource`;
- seleção determinística por prioridade, dependência, relevância e custo;
- preservação de reservas de saída e controle no orçamento;
- exclusão de itens opcionais quando o orçamento exceder;
- falha quando item `REQUIRED` autorizado não couber;
- ordenação estável e reprodutível com os mesmos candidatos, política e cutoff;
- manifesto contendo referências incluídas, excluídas e transformações;
- proveniência e cadeia de transformação sem payload sensível;
- isolamento entre usuários, Workspaces e Agents;
- referência quebrada, expirada, acima da classificação ou de ownership divergente;
- sanitização/redaction de segredo e conteúdo não confiável sem registrar o valor removido;
- nenhum conteúdo de Memory convertido em Memory novamente;
- `apply_turn` rejeitando turno inesperado ou manifesto anterior divergente;
- `apply_turn` preservando referências de resultado de Provider/Tool sem copiar payload completo;
- histórico completo não sendo enviado automaticamente;
- falha de fonte opcional degradando conforme a política;
- falha de fonte obrigatória terminando explicitamente em erro;
- cancelamento antes de nova coleta e durante transformação, sem manifesto utilizável parcial;
- `finalize` descartando estado efêmero e não chamando Memory;
- recorder sendo a única porta de manifesto, sem `TransactionalPersistence` direto;
- ausência de publicação direta de Event pelo Context Manager;
- integração com o Runtime existente preservando os testes atuais;
- nenhum segredo, prompt completo, argumento privado, token, credencial ou payload proprietário nos tipos, manifestos, logs de teste ou outcomes.

## Restrições inegociáveis

- Backend Python 3.13+ somente.
- Não importar FastAPI, Playwright, Redis, SQLAlchemy, SDKs de IA ou SDKs de Provider.
- Não chamar `TransactionalPersistence` diretamente.
- Não publicar eventos diretamente.
- Não persistir Context como Memory.
- Não resolver referência por caminho físico, tenant codificado ou tecnologia embutida no ID.
- Não usar histórico completo por padrão.
- Não fazer fallback para outro ownership, classificação, versão ou referência sem autorização explícita.
- Não retornar snapshot parcial como snapshot válido.
- Não introduzir `switch/case` por fonte, tecnologia ou adapter.
- Não alterar a máquina de estados da `Execution`.
- Não registrar conteúdo removido por redaction.

## Processo obrigatório

1. Inspecione a estrutura atual e leia integralmente as RFCs listadas.
2. Apresente um desenho curto do Context Manager, incluindo fronteiras e 2–3 alternativas, e aguarde aprovação antes de editar produção.
3. Depois da aprovação, registre especificação e plano curto de arquivos.
4. Escreva testes que falham antes de qualquer código de produção.
5. Implemente o mínimo necessário para os testes passarem, em ciclos RED/GREEN.
6. Execute a suíte relevante após cada ciclo.
7. Faça auto-revisão contra as RFCs 050, 060, 101, 102, 103, 104, 301, 303, 501, 502 e 601.
8. Antes de declarar conclusão, execute suíte completa, compilação e busca de imports proibidos.

## Resultado esperado

Ao final, entregue:

- arquivos criados/modificados;
- testes executados e resultado;
- decisões de interpretação da RFC 104;
- política de orçamento e ordem de seleção adotadas;
- limitações e pontos fora de escopo;
- confirmação explícita de que Context continua temporário, que Memory não foi alterada implicitamente e que não foi criado nenhum adapter tecnológico.

Não avance para Memory real, Artifact Storage, fontes concretas, tokenizer/embeddings, Provider, Model Catalog, Tools, Capabilities, workers, Event Bus ou persistência PostgreSQL nesta sessão.
