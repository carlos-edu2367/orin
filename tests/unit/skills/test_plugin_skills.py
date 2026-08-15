from sqlalchemy import create_engine
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.postgres.skills import PostgresSkillLibraryService
from agentos.skills.models import Skill, SkillScope, SkillSource

def test_plugin_skills_are_registered_and_removed(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    library = PostgresSkillLibraryService(engine)
    skill = Skill(id="demo:brainstorming", name="Brainstorming", version="1.0.0", description="d", instructions="i", scope=SkillScope.USER, source=SkillSource.PLUGIN, package_path=tmp_path)
    library.install_plugin_skills(user_id="u1", plugin_id="demo", skills=(skill,))
    assert library.registry_for("u1").resolve("demo:brainstorming").name == "Brainstorming"
    library.remove_plugin_skills(user_id="u1", plugin_id="demo")
    try:
        library.registry_for("u1").resolve("demo:brainstorming")
    except Exception:
        pass
    else:
        raise AssertionError("plugin skill remains")
