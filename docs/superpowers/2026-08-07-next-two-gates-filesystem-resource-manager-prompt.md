# Prompt da próxima sessão — Fechamento one-shot dos próximos 2 gates do AgentOS

Você é o agente responsável por fechar integralmente, na mesma sessão e sem solicitar decisões ao usuário, os dois próximos gates normativos do backend do AgentOS:

1. **Gate A — RFC 403: Filesystem Resource**;
2. **Gate B — RFC 402: Resource Manager**, integrado ao Filesystem concluído no Gate A.

O Gate A deve ser concluído antes do Gate B. O Gate B pode consumir as portas e adapters já fechados no Gate A, mas os dois gates devem chegar ao estado final na mesma execução.

## Regra absoluta de conclusão

Não finalize, não entregue resposta parcial e não declare sucesso enquanto os dois gates não estiverem 100% implementados, funcionais, testados, documentados e revisados no escopo autorizado do repositório.

“100% completo” significa:

- toda a semântica normativa das RFCs 403 e 402 implementada nas portas públicas, no adapter de referência e nos adapters operacionais que a documentação e os ADRs exigirem;
- nenhuma operação obrigatória reduzida a stub, TODO, placeholder, `pass`, caminho feliz artificial ou contrato sem implementação;
- nenhum requisito normativo convertido em “futuro”, “pendente”, “fora deste gate” ou “decidir depois”;
- toda autorização, ownership, isolamento, lease, fencing, quota/budget, containment, canonicalização, race safety, cleanup, reconciliação, idempotência, auditoria, eventos e persistência demonstrados por testes;
- matriz de requisitos, specs, plano, closeout e prompt de próxima sessão atualizados com evidência fresca;
- nenhum trabalho obrigatório deixado para um agente futuro.

Você **não deve fazer perguntas** ao usuário. Se houver ambiguidade, escolha a alternativa mais aderente às RFCs, ADRs, contratos já existentes e princípios de segurança; registre a decisão na spec/closeout e continue. Não pare para confirmação, aprovação, escolha de nome ou definição de escopo.

Limitações tecnológicas explicitamente fora das RFCs podem permanecer fora somente quando a semântica pública e o adapter de referência estiverem completos. Não use limitações tecnológicas para adiar invariantes normativos.

## Resultado obrigatório

Ao final, o repositório deve conter:

- um `FilesystemPort` seguro e completo, com adapter de referência e adapter local operacional compatível com a política de Workspaces;
- um `ResourceManager` completo, com catálogo, leases, autorização por operação, adapters de Resource, cleanup e reconciliação;
- integração real entre Resource Manager, Workspaces e Filesystem, sem bypass de lease, ownership ou containment;
- persistência pela porta RFC 601, eventos/outbox pela RFC 103 e integração com Artifact Storage pela RFC 602 quando exigida pelas interfaces;
- testes de unidade, integração, concorrência, segurança, restart/crash recovery e regressão completa;
- documentação e evidência final suficiente para afirmar que os dois gates estão fechados sem pendência futura.

## Contexto atual e dependências

O gate RFC 603 — Workspaces está concluído. Consuma as portas existentes de `agentos.workspaces` para resolver Workspace, root opaca, identity, leases, fencing, quotas e lifecycle. Não reimplemente ownership ou lifecycle de Workspace dentro de Filesystem ou Resource Manager.

As fronteiras canônicas disponíveis incluem, conforme o estado real do repositório:

- RFC 601 — Persistência e transações/outbox;
- RFC 602 — Artifact Storage;
- RFC 603 — Workspaces;
- RFC 103 — Eventos;
- RFC 102 — Execution lifecycle;
- RFC 104 — Context pipeline;
- Runtime, Execution, Context, Memory e Artifact Storage já existentes.

Inspecione as portas existentes antes de criar novas abstrações. Preserve compatibilidade pública e não duplique autoridade de estado.

## Regras de segurança e preservação do workspace

