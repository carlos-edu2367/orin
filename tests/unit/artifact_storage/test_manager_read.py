from datetime import datetime, timedelta, timezone
from io import BytesIO

from agentos.artifact_storage import ArtifactCategory, ArtifactError, ArtifactErrorCode, ArtifactOperationContext, ArtifactProvenance, ChecksumAlgorithm, ContentChecksum, DataClassification
from agentos.artifact_storage.in_memory import InMemoryArtifactStorage
from agentos.artifact_storage.metadata import InMemoryArtifactMetadataRepository, QuotaPolicy
from agentos.artifact_storage.models import ArtifactProvenanceKind
from agentos.artifact_storage.ports import AppendArtifactChunk, BeginArtifactWrite, FinalizeArtifactWrite, OpenArtifactRead, ReadArtifactRange
from agentos.artifact_storage.service import ArtifactManagerService


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def context(**overrides):
    values = {"user_id": "user:1", "workspace_id": "workspace:1", "agent_id": "agent:1", "execution_id": "execution:1", "correlation_id": "correlation:1", "purpose": "artifact.write", "actor": "actor:1"}
    values.update(overrides)
    return ArtifactOperationContext(**values)


def checksum(data):
    import hashlib
    return ContentChecksum(ChecksumAlgorithm.SHA256, hashlib.sha256(data).hexdigest())


def make_service():
    return ArtifactManagerService(InMemoryArtifactStorage(clock=lambda: NOW), InMemoryArtifactMetadataRepository(clock=lambda: NOW, quota=QuotaPolicy(100, 100, 100)), clock=lambda: NOW)


def make_artifact(service, classification=DataClassification.INTERNAL):
    write_context = context()
    request = BeginArtifactWrite("begin", write_context, ArtifactCategory.RESULT, "result.json", "application/json", 11, checksum(b"hello world"), classification, "retention", ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (), write_context), "begin-idem")
    session = service.begin_write(request)
    service.append(AppendArtifactChunk("chunk", write_context, session.write_session_id, 0, 11, checksum(b"hello world"), "chunk-idem"), BytesIO(b"hello world"))
    reference = service.finalize(FinalizeArtifactWrite("finalize", write_context, session.write_session_id, 11, checksum(b"hello world"), "finalize-idem"))
    return reference


def read_request(reference, ctx, *, maximum_bytes=11, ceiling=DataClassification.RESTRICTED):
    return OpenArtifactRead("open", ctx, reference, maximum_bytes, "artifact.read", ceiling)


def test_read_fixes_version_checksum_and_honors_maximum_bytes():
    service = make_service()
    reference = make_artifact(service)
    read_context = context(purpose="artifact.read")
    session = service.open_read(read_request(reference, read_context, maximum_bytes=5))
    sink = BytesIO()

    receipt = service.read(ReadArtifactRange("read", read_context, session.read_session_id, 0, 5), sink)

    assert sink.getvalue() == b"hello"
    assert receipt.next_offset_bytes == 5
    assert session.version == reference.version


def test_cross_user_workspace_agent_execution_and_purpose_fail_closed():
    service = make_service()
    reference = make_artifact(service)
    cases = (
        context(user_id="user:2", purpose="artifact.read"),
        context(workspace_id="workspace:2", purpose="artifact.read"),
        context(agent_id="agent:2", purpose="artifact.read"),
        context(execution_id="execution:2", purpose="artifact.read"),
        context(purpose="artifact.other"),
    )

    results = [service.open_read(read_request(reference, candidate)) for candidate in cases]

    assert all(isinstance(result, ArtifactError) for result in results)
    assert all(result.code in {ArtifactErrorCode.UNAUTHORIZED, ArtifactErrorCode.NOT_FOUND} for result in results)


def test_classification_ceiling_and_expired_handle_are_rejected():
    service = make_service()
    reference = make_artifact(service, classification=DataClassification.RESTRICTED)
    read_context = context(purpose="artifact.read")
    denied = service.open_read(read_request(reference, read_context, ceiling=DataClassification.CONFIDENTIAL))

    assert isinstance(denied, ArtifactError)
    assert denied.code is ArtifactErrorCode.UNAUTHORIZED
