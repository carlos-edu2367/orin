from datetime import datetime, timedelta, timezone
from io import BytesIO

from agentos.artifact_storage import ArtifactError, ArtifactErrorCode, ArtifactState, DataClassification
from agentos.artifact_storage.metadata import InMemoryArtifactMetadataRepository, QuotaPolicy
from agentos.artifact_storage.in_memory import InMemoryArtifactStorage
from agentos.artifact_storage.ports import BeginArtifactWrite
from agentos.artifact_storage.models import ArtifactCategory, ArtifactProvenance
from agentos.artifact_storage.models import ArtifactProvenanceKind
from agentos.artifact_storage.service import ArtifactManagerService
from agentos.artifact_storage.ports import ApplyArtifactRetention, DeleteArtifact, FinalizeArtifactWrite, InspectArtifact, ReadArtifactRange, VerifyArtifact
from agentos.artifact_storage.service import InMemoryArtifactEventSink
from test_manager_read import context, make_artifact, make_service, read_request


service_now = datetime(2026, 8, 6, tzinfo=timezone.utc)


def test_artifact_events_are_minimal_and_only_follow_confirmed_facts():
    service = make_service()
    write_context = context()
    reference = make_artifact(service)
    read_context = context(purpose="artifact.read")
    session = service.open_read(read_request(reference, read_context))
    service.read(ReadArtifactRange("read", read_context, session.read_session_id, 0, 11), BytesIO())
    service.apply_retention(ApplyArtifactRetention("retention", write_context, service.namespace_for(write_context, reference.category), "retention", service._now() + timedelta(minutes=1), 10, "retention-idem"))

    event_types = [event.event_type for event in service.event_sink.events]

    assert event_types[:3] == ["ArtifactWriteStarted", "ArtifactStored", "ArtifactReadFinished"]
    assert "ArtifactExpired" in event_types
    for event in service.event_sink.events:
        assert "hello world" not in repr(event.payload)
        assert "path" not in repr(event.payload).lower()
        assert "password" not in repr(event.payload).lower()
        assert "bytes" not in event.payload


def test_quarantine_and_cleanup_failed_are_reported_after_effect_or_uncertainty():
    service = make_service()
    write_context = context()
    reference = make_artifact(service)
    service.storage._objects[next(iter(service.storage._objects))].data = b"corrupted"
    verified = service.verify(VerifyArtifact("verify", context(purpose="artifact.read"), reference, "verify-idem"))
    assert verified.integrity_state.value == "MISMATCH"
    assert service.event_sink.events[-1].event_type == "ArtifactQuarantined"

    service = make_service()
    reference = make_artifact(service)
    service.storage.fail_next("delete", ArtifactErrorCode.IO_UNAVAILABLE)
    result = service.delete(DeleteArtifact("delete", write_context, reference, reference.version, "cleanup", timedelta(minutes=1), "delete-idem"))

    assert isinstance(result, ArtifactError)
    assert result.effect_state.value == "UNKNOWN"
    assert service.event_sink.events[-1].event_type == "ArtifactCleanupFailed"


def test_failed_metadata_confirmation_emits_no_started_event():
    class RejectingMetadata(InMemoryArtifactMetadataRepository):
        def create_staging(self, *args, **kwargs):
            return ArtifactError(ArtifactErrorCode.IO_UNAVAILABLE, "NON_RETRYABLE", "NOT_APPLIED")

    service = ArtifactManagerService(InMemoryArtifactStorage(clock=lambda: service_now), RejectingMetadata(clock=lambda: service_now, quota=QuotaPolicy(100, 100)), clock=lambda: service_now)
    ctx = context()
    request = BeginArtifactWrite("begin", ctx, ArtifactCategory.RESULT, "result.json", None, 0, None, DataClassification.INTERNAL, "retention", ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (), ctx), "idem")
    result = service.begin_write(request)

    assert isinstance(result, ArtifactError)
    assert service.event_sink.events == []
