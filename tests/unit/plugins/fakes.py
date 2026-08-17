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

class FakeCommandLibrary:
    def __init__(self) -> None:
        self.installed: dict[str, tuple] = {}

    def install_plugin_commands(self, *, user_id, plugin_id, install_path, commands, commands_path="commands"):
        self.installed[f"{user_id}/{plugin_id}"] = tuple(commands)

    def remove_plugin_commands(self, *, user_id, plugin_id):
        self.installed.pop(f"{user_id}/{plugin_id}", None)

class FakeHookEngine:
    def __init__(self) -> None:
        self.registered: dict[str, tuple] = {}
        self.enabled: dict[str, bool] = {}

    def register(self, *, user_id, plugin_id, install_path, hooks, enabled):
        key = f"{user_id}/{plugin_id}"
        self.registered[key] = tuple(hooks)
        self.enabled[key] = enabled

    def unregister(self, *, user_id, plugin_id):
        key = f"{user_id}/{plugin_id}"
        self.registered.pop(key, None)
        self.enabled.pop(key, None)
