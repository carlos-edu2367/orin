from __future__ import annotations

import pytest

from agentos.providers.models import (
    CancellationSignalRef,
    ModelRef,
    ProviderOperationContext,
)


def test_provider_context_requires_all_sensitive_fields():
    with pytest.raises(ValueError):
        ProviderOperationContext(
            user_id="user-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            execution_id="execution-1",
            correlation_id="correlation-1",
            purpose="",
            actor_ref="actor-1",
        )


def test_public_references_are_opaque_and_non_blank():
    with pytest.raises(ValueError):
        ModelRef("")


def test_cancellation_signal_is_reference_only():
    signal = CancellationSignalRef("cancel:1")
    assert signal == "cancel:1"
    assert "secret" not in repr(signal).lower()