- Preserve todas as alterações existentes; registre e separe o estado preexistente antes de editar.
- Não use `git reset --hard`, `git checkout --`, limpeza ampla, remoção recursiva ou qualquer operação que descarte trabalho do usuário.
- Não altere arquivos preexistentes não relacionados ao escopo, salvo correção mínima necessária para integração comprovada.
- Não inclua alterações preexistentes não relacionadas em commits.
- Nunca exponha path físico, handle nativo, PID, processo, cookie, sessão de browser, segredo, credencial, conteúdo volumoso ou objeto proprietário em contrato, erro, Event, Context, auditoria, snapshot ou checkpoint.
- O adapter pode conhecer tecnologia física internamente, mas a tecnologia não pode atravessar a porta de domínio.
- Falha, incerteza, timeout, corrida ou cleanup inconclusivo devem falhar fechado e produzir estado/resultado reconciliável.

## Leitura obrigatória antes de editar

Leia integralmente:

- `docs/architecture/000-overview.md`
- `docs/architecture/050-design-principles.md`
- `docs/architecture/060-glossary-and-conventions.md`
- `docs/architecture/100-kernel/101-runtime.md`
- `docs/architecture/100-kernel/102-execution-lifecycle.md`
- `docs/architecture/100-kernel/103-event-system.md`
- `docs/architecture/100-kernel/104-context-pipeline.md`
- `docs/architecture/300-context-memory/301-memory.md`
- `docs/architecture/400-tools-resources/401-tool-runtime.md`
- `docs/architecture/400-tools-resources/402-resource-manager.md`
- `docs/architecture/400-tools-resources/403-filesystem.md`
- `docs/architecture/400-tools-resources/404-terminal.md`
- `docs/architecture/400-tools-resources/405-browser.md`
- `docs/architecture/600-platform-data/601-persistence.md`
- `docs/architecture/600-platform-data/602-artifact-storage.md`
- `docs/architecture/600-platform-data/603-workspaces.md`
- `docs/architecture/600-platform-data/604-configuration.md`
- `docs/adr/002-postgresql-as-system-of-record.md`
- `docs/adr/005-local-workspaces.md`
- `docs/adr/012-sqlalchemy-alembic-persistence-adapters.md`
- `docs/superpowers/2026-08-07-workspaces-closeout.md`
- `docs/superpowers/2026-08-07-workspaces-requirement-matrix.md`
- specs, planos, matrizes e closeouts anteriores de Persistence, Artifact Storage e Workspaces em `docs/superpowers/`.

Inspecione também `src/agentos/workspaces`, `src/agentos/persistence`, `src/agentos/artifact_storage`, `src/agentos/events`, `src/agentos/execution`, `src/agentos/context`, `src/agentos/memory` e `src/agentos/runtime`, além dos testes correspondentes.

## Processo obrigatório

1. Registre `git status --short --branch`, histórico recente e baseline real de `python -m pytest -q`.
2. Faça um brainstorming curto com 2–3 alternativas para a fronteira Filesystem/Resource Manager. Escolha e registre a decisão considerando segurança de containment, isolamento, leases, atomicidade, testabilidade, compatibilidade Windows/Linux e integração com RFCs 601/602/603.
3. Escreva antes da implementação:
   - `docs/superpowers/specs/2026-08-07-filesystem-resource-manager-design.md`
   - `docs/superpowers/plans/2026-08-07-filesystem-resource-manager.md`
4. Mantenha uma matriz única cobrindo os dois gates:
   - `docs/superpowers/2026-08-07-filesystem-resource-manager-requirement-matrix.md`
5. Execute Gate A em TDD: teste RED, implementação mínima GREEN, refatoração, testes de corrida e commit coerente.
6. Faça revisão contra a RFC 403, corrija tudo, execute os gates do Gate A e só então avance para Gate B.
7. Execute Gate B em TDD, integrado ao Gate A, com revisão independente focada em autorização, leases, handle binding, cleanup e bypass entre Resource e Filesystem.
8. Atualize o closeout final somente depois dos dois gates passarem juntos:
   - `docs/superpowers/2026-08-07-filesystem-resource-manager-closeout.md`
