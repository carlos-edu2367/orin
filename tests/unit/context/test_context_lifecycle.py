from __future__ import annotations

from dataclasses import replace

import pytest

from agentos.context.models import ContextDisposition, ContextError, ContextErrorCategory, ContextItemKind, ContextTurnUpdate, TokenAccounting, TurnReference


def test_apply_turn_chains_manifest_without_loading_full_history(context_fixture):
    first = context_fixture.manager.assemble(context_fixture.request)
    update = ContextTurnUpdate(
        context=context_fixture.request.context,
        expected_turn=1,
        previous_manifest_ref=first.manifest_ref,
        model_message=TurnReference(reference="result:1"),
        usage=TokenAccounting(input_tokens=3, output_tokens=2),
    )
    second = context_fixture.manager.apply_turn(update)
    assert second.turn == 2
    assert context_fixture.recorder.manifests[-1].previous_manifest_id == first.manifest_ref


def test_apply_turn_rejects_stale_turn(context_fixture):
    first = context_fixture.manager.assemble(context_fixture.request)
    with pytest.raises(ValueError):
        replace(context_fixture.update, previous_manifest_ref=first.manifest_ref, expected_turn=0)


def test_cancelled_assembly_does_not_record_usable_manifest(context_fixture):
    context_fixture.cancellation.cancelled = True
    with pytest.raises(ContextError) as error:
        context_fixture.manager.assemble(context_fixture.request)
    assert error.value.category is ContextErrorCategory.CANCELLED
    assert context_fixture.recorder.manifests == []


def test_finalize_discards_ephemeral_state_and_never_saves_memory(context_fixture):
    context_fixture.manager.assemble(context_fixture.request)
    context_fixture.manager.finalize("execution-1", ContextDisposition.DISCARD)
    assert context_fixture.manager.active_executions == ()
    assert context_fixture.recorder.finalized == [("execution-1", ContextDisposition.DISCARD)]


def test_apply_turn_preserves_source_kind_for_decisions_and_observed_events(context_fixture):
    first = context_fixture.manager.assemble(context_fixture.request)
    update = ContextTurnUpdate(
        context=context_fixture.request.context,
        expected_turn=1,
        previous_manifest_ref=first.manifest_ref,
        decisions=(TurnReference(reference="decision:1", kind=ContextItemKind.DECISION),),
        observed_events=(TurnReference(reference="event:1", kind=ContextItemKind.EVENT),),
    )

    snapshot = context_fixture.manager.apply_turn(update)

    sources = {str(item.content.reference): item.provenance.source_kind for item in snapshot.items if hasattr(item.content, "reference")}
    assert str(sources["decision:1"]) == "DECISION"
    assert str(sources["event:1"]) == "EVENT"
