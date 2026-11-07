"""Code mode's confirmation guard for commands with production effects (deploy, publish, DB writes, push, PR).

What needs a person's confirmation is decided by effect (``production_actions`` and ``publish_actions``), never
by a word in the command. A confirmation can come from three places, checked in this order:

1. The person already approved it in the conversation: their answer to an AskUserQuestion (in this turn, or the
   message that started this turn) is affirmative and the question, or the answer itself, is about this kind of
   action. "Pode aplicar tudo" answering "Posso fazer o deploy das duas edge functions?" approves the deploys.
2. An approval card: the run can ask (interactive permissions), so the person sees the exact command and what it
   does, and clicks "Aprovar". Such a card is never turned into a permanent allow rule.
3. Otherwise nothing runs, and the result says exactly what was blocked, that no part of the command ran, and
   how to get the approval.

Full autonomy (bypassPermissions) permits git push and pull requests; production effects always need one of the
confirmations above.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from agentos.agentic.tools._shared import ToolOutcome
from agentos.agentic.tools.shell.production import ProductionAction, production_actions
from agentos.agentic.tools.shell.publish import publish_actions

if TYPE_CHECKING:  # pragma: no cover
    from agentos.agentic.agent_tools import AgentToolset

ANSWERS_HEADER = "Respostas às perguntas do agente:"

_TOPIC_PATTERNS: dict[str, str] = {
    "deploy": r"deploy|publica|publish|produ[cç][aã]o|production|\bprod\b|subir|sobe|release|edge function|fun[cç](?:[aã]o|[oõ]es)|vercel|netlify|supabase|firebase|wrangler|segredo|secret",
    "db_write": r"migra|banco|database|\bdb\b|schema|\bsql\b|tabela|table|coluna|column|produ[cç][aã]o|production|supabase|alter|insert|update|delete",
    "publish": r"publica|publish|release|npm|pacote|package|imagem|image|docker|vers[aã]o",
    "release": r"release|workflow|publica|tag|vers[aã]o|deploy",
    "infra": r"terraform|pulumi|infra|apply|kubectl|helm|cluster|produ[cç][aã]o|deploy",
    "credentials": r"chave|\bkeys?\b|segredo|secret|credencia|service.?role|token|api.?key",
    "push": r"push|enviar|envie|subir|sobe|suba|remoto|remote|origin|github|gitlab|publica",
    "pr": r"pull request|\bpr\b|github|merge request",
}
# Narrower: the words that name the action itself, for an answer that asks for it without a question.
_ACTION_PATTERNS: dict[str, str] = {
    "deploy": r"deploy\w*|publica\w*|publish\w*|subir|sobe|suba|release",
    "db_write": r"migra\w*|migration\w*|\bsql\b|schema|banco",
    "publish": r"publica\w*|publish\w*|release",
    "release": r"release|workflow|publica\w*",
    "infra": r"\bapply\b|terraform|pulumi|infra\w*",
    "credentials": r"chaves?|\bkeys?\b|segredos?|secrets?|credencia\w*|service.?role",
    "push": r"push\w*|envi\w*|subir|sobe|suba|publica\w*",
    "pr": r"pull request|\bpr\b",
}
_AFFIRMATIVE_WORDS = (
    r"sim|pode|podem|aprov\w*|autoriz\w*|confirm\w*|ok|okay|yes|yep|sure|libera\w*|go ahead|proceed|prossiga|"
    r"manda|mande|aplica|aplique|aplicar|segue|siga|claro|bora|fa[cç]a|faz|fazer|execute|rode|vai em frente|"
    r"publique|publica|suba|sobe|subir|envie|do it|run"
)
_AFFIRMATIVE = re.compile(rf"\b(?:{_AFFIRMATIVE_WORDS})\b", re.IGNORECASE)
_NEGATIVE = re.compile(
    r"^\s*(n[aã]o|no|nope|nega\w*|cancel\w*|recus\w*|pare|stop|espera|espere|aguarde|ainda n[aã]o|nunca|jamais)\b"
    r"|\bn[aã]o\s+(fa[cç]a|faz|pode|podem|aplique|aplica|publique|suba|sobe|rode|execute|mexa|altere|envie|quero)\b"
    r"|\b(don'?t|do not|never)\b|\bsem\s+(deploy|push|publicar)\b",
    re.IGNORECASE,
)


def parse_answer_blocks(text: str) -> list[tuple[str, str]]:
    """(question, answer) pairs of the UI's answer message; a plain message is one pair with no question."""
    body = str(text or "").strip()
    if not body.startswith(ANSWERS_HEADER):
        return [("", body)] if body else []
    pairs: list[tuple[str, str]] = []
    for block in re.split(r"\n\s*\n", body[len(ANSWERS_HEADER):]):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        question_lines = [line for line in lines if not line.startswith(("Seleção:", "Observação:"))]
        answer_lines = [line.split(":", 1)[1].strip() for line in lines if line.startswith(("Seleção:", "Observação:"))]
        answer = " · ".join(part for part in answer_lines if part and part.lower() != "nenhuma")
        if answer:
            pairs.append((" ".join(question_lines), answer))
    return pairs


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, Mapping))
    return ""


