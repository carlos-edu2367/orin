# Prompt da próxima sessão — Conclusão 100% do subsistema Memory

Você vai concluir o subsistema `Memory` do AgentOS. A RFC 301 já possui uma
implementação de referência bounded/in-memory, mas a próxima sessão só poderá
terminar quando todos os requisitos documentados estiverem cobertos por
contratos públicos, integração, testes e evidência. Não trate a suíte verde
atual como prova de conclusão: faça uma auditoria requisito a requisito e
feche todas as lacunas reais dentro do escopo das RFCs.

## Definição de pronto

Ao final, declare `Memory 100% completo` somente se houver evidência de que:

- a RFC 301 está implementada integralmente em suas portas, modelos,
  ownership, autorização, proveniência, classificação, lifecycle, retenção,
  invalidamento, consolidação, lineage, auditoria e Events;
- a separação `Context` temporário versus `Memory` persistente está preservada
  e toda escrita em Memory continua explícita, autorizada e vinculada a uma
  `Execution`;
- a recuperação é compatível com RAG: ingestão explícita, referências opacas,
  proveniência/citações, filtros de segurança antes da recuperação e ranking,
  relevância explicável, limites de contexto, freshness/status, deduplicação,
  lineage e entrega reference-first ao `ContextManager`;
- memórias compartilhadas funcionam por referências autorizadas usando os
  contratos canônicos da RFC 303, com Grant mínimo, finalidade, Agent e
  `Execution` de destino, classificação, orçamento, expiração, revogação,
  consumo e falha fechada;
- `PRIVATE`, `WORKSPACE`, `USER` e `SEMANTIC` não cruzam ownership por
  conveniência: `SEMANTIC` continua subordinada a `base_scope`;
- nenhum dado não autorizado, prompt, segredo, credencial, token, conteúdo
  completo ou localização física aparece em Context, Event, log, erro, `repr`,
  receipt ou referência pública;
- o adapter in-memory é determinístico e substituível, e a porta está pronta
  para composição com a RFC 601 sem fingir que memória em processo é
  durabilidade de produção;
- todos os testes, scans, compilação e documentação possuem evidência fresca.

“100% completo” significa completo contra as RFCs e ADRs aplicáveis. Não
significa inventar um banco, um provedor de embeddings ou uma API que as RFCs
301/303 explicitamente deixam para adapters e decisões próprias. Se uma
capacidade concreta de produção não estiver normatizada, entregue a porta,
capabilities declaradas, adapter de referência e integração testável, e
registre a composição futura sem mascará-la como concluída.

## Estado conhecido antes da sessão

A sessão anterior entregou:

- `src/agentos/memory/models.py`, `ports.py`, `security.py`, `in_memory.py`,
  `context_compat.py` e exports públicos em `__init__.py`;
- contratos de `MemoryManager`, `MemoryStore`, `MemorySearchAdapter`, política
  de autorização, comandos, queries, referências, receipts e erros sanitizados;
- adapter bounded/in-memory com save, get, search textual, invalidate,
  consolidate, retention, versionamento, idempotência, tombstones, lineage,
  auditoria e outbox pós-commit;
- adaptação de Memory ao `ContextSource` existente usando
  `SourceKind.MEMORY`, referências e trechos mínimos, sem escrita implícita;
- testes em `tests/unit/memory/` e
  `tests/unit/integration/test_memory_boundaries.py`;
- especificação e plano em:
  - `docs/superpowers/specs/2026-08-06-memory-design.md`
  - `docs/superpowers/plans/2026-08-06-memory.md`;
- verificação anterior: `361 passed, 1 skipped`, `compileall` sem erro, scan
  de dependências proibidas sem matches e `git diff --check` sem erro;
- commits da implementação: `59b1521`, `c507be0`, `474d215`, `02c7f94`,
  `e18b86b`, `98b054c`, `f938b40` e `e586b96`.

Há alterações preexistentes no working tree em outros subsistemas. Preserve-as
e não use `git reset --hard`, `git checkout` destrutivo, limpeza ampla ou
staging do trabalho de outra pessoa.

## Leitura obrigatória antes de editar

Leia integralmente:

