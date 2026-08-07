# Matriz de requisitos — RFC 602 Artifact Storage

| Requisito | Contrato/implementação | Teste | Evidência atual |
|---|---|---|---|
| Contexto completo e ownership | `artifact_storage.models.ArtifactOperationContext` | `test_contracts.py` | 11 testes focados verdes |
| Namespace derivado e nome sem path traversal | `security.py` | `test_security.py` | namespace determinístico e sanitização cobertos |
| Metadata, referência, grant e classificação | `models.py` | `test_contracts.py` | modelos congelados, grant com scope/version/purpose |
| Handles opacos e ausência de bytes/path | `models.py` | `test_contracts.py` | reprs e atributos públicos cobertos |
| Adapter de bytes substituível | `ports.py` | pendente | Task 2 |
| Staging/chunks/seal/checksum | `ports.py` | pendente | Task 2/4 |
| Metadata transacional/outbox RFC 601 | `ports.py` | pendente | Task 3/6 |
| Lifecycle/quota/retenção/delete | `ports.py` | pendente | Task 4 |
| Leitura por range/reautorização | `ports.py` | pendente | Task 5 |
| Eventos mínimos pós-fato | integração | pendente | Task 6 |
| Adapter SQLAlchemy/Alembic | composição RFC 601 | pendente | schema genérico reutilizado; migration específica só se necessária |
| Segurança de fronteira | pacote sem imports proibidos | pendente | Task 6/7 |

## Evidência inicial

- Baseline antes do gate: `python -m pytest -q` → `387 passed, 1 skipped`.
- RED confirmado: import de `agentos.artifact_storage` falhou antes dos contratos existirem.
- GREEN atual: `python -m pytest -q tests/unit/artifact_storage/test_contracts.py tests/unit/artifact_storage/test_security.py` → `11 passed`.