def dialogue_pairs(history: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(question, answer) pairs from the last message the person sent in ``history``.

    The UI's AskUserQuestion answer carries its questions. A plain message is paired with the question(s) the
    agent asked at the end of its previous message, so "pode" answering "Faço o deploy agora?" counts.
    """
    items = [item for item in history if isinstance(item, Mapping)]
    index = len(items) - 1
    while index >= 0 and not (items[index].get("role") == "user" and _text_of(items[index].get("content")).strip()):
        index -= 1
    if index < 0:
        return []
    pairs = parse_answer_blocks(_text_of(items[index].get("content")))
    if pairs and all(question for question, _ in pairs):
        return pairs
    asked = ""
    for previous in reversed(items[:index]):
        role = previous.get("role")
        if role == "user":
            break
        content = _text_of(previous.get("content")).strip()
        if role == "assistant" and content:
            sentences = re.split(r"(?<=[.!?])\s+|\n+", content[-1500:])
            asked = " ".join(sentence.strip() for sentence in sentences if sentence.strip().endswith("?"))
            break
    return [(asked, answer) for _, answer in pairs]


def _mentions(text: str, kinds: Iterable[str]) -> bool:
    return any(re.search(_TOPIC_PATTERNS.get(kind, kind), text or "", re.IGNORECASE) for kind in kinds)


def _asks_for(text: str, kinds: Iterable[str]) -> bool:
    """An approval verb close before the action ("faça o deploy", "pode subir") or right after it ("deploy liberado")."""
    for kind in kinds:
        topic = _ACTION_PATTERNS.get(kind, re.escape(kind))
        before = rf"\b(?:{_AFFIRMATIVE_WORDS})\b(?:\W+[\w-]+){{0,5}}?\W+(?:{topic})"
        after = rf"(?:{topic})\w*(?:\W+[\w-]+){{0,2}}?\W+(?:liberad\w*|aprovad\w*|autorizad\w*|ok|pode)\b"
        if re.search(before, text or "", re.IGNORECASE) or re.search(after, text or "", re.IGNORECASE):
            return True
    return False


def dialogue_approves(pairs: Iterable[tuple[str, str]], kinds: Iterable[str]) -> bool:
    """True when some answer approves this kind of action.

    ``question`` is what the person was answering (an AskUserQuestion, or the question the agent asked at the end
    of its last message); it may be empty. A negative answer never approves. An affirmative answer approves when
    the question was about this action; without such a question the answer itself must ask for the action.
    """
    wanted = tuple(kinds)
    for question, answer in pairs:
        if not answer or _NEGATIVE.search(answer):
            continue
        if _asks_for(answer, wanted):
            return True
        if question and _AFFIRMATIVE.search(answer) and _mentions(question, wanted):
            return True
    return False


def _approval_pairs(toolset: "AgentToolset") -> list[tuple[str, str]]:
    pairs = list(getattr(toolset, "_user_answers", []) or [])
    source: Callable[[], Iterable[tuple[str, str]]] | None = getattr(toolset, "_approval_dialogue", None)
    if callable(source):
        try:
            pairs.extend(source())
        except Exception:  # noqa: BLE001 - an unreadable history only means no prior approval
            pass
    return pairs


def _permits_full_autonomy(toolset: "AgentToolset") -> bool:
    mode = getattr(toolset, "_permission_mode", None)
    return str(getattr(mode, "value", mode) or "") == "full_autonomy"


def _blocked_actions(toolset: "AgentToolset", name: str, command: str) -> tuple[list[ProductionAction], list[tuple[str, str, str]]]:
    production = production_actions(command, name, cwd=getattr(toolset, "_current_cwd", None))
    publish = publish_actions(command, name)
    blocked: list[tuple[str, str, str]] = [(action.kind, action.label, action.segment) for action in production]
    full = _permits_full_autonomy(toolset)
    if "push" in publish and not (toolset._code_mode_permits_push or full):
        blocked.append(("push", "git push (envia commits para o repositório remoto)", ""))
    if publish & {"pr", "repo"} and not (toolset._code_mode_permits_pr or full):
        blocked.append(("pr", "pull request / repositório no GitHub (gh)", ""))
    return production, blocked


def needs_confirmation(toolset: "AgentToolset", name: str, arguments: Mapping[str, Any]) -> bool:
    """True when ``guard_sensitive_command`` will ask for this command, so an ordinary permission prompt is redundant."""
    try:
        return bool(_blocked_actions(toolset, name, str(arguments.get("command") or ""))[1])
    except Exception:  # noqa: BLE001 - when unsure, the ordinary prompt still asks
        return False


def guard_sensitive_command(toolset: "AgentToolset", name: str, arguments: Mapping[str, Any], tool_kind: str) -> ToolOutcome | None:
    """None when the command may run; otherwise the failed outcome the model reads (nothing has run)."""
    command = str(arguments.get("command") or "")
    production, blocked = _blocked_actions(toolset, name, command)
    if not blocked:
        return None
    kinds = {kind for kind, _, _ in blocked}
    error_code = "CODE_DEPLOY_CONFIRMATION_REQUIRED" if production else "CODE_PUSH_CONFIRMATION_REQUIRED"
    title = "Confirmação necessária para produção" if production else "Confirmação necessária para publicar"
    lines = "\n".join(f"- {label}" + (f" — trecho: `{segment[:160]}`" if segment else "") for _, label, segment in blocked)
    payload = {"tool_kind": tool_kind, "code_mode": True, "sensitive_actions": [label for _, label, _ in blocked]}

    approvals: set[str] = getattr(toolset, "_sensitive_approvals", set())
    if command in approvals or dialogue_approves(_approval_pairs(toolset), kinds):
        approvals.add(command)
        toolset._sensitive_approvals = approvals
        return None

    if getattr(toolset, "_interactive_permissions", False):
        request = {
            "tool_name": name, "arguments": arguments, "turn_id": toolset.turn_id,
            "conversation_id": toolset.conversation_id, "permission_mode": toolset._permission_mode,
            "emit_request": toolset._on_permission_request,
        }
        try:
            try:
                result = toolset._permission_broker.request_and_wait(
                    **request, sensitive={"kinds": sorted(kinds), "labels": [label for _, label, _ in blocked]},
                )
            except TypeError:  # a broker without production cards still asks for the command itself
                result = toolset._permission_broker.request_and_wait(**request)
        except Exception as error:  # noqa: BLE001 - a broken card must not run the command
            result = {"decision": "deny", "reason": f"falha ao pedir aprovação: {error}"}
        if result.get("decision") in {"allow_once", "allow_always"}:
            approvals.add(command)
            toolset._sensitive_approvals = approvals
            return None
        reason = str(result.get("reason") or "").strip()
        status = str(result.get("status") or "")
        why = "a aprovação expirou sem resposta" if status == "timed_out" else "a pessoa negou a aprovação"
        return ToolOutcome(
            "failed", "Ação de produção não aprovada",
            f"Nada foi executado: {why}, então o comando inteiro foi barrado antes de rodar (nenhum trecho dele rodou).\n"
            f"Ações que precisavam de aprovação:\n{lines}"
            + (f"\nOrientação da pessoa: {reason}" if reason else ""),
            {**payload, "permission_request_id": result.get("request_id")}, error_code,
        )

    return ToolOutcome(
        "failed", title,
        "Nada foi executado: o comando inteiro foi barrado antes de rodar (nenhum trecho dele rodou, nem os de leitura).\n"
        f"Precisa da confirmação explícita da pessoa:\n{lines}\n"
        "Como liberar: pergunte à pessoa com AskUserQuestion citando a ação e o alvo (ex.: \"Posso fazer o deploy da função X "
        "em produção?\"). Se a resposta aprovar, rode o mesmo comando de novo; a aprovação vale para o resto deste turno. "
        "Comandos só de leitura (git status, git log, grep) podem rodar separados, sem esta confirmação.",
        payload, error_code,
    )


__all__ = ["ANSWERS_HEADER", "dialogue_approves", "guard_sensitive_command", "parse_answer_blocks"]