9. Não encerre entre Gate A e Gate B, salvo impossibilidade externa real; nesse caso, continue tentando alternativas seguras e não classifique uma lacuna obrigatória como limitação legítima.

## Gate A — RFC 403: Filesystem Resource

### Contratos públicos

Implemente uma porta pública independente de sistema operacional, biblioteca, banco, volume ou fornecedor, cobrindo no mínimo:

- `FilesystemOperationContext` completo: `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`;
- `WorkspacePath` composto por segmentos seguros, relativo, normalizado e bounded;
- `FilesystemEntry`, tipo, tamanho, versão, classificação e timestamps sem path físico;
- `FilesystemOperation`, limites, timeout, idempotency key, lease binding e expected version/identity quando aplicável;
- `FilesystemPort.stat`, `list`, `read`, `create_directory`, `write`, `move`, `copy` e `remove`;
- resultados tipados para sucesso, rejeição, not found, conflito, quota, timeout e efeito indeterminado;
- leitura por stream limitado ou referência segura, nunca payload volumoso em Event/Context;
- política explícita de overwrite, atomic write, symlink e tipo de entrada;
- `WorkspaceRootResolver`, root canonicalizada, identity opaca e policy version;
- handles de caminho efêmeros, não serializáveis e vinculados a lease/operação quando necessários.

### Containment e segurança

Prove, por teste e implementação:

- rejeição de caminho absoluto, drive, UNC, URL, device namespace, `~`, variável de ambiente, alternate data stream, `.`/`..`, separadores ambíguos, Unicode/case ambíguo e segmentos vazios;
- rejeição de symlink, junction, mount, reparse point, hard-link ambiguity e qualquer componente que escape da root;
- canonicalização/identity comparada contra a root do Workspace em cada operação;
- revalidação imediatamente antes do efeito e proteção contra troca da root ou do alvo entre resolução e commit;
- `move` e `copy` somente dentro da mesma root autorizada, salvo operação explícita e autorizada prevista na RFC;
- descriptor-relative semantics ou mecanismo equivalente seguro no adapter operacional;
- fail-closed quando a plataforma não permitir prova suficiente;
- nenhuma possibilidade de acessar outro Workspace, host, diretório pessoal, share, device ou root do sistema.

### Adapter de referência e adapter local

Entregue ambos, se compatível com a arquitetura atual:

- adapter de referência/in-memory determinístico para testes de todos os casos de corrida e falha;
- adapter local operacional usando somente a raiz fornecida internamente pelo `WorkspaceRootResolver`, com operações seguras, limites e cleanup.

O adapter local pode usar APIs específicas internamente, mas não pode vazar essas APIs pela porta pública. Testes condicionados à capacidade da plataforma devem ser explicitamente marcados; nunca silencie uma falha de segurança com `skip` indiscriminado.

### Quotas, atomicidade e eventos

- aplique bytes, entries, depth, tamanho máximo por arquivo, overwrite e timeout antes do efeito;
- reserve/contabilize por meio das autoridades existentes, sem bypass da quota de Workspace ou Artifact;
- escrita atômica deve usar staging temporário dentro da mesma boundary e promoção confirmada, ou retornar capacidade/erro explícito quando a garantia não existir;
- operações parciais, canceladas ou indeterminadas devem ser reconciliáveis por operation id/idempotency key;
- emita somente fatos confirmados, incluindo quando aplicável `FilesystemReadFinished`, `FilesystemEntryCreated`, `FilesystemEntryChanged`, `FilesystemEntryRemoved` e `FilesystemOperationRejected`;
- payloads devem conter IDs, Workspace, versão, tipo, contagens, outcome e razão categórica, nunca nomes sensíveis, path físico ou conteúdo.

## Gate B — RFC 402: Resource Manager

### Contratos e catálogo

Implemente um `ResourceManager` independente de tecnologia, com:

