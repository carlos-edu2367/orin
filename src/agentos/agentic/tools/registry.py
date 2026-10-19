"""Every built-in tool spec, in the order the model has always seen them (W4)."""
from __future__ import annotations

from functools import lru_cache

from agentos.agentic.tools.files.read import SPECS as _files_read
from agentos.agentic.tools.files.write import SPECS as _files_write
from agentos.agentic.tools.files.edit import SPECS as _files_edit
from agentos.agentic.tools.files.notebook import SPECS as _files_notebook
from agentos.agentic.tools.search.glob import SPECS as _search_glob
from agentos.agentic.tools.search.grep import SPECS as _search_grep
from agentos.agentic.tools.planning.plan_mode import SPECS as _planning_plan_mode
from agentos.agentic.tools.orin.verify import SPECS as _orin_verify
from agentos.agentic.tools.planning.todo import SPECS as _planning_todo
from agentos.agentic.tools.planning.tasks import SPECS as _planning_tasks
from agentos.agentic.tools.web.fetch import SPECS as _web_fetch
from agentos.agentic.tools.interaction.ask_user import SPECS as _interaction_ask_user
from agentos.agentic.tools.web.search import SPECS as _web_search
from agentos.agentic.tools.orin.search_code import SPECS as _orin_search_code
from agentos.agentic.tools.shell.bash import SPECS as _shell_bash
from agentos.agentic.tools.shell.tasks import SPECS as _shell_tasks
from agentos.agentic.tools.git.worktree import SPECS as _git_worktree
from agentos.agentic.tools.orin.preview import SPECS as _orin_preview
from agentos.agentic.tools.orin.memory import SPECS as _orin_memory
from agentos.agentic.tools.agents.agent import SPECS as _agents_agent
from agentos.agentic.tools.orin.skills_admin import SPECS as _orin_skills_admin
from agentos.agentic.tools.mcp.admin import SPECS as _mcp_admin
from agentos.agentic.tools.orin.plugins_admin import SPECS as _orin_plugins_admin
from agentos.agentic.tools.orin.projects import SPECS as _orin_projects
from agentos.agentic.tools.orin.media import SPECS as _orin_media
from agentos.agentic.tools.spec import ToolRegistry


@lru_cache(maxsize=1)
def default_registry() -> ToolRegistry:
    return ToolRegistry((
        *_files_read,
        *_files_write,
        *_files_edit,
        *_files_notebook,
        *_search_glob,
        *_search_grep,
        *_planning_plan_mode,
        *_orin_verify,
        *_planning_todo,
        *_planning_tasks,
        *_web_fetch,
        *_interaction_ask_user,
        *_web_search,
        *_orin_search_code,
        *_shell_bash,
        *_shell_tasks,
        *_git_worktree,
        *_orin_preview,
        *_orin_memory,
        *_agents_agent,
        *_orin_skills_admin,
        *_mcp_admin,
        *_orin_plugins_admin,
        *_orin_projects,
        *_orin_media,
    ))