- `C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\200-agents\201-agent.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\200-agents\203-multi-agent.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\301-memory.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\300-context-memory\303-context-sharing.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\602-artifact-storage.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\002-postgresql-as-system-of-record.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\008-artifact-storage-abstraction.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\009-redis-for-ephemeral-coordination.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\012-sqlalchemy-alembic-persistence-adapters.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\013-asyncio-concurrency-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\014-pydantic-boundary-validation.md`

Inspecione integralmente:

- `src/agentos/memory/`, `src/agentos/context/`, `src/agentos/events/`,
  `src/agentos/persistence/` e `src/agentos/multi_agent/`;
- `tests/unit/memory/`, `tests/unit/context/`, `tests/unit/events/` e
  `tests/unit/integration/test_memory_boundaries.py`;
- `docs/superpowers/specs/2026-08-06-memory-design.md`;
- `docs/superpowers/plans/2026-08-06-memory.md`;
- as specs e planos correspondentes de Context, Events, Persistence e
  Multi-agent.

Comece exatamente com:

```text
git status --short --branch
git log --oneline -12
python -m pytest -q
```

Depois produza uma matriz antes de alterar código:

```text
requisito RFC -> contrato/arquivo -> teste existente -> lacuna -> correção -> evidência
```

Não reimplemente o que já está coberto. Não marque no plano uma etapa como
concluída sem implementação e teste correspondentes.

## Lacunas que precisam ser auditadas e fechadas

### 1. Contratos e lifecycle da RFC 301

Confirme e complete, se necessário:

- `SaveMemory`, `GetMemory`, `SearchMemory`, `InvalidateMemory`,
  `ConsolidateMemory` e `ApplyMemoryRetention` com contexto completo,
  finalidade, actor, `execution_id`, correlação, classificação e idempotência;
- invariantes de `PRIVATE`, `WORKSPACE`, `USER`, `SEMANTIC/base_scope`,
  proveniência, integridade, bounds, versões e referências opacas;
- criação, atualização por `expected_version`, conflito sem last-write-wins,
  idempotência por fingerprint, invalidamento, expiração, supersession,
  tombstone e ausência de ressurreição;
- consolidação atômica, fontes reautorizadas, classificação mais restritiva,
  preservação de incerteza e lineage, sem saída parcial;
- retenção limitada ao conjunto explicitamente autorizado, com contagens,
  políticas versionadas e auditoria;
- todos os outcomes de sucesso, negação, falha, cancelamento e estado incerto
  sem conversão indevida para sucesso.

### 2. Recuperação compatível com RAG

Implemente a capacidade de recuperação como contrato substituível, sem acoplar
Memory a um provedor, banco, SDK ou algoritmo proprietário. O fluxo deve ser:

```text
escrita explícita autorizada
  -> proveniência, classificação, integridade e lineage
  -> referência/versão recuperável
  -> filtros de ownership e policy antes de tocar conteúdo
  -> recuperação declarada (lexical, híbrida ou semântica quando disponível)
  -> ranking determinístico e explicável
  -> top-k + orçamento + freshness/status
  -> referências, trechos mínimos e citações/proveniência
  -> ContextManager monta o Context temporário
```

Verifique e teste que:

- a busca aceita uma intenção bounded e filtros de escopo, tipo, status,
  classificação, origem, autoria, confiança, validade e janela temporal;
- ownership, Grant, classificação e status são aplicados antes de ranking,
  chunk/excerpt, materialização ou consulta a qualquer índice;
- o adapter declara capabilities: busca textual, híbrida ou semântica; a
  ausência de embeddings não pode ser mascarada como relevância semântica;
- quando houver recuperação semântica, embeddings e índices são detalhes de
  adapter, com versão, integridade, tenant/ownership, reindexação e
  invalidação por versão; não crie dependência concreta sem especificação e
  teste de fronteira;
- ranking tem desempate determinístico, não retorna registros expirados,
  invalidados, superseded ou fora do `classification_ceiling`, e respeita
  `maximum_results` e `maximum_content_units`;
- cada match conserva `MemoryReference`, versão, `source_ref`, provenance,
  classificação, relevância e razões; o resultado funciona como citação
  verificável para o gerador, sem copiar o documento inteiro;
- conteúdo recuperado é tratado como dado não confiável: nunca vira instrução
  de sistema, não altera policy e não executa prompt/tool/Provider;
