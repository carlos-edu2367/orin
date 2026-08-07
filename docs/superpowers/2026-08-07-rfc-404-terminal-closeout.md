# RFC 404 Terminal — Closeout

**Data:** 2026-08-07  
**Resultado:** gate fechado após implementação, revisão independente e verificação final.

**Commits do gate:** `a0c3d46` (`docs: define RFC 404 terminal design`) e o commit final `feat: close RFC 404 terminal gate` (hash registrado pelo Git no handoff final).

## Entrega

- `src/agentos/terminal/models.py`: contratos públicos bounded, context, estados, erros, buffer, chunks, commands, outcomes e receipts.
- `src/agentos/terminal/ports.py`: `TerminalPort`, `TerminalAdapter`, `ProcessSupervisor` e sink.
- `src/agentos/terminal/reference.py`: adapter determinístico com árvore, input, stream, cancelamento, idempotência e cleanup.
- `src/agentos/terminal/local.py`: adapter operacional isolado, `shell=False`, environment allowlist, cwd resolver privado e process group.
- `src/agentos/terminal/service.py`: autorização, lifecycle, lease revalidation, Workspace validation, events, idempotência, overflow, restore e recovery.
- `src/agentos/terminal/persistence.py`: snapshot allowlisted, transaction + outbox e inspeção de commit indeterminado.
- testes em `tests/unit/terminal` e `tests/integration/terminal`.

## Decisões e alternativas rejeitadas

- Resource Manager continuou autoridade de catálogo/lease/fence/cleanup; catálogo ou lease paralelo no Terminal foi rejeitado.
- `WorkspacePath` foi mantido como única representação pública de cwd; caminho físico fornecido pelo chamador foi rejeitado.
- Adapter determinístico foi escolhido como referência para testes; depender de processo real em todos os testes foi rejeitado.
- Adapter local usa `shell=False` e argv derivado pelo próprio boundary; `shell=True`/`os.system` foram rejeitados.
- Output overflow usa truncation segura por padrão e writer de Artifact opcional por referência; bytes em Events/snapshots foram rejeitados.
- Commit indeterminado usa `inspect_commit`; retry cego foi rejeitado.

## Revisão independente

A segunda passagem foi feita fora do fluxo de implementação sobre leases, ownership, cwd/containment, secrets, output, cancelamento, cleanup, persistence e bypass. Findings corrigidos durante a sessão:

1. O adapter de referência inicialmente não mantinha comando interativo registrado; foi corrigido para permitir input/cancelamento sobre estado `RUNNING`.
2. O stream inicialmente não refletia overflow no snapshot; foi corrigido com accounting de buffer e `HEAD_DROPPED`.
3. Publicação de Artifact podia repetir em streams posteriores; foi corrigida preservando `output_ref` no snapshot e usando idempotency por sessão/comando.
4. A validação de Workspace usava finalidade incompatível com a porta existente; foi corrigida para `workspace.terminal`.
5. O journal usava sequência fixa; foi corrigido para sequência monotônica por `execution_id`.

## Comandos e evidência

Os comandos obrigatórios foram executados nesta sessão com estes resultados reais:

```text
python -m pytest -q
565 passed, 5 skipped in 6.88s
python -m compileall -q src tests
exit code 0
git diff --check
exit code 0; somente avisos de normalização LF/CRLF do Git em arquivos preexistentes
git status --short --branch
## main; alterações do gate aparecem como arquivos novos abaixo, alterações preexistentes foram preservadas
```

Scan obrigatório:

```text
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|requests|httpx|kafka|rabbit|broker|scheduler|worker|shell=True|os\.system|subprocess|root_path|physical_path|native_handle|pid|secret|output|command" src/agentos/terminal
```

O único uso de `subprocess` fica em `local.py`, na boundary operacional. O domínio/portas não importam essa API. `shell=True` e `os.system` não aparecem. `pid`, `secret`, `output`, `command` e cwd físico só aparecem em modelos/adapters internos com repr/persistência/eventos redigidos ou allowlisted.

O scan final retornou ocorrências somente nos campos/modelos sanitizados e em `local.py`; não encontrou `shell=True`, `os.system`, tecnologia externa, `root_path`, `physical_path` ou `native_handle` no domínio/portas. `subprocess` apareceu somente em `local.py`.

PostgreSQL opcional: `python -m pytest -q tests/integration/persistence/test_postgres_optional.py tests/integration/workspaces/test_postgres_optional.py tests/integration/persistence/test_filesystem_resource_postgres_optional.py tests/integration/artifact_storage/test_artifact_postgres_optional.py` retornou `4 skipped in 0.93s`, porque `AGENTOS_TEST_POSTGRES_DSN` não estava configurado; não houve simulação de sucesso.

## Próximo gate

RFC 405 — Browser Resource.
