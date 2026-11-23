"""``orin pipeline list|status|resume|cancel``: drive pipeline runs of the local Orin from a terminal (WS-C10).

Decision S3: the worker would pick up a turn written straight into the database, but it exists only while Orin is
running and then the API is there too. So every command calls the local API with this installation's token (the same
path as ``orin code``: authentication, idempotency, the conversation lock and the events come for free). Writing to the
database from here is deliberately not offered: with no running Orin nothing would execute the turn, and the watchdog
would fail it after 45 seconds. A command with Orin stopped answers "inicie o Orin" and exits with code 2.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agentos.cli_code.errors import CliRuntimeError
from agentos.installation import OrinPaths

from .ui import Console

_CODE = re.compile(r"\((\d{3})(?:: ([A-Za-z_]+))?\)")
_MESSAGES = {
    "PIPELINE_RUN_NOT_FOUND": "Execução não encontrada.",
    "PIPELINE_NOT_RESUMABLE": "Esta execução não pode ser retomada no estado atual.",
    "PIPELINE_NOT_CANCELLABLE": "Esta execução não pode ser cancelada no estado atual.",
    "PIPELINE_BUDGET_OVERRIDE_REQUIRED": "O orçamento acabou. Para continuar mesmo assim, use --budget-override (o contador recomeça do zero).",
    "PIPELINE_DEFINITION_CHANGED": (
        "A definição do pipeline mudou desde o início da execução. Use --definition original para retomar com a definição "
        "do início, ou --definition new_run para iniciar uma nova execução com a definição atual."
    ),
    "PIPELINE_NOT_APPROVED": "O pipeline do projeto não está mais aprovado. Aprove-o de novo na tela de pipelines.",
    "PIPELINE_SNAPSHOT_MISSING": "A execução não guardou a definição do pipeline e não pode ser retomada.",
    "PIPELINE_INVALID": "A definição guardada não passa mais na validação.",
    "E_DISABLED": "Os pipelines estão desligados nas configurações (`pipelines.enabled`).",
    "capability_unavailable": "Pipelines só funcionam no Orin local (sem pastas do computador no servidor compartilhado).",
}
EXIT_REFUSED = 1
EXIT_NOT_RUNNING = 2


def add_pipeline_parser(commands: Any) -> None:
    pipeline = commands.add_parser("pipeline", help="list, inspect, resume or cancel pipeline runs")
    actions = pipeline.add_subparsers(dest="pipeline_command", metavar="action", required=True)
    listing = actions.add_parser("list", help="list recent runs")
    listing.add_argument("--conversation", default=None, metavar="ID", help="only the runs of this conversation")
    listing.add_argument("--state", default=None, help="only runs in this state (running, interrupted, failed, ...)")
    status = actions.add_parser("status", help="show one run, step by step")
    status.add_argument("run_id")
    resume = actions.add_parser("resume", help="continue a stopped run from its journal")
    resume.add_argument("run_id")
    resume.add_argument("--rerun-gates", action="store_true", help="run the gates again instead of reusing the journaled result")
    resume.add_argument("--budget-override", action="store_true", help="continue a run that stopped on its budget (the counter restarts)")
    resume.add_argument("--definition", choices=("original", "new_run"), default=None, help="what to do when the pipeline file changed")
    cancel = actions.add_parser("cancel", help="cancel a run")
    cancel.add_argument("run_id")


def _explain(error: CliRuntimeError) -> str:
    match = _CODE.search(str(error))
    code = match.group(2) if match else None
    if code and code in _MESSAGES:
        return _MESSAGES[code]
    return f"O Orin recusou o pedido ({code or str(error)})."


def _money(value: Any) -> str:
    return "desconhecido" if value is None else f"{float(value):.4f}".replace(".", ",")


def _print_run(console: Console, run: dict[str, Any]) -> None:
    reason = f" ({run['stop_reason']})" if run.get("stop_reason") else ""
    console.line(f"{run['run_id']}  {run['pipeline_id']}  {run['state']}{reason}  {run['used_tokens']} tokens  US$ {_money(run['used_cost_usd'])}")


def command_pipeline(
    arguments: Any, paths: OrinPaths, console: Console, *, connect: Callable[[], Any] | None = None
) -> int:
    from agentos.cli_code.runtime import connect_local

    try:
        client = connect() if connect is not None else connect_local(paths, start=False)
    except CliRuntimeError:
        console.error("O Orin não está em execução. Inicie com `orin start` e tente de novo.")
        return EXIT_NOT_RUNNING
    action = arguments.pipeline_command
    try:
        if action == "list":
            params = {key: value for key, value in (("conversation_id", arguments.conversation), ("state", arguments.state)) if value}
            runs = client.get("/v1/pipeline-runs", **params)["runs"]
            if not runs:
                console.line("Nenhuma execução de pipeline.")
            for run in runs:
                _print_run(console, run)
            return 0
        if action == "status":
            run = client.get(f"/v1/pipeline-runs/{arguments.run_id}")
            _print_run(console, run)
            for step in run["steps"]:
                item = f"[{step['item_key']}]" if step["item_key"] else ""
                console.line(f"  {step['step_id']}{item}  {step['state']}  tentativa {step['attempt']}  {step['input_tokens'] + step['output_tokens']} tokens")
            if run["state"] in {"interrupted", "failed", "cancelled", "budget_exceeded"}:
                console.line(f"Para continuar: orin pipeline resume {run['run_id']}")
            return 0
        if action == "resume":
            body = {"rerun_gates": bool(arguments.rerun_gates), "budget_override": bool(arguments.budget_override), "definition": arguments.definition}
            result = client.post(f"/v1/pipeline-runs/{arguments.run_id}/resume", body)
            console.line(f"Execução {result['run_id']} retomada (turno {result['turn_id']}).")
            return 0
        if action == "cancel":
            client.post(f"/v1/pipeline-runs/{arguments.run_id}/cancel", {})
            console.line(f"Cancelamento de {arguments.run_id} pedido.")
            return 0
    except CliRuntimeError as error:
        console.error(_explain(error))
        return EXIT_REFUSED
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    console.error("Ação desconhecida.")
    return EXIT_REFUSED


__all__ = ["add_pipeline_parser", "command_pipeline"]