- deduplicação, freshness, conflito de versões, revogação e invalidação
  retiram ou bloqueiam resultados antigos também em cache/index;
- `MemoryContextSource` entrega candidatos reference-first ao orçamento do
  Context e nunca grava, renova, consolida ou altera Memory durante collect,
  montagem, descarte ou finalização.

### 3. Memórias compartilhadas e RFC 303

Não crie um segundo contrato de compartilhamento. Integre Memory aos contratos
canônicos já existentes em `agentos.context.sharing`:

- `ContextShareGrant`, `SharedContextReference`, `DelegatedGrantRef`,
  `StructuredHandoff` e `ContextSharingService`;
- Private Memory só pode ser compartilhada pelo owner autorizado; Workspace
  Memory exige autorização no Workspace; User Memory continua sem Workspace e
  só pode ser exposta por Grant explícito, filtrado e temporário;
- uma referência compartilhada aponta para `memory_id` + versão e preserva
  `user_id`, `workspace_id`, source/target Agent, source/target Execution,
  finalidade, classificação, integridade e expiração;
- resolver exige simultaneamente autorização da fonte, Grant de compartilhamento
  e autorização do destino; conhecer `memory_id` ou possuir a referência não
  concede acesso;
- Grants são mínimos, bounded, revogáveis, expirables, vinculados à finalidade
  e não permitem redelegação implícita;
- filtros são somente os campos canônicos da RFC 303; não permita filtro livre
  de conteúdo como atalho de autorização;
- compartilhamento entrega referência ou resumo bounded, nunca Context completo,
  histórico bruto, prompt, segredo, cadeia de raciocínio ou ownership transfer;
- resolução respeita orçamento, idempotência e contagem de consumo; retry da
  mesma chave não consome novamente;
- revogação, expiração, mudança de versão e invalidamento fazem a resolução
  falhar fechada e impedem novos resultados vindos de cache ou índice;
- cross-user e cross-workspace permanecem negados por padrão; exceção exige
  contrato explícito e Grant dedicado;
- o destino recebe candidatos para o próprio `ContextManager`; Memory nunca
  monta o Context do Agent destinatário;
- Events de autorização, criação, resolução, consumo, revogação, expiração,
  negação e falha são mínimos, correlacionáveis e sem conteúdo.

Adicione testes de integração para o ciclo:

```text
MemoryReference autorizada
  -> ContextShareGrant
  -> SharedContextReference / handoff
  -> resolve no Agent/Execution destino
  -> MemoryContextSource / ContextManager
  -> revogação ou expiração
  -> nova resolução falha fechada
```

### 4. Durabilidade e composição com RFC 601

Não declare durabilidade de produção apenas porque o adapter in-memory passou.
Verifique que:

- a porta de Memory pode ser composta com a autoridade transacional da RFC 601;
- estado, revisão, auditoria e outbox não confirmam em ordens que criem
  Memory sem fato correspondente;
- falha depois de efeito externo, timeout ou resultado `UNKNOWN` exige
  reconciliação/inspeção, não retry cego;
- a implementação não conhece SQLAlchemy, Alembic, PostgreSQL, Redis,
  filesystem, Artifact Storage, broker, worker, scheduler, API ou Provider;
- se a sessão implementar adapter durável, ele deve seguir uma spec/ADR
  própria, usar a porta RFC 601, ter migrations e testes isolados, e não
  contaminar o domínio Memory. Caso contrário, registre a composição como
  próxima entrega sem chamar o adapter in-memory de produção.

## Processo obrigatório

1. Registrar status, histórico e baseline.
2. Ler as RFCs/ADRs listadas e inspecionar código/testes atuais.
3. Produzir a matriz requisito → evidência → lacuna.
4. Fazer brainstorming técnico curto sobre as lacunas; comparar alternativas
   para RAG e compartilhamento e registrar a decisão antes de editar contratos.
5. Atualizar ou criar spec e plano executável somente para lacunas comprovadas.
   Se houver alteração de arquitetura, registrar em:
   - `docs/superpowers/specs/2026-08-06-memory-completion-design.md`;
   - `docs/superpowers/plans/2026-08-06-memory-completion.md`.
6. Implementar em ciclos TDD: teste RED, mudança mínima GREEN, refatoração
   somente com a suíte verde.
7. Usar adapters e portas públicas existentes; não acessar atributos privados
   de outro domínio nem duplicar contratos canônicos.
