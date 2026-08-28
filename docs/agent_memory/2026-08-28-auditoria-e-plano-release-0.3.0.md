# Memoria tecnica — auditoria e plano da release 0.3.0

Data: 2026-08-28

## Contexto

Foi realizada auditoria de produto, arquitetura, runtime agentico, frontend, Electron, instalacao, atualizacao, CI/release e UX do Orin para definir a `0.3.0` voltada ao publico nao tecnico.

## Descobertas que devem orientar tarefas futuras

- `RuntimeService` e a autoridade canonica de chat e deve permanecer assim. `AgenticTurnRuntime` e adaptador.
- Efeitos externos `UNKNOWN` devem pausar para reconciliacao; nunca fazer replay automatico.
- A base agentica e forte, mas falta contrato persistente de objetivo/aceite e evidencia estruturada de conclusao.
- O caminho de instalacao publico da `v0.2.11` e ZIP + `install.ps1`; nao ha instalador grafico `.exe` publicado nem assinatura Authenticode encontrada.
- O Electron atual depende de `--status-file`; um atalho direto padrao para o executavel desktop pode ficar preso na splash. O bootstrap deve passar pelo supervisor com protecao contra loop/multiplas instancias.
- A atualizacao mantem binarios lado a lado, mas nao foi encontrado backup/restauracao do SQLite antes de migration. Rollback seguro exige tratar binario e banco em conjunto.
- A splash expõe banco, migrations, API, worker e scheduler; o percurso principal da `0.3.0` deve usar linguagem humana e manter diagnostico em detalhes.
- Nao ha onboarding persistente; a Home pode assumir provider ainda nao configurado.
- CI da release nao depende hoje do gate completo. A `v0.2.11` foi publicada para um commit cujo workflow de validacao falhou.
- Baseline em 2026-08-28: Python 1826 aprovados/69 skips/19 avisos; frontend unitario 390 aprovados; lint 9 erros e 1 aviso; Playwright 27 aprovados e 1 falhou; build aprovado com chunks grandes.
- Nao ha `DESIGN.md`, `UX-CONTRACT.md` ou manifesto de ownership visual. O frontend e a splash duplicam tokens.
- O caso recorrente `EFFECT_RECONCILIATION_REQUIRED` detecta uma incerteza real ou conservadora e deixa a conversa pausada sem fluxo publico de inspecao/retomada. `ToolOutcome` nao carrega estado de efeito e falhas de tools mutaveis sao promovidas genericamente a `UNKNOWN`. Interrupcao de stream do provider apos produzir evento tambem marca `UNKNOWN` e ainda pode entrar no retry budget. A UI projeta a pausa como falha.

## Decisoes propostas para a 0.3.0

- Tema da release: confianca, orientacao e assertividade.
- Instalador grafico assinado como caminho principal; PowerShell somente avancado.
- Update duravel com staging, assinatura/hash, backup, quiesce, migration, readiness, commit e rollback.
- Onboarding testa um caminho real de inferencia antes da primeira conversa.
- Modo iniciante esconde a complexidade de skills/MCP/plugins/runtime em `Ferramentas avancadas`, sem reduzir autorizacao backend.
- Tarefas nao triviais ganham contrato persistente de objetivo, entregaveis, restricoes e criterios.
- Conclusao modificadora exige evidencia por criterio ou marca explicita de item nao verificavel.
- Tools estruturadas e avaliacao por perfis elevam o desempenho de modelos fracos sem depender de um prompt monolitico.
- UI mostra objetivo, etapas, mudancas, pedidos e evidencia; nao mostra chain-of-thought.
- Reconciliacao ganha contrato por adapter, inspecao automatica, API autorizada, decisao humana contextual e retomada de checkpoint sem replay.

## Documentos de referencia

- `docs/audits/2026-08-28-auditoria-produto-arquitetura-e-prontidao-publica.md`
- `docs/plans/2026-08-28-release-0.3.0-publico-leigo.md`
- `docs/reports/2026-08-28-visao-futura-orin.md`

## Dependencia externa nao confirmada

A existencia de certificado Authenticode nao foi comprovada. Sem assinatura, a build pode ser distribuida como beta tecnica mediante decisao explicita, mas nao deve ser apresentada como experiencia plenamente preparada para leigos.
