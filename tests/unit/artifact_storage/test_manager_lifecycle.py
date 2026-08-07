from datetime import datetime, timedelta, timezone

from agentos.artifact_storage import ArtifactCategory, ArtifactError, ArtifactErrorCode, ArtifactOperationContext, ArtifactProvenance, ChecksumAlgorithm, ContentChecksum, DataClassification
from agentos.artifact_storage.in_memory import InMemoryArtifactStorage
from agentos.artifact_storage.metadata import InMemoryArtifactMetadataRepository, QuotaPolicy
from agentos.artifact_storage.models import ArtifactProvenanceKind, ArtifactState
from agentos.artifact_storage.ports import ApplyArtifactRetention, BeginArtifactWrite, DeleteArtifact
from agentos.artifact_storage.service import ArtifactManagerService, InMemoryArtifactEventSink


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def context():
    return ArtifactOperationContext("user:1", "workspace:1", "agent:1", "execution:1", "correlation:1", "artifact.write", "actor:1")


def manager():
    return ArtifactManagerService(
        InMemoryArtifactStorage(clock=lambda: NOW),
        InMemoryArtifactMetadataRepository(clock=lambda: NOW, quota=QuotaPolicy(100, 100, 100)),
        clock=lambda: NOW,
        event_sink=InMemoryArtifactEventSink(),
    )


def test_delete_marks_deleting_before_recoverable_cleanup_and_respects_hold():
    service = manager()
    ctx = context()
    request = BeginArtifactWrite("op", ctx, ArtifactCategory.RESULT, "result.json", None, 0, None, DataClassification.INTERNAL, "retention", ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (), ctx), "idem")
    session = service.begin_write(request)
    ref = service.finalize(__import__("agentos.artifact_storage.ports", fromlist=["FinalizeArtifactWrite"]).FinalizeArtifactWrite("finish", ctx, session.write_session_id, 0, None, "finish-idem"))
    service.hold(ctx, ref.artifact_id)

    result = service.delete(DeleteArtifact("delete", ctx, ref, ref.version, "user requested", timedelta(minutes=5), "delete-idem"))

    assert isinstance(result, ArtifactError)
    assert result.code is ArtifactErrorCode.RETENTION_BLOCKED


def test_retention_expires_available_artifacts_and_is_bounded():
    service = manager()
    ctx = context()
    request = BeginArtifactWrite("op", ctx, ArtifactCategory.RESULT, "result.json", None, 0, None, DataClassification.INTERNAL, "retention", ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (), ctx), "idem")
    session = service.begin_write(request)
    ref = service.finalize(__import__("agentos.artifact_storage.ports", fromlist=["FinalizeArtifactWrite"]).FinalizeArtifactWrite("finish", ctx, session.write_session_id, 0, None, "finish-idem"))

    receipt = service.apply_retention(ApplyArtifactRetention("retention", ctx, service.namespace_for(ctx, ArtifactCategory.RESULT), "retention", NOW + timedelta(minutes=1), 10, "retention-idem"))

    assert receipt.transitioned_artifact_ids == (ref.artifact_id,)
    read_context = ctx.__class__(ctx.user_id, ctx.workspace_id, ctx.agent_id, ctx.execution_id, ctx.correlation_id, "artifact.read", ctx.actor)
    assert service.inspect(__import__("agentos.artifact_storage.ports", fromlist=["InspectArtifact"]).InspectArtifact(read_context, ref, "artifact.read")).state is ArtifactState.EXPIRED
