from datetime import datetime, timezone
from io import BytesIO

from agentos.artifact_storage import ArtifactError, ArtifactErrorCode, ArtifactOperationContext
from agentos.artifact_storage.ports import ReadArtifactRange
from test_manager_read import make_artifact, make_service, context, read_request


def test_expired_read_handle_and_cancelled_sink_do_not_serve_unbounded_content():
    service = make_service()
    reference = make_artifact(service)
    read_context = context(purpose="artifact.read")
    session = service.open_read(read_request(reference, read_context, maximum_bytes=11))
    service._reads[session.read_session_id.value].session = session.__class__(session.read_session_id, session.artifact_id, session.version, session.size_bytes, session.checksum, session.maximum_bytes, datetime(2026, 8, 5, tzinfo=timezone.utc))

    expired = service.read(ReadArtifactRange("read", read_context, session.read_session_id, 0, 11), BytesIO())

    assert isinstance(expired, ArtifactError)
    assert expired.code is ArtifactErrorCode.HANDLE_EXPIRED or expired.code is ArtifactErrorCode.REFERENCE_EXPIRED
