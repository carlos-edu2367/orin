"""Read how efficiently recent turns spent their tool calls, and compare runs.

The trilha's target -- "half the tool calls per completed task, redundancy
under five percent" -- only means something against a baseline measured the
same way. Every turn already records its own efficiency in
``turn_quality_metrics``; this reads that back, prints it, and diffs it
against a previous run.

Deliberately does **not** drive the agent. Sending the tasks is the
operator's job (through the interface, with whatever provider they are
benchmarking), because that is the only way the numbers describe the real
product rather than a harness. The reference tasks are in
``tests/fixtures/agent_bench/``; ``--tasks`` prints them in order.

    # 1. capture the baseline before changing anything
    python scripts/agent_bench.py --tasks
    python scripts/agent_bench.py --record baseline

    # 2. after the change, run the same tasks again and compare
    python scripts/agent_bench.py --record depois --compare baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

TASKS_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "agent_bench"
RECORDS_ROOT = REPOSITORY_ROOT / "docs" / "agent_memory" / "bench"

# The three measures the trilha is judged by, and the direction that counts as
# an improvement.
METRICS: tuple[tuple[str, str, str], ...] = (
    ("tool_calls_per_completed_turn", "Chamadas por turno concluído", "lower"),
    ("redundant_fraction", "Fração redundante", "lower"),
    ("completion_rate", "Taxa de conclusão", "higher"),
    ("input_tokens_per_completed_turn", "Tokens de entrada por turno", "lower"),
    ("cached_fraction", "Fração vinda de cache", "higher"),
)


def load_tasks() -> list[dict[str, object]]:
    if not TASKS_ROOT.is_dir():
        raise SystemExit(f"Nenhuma tarefa de referência em {TASKS_ROOT}")
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(TASKS_ROOT.glob("*.json"))]


def print_tasks() -> None:
    for task in load_tasks():
        print(f"\n[{task['category']}] {task['id']}")
        for index, message in enumerate(task["messages"], start=1):
            print(f"  {index}. {message}")


def read_quality(base_url: str, days: int) -> list[dict[str, object]]:
    """Fetch the aggregate from a running Orin."""
    import httpx  # noqa: PLC0415 - only needed on this path

    response = httpx.get(f"{base_url.rstrip('/')}/v1/runtime/quality", params={"days": days}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("items") or ())


def pick(items: Sequence[Mapping[str, object]], provider: str | None, model: str | None) -> Mapping[str, object] | None:
    """The row being benchmarked: the named model, or the busiest one."""
    for item in items:
        if provider and str(item.get("provider")) != provider:
            continue
        if model and str(item.get("model_id")) != model:
            continue
        return item
    return items[0] if items and not provider and not model else None


def change(now: object, before: object, direction: str) -> str:
    if not isinstance(now, (int, float)) or not isinstance(before, (int, float)) or not before:
        return "—"
    delta = (now - before) / before * 100
    improved = delta < 0 if direction == "lower" else delta > 0
    return f"{delta:+.1f}% {'✓' if improved else '✗'}"


def render(now: Mapping[str, object], before: Mapping[str, object] | None) -> str:
    lines = [
        f"{now.get('provider', '?')} / {now.get('model_id', '?')}  —  {now.get('turns', 0)} turnos, "
        f"{now.get('completed_turns', 0)} concluídos",
        "",
        f"{'Métrica':<34}{'Agora':>12}{'Antes':>12}{'Variação':>14}",
    ]
    for key, label, direction in METRICS:
        lines.append(
            f"{label:<34}{_cell(now.get(key)):>12}{_cell((before or {}).get(key)):>12}"
            f"{change(now.get(key), (before or {}).get(key), direction):>14}"
        )
    lines += ["", _verdict(now, before)]
    return "\n".join(lines)


def _verdict(now: Mapping[str, object], before: Mapping[str, object] | None) -> str:
    """State plainly whether the trilha's own target was met."""
    redundant = now.get("redundant_fraction")
    if isinstance(redundant, (int, float)) and redundant >= 0.05:
        redundancy = f"Redundância em {redundant:.1%} — acima do alvo de 5%."
    elif isinstance(redundant, (int, float)):
        redundancy = f"Redundância em {redundant:.1%} — dentro do alvo de 5%."
    else:
        redundancy = "Redundância não medida."
    if before is None:
        return f"{redundancy} Sem linha de base para comparar; grave esta execução com --record."
    calls_now, calls_before = now.get("tool_calls_per_completed_turn"), before.get("tool_calls_per_completed_turn")
    if isinstance(calls_now, (int, float)) and isinstance(calls_before, (int, float)) and calls_before:
        drop = (calls_before - calls_now) / calls_before
        met = "atingido" if drop >= 0.50 else "não atingido"
        return f"{redundancy} Queda de {drop:.1%} em chamadas por turno — alvo de 50% {met}."
    return f"{redundancy} Chamadas por turno não comparáveis."


def _cell(value: object) -> str:
    if value is None:
        return "—"
    return f"{value:.4g}" if isinstance(value, float) else str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", action="store_true", help="Print the reference tasks and exit")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="A running Orin")
    parser.add_argument("--days", type=int, default=1, help="How far back to aggregate")
    parser.add_argument("--provider", default=None, help="Restrict to one provider")
    parser.add_argument("--model", default=None, help="Restrict to one model id")
    parser.add_argument("--record", default=None, help="Save this run under docs/agent_memory/bench/<name>.json")
    parser.add_argument("--compare", default=None, help="A previously recorded run to diff against")
    arguments = parser.parse_args()

    if arguments.tasks:
        print_tasks()
        return 0

    try:
        items = read_quality(arguments.base_url, arguments.days)
    except Exception as error:  # noqa: BLE001 - this is a CLI, say what went wrong
        print(f"Não foi possível ler {arguments.base_url}/v1/runtime/quality: {error}", file=sys.stderr)
        return 2

    now = pick(items, arguments.provider, arguments.model)
    if now is None:
        print("Nenhum turno medido nessa janela. Rode as tarefas de referência primeiro (--tasks).", file=sys.stderr)
        return 1

    before = None
    if arguments.compare:
        path = (RECORDS_ROOT / f"{arguments.compare}.json") if not arguments.compare.endswith(".json") else Path(arguments.compare)
        if not path.exists():
            print(f"Linha de base não encontrada: {path}", file=sys.stderr)
            return 1
        before = json.loads(path.read_text(encoding="utf-8"))

    print(render(now, before))

    if arguments.record:
        RECORDS_ROOT.mkdir(parents=True, exist_ok=True)
        destination = RECORDS_ROOT / f"{arguments.record}.json"
        destination.write_text(
            json.dumps({**now, "recorded_at": datetime.now(UTC).isoformat()}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nGravado em {destination.relative_to(REPOSITORY_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