8. Atualizar as specs e planos somente com evidência real.
9. Auditar RFC 301, RFC 303, RFC 104, RFC 103, RFC 601 e ADRs aplicáveis,
   incluindo todas as limitações que ainda forem verdadeiras.
10. Só declarar conclusão depois dos comandos obrigatórios e da revisão final
    requisito a requisito.

## Restrições inegociáveis

- Não gravar automaticamente mensagens, prompts, Context, resultados ou
  compartilhamentos em Memory.
- Não usar memória compartilhada para transferir ownership, grants, secrets,
  configuração, Tool, Skill, Provider ou autoridade transitiva.
- Não fazer fallback para outro usuário, Workspace, Agent, classificação,
  finalidade ou versão quando a autorização falhar.
- Não inserir conteúdo completo em Events, Context, logs, traces, erros,
  `repr`, receipts ou snapshots.
- Não implementar Blackboard, Artifact Storage, Provider, Tool, API, frontend,
  consentimento visual ou máquina de estados de Execution nesta sessão.
- Não escolher silenciosamente banco, índice vetorial, modelo de embedding,
  algoritmo de ranking ou serviço externo. Essas escolhas precisam de contrato,
  capability, isolamento, ADR/spec e testes próprios.
- Não marcar 100% enquanto houver teste crítico faltante, integração de share
  ausente, RAG sem provenance/citação, resultado não bounded ou limitação
  escondida atrás de fake.

## Verificação final obrigatória

Execute e capture a saída de:

```text
python -m pytest -q
python -m compileall -q src tests
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit|broker|worker|scheduler" src/agentos/memory src/agentos/context
git diff --check
git status --short --branch
```

O scan deve retornar zero matches de tecnologia concreta proibida no domínio
Memory e suas integrações. Faça também scans direcionados e inspecione os
matches:

```text
rg -n "\.save\(|save\(|MemoryManager|MemoryContextSource|SourceKind\.MEMORY" src/agentos/context src/agentos/memory tests
rg -n "prompt|secret|token|credential|password|api[_-]?key|authorization" src/agentos/memory tests/unit/memory
```

Os primeiros devem provar que não existe escrita implícita durante montagem
ou finalização de Context; os segundos devem mostrar apenas validações,
redaction e testes, nunca vazamento de dados.

Execute testes adicionais para RAG e compartilhamento, incluindo:

- prefilter de ownership/classificação/status antes de ranking e materialização;
- referência, versão, excerpt bounded, citação/proveniência e razões de match;
- ranking determinístico, top-k, orçamento, freshness e deduplicação;
- conteúdo recuperado não promovido a instrução nem executado;
- share PRIVATE/WORKSPACE/USER com Grant, finalidade, destino, expiração,
  revogação, consumo e cross-scope denial;
- integração `MemoryReference` → `SharedContextReference` → resolução no
  destino → Context temporário;
- cache/index não servindo versão invalidada, expirada, superseded ou revogada;
- atomicidade de Memory + revisão + auditoria + outbox e ausência de efeitos
  parciais em falha/cancelamento/timeout.

## Resposta final esperada do próximo agente

Somente após concluir e validar tudo, a resposta final deve informar:

- confirmação explícita de `Memory 100% completo contra RFC 301 e integração
  aplicável da RFC 303`, ou um bloqueador objetivo se isso não for verdade;
- matriz resumida requisito → implementação → teste → evidência;
- arquivos, specs, planos e commits alterados;
- resultado fresco da suíte, compilação, scans e `git diff --check`;
- como RAG foi coberto: capabilities, filtros, ranking, provenance, citações,
  limites e invalidação;
- como memórias compartilhadas foram cobertas: Grants, refs, destino,
  consumo, revogação e falha fechada;
- limitações de produção que a RFC explicitamente mantém fora de escopo,
  sem apresentá-las como funcionalidades implementadas;
- confirmação de que nenhuma alteração preexistente de outro subsistema foi
  apagada ou incluída indevidamente.

Não escreva uma resposta final otimista baseada somente em `pytest` verde.
Se existir lacuna dentro do escopo, continue trabalhando até fechá-la; se
existir dependência explicitamente fora do escopo documental, registre-a com
precisão e não a esconda atrás da expressão “100%”.
