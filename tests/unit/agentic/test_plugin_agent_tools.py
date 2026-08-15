import pytest
from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.workspace import ConversationWorkspace

class FakePluginService:
    def __init__(self, installable=True): self.inspected=[]; self.installable=installable
    def search(self, query): return [{"name":"superpowers"}]
    def inspect(self, *, user_id, reference):
        self.inspected.append(reference)
        if not self.installable: raise ValueError("nothing usable")
        return {"plugin_id":"superpowers","display_name":"Superpowers","state":"pending_approval","skills":[]}
    def list(self, user_id): return [{"plugin_id":"superpowers","state":"active"}]
    def remove(self, *, user_id, plugin_id): return {"plugin_id":plugin_id,"removed":True}

def _toolset(tmp_path, service): return AgentToolset(ConversationWorkspace(root=tmp_path, conversation_id="c1"), plugin_service=service, plugin_user_id="u1")

def test_plugin_tools_are_opt_in_and_wait_for_approval(tmp_path):
    assert "install_plugin" not in {item.name for item in AgentToolset(ConversationWorkspace(root=tmp_path, conversation_id="c1")).definitions()}
    service = FakePluginService()
    outcome = _toolset(tmp_path, service).install_plugin("obra/superpowers")
    assert outcome.payload["plugin_approval"] and outcome.payload["wait_for_user"]
    assert service.inspected == ["obra/superpowers"]

def test_uninstall_requires_confirmation(tmp_path):
    toolset = _toolset(tmp_path, FakePluginService())
    with pytest.raises(AgentToolError): toolset.uninstall_plugin("superpowers")
    assert toolset.uninstall_plugin("superpowers", confirmed=True).payload["removed"]
