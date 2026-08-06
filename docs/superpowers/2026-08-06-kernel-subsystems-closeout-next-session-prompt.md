# Prompt da próxima sessão — Fechamento formal do Kernel do AgentOS

Você vai fechar e auditar formalmente os cinco subsistemas que já possuem uma
base de código no AgentOS: `Execution`, `Runtime`, `Context`, `Events` e
`Providers`. O objetivo desta sessão não é começar outro domínio; é comparar
o código atual requisito por requisito com as RFCs, corrigir lacunas reais,
adicionar os testes faltantes e deixar os planos/documentos coerentes com o
estado efetivamente entregue.

Não declare conclusão apenas porque a suíte atual passa. Diferencie claramente:

- contrato ou modelo existente;
- adapter em memória para testes;
- integração entre subsistemas;
- requisito normativo realmente coberto;
- infraestrutura de produção ainda fora de escopo.

O Agent da RFC 201 já foi implementado e revisado. Preserve sua fronteira,
compatibilidade e testes; altere-o somente se a auditoria demonstrar uma
regressão necessária para a integração com os cinco subsistemas desta sessão.

## Leitura obrigatória antes de editar

Leia integralmente:

- `C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\101-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\102-execution-lifecycle.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\200-agents\201-agent.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\501-provider-api.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\502-model-catalog.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md`

Leia também os planos e especificações já registrados:

- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-execution-lifecycle.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-provider-model.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-runtime-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-context-pipeline-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-event-system-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-provider-model-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-agent-design.md`

Inspecione o código e os testes atuais:

- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\runtime\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\context\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\events\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\providers\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\agents\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\execution\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\runtime\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\context\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\events\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\providers\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\agents\`

Comece registrando o estado inicial:

```text
git status --short --branch
git log --oneline -12
python -m pytest -q
```

## Objetivo de conclusão

Ao final, `Execution`, `Runtime`, `Context`, `Events` e `Providers` devem
estar formalmente auditados e, quando necessário, corrigidos dentro do escopo
das RFCs. Cada plano correspondente deve refletir a verdade: marque uma etapa
como concluída somente quando houver implementação e teste que sustentem a
afirmação. Se uma etapa continuar fora de escopo, registre explicitamente a
limitação em vez de marcá-la como concluída.

## Processo obrigatório

1. Faça um inventário inicial por subsistema, apontando para arquivos e testes.
2. Produza uma matriz requisito → implementação → teste → lacuna. Não edite
   código antes de entender as lacunas e as dependências entre camadas.
3. Registre uma especificação agregada em
   `docs/superpowers/specs/2026-08-06-kernel-closeout-design.md`.
4. Registre um plano executável em
   `docs/superpowers/plans/2026-08-06-kernel-closeout.md`.
5. Execute o plano em ciclos TDD: escreva testes de regressão antes de cada
   correção de produção, observe RED, implemente o mínimo GREEN e refatore
   somente com a suíte verde.
6. Faça as correções em ordem de dependência: Execution → Events/outbox →
   Context → Providers → Runtime → integração transversal. Se a auditoria
   mostrar outra ordem necessária, registre a decisão.
7. Reexecute a suíte relevante após cada ciclo e a suíte completa ao final.
8. Faça uma revisão final requisito por requisito contra RFCs 050, 060, 101,
   102, 103, 104, 201, 501, 502 e 601.
9. Não declare “finalizado” se houver bloqueador, teste faltante crítico,
   plano falsamente marcado como concluído ou integração essencial ausente.

## Checklist de auditoria por subsistema

### Execution — RFC 102

Verifique e teste:

- máquina de estados completa, transições válidas e estados terminais;
- aquisição concorrente, ownership e isolamento por `user_id`/`workspace_id`;
- idempotência, fingerprint, conflitos de versão e resultados repetidos;
- cancelamento, pausa, retomada, entrada do usuário e reconciliação;
- limites de uso, custo, iteração e efeitos externos;
- `COMMITTED`, `NOT_COMMITTED` e `UNKNOWN` sem mascaramento;
- mudanças de estado, auditoria e outbox na mesma unidade transacional;
- nenhum reabrir de estado terminal e nenhum payload sensível em erros/events;
- ausência de acesso direto de consumidores ao armazenamento interno.

### Events — RFC 103 e RFC 601

Verifique e teste:

- envelope canônico, causalidade, correlação, ownership, classificação e
  `sequence`;
- payload mínimo, bounded e sem prompt, resposta, credencial, token,
  argumento privado ou exceção tecnológica;
- publicação somente após commit confirmado;
- outbox publisher, cursor/lease, retry e preservação do mesmo `event_id`;
- entrega ao-menos-uma-vez, `delivery_id` distinto, deduplicação por consumidor
  e ACK somente após sucesso;
- ordenação por Execution, evento atrasado, duplicata e lacuna explícita;
- archive/query/replay autorizado, paginado e sem alterar identidade histórica;
- classificação, ownership, finalidade, quarentena e cancelamento de replay;
- Runtime, Context, Provider e Agent sem publicar diretamente no Event Bus.

### Context — RFC 104

Verifique e teste:

- montagem por fontes públicas com ownership, finalidade e classificação;
- orçamento, reservas, prioridades, dependências e seleção determinística;
- proveniência, cutoff, integridade e sanitização de conteúdo não confiável;
- referências para conteúdo volumoso, sem copiar payloads sensíveis;
- falha explícita de item obrigatório e degradação controlada de item opcional;
- manifestos com inclusões, exclusões e transformações sem vazamento;
- `apply_turn` com turno/manifests esperados e sem histórico automático completo;
- cancelamento, descarte efêmero e ausência de gravação implícita em Memory;
- Context Manager usando apenas `ContextSource`, recorder, clock e políticas
  injetadas.

### Providers — RFCs 501 e 502

Verifique e teste:

- descriptors, revisions, status, transições e versionamento do catálogo;
- hard constraints antes de score, classificação, região, capabilities,
  limits, input/output e custo desconhecido;
- seleção determinística, snapshot aprovado, integridade, validade e ownership;
- fallback explícito, sem ampliar permissões, classificação ou budget;
- retry apenas quando retryability, idempotência e política permitirem;
- timeout, autorização, rate limit, invalid request, cancelamento e
  `INDETERMINATE` como outcomes distintos;
- uso e custo acumulados monotonicamente entre primary/fallback;
- Provider API recebendo somente referências e snapshots públicos;
- ausência de SDK, credencial, HTTP client e provider concreto no domínio.

### Runtime — RFC 101

Verifique e teste:

- uma única Execution por aquisição e concorrência rejeitada;
- carregamento e validação de ownership antes de qualquer efeito;
- `QUEUED → STARTING → RUNNING` e todos os caminhos terminais;
- montagem de Context, resolução de modelo e chamada Provider exclusivamente
  por ports públicos;
- resposta final, Tool/Capability round-trip e entrada do usuário;
- cancelamento e pausa antes/durante/depois de efeitos externos;
- timeout, budget, iterações, custo, tokens e checkpoints;
- recuperação sem repetir efeito já confirmado;
- `ExecutionControl` como única porta mutante;
- Runtime sem conhecer Event Bus, persistence concreta, Agent registry,
  Provider SDK ou tecnologia de fila.

## Integração transversal obrigatória

Prove com testes que:

- uma Execution carrega o `agent_config_version` autorizado sem quebrar
  snapshots antigos;
- Agent, Execution, Context, Provider e Events preservam os mesmos campos de
  ownership, `correlation_id`, `execution_id` e `purpose` onde aplicável;
- um Agent suspenso/arquivado não inicia nova Execution;
- Context não vira Memory automaticamente;
- Runtime não publica Event diretamente;
- Provider e Context não acessam persistência concreta;
- confirmação de estado e outbox permanecem atomicamente conceituais;
- nenhuma fronteira pública revela segredo, payload proprietário ou entidade de
  outro usuário/Workspace.

## Restrições inegociáveis

- Não implementar Orchestrator, Multi-agent, Memory, Blackboard, Tools,
  Capabilities, Artifact Storage, Workspaces, API, SSE, workers ou Scheduler
  nesta sessão.
- Não criar PostgreSQL, SQLAlchemy, Alembic, Redis, Kafka, RabbitMQ, FastAPI,
  HTTP client, filesystem ou SDK de Provider.
- Não trocar uma porta pública por acesso a atributo privado de outro domínio.
- Não duplicar envelopes ou contratos canônicos já existentes sem adapter
  justificado e testado.
- Não ocultar uma lacuna de produção atrás de um fake de teste.
- Não remover testes existentes para fazer a suíte passar.
- Não marcar planos como concluídos sem evidência correspondente.

## Verificação final obrigatória

Execute exatamente, capturando a saída:

```text
python -m pytest -q
python -m compileall -q src tests
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit" src/agentos/execution src/agentos/runtime src/agentos/context src/agentos/events src/agentos/providers
git diff --check
git status --short --branch
```

O `rg` deve produzir zero matches e retornar código 1. Audite também que os
planos correspondentes foram atualizados honestamente. Se houver uma falha,
corrija-a ou informe-a como bloqueador; não declare conclusão com base em
sucesso parcial.

## Resultado esperado da resposta final

A resposta final deve conter somente depois da implementação/auditoria:

- inventário por subsistema: completo, parcial ou pendente;
- arquivos e planos alterados;
- matriz resumida de requisitos atendidos e limitações restantes;
- testes, compilação, scans e status do Git com evidência fresca;
- decisões de interpretação das RFCs;
- confirmação explícita de que nenhuma infraestrutura concreta foi criada;
- próximos RFCs fora do escopo, se ainda houver lacunas.

Não dê a resposta final antes de corrigir todas as lacunas que estejam dentro
do escopo desta sessão. Se uma exigência das RFCs depender de um subsistema
fora do escopo, registre-a como dependência explícita e não como conclusão.
