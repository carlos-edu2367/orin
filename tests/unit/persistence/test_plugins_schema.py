from agentos.persistence.postgres.schema import plugin_contributions, plugin_marketplaces, plugins, skills

def test_plugin_tables_and_skill_attribution_exist():
    assert {"plugin_id", "user_id", "version", "display_name", "install_path", "package_digest", "state", "warnings"} <= set(plugins.c.keys())
    assert "uq_plugins_identity" in {item.name for item in plugins.constraints if item.name}
    assert {"id", "plugin_id", "user_id", "kind", "reference", "display_name", "enabled"} <= set(plugin_contributions.c.keys())
    assert "plugin_id" in skills.c
    assert {"marketplace_id", "user_id", "name", "reference", "clone_path", "updated_at"} <= set(plugin_marketplaces.c.keys())