- `ResourceOperationContext` completo e validação de binding;
- `ResourceType` `FILESYSTEM`, `TERMINAL` e `BROWSER`;
- `ResourceDescriptor`, capabilities, isolation modes, limits, health e adapter ref;
- catálogo tipado, registro, snapshot e resolução de adapter;
- `ResourceLeaseRequest`, `ResourceLease`, grant/rejection/unavailable e estados exatos;
- `acquire`, `renew`, `authorize`, `release`, `revoke` e `inspect`;
- `AuthorizedResourceHandle` efêmero, opaco, não serializável e vinculado a lease, operação, capability e expiration;
- `ResourceUsageRecord`, budget, contadores, outcome e auditoria bounded;
- `CleanupSupervisor.sweep` e `reconcile` com checkpoints, quarentena e estado incerto explícito.

### Leasing, autorização e isolamento

Prove que:

- nenhum Resource é usado sem lease válido e autorização da operação;
- acquire valida contexto, ownership, Workspace, Agent, Execution, correlação, purpose, capability, budget, quota, duração, health e isolation policy;
- o chamador não escolhe `isolation_key`; ela é derivada internamente de ownership e policy;
- Filesystem usa root canonicalizada do Workspace e permissões de operação;
- Terminal e Browser possuem adapters de referência completos, com handles opacos, lifecycle, cancelamento, cleanup e isolamento lógico, sem exigir implementação de shell/browser real se a RFC os declarar tecnológicos fora do escopo;
- leases expirados, liberados ou revogados nunca são reabertos nem aceitam operações tardias;
- renew revalida policy, health, estado, contexto e limites de duração;
- release/revoke são idempotentes, sinalizam cleanup e não devolvem Resource sujo ao pool saudável;
- corrida entre allocate e confirmação, cancelamento, timeout, falha do adapter e resultado tardio não criam lease fantasma;
- fencing/versionamento impede stale writer, lease transferido ou adapter antigo de mutar estado atual.

### Cleanup, reconciliação e eventos

- cleanup deve fechar handles, cancelar operações e limpar temporários com limite e deadline;
- falha de cleanup marca Resource como incerto/quarentenado, emite `ResourceCleanupFailed` e permite retry supervisionado;
- `ResourceLeaseGranted`, `ResourceLeaseRenewed`, `ResourceLeaseReleased`, `ResourceLeaseRevoked`, `ResourceLeaseExpired` e `ResourceCleanupFailed` só são publicados após o fato correspondente;
- persistência e outbox seguem RFC 601/103; não persista handle vivo;
- auditoria deve responder quem, Workspace, Agent, Execution, purpose, capability, Resource lógico, tempo, decisão e uso agregado, sem segredo ou conteúdo;
- reconciliação após restart deve recuperar leases, fences, reservations, cleanup checkpoints e estado de saúde sem ampliar escopo.

## Integração obrigatória entre os gates

Demonstre com testes ponta a ponta:

- `ResourceManager.acquire(FILESYSTEM)` usa o adapter/lease apropriado e o `FilesystemPort` rejeita operação sem `AuthorizedResourceHandle` válido;
- um handle de Filesystem não funciona em outro lease, Workspace, Agent, Execution, purpose ou adapter;
- revoke/release do Resource bloqueia imediatamente novas operações Filesystem;
- mudança de root/identity/estado do Workspace invalida ou suspende o Resource conforme policy;
- quota de Workspace, budget de Resource, limites de Filesystem e quota de Artifact não podem ser contornados usando stores diferentes;
- cleanup de Resource chama a porta correta do Filesystem, preserva ownership e nunca apaga root divergente;
- eventos e auditoria correlacionam Workspace, Resource, lease, operation, Execution e correlação sem vazar tecnologia;
- restart no meio de acquire, authorize, write, release, revoke e cleanup é idempotente e reconciliável.

## Testes obrigatórios

Cubra no mínimo:

