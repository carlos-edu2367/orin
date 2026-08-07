# Matriz de requisitos — RFC 602 Artifact Storage

| Requisito | Contrato/implementação | Teste | Evidência atual |
|---|---|---|---|
| Contexto completo e ownership | `models.py`, `service.py` | `test_artifact_contracts.py`, `test_manager_authorization.py` | Verde |
| Namespace derivado e nome sem path traversal | `security.py` | `test_security.py` | Verde |
| Metadata, referência, grant e classificação | `models.py`, `metadata.py` | `test_artifact_contracts.py`, `test_metadata_repository.py` | Verde |
| Handles opacos, receipts e limites | `models.py`, `ports.py` | `test_artifact_contracts.py`, `test_in_memory_storage.py` | Verde |
| Adapter de bytes substituível | `ports.py`, `in_memory.py` | `test_in_memory_storage.py` | Verde |
| Staging/chunks/seal/checksum | `in_memory.py`, `service.py` | `test_in_memory_storage.py`, `test_manager_write.py` | Verde |
| Metadata transacional/outbox RFC 601 | `persistence.py` | `test_persistence_boundary.py` | Verde; outbox confirmado na mesma transação |
| Lifecycle/quota/retenção/delete/reconciliação | `metadata.py`, `service.py` | `test_manager_lifecycle.py` | Verde |
| Leitura por range/reautorização | `service.py` | `test_manager_read.py`, `test_manager_authorization.py` | Verde |
| Integridade/quarantine | `service.py` | `test_events.py`, `test_in_memory_storage.py` | Verde |
| Sete eventos mínimos pós-fato | `service.py`, `persistence.py` | `test_events.py`, `test_persistence_boundary.py` | Verde |
| Adapter SQLAlchemy/Alembic | composição RFC 601 | `test_artifact_postgres_optional.py` | Adapter de metadata reutiliza schema genérico; sem migration específica necessária |
| Segurança de fronteira | pacote sem imports proibidos | `test_artifact_boundary_scan.py` + scan final | Zero matches |

## Evidência inicial

- Baseline antes do gate: `python -m pytest -q` → `387 passed, 1 skipped`.
- RED confirmado: import de `agentos.artifact_storage` falhou antes dos contratos existirem; também houve RED para bytes, Manager, outbox e reconciliação.
- Evidência focada final: `python -m pytest -q tests/unit/artifact_storage tests/integration/artifact_storage/test_artifact_postgres_optional.py` → `40 passed, 1 skipped`.
- Evidência de suíte completa antes do último ajuste documental: `python -m pytest -q` → `423 passed, 2 skipped`.
- PostgreSQL opcional: `skipped` sem `AGENTOS_TEST_POSTGRES_DSN`; não houve simulação de sucesso.
