from types import SimpleNamespace

import pytest

from agentos.provider_catalog.child_policy import ParentProviderChildModelPolicy


def _agent(provider: str):
    return SimpleNamespace(model_profile_ref=f"model-profile:{provider}:model-a:version")


def test_child_model_policy_returns_only_favorites_from_the_parent_provider_and_rejects_other_models():
    calls = []

    def list_models(context, provider, favorites_only=False):
        calls.append((context, provider, favorites_only))
        return [SimpleNamespace(provider=provider, model_id="model-a", display_name="Model A")]

    catalog = SimpleNamespace(list=list_models)
    command = SimpleNamespace(authorization_ref="grant:1", user_id="user:1", workspace_id=None, purpose="delegate")
    policy = ParentProviderChildModelPolicy(catalog)
    assert [item.provider for item in policy.list_available_models(user_id="user:1", parent=_agent("openrouter"), purpose="delegate")] == ["openrouter"]
    assert calls[-1][2] is True
    policy.validate(parent=_agent("openrouter"), child=_agent("openrouter"), command=command)
    with pytest.raises(PermissionError, match="parent provider"):
        policy.validate(parent=_agent("openrouter"), child=_agent("openai"), command=command)
    with pytest.raises(PermissionError, match="favorite"):
        policy.validate(parent=_agent("openrouter"), child=SimpleNamespace(model_profile_ref="model-profile:openrouter:model-b:version"), command=command)


def test_child_model_policy_rejects_cross_provider_even_when_a_grant_is_present():
    grants = SimpleNamespace(allows=lambda **kwargs: True)
    policy = ParentProviderChildModelPolicy(SimpleNamespace(list=lambda *args, **kwargs: []), grants)
    with pytest.raises(PermissionError, match="parent provider"):
        policy.validate(parent=_agent("openrouter"), child=_agent("openai"), command=SimpleNamespace(authorization_ref="grant:approved", user_id="user:1", workspace_id=None, purpose="delegate"))
