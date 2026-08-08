Prompt da próxima sessão — Fases B, C e D do fechamento do AgentOS Frontend

Você é o agente responsável por implementar, nesta sessão, exatamente as Fases B, C e D descritas em `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`. A Fase 0 (composição de produção — Security, Execution, ClientEventStream reais sobre Postgres) já está concluída e verificada; não a refaça, não a redesenhe, apenas construa sobre ela. As Fases E, F, H e a verificação/documentação final **não fazem parte do escopo desta sessão** — ficam para uma sessão seguinte, registradas no roadmap. Não as antecipe e não invente pendência para elas além do que o roadmap já registra.

## Regra de conclusão para este escopo

Não finalize, não entregue resposta parcial e não declare sucesso enquanto:

* a Fase B (bridge tool_runtime/multi_agent → `ClientEventStream`) não estiver implementada e comprovada por um teste de integração real mostrando um evento de Tool e um evento de Delegation atravessando outbox → stream público → `ClientEvent`, contra Postgres real (não fixture, não fake);
* a Fase C (resolução de `result_ref` → `display_text`) não estiver implementada, **ou** a investigação real (grep/leitura, não suposição) tiver concluído de forma documentada que não há armazenamento texto-seguro por trás do ref e que o menor adapter viável foi tentado antes de desistir;
* a Fase D (`ProviderConfigurationApplication` em produção) não estiver implementada e comprovada por um teste de integração fim a fim (`PUT`/`GET`/`DELETE /v1/providers/{provider}`) contra Postgres real;
* a suíte completa do backend (`python -m pytest -q`, com `AGENTOS_TEST_POSTGRES_DSN` setado para o Postgres do próprio `docker-compose.yml` do projeto — `postgresql://agentos@localhost:5433/agentos`, já rodando) não estiver verde;
* `docs/frontend/IMPLEMENTATION_PLAN.md` (seção "Decisões locais registradas para a Fase 0" já existe como padrão a seguir) e `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md` não estiverem atualizados com o que foi de fato implementado, incluindo qualquer decisão de design tomada durante B/C/D.

Cada gap que sobrar dentro de B, C ou D precisa de uma nota de limitação real e específica, nunca uma pendência silenciosa.

## Autonomia obrigatória

Não faça perguntas ao usuário. Toda ambiguidade de contrato de payload, nome de campo ou decisão de design deve ser resolvida lendo o código real (domínio, ports, testes existentes) e escolhendo a alternativa mais aderente ao que já existe — nunca inventando um endpoint, campo, tabela ou evento que o domínio não sustente. Registre cada decisão local, com a razão, na seção "Decisões locais" da fase correspondente em `IMPLEMENTATION_PLAN.md`.

Preserve integralmente o worktree existente. Não use `git reset --hard`, `git checkout --`, remoções amplas ou qualquer operação que descarte trabalho. Não faça commit, push ou PR sem pedido explícito do usuário.

## Leitura obrigatória antes de alterar código

Nesta ordem:

1. `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md` completo — é o plano desta sessão, já grounded em investigação real.
2. `docs/frontend/IMPLEMENTATION_PLAN.md`, seção "Fase 0" e "Decisões locais registradas para a Fase 0" — contrato e decisões já em produção que B/C/D devem respeitar.
3. `src/agentos/persistence/postgres/execution_adapters.py`, `event_stream.py`, `security.py` — os três adapters da Fase 0; B e C estendem `event_stream.py`/`execution_adapters.py` sem duplicar os padrões já usados ali (resolução de escopo via `persistence_records`, contexto determinístico, exceções `Application*Error`).
4. `src/agentos/multi_agent/ports.py` (`MultiAgentEventRecorder`), `in_memory.py` (`InMemoryMultiAgentStore.record_event`), `service.py` (`_record_fact`/`_record_message_fact`) — a porta que a Fase B.1 implementa.
5. `src/agentos/tool_runtime/runtime.py` (`ToolRuntimeService.__init__`, `_entry`, `self.outbox`), `models.py` (`ToolOutboxEntry`) — o ponto sem porta de injeção que a Fase B.2 precisa abrir.
6. `src/agentos/artifact_storage/` (modelos + `tests/integration/artifact_storage/test_artifact_postgres_optional.py` como referência de adapter Postgres já testado) e `src/agentos/memory/` — candidatos a armazenamento por trás de `result_ref` para a Fase C. Depois, `grep -rn "result_ref" src/agentos/runtime src/agentos/execution` para confirmar onde e como o ref é produzido hoje, antes de assumir que é um artifact ref.
7. `src/agentos/providers/catalog.py`, `resolver.py`, `compat.py` — o storage/lógica de provider já existente que a Fase D compõe; `src/agentos/api/contracts.py` (`ProviderConfigurationApplication`) e `tests/unit/api/test_api_asgi.py` (`FakeProviderConfiguration`) para o contrato exato que o gateway espera (nunca reler/reexibir a API key).
8. `src/agentos/persistence/postgres/schema.py` e a pasta `migrations/versions/` — próxima migration deve ser `0006_*`; siga o estilo das migrations `0004`/`0005` (uma linha por `create_table`, índices explícitos).

