from __future__ import annotations

from agentos.browser.integration import InMemoryBrowserArtifactOutput, InMemoryBrowserInputResolver
from agentos.browser.models import BrowserArtifactRef, BrowserOperationContext, BrowserWorkerGrant, GrantCapability
from agentos.browser.models import AuthorizedFileReference
from agentos.browser.ports import GrantedArtifactWrite


def ctx(purpose="browser.download"):
    return BrowserOperationContext("u", "ws", "a", "e", "c", purpose, "agent:a")


def grant():
    from datetime import datetime, timedelta, timezone
    return BrowserWorkerGrant("g", ctx(), "lease", "p", "s", (GrantCapability.DOWNLOAD, GrantCapability.UPLOAD), datetime.now(timezone.utc) + timedelta(minutes=1), 1)


def test_artifact_output_commits_bounded_content_and_aborts_partial_sink() -> None:
    output = InMemoryBrowserArtifactOutput(maximum_bytes=4)
    sink = output.begin("download", ctx(), grant(), 4)
    assert sink.write(b"data") == 4
    ref = output.commit(sink, "application/octet-stream")
    assert isinstance(ref, BrowserArtifactRef)
    partial = output.begin("download", ctx(), grant(), 4)
    partial.write(b"x")
    output.abort(partial)
    assert output.read(ref) == b"data"
    request = GrantedArtifactWrite("download", ctx(), grant(), 4, "application/octet-stream")
    assert request.maximum_bytes == 4
    sink2 = output.begin(request)
    assert sink2.write(b"ok") == 2
    output.abort(sink2)


def test_input_resolver_rejects_physical_path_and_foreign_reference() -> None:
    resolver = InMemoryBrowserInputResolver({"file-1": (b"ok", ctx())})
    assert resolver.open("file-1", grant()).read(10) == b"ok"
    try:
        resolver.open("C:\\secret.txt", grant())
    except ValueError as exc:
        assert "path" in str(exc).lower()
    else:
        raise AssertionError("physical path accepted")



def test_authorized_file_reference_is_logical_only() -> None:
    reference = AuthorizedFileReference("file-1", ctx(), 2, "text/plain", "INTERNAL")
    assert "path" not in repr(reference).lower()
