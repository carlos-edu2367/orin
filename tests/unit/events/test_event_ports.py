import pytest

from agentos.events import (
    DataClassification,
    EventDelivery,
    OrderingRequirement,
    OwnershipScope,
    PublicationLease,
    PublishOutboxBatch,
    ReplayPolicy,
    SubscriptionSpec,
    VersionRange,
)


def test_delivery_disposition_is_explicit_and_delivery_ids_are_distinct(event_factory):
    delivery_a = EventDelivery(event=event_factory(), delivery_id="delivery:1", attempt=1)
    delivery_b = EventDelivery(event=event_factory(), delivery_id="delivery:2", attempt=2)
    assert delivery_a.delivery_id != delivery_b.delivery_id
    from agentos.events import DeliveryDisposition

    assert DeliveryDisposition.ACKNOWLEDGED.value == "ACKNOWLEDGED"


def test_publish_request_requires_bounded_limit_and_lease():
    with pytest.raises(ValueError):
        PublishOutboxBatch(
            "publisher:1", "partition:1", None, 0, PublicationLease("lease:1", "owner:1", 1)
        )


def test_subscription_declares_types_versions_clearance_and_replay_policy():
    spec = SubscriptionSpec(
        consumer_name="audit",
        accepted_event_types=("Execution*",),
        accepted_versions=(VersionRange(1, 2),),
        ownership_scope=OwnershipScope("user-1", "workspace-1"),
        ordering_requirement=OrderingRequirement.PER_EXECUTION,
        data_clearance=DataClassification.INTERNAL,
        replay_policy=ReplayPolicy.ALLOWED,
    )
    assert spec.accepted_versions[0].maximum == 2


def test_query_and_replay_types_reject_ambiguous_selectors():
    from agentos.events import EventContext, ReplayJobRef, ReplayRequest, SubscriptionRef

    context = EventContext("user:1", "workspace:1", "agent:1", "execution:1", "correlation:1", "test")
    with pytest.raises(ValueError):
        ReplayRequest(context, SubscriptionRef("subscription:1"))
    assert str(ReplayJobRef("replay:1")) == "replay:1"