Faça uma leitura read-only completa do que listar acima antes de qualquer edição.

## Escopo obrigatório

### Fase B — Bridge tool_runtime/multi_agent → `ClientEventStream`

Ver detalhamento em `PROJECT_CLOSEOUT_ROADMAP.md`, seção "Fase B". Resumo:

- **B.1**: `PostgresMultiAgentEventRecorder` implementando `MultiAgentEventRecorder.record_event`, tabela dedicada (recomendação do roadmap: `multi_agent_events`, sem FK para `persistence_records` — delegação não é uma "record" versionada). Compor no lugar real onde `MultiAgentService` é construído em produção (não existe hoje — provavelmente precisa ser adicionado a `bootstrap/production.py`).
- **B.2**: adicionar um sink injetável opcional a `ToolRuntimeService` (`__init__(..., sink: Callable[[ToolOutboxEntry], None] | None = None)`, chamado em `_entry()` além do `self.outbox.append` existente — preserva 100% do comportamento atual). Tabela dedicada `tool_activity_events`. `PostgresToolActivitySink` grava as entradas.
- **B.3**: estender `PostgresClientEventStream.read()` para unir `persistence_outbox` com as duas tabelas novas, projetando tudo para o mesmo `ClientEvent`. Antes de fixar os nomes de payload, comparar com o que `frontend/src/features/activities/activityNormalizer.ts` e `frontend/src/features/agents/agentGraphProjection.ts` já assumem; traduzir no projetor se os nomes reais divergirem, documentando a tradução na Fase B do `IMPLEMENTATION_PLAN.md`/roadmap.

TDD: teste de integração primeiro (RED confirmado por tabela/adapter ausente), depois implementação mínima, depois `tests/integration/persistence/test_event_stream_postgres_optional.py` ganha um caso com um evento de cada fonte lido em ordem por um único `read()`.

### Fase C — Resolução de `result_ref` → `display_text`

Ver `PROJECT_CLOSEOUT_ROADMAP.md`, seção "Fase C". Investigar antes de codar (item 6 da leitura obrigatória). Se `result_ref` for de fato resolvível: adapter mínimo, mesma autorização por `user_id` já usada em `ExecutionQueryAdapter`, populando `result.display_text` em `_to_execution_view` sem nunca inventar texto quando não resolvível. Se a investigação real concluir que não há caminho seguro hoje, documentar a limitação especificamente (o que foi checado, por que não dá) em vez de deixar como pendência muda.

### Fase D — `ProviderConfigurationApplication` em produção

Ver `PROJECT_CLOSEOUT_ROADMAP.md`, seção "Fase D". Adapter sobre `src/agentos/providers/` já existente, composto em `compose_production_services`. Teste de integração fim a fim contra Postgres real cobrindo `PUT`/`GET`/`DELETE /v1/providers/{provider}`, confirmando que a API key nunca volta em nenhuma resposta (mesmo padrão de `_provider_public()` no gateway).

## Restrições

- Não invente endpoint, campo de payload, evento, tabela ou DTO que o domínio não sustente — sempre grounded em código real lido nesta sessão.
- Não reintroduza fallback in-memory em produção; o guard existente em `create_production_app` que rejeita isso deve continuar passando.
- Não quebre nenhum teste já verde (`690 passed, 2 skipped` na composição atual da suíte completa).
- Siga TDD sem exceção: teste primeiro, RED confirmado, implementação mínima, GREEN.
- Migrations novas seguem o padrão `000N_*` já estabelecido; nunca edite uma migration já aplicada (`0001`–`0005`).

