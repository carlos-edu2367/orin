class FakeSkillLibrary:
    def __init__(self): self.installed = {}
    def install_plugin_skills(self, *, user_id, plugin_id, skills): self.installed[plugin_id] = tuple(skills)
    def remove_plugin_skills(self, *, user_id, plugin_id): self.installed.pop(plugin_id, None)

class FakeMcpService:
    def __init__(self, *, failing=False): self.proposed, self.removed, self.failing = [], [], failing
    def propose(self, command):
        if self.failing: raise RuntimeError("failed")
        self.proposed.append(dict(command))
        return {"server_id": f"srv-{len(self.proposed)}", "state": "pending_approval"}
    def remove(self, user_id, server_id): self.removed.append(server_id)
