"""W6 Task 21: `orin code plugin` and `orin code <prompt>`, against the live runtime."""
from __future__ import annotations

import sys

from .live_runtime import LiveRuntime
from .test_headless import MODEL, orin_code


def _no_browser(runtime: LiveRuntime) -> dict[str, str]:
    # webbrowser runs $BROWSER with the URL: a Python that does nothing, so no window opens during the tests.
    return {**runtime.env, "BROWSER": f'"{sys.executable.replace(chr(92), "/")}" -c "import sys" %s'}



def test_plugin_list_answers(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("plugins"), "plugin", "list")
    assert done.returncode == 0, done.stderr


def test_orin_code_with_a_prompt_opens_the_session(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("open")
    done = orin_code(orin_runtime, workspace, "say hello", *MODEL, env=_no_browser(orin_runtime))
    assert done.returncode == 0, done.stderr
    url = done.stdout.strip()
    assert url.startswith(orin_runtime.url + "/chats/chat_")
    with orin_runtime.api() as api:
        conversation = api.get(f"/v1/conversations/{url.rsplit('/', 1)[1]}").json()
    assert conversation["mode"] == "code" and conversation["permission_mode"] == "ask_permissions"