## Verificação obrigatória antes da conclusão

```
python -m pytest -q
```

(rodar uma vez com `AGENTOS_TEST_POSTGRES_DSN=postgresql://agentos@localhost:5433/agentos` setado — o Postgres do `docker-compose.yml` deste projeto já roda nessa porta — e confirmar que os testes de integração novos de B/C/D passam de verdade, não só que o skip funciona)

```
python -m compileall -q src tests
git diff --check
```

## Documentação obrigatória antes do fechamento

- `docs/frontend/IMPLEMENTATION_PLAN.md`: novas seções "Decisões locais registradas" para B, C e D, no mesmo padrão já usado nas Fases 0–6.
- `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md`: marcar B, C, D como concluídos com evidência real (arquivo de teste, comando rodado), atualizar a tabela de estado atual no topo.
- `docs/frontend/BACKEND_DISCOVERY.md` e `BACKEND_CAPABILITY_MATRIX.md`: remover qualquer hipótese que a composição real de B/C/D contradiga.
- Este arquivo: acrescentar o "Registro de encerramento" abaixo ao concluir.

## Relatório final obrigatório

Ao concluir, informe: arquivos alterados; decisões de desenho e alternativas rejeitadas com a razão; evidência de um evento de Tool e de um evento de Delegation atravessando outbox → stream público (Fase B); como o resultado textual é resolvido ou a limitação real que impediu isso (Fase C); confirmação de que `PUT`/`GET`/`DELETE /v1/providers/{provider}` funcionam contra Postgres real sem vazar a API key (Fase D); comandos executados e resultados reais; limitações legítimas remanescentes, apenas as que a investigação real comprovou. Não transforme requisito obrigatório de B, C ou D em backlog sem justificativa técnica real e documentada.

## Registro de encerramento — a ser preenchido pelo agente executor

Ao fechar este escopo (B, C, D), acrescente aqui a evidência real de implementação, testes, decisões e limitações legítimas. Não deixe este registro vazio, genérico ou baseado em intenção.

### Fechamento (Fases B, C, D)

**Fase B — Concluída.** `PostgresMultiAgentEventRecorder` (`src/agentos/persistence/postgres/multi_agent_events.py`) implementa `MultiAgentEventRecorder.record_event`, idempotente por `event_id`, gravando em `multi_agent_events` (migration `0006_multi_agent_and_tool_events`). `ToolRuntimeService.__init__` (`src/agentos/tool_runtime/runtime.py`) ganhou um `sink: Callable[[ToolOutboxEntry], None] | None = None` opcional, chamado em `_entry()` sem alterar o comportamento do outbox em memória existente; `PostgresToolActivitySink` (`src/agentos/persistence/postgres/tool_activity.py`) grava em `tool_activity_events` (mesma migration). `PostgresClientEventStream.read()` (`src/agentos/persistence/postgres/event_stream.py`) agora une as três tabelas (`persistence_outbox`, `multi_agent_events`, `tool_activity_events`), ordenadas por `(created_at, fonte, id)`, no mesmo `ClientEvent`; o cursor opaco passou de um inteiro único para um mapa `{fonte: posição}` (uma posição por fonte). Evidência: `tests/integration/persistence/test_event_stream_postgres_optional.py::test_a_tool_event_and_a_delegation_event_cross_the_bridge_into_the_same_client_event_stream` grava um `DelegationCreated` real via `PostgresMultiAgentEventRecorder` e um `ToolStarted`/`ToolFinished` real via um `ToolRuntimeService` com `PostgresToolActivitySink` injetado, abre um stream e lê os quatro tipos de evento (`ExecutionQueued`, `DelegationCreated`, `ToolStarted`, `ToolFinished`) em um único `read()` contra `postgresql://agentos@localhost:5433/agentos`. `bootstrap/multi_agent.py` expõe `compose_multi_agent_event_recorder(engine)` para uma futura composição completa do coordinator (que não existe hoje — ver decisões locais).

