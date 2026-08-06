import pytest


def test_execution_event_requires_sequence_and_workspace_ownership(event_factory):
    with pytest.raises(ValueError):
        event_factory(execution_id="execution-1", sequence=None)


def test_event_without_execution_has_no_sequence(event_factory):
    event = event_factory(execution_id=None, sequence=None, workspace_id=None)
    assert event.execution_id is None
    assert event.sequence is None


def test_payload_rejects_secret_keys_and_exposes_only_bounded_data(event_factory):
    with pytest.raises(ValueError):
        event_factory(payload={"api_key": "private"})
    event = event_factory(payload={"result_ref": "artifact:1"})
    assert "private" not in repr(event)
    assert "artifact:1" not in repr(event)


def test_event_requires_offset_aware_time_and_positive_version(event_factory, naive_datetime):
    with pytest.raises(ValueError):
        event_factory(occurred_at=naive_datetime)
    with pytest.raises(ValueError):
        event_factory(event_version=0)


def test_sequence_and_ids_are_validated(event_factory):
    with pytest.raises(ValueError):
        event_factory(event_id=" ")
    with pytest.raises(ValueError):
        event_factory(sequence=0)
    with pytest.raises(ValueError):
        event_factory(execution_id="execution:1", sequence=None)