- fluxo feliz de cada operação Filesystem;
- fluxo feliz e falhas de cada método Resource Manager;
- cross-user, cross-workspace, cross-agent, cross-execution, actor/purpose incompatível;
- lease expirado, revogado, liberado, transferido, stale, renewal além do limite e fence antigo;
- path traversal, links, mount/reparse, hard-link, root swap e race entre resolve/revalidate/effect;
- overwrite, atomic write, partial write, copy/move cross-root, quota bytes/entries/depth, timeout e cancellation;
- concorrência de acquire, authorize, write, quota, release, revoke e cleanup;
- idempotência, retry indeterminado, optimistic version conflict, restart e crash recovery;
- handles não serializáveis e ausência de path físico em `repr`, erro, evento, auditoria, persistence, trace e logs;
- outbox pós-commit, round-trip pela porta RFC 601 e integração com Workspaces/Artifacts;
- adapter in-memory e adapter local em todas as capacidades suportadas;
- regressão integral de toda a suíte existente.

## Verificação obrigatória antes da conclusão

Execute e registre a saída real de:

```text
python -m pytest -q
python -m compileall -q src tests
git diff --check
git status --short --branch
```

Faça também scans ajustados aos nomes finais dos pacotes, no mínimo:

```text
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|requests|httpx|kafka|rabbit|broker|scheduler|worker|root_path|physical_path|native_handle|subprocess|os\.system|shell=True" src/agentos/<filesystem-package> src/agentos/<resource-manager-package>
```

O scan deve retornar nenhuma ocorrência proibida nos domínios. Se um adapter operacional precisar de uma API de sistema internamente, isole-a no módulo adapter, documente a boundary e faça scan separado para provar que ela não atravessa as portas públicas.

Rode testes PostgreSQL opcionais quando `AGENTOS_TEST_POSTGRES_DSN` estiver configurado. Sem DSN, registre `skipped`; nunca simule sucesso.

Faça revisão final requisito por requisito contra RFC 403, RFC 402, RFC 603 e ADRs relacionados. Qualquer falha, placeholder, TODO, bypass, vazamento, teste ausente ou comportamento inseguro significa que o trabalho continua.

## Relatório final obrigatório

Somente ao concluir os dois gates, informe:

- arquivos alterados e commits realizados;
- decisões de desenho e alternativas rejeitadas;
- matriz de cobertura requisito por requisito para RFC 403 e RFC 402;
- integração comprovada com RFC 601, RFC 602, RFC 603 e RFC 103;
- comandos executados e resultados reais;
- testes condicionados e motivos de qualquer `skipped`;
- revisão independente e findings corrigidos;
- limitações tecnológicas legítimas, somente as previstas nas RFCs;
- confirmação explícita de que os **dois gates estão 100% completos, funcionais e sem pendências futuras de implementação**;
- próximo gate indicado pela documentação atualizada.

Não entregue “quase pronto”, não pare entre os gates, não peça confirmação e não transforme requisito obrigatório em backlog. A sessão só termina quando RFC 403 e RFC 402 estiverem realmente fechadas, integradas, verificadas e documentadas.

## Registro de encerramento desta sessão — 2026-08-07

RFC 403 e RFC 402 foram fechadas no escopo normativo do repositório. A evidência fresca, a matriz requisito por requisito, as decisões, limitações legítimas, commits e o próximo gate estão em [2026-08-07-filesystem-resource-manager-closeout.md](2026-08-07-filesystem-resource-manager-closeout.md). A matriz correspondente está em [2026-08-07-filesystem-resource-manager-requirement-matrix.md](2026-08-07-filesystem-resource-manager-requirement-matrix.md).

Resultado verificado: `541 passed, 5 skipped`; compilação e `git diff --check` com exit code 0; scan dos pacotes Filesystem/Resource sem ocorrências proibidas; teste PostgreSQL opcional executado como `skipped` por ausência de `AGENTOS_TEST_POSTGRES_DSN`. Próximo gate: RFC 404 — Terminal, seguido de RFC 405 — Browser.
