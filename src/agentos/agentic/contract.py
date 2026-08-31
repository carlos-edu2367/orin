"""The task the agent agreed to do, in a shape it can be held to.

The loop had no notion of a goal. It ran until the model chose to stop, with
nothing recording what "done" was supposed to mean, so a strong model
compensated by reasoning and a weaker one drifted: it forgot constraints,
declared success early, or explored forever.

A contract fixes the objective, the deliverables, the constraints and --
most importantly -- the acceptance criteria, before any work starts. It is
pinned into every request of the turn, survives into the next turn, and is
what the verification phase checks against.

It is a commitment, not a program: the agent still decides how to satisfy
it. That is why ``steps`` is advisory and ``acceptance`` is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from agentos.code_mode.models import detect_code_request


# Coarse families of capability a task can need. Naming them in the contract
# is what lets a phase publish ten relevant tools instead of fifty, which is
# the difference between a small model choosing well and choosing at random.
TOOLKITS = frozenset({"files", "terminal", "web", "browser", "delegation", "mcp", "plugins"})

# How an acceptance criterion gets confirmed: by running something, or by
# looking at something. Anything else is not a criterion, it is a hope.
VERIFICATION_MODES = frozenset({"tool", "inspection"})

MAX_OBJECTIVE_CHARS = 500
MAX_ITEMS = 12


def _is_frontend_work(objective: str, deliverables: Sequence["Deliverable"] = ()) -> bool:
    """Conservative frontend signal used only to demand missing verification."""
    text = " ".join((objective, *(item.path + " " + item.description for item in deliverables))).lower()
    return any(token in text for token in ("frontend", "react", "next", "vite", "spa", "página", "pagina", "tela", ".tsx", ".jsx", ".vue", ".svelte"))


def _is_software_work(objective: str, toolkits: set[str]) -> bool:
    return "terminal" in toolkits or detect_code_request(objective) is not None


def _require_mechanical_acceptance(
    *, objective: str, toolkits: set[str], acceptance: Sequence["Acceptance"], deliverables: Sequence["Deliverable"] = (),
) -> None:
    """Keep code contracts tied to executable evidence rather than inspection."""
    if not _is_software_work(objective, toolkits):
        return
    if "terminal" not in toolkits:
        raise ContractError("Tarefas de software precisam declarar o toolkit 'terminal' para verificar instalação, build, typecheck, lint ou testes.")
    if not any(item.how == "tool" for item in acceptance):
        raise ContractError("Tarefas de software precisam de ao menos um critério de aceite com how='tool' para uma verificação mecânica.")
    if _is_frontend_work(objective, deliverables) and "browser" not in toolkits:
        raise ContractError("Tarefas de frontend precisam declarar o toolkit 'browser' para verificar que uma rota renderiza.")


class ContractError(ValueError):
    """The contract is not usable, and the message says which field is at fault."""


@dataclass(frozen=True, slots=True)
class Deliverable:
    path: str
    description: str


@dataclass(frozen=True, slots=True)
class Acceptance:
    id: str
    check: str
    how: str


@dataclass(frozen=True, slots=True)
class TaskContract:
    objective: str
    acceptance: tuple[Acceptance, ...]
    toolkits: frozenset[str]
    deliverables: tuple[Deliverable, ...] = ()
    constraints: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()

    def render(self) -> str:
        """The block pinned into every request of the turn.

        Deliberately rebuilt per request rather than stored in the message
        list: a message can be trimmed or folded into a compaction summary,
        and the one thing that must never disappear mid-task is the
        definition of the task.
        """
        lines = ["## Contrato desta tarefa", f"Objetivo: {self.objective}"]
        if self.deliverables:
            lines.append("Entregáveis:")
            lines += [f"- {item.path} — {item.description}" for item in self.deliverables]
        if self.constraints:
            lines.append("Restrições:")
            lines += [f"- {item}" for item in self.constraints]
        lines.append("Critérios de aceite (todos precisam ser satisfeitos antes de você declarar conclusão):")
        lines += [f"- [{item.id}] {item.check} (verificar por: {item.how})" for item in self.acceptance]
        if self.steps:
            lines.append("Passos previstos (orientação, não obrigação):")
            lines += [f"- {item}" for item in self.steps]
        lines.append("Ferramentas declaradas: " + ", ".join(sorted(self.toolkits)) + ".")
        return "\n".join(lines)

    def as_payload(self) -> dict[str, object]:
        return {
            "objective": self.objective,
            "deliverables": [{"path": item.path, "description": item.description} for item in self.deliverables],
            "constraints": list(self.constraints),
            "acceptance": [{"id": item.id, "check": item.check, "how": item.how} for item in self.acceptance],
            "toolkits": sorted(self.toolkits),
            "steps": list(self.steps),
        }


def _text(value: object, field: str, *, limit: int = MAX_OBJECTIVE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"'{field}' é obrigatório e precisa ser um texto não vazio.")
    return value.strip()[:limit]


def parse(payload: Mapping[str, object]) -> TaskContract:
    """Build a contract, or say exactly which field prevents it.

    The error text goes back to the model as a tool result, so it names the
    field and the allowed values rather than describing the failure in the
    abstract; a model that cannot see what was wrong will make the same
    mistake again.
    """
    if not isinstance(payload, Mapping):
        raise ContractError("O contrato precisa ser um objeto.")
    objective = _text(payload.get("objective"), "objective")

    raw_acceptance = payload.get("acceptance")
    if not isinstance(raw_acceptance, Sequence) or isinstance(raw_acceptance, str) or not raw_acceptance:
        raise ContractError("'acceptance' é obrigatório: liste ao menos um critério verificável.")
    acceptance: list[Acceptance] = []
    for index, item in enumerate(list(raw_acceptance)[:MAX_ITEMS]):
        if not isinstance(item, Mapping):
            raise ContractError("Cada item de 'acceptance' precisa ser um objeto com id, check e how.")
        how = str(item.get("how") or "inspection").strip()
        if how not in VERIFICATION_MODES:
            raise ContractError(f"'how' precisa ser um de: {', '.join(sorted(VERIFICATION_MODES))}. Recebido: {how!r}.")
        acceptance.append(Acceptance(
            id=str(item.get("id") or f"c{index + 1}")[:64],
            check=_text(item.get("check"), "acceptance[].check"),
            how=how,
        ))

    raw_toolkits = payload.get("toolkits")
    if not isinstance(raw_toolkits, Sequence) or isinstance(raw_toolkits, str) or not raw_toolkits:
        raise ContractError(f"'toolkits' é obrigatório: escolha entre {', '.join(sorted(TOOLKITS))}.")
    toolkits = {str(item).strip() for item in raw_toolkits}
    unknown = toolkits - TOOLKITS
    if unknown:
        raise ContractError(
            f"Toolkit desconhecido: {', '.join(sorted(unknown))}. Válidos: {', '.join(sorted(TOOLKITS))}."
        )

    deliverables = tuple(
        Deliverable(path=str(item.get("path") or "")[:512], description=str(item.get("description") or "")[:300])
        for item in (payload.get("deliverables") or ())
        if isinstance(item, Mapping) and str(item.get("path") or "").strip()
    )[:MAX_ITEMS]
    constraints = tuple(
        str(item)[:300] for item in (payload.get("constraints") or ()) if isinstance(item, str) and item.strip()
    )[:MAX_ITEMS]
    steps = tuple(
        str(item)[:300] for item in (payload.get("steps") or ()) if isinstance(item, str) and item.strip()
    )[:MAX_ITEMS]

    _require_mechanical_acceptance(
        objective=objective, toolkits=toolkits, acceptance=acceptance, deliverables=deliverables,
    )

    return TaskContract(
        objective=objective, acceptance=tuple(acceptance), toolkits=frozenset(toolkits),
        deliverables=deliverables, constraints=constraints, steps=steps,
    )


def synthesize(request: str) -> TaskContract:
    """A minimal contract derived from the person's own words.

    Used when the model has repeatedly failed to produce a valid one. A weak
    model that cannot fill the schema must still be able to work: a poor
    contract loses some of the benefit, while no contract would stall the
    turn entirely.
    """
    objective = (request or "Atender ao pedido da pessoa.").strip()[:MAX_OBJECTIVE_CHARS]
    software = detect_code_request(objective) is not None
    frontend = _is_frontend_work(objective)
    acceptance = [Acceptance(id="pedido", check=f"O pedido foi atendido: {objective}", how="inspection")]
    toolkits = {"files"}
    if software:
        acceptance.append(Acceptance(
            id="verificacao_projeto",
            check="As etapas detectadas de instalação, typecheck, lint, build e testes foram executadas e passaram.",
            how="tool",
        ))
        toolkits.add("terminal")
    if frontend:
        acceptance.append(Acceptance(
            id="renderizacao", check="A rota principal renderiza no navegador isolado.", how="tool",
        ))
        toolkits.add("browser")
    return TaskContract(
        objective=objective,
        acceptance=tuple(acceptance),
        toolkits=frozenset(toolkits),
    )


__all__ = [
    "Acceptance", "ContractError", "Deliverable", "MAX_OBJECTIVE_CHARS", "TOOLKITS",
    "TaskContract", "VERIFICATION_MODES", "parse", "synthesize",
]
