from types import SimpleNamespace

import pytest

from agentos.provider_catalog.child_policy import ParentProviderChildModelPolicy


def _agent(provider: str):
    return SimpleNamespace(model_profile_ref=f"model-profile:{provider}:model-a:version")


def test_child_model_policy_returns_only_parent_provider_catalog_and_requires_grant_for_cross_provider():
    catalog = SimpleNamespace(list=lambda context, provider: [SimpleNamespace(provider=provider, model_id="model-a", display_name="Model A")])
    command = SimpleNamespace(authorization_ref="grant:1", user_id="user:1", workspace_id=None, purpose="delegate")
    policy = ParentProviderChildModelPolicy(catalog)
    assert [item.provider for item in policy.list_available_models(user_id="user:1", parent=_agent("openrouter"), purpose="delegate")] == ["openrouter"]
    policy.validate(parent=_agent("openrouter"), child=_agent("openrouter"), command=command)
    with pytest.raises(PermissionError, match="cross-provider"):
        policy.validate(parent=_agent("openrouter"), child=_agent("openai"), command=command)


def test_child_model_policy_accepts_cross_provider_only_with_explicit_grant():
    grants = SimpleNamespace(allows=lambda **kwargs: kwargs["authorization_ref"] == "grant:approved")
    policy = ParentProviderChildModelPolicy(SimpleNamespace(list=lambda *args: []), grants)
    policy.validate(parent=_agent("openrouter"), child=_agent("openai"), command=SimpleNamespace(authorization_ref="grant:approved", user_id="user:1", workspace_id=None, purpose="delegate"))