**Fase C — Fechada como limitação documentada, não implementada.** Investigação real (grep + leitura) confirmou: `result_ref` só é produzido hoje por `providers/compat.py:RuntimeProviderAdapter._map_outcome`, como a string sintética `f"result:{invocation_id}"` — o texto real gerado (`GenerationSucceeded.message`) é descartado, nunca persistido. `RuntimeService`/`RuntimeProviderAdapter` não são compostos em nenhum lugar de `src/agentos/bootstrap/` — nenhuma execution chega a `COMPLETED` por esse caminho em produção hoje. `artifact_storage.ArtifactManager.inspect`/`.read` exigem um `ArtifactReference` completo (checksum, `authorization_ref`, tamanho) que nada deriva de uma string de `result_ref`; `memory/` é indexado por `memory_id`, sem relação com o formato do ref. Nenhum adapter foi escrito — inventar um significaria fabricar uma relação `execution↔artifact_storage` que o domínio não sustenta. `ExecutionQueryAdapter._to_execution_view` permanece sem `result.display_text`, como já estava desde a Fase 0.

**Fase D — Concluída.** `PostgresProviderConfigurationAdapter` (`src/agentos/persistence/postgres/provider_configuration.py`) implementa `configure`/`inspect`/`revoke` sobre uma tabela dedicada `provider_configurations` (migration `0007_provider_configurations`), escopada por `(user_id, provider)`; composto em `compose_production_services` (`src/agentos/bootstrap/production.py`). Evidência: `tests/integration/api/test_provider_configuration_postgres_optional.py` (4/4, Postgres real) — `PUT`/`GET` round-trip preservando `secret_ref`, `DELETE` desabilita sem vazar a chave, `GET` de provider nunca configurado devolve 404, isolamento por usuário (stranger recebe 404). Toda asserção usa `assert secret_api_key not in response.text` (texto bruto da resposta, não só o JSON), então uma fuga em qualquer serialização teria sido pega.

**Comandos executados e resultados reais:**
- `AGENTOS_TEST_POSTGRES_DSN=postgresql://agentos@localhost:5433/agentos python -m pytest -q` → `701 passed, 2 skipped` (era `690 passed, 2 skipped` antes desta sessão; as 11 novas são os testes de integração de B/C/D e o teste unitário do sink).
- `python -m pytest -q` (sem `AGENTOS_TEST_POSTGRES_DSN`) → `663 passed, 40 skipped` — confirma que o skip funciona igual sem a variável.
- `python -m compileall -q src tests` → sem erro.
- `git diff --check` → sem erro de whitespace (só avisos de normalização de fim de linha LF/CRLF pré-existentes, não introduzidos nesta sessão).

**Limitações legítimas remanescentes (só as que a investigação real comprovou):**
- Nem `MultiAgentCoordinatorService` nem `ToolRuntimeService` são compostos por nenhuma rota HTTP hoje — a ponte até o `ClientEventStream` está provada por teste de integração direto contra os adapters/sinks, mas nenhum usuário real alcança esse caminho via gateway ainda (documentado em `BACKEND_DISCOVERY.md`/`BACKEND_CAPABILITY_MATRIX.md`/`BACKEND_UI_MAPPING.md`).
- `AgentMessageCreated.payload.sender_agent_id` nunca é persistido pelo domínio (`multi_agent/service.py:_record_message_fact` só grava `recipient_agent_id`) — o projetor da Fase B não pode traduzir um dado que não existe; a aresta de mensagem correspondente no frontend simplesmente não é desenhada, sem inventar o campo.
- `result_ref`/`display_text` (Fase C) seguem sem resolução: é uma limitação de duas camadas do domínio (texto descartado pelo compat adapter + Runtime não composto em produção), não algo que um adapter desta sessão pudesse legitimamente corrigir sem inventar um contrato novo.
- A API key de provider (Fase D) é armazenada em texto plano — não existe infraestrutura de criptografia em repouso neste código-fonte hoje; a garantia real é que a chave nunca sai pela API pública, não que o armazenamento seja criptografado.

Arquivos alterados/criados nesta sessão: ver `docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md` (seção "Arquivos novos desta sessão") e `docs/frontend/IMPLEMENTATION_PLAN.md` (seções "Fase B"/"Fase C"/"Fase D" e respectivas "Decisões locais") para a lista completa com justificativa de cada decisão.
