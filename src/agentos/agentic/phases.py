"""The stages a turn moves through, and what the agent can reach in each.

Two problems share one cause. Fifty tools were published on every request,
which is a decision space a small model cannot navigate reliably; and the
loop had no stage structure, so a model that started badly kept going badly
until it ran out of patience rather than out of budget.

Phases fix both. Each publishes a small, relevant tool set and carries its
own budget, and the runtime -- never the model -- decides when one ends.

The sequencing is deliberately not a funnel. ``ORIENT`` already carries the
working tools, so an ordinary task finishes there in exactly the number of
provider calls it takes today; what it does not carry is the browser, MCP,
plugins and delegation, which is where the fifty came from. ``PLAN`` is
entered when ``ORIENT`` runs out of budget without finishing -- the moment
the agent is demonstrably flailing and should stop and commit to a contract
instead of trying one more thing.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class Phase(StrEnum):
    ORIENT = "orient"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    RESPOND = "respond"


@dataclass(frozen=True, slots=True)
class PhaseBudget:
    iterations: int
    actions: int


DEFAULT_PHASE_BUDGETS: Mapping[Phase, PhaseBudget] = {
    # Enough to read, search and do ordinary work. Running out is the signal
    # that this task needs a contract, not one more attempt.
    Phase.ORIENT: PhaseBudget(iterations=6, actions=20),
    Phase.PLAN: PhaseBudget(iterations=2, actions=3),
    Phase.EXECUTE: PhaseBudget(iterations=20, actions=60),
    Phase.VERIFY: PhaseBudget(iterations=4, actions=10),
    Phase.RESPOND: PhaseBudget(iterations=1, actions=0),
}

# Tools every working phase needs. Naming them explicitly is the point: the
# alternative is publishing whatever happens to be registered.
_READ_TOOLS = (
    "read_file", "view_file", "transcribe_pdf", "list_files", "search_files", "search_code",
    "read_process_output",
)
# ``verify_project`` must be reachable while implementing: Code mode cannot
# earn structured validation evidence otherwise. ``stop_process`` remains in
# the terminal toolkit, which preserves the 16-tool ceiling for the common
# files+terminal contract.
_WRITE_TOOLS = ("write_file", "edit_file", "run_command", "verify_project")
_ALWAYS = ("write_contract", "ask_user")

PHASE_TOOLS: Mapping[Phase, tuple[str, ...]] = {
    Phase.ORIENT: (*_ALWAYS, *_READ_TOOLS, *_WRITE_TOOLS, "recall", "remember"),
    Phase.PLAN: (*_ALWAYS, "search_skills", "list_skills", "use_skill", "recall", "list_files", "read_file"),
    Phase.EXECUTE: (*_ALWAYS, *_READ_TOOLS, *_WRITE_TOOLS, "recall", "remember"),
    # Verification may look and may run a check; it may not change anything.
    # ``verify_project``/``verify_frontend`` mutate nothing themselves but can
    # install dependencies or start a browser session as a side effect of
    # actually checking something, which is why they sit with ``run_command``
    # here rather than with the read-only inspection tools.
    Phase.VERIFY: (
        "read_file", "view_file", "list_files", "search_files",
        "run_command", "read_process_output", "verify_project",
        "browser_observe", "browser_screenshot", "verify_frontend",
        "report_verification",
    ),
    Phase.RESPOND: (),
}

# Families a contract can declare. Only what the contract asked for is
# published, which is what keeps the browser's twelve tools and every MCP
# server out of a request that has no use for them.
TOOLKIT_TOOLS: Mapping[str, tuple[str, ...]] = {
    "files": (*_READ_TOOLS, *_WRITE_TOOLS),
    "terminal": ("run_command", "read_process_output", "stop_process"),
    "web": ("fetch_url", "web_search"),
    "browser": (
        "browse_page", "browser_observe", "browser_click", "browser_fill", "browser_press",
        "browser_select", "browser_check", "browser_screenshot", "browser_back",
        "browser_scroll", "browser_wait_for", "browser_submit",
    ),
    "delegation": ("create_agent", "ask_agent", "ask_agents"),
}
# ``mcp`` and ``plugins`` are declared the same way but resolve by tool kind
# rather than by name, since their tools are discovered at runtime.
TOOLKIT_KINDS: Mapping[str, tuple[str, ...]] = {"mcp": ("mcp",), "plugins": ("plugin",)}

# What a phase adds to the system prompt. Short on purpose: a small model
# reading fifteen lines about the current stage follows them; the same model
# reading two hundred lines covering every stage at once does not.
PHASE_INSTRUCTIONS: Mapping[Phase, str] = {
    Phase.ORIENT: (
        "## Agora\n"
        "- Entenda o pedido e o estado do workspace, e resolva a tarefa se ela for direta.\n"
        "- Se em poucos passos ficar claro que a tarefa é maior do que parecia, chame `write_contract` "
        "para fixar objetivo, entregáveis, restrições e critérios de aceite antes de continuar.\n"
        "- Ferramentas de navegador, MCP, plugins e subagentes só ficam disponíveis depois que o "
        "contrato declarar que você precisa delas."
    ),
    Phase.PLAN: (
        "## Agora\n"
        "- Você gastou o orçamento desta etapa sem concluir. Pare de tentar e declare o plano.\n"
        "- Chame `write_contract` com objetivo, entregáveis, restrições, critérios de aceite verificáveis "
        "e as famílias de ferramenta que a tarefa realmente exige.\n"
        "- Não execute nada nesta etapa."
    ),
    Phase.EXECUTE: (
        "## Agora\n"
        "- Cumpra o contrato. Ele está acima e continua visível.\n"
        "- Trabalhe pelos critérios de aceite, não pela sua impressão de progresso.\n"
        "- Para um projeto novo, comece pelo gerador oficial da stack através de `verify_project` com `scaffold`; "
        "não escreva package.json ou a estrutura inicial à mão, salvo se o gerador falhar e você registrar a ressalva.\n"
        "- Se a tarefa se revelar diferente do contrato, reescreva o contrato em vez de improvisar."
    ),
    Phase.VERIFY: (
        "## Agora\n"
        "- Pare de produzir e confira o que existe, critério por critério do contrato.\n"
        "- Você só pode ler e rodar verificações; nada de alterar arquivos nesta etapa.\n"
        "- Prefira `verify_project` a adivinhar comandos, e `verify_frontend` para confirmar que uma página renderiza de verdade.\n"
        "- Um critério que você não conseguir verificar deve ser declarado como não verificado.\n"
        "- Termine chamando `report_verification` com o resultado real. Se algo não se sustentar, descreva o erro "
        "concreto — você voltará para corrigir. Uma resposta em texto livre não encerra esta etapa."
    ),
    Phase.RESPOND: (
        "## Agora\n"
        "- Responda à pessoa com o que foi efetivamente feito.\n"
        "- Diga claramente o que ficou faltando e o que não foi possível verificar.\n"
        "- Não peça mais ferramentas."
    ),
}

_ORDER = (Phase.ORIENT, Phase.PLAN, Phase.EXECUTE, Phase.VERIFY, Phase.RESPOND)


class PhaseController:
    """Owns which phase a turn is in and when that changes.

    The model never selects a phase. It observes the one it is in, through
    the prompt block and the published tools, and the controller advances on
    evidence: a contract was written, or a budget ran out.
    """

    def __init__(
        self,
        *,
        budgets: Mapping[Phase, PhaseBudget] | None = None,
        model_calls_tools: bool = True,
        resumed_contract: bool = False,
    ) -> None:
        self.budgets = dict(budgets or DEFAULT_PHASE_BUDGETS)
        self.model_calls_tools = bool(model_calls_tools)
        # A model that cannot call tools has one thing to do, and the phase
        # machinery would only get in the way of it.
        if not self.model_calls_tools:
            self.current = Phase.RESPOND
        elif resumed_contract:
            # A follow-up on a task that already has a contract resumes the
            # work; re-orienting would rediscover what the transcript already
            # carries.
            self.current = Phase.EXECUTE
        else:
            self.current = Phase.ORIENT
        self._iterations = 0
        self._actions = 0

    @property
    def budget(self) -> PhaseBudget:
        return self.budgets.get(self.current, DEFAULT_PHASE_BUDGETS[self.current])

    @property
    def exhausted(self) -> bool:
        budget = self.budget
        return self._iterations >= budget.iterations or self._actions >= budget.actions

    @property
    def is_final(self) -> bool:
        return self.current is Phase.RESPOND

    def note_iteration(self, actions: int = 0, *, productive: bool = False) -> None:
        """Count one iteration against the phase's budget, unless it earned a pass.

        The budget exists to catch flailing -- an agent that repeats itself
        without moving forward -- not to cap real work. An iteration that
        wrote a successful change or produced a verification result is
        exempted; the turn's outer limits (deadline, total actions) are the
        backstop that still bounds it.
        """
        if productive:
            return
        self._iterations += 1
        self._actions += max(0, int(actions))

    def observe(self, *, wrote_contract: bool) -> None:
        """Advance if this iteration produced a reason to.

        Writing a contract during ``ORIENT`` means the agent has already
        planned, so ``PLAN`` would be ceremony; it goes straight to work.
        """
        if wrote_contract and self.current in (Phase.ORIENT, Phase.PLAN):
            self._enter(Phase.EXECUTE)
            return
        if self.exhausted:
            self._enter(self._next())

    def force_execute(self) -> None:
        """Used when a contract had to be synthesized for a model that could not write one,
        or when a failed verification sends the agent back to fix what it found."""
        self._enter(Phase.EXECUTE)

    def force_verify(self) -> None:
        """Used when the runtime detects an unverified change the model tried to finish without checking."""
        self._enter(Phase.VERIFY)

    def force_respond(self) -> None:
        """Used when verification passed, or its repair-round budget ran out."""
        self._enter(Phase.RESPOND)

    def _next(self) -> Phase:
        index = _ORDER.index(self.current)
        return _ORDER[min(index + 1, len(_ORDER) - 1)]

    def _enter(self, phase: Phase) -> None:
        if phase is self.current:
            return
        self.current = phase
        self._iterations = 0
        self._actions = 0


def tools_for(phase: Phase, toolkits: frozenset[str] | None = None) -> frozenset[str]:
    """Tool names this phase publishes, given what the contract declared.

    A phase's own set is always available; a toolkit only adds to it, and
    only when the contract named it. There is no truncation: silently
    dropping a tool the task needs would trade one failure mode for a worse,
    quieter one.
    """
    names = set(PHASE_TOOLS.get(phase, ()))
    if phase in (Phase.ORIENT, Phase.EXECUTE):
        for toolkit in toolkits or frozenset():
            names.update(TOOLKIT_TOOLS.get(toolkit, ()))
    return frozenset(names)


def kinds_for(toolkits: frozenset[str] | None) -> frozenset[str]:
    """Tool *kinds* the contract opened up, for families discovered at runtime."""
    kinds: set[str] = set()
    for toolkit in toolkits or frozenset():
        kinds.update(TOOLKIT_KINDS.get(toolkit, ()))
    return frozenset(kinds)


__all__ = [
    "DEFAULT_PHASE_BUDGETS", "PHASE_INSTRUCTIONS", "PHASE_TOOLS", "Phase", "PhaseBudget",
    "PhaseController", "TOOLKIT_KINDS", "TOOLKIT_TOOLS", "kinds_for", "tools_for",
]
