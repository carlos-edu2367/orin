"""Control-flow graph of a pipeline: loops must be bounded, unreachable steps are reported.

Edges: the next step in file order (unless ``next`` replaces it), ``next`` jumps and
``on_fail.goto`` jumps. A ``when`` can skip a step, so a step with ``when`` keeps its
sequence edge even when it also has ``next``. Every cycle must contain at least one
``on_fail.goto`` with ``max_loops`` between 1 and 5.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .model import Step
from .schema_v1 import MAX_LOOPS_RANGE


@dataclass(frozen=True, slots=True)
class Edge:
    source: int
    target: int
    kind: str  # sequence | next | goto
    bounded: bool = False


@dataclass(frozen=True, slots=True)
class UnboundedLoop:
    """A cycle with no bounded ``goto``; ``source`` is the step holding the edge to report."""

    source: int
    target: int
    kind: str
    members: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GraphAnalysis:
    unbounded: tuple[UnboundedLoop, ...]
    unreachable: tuple[int, ...]


def build_edges(steps: Sequence[Step]) -> list[Edge]:
    index = {step.id: position for position, step in enumerate(steps)}
    edges: list[Edge] = []
    for position, step in enumerate(steps):
        target = index.get(step.next) if step.next else None
        if target is not None:
            edges.append(Edge(position, target, "next"))
        if (target is None or step.when) and position + 1 < len(steps):
            edges.append(Edge(position, position + 1, "sequence"))
        goto = index.get(step.on_fail.goto) if step.on_fail.goto else None
        if goto is not None:
            loops = step.on_fail.max_loops
            bounded = isinstance(loops, int) and MAX_LOOPS_RANGE[0] <= loops <= MAX_LOOPS_RANGE[1]
            edges.append(Edge(position, goto, "goto", bounded))
    return edges


def _components(size: int, edges: Sequence[Edge]) -> list[list[int]]:
    """Strongly connected components, iterative Tarjan."""
    adjacency: list[list[int]] = [[] for _ in range(size)]
    for edge in edges:
        adjacency[edge.source].append(edge.target)
    counter = 0
    order = [-1] * size
    low = [0] * size
    on_stack = [False] * size
    stack: list[int] = []
    result: list[list[int]] = []
    for root in range(size):
        if order[root] != -1:
            continue
        work: list[tuple[int, int]] = [(root, 0)]
        while work:
            node, cursor = work.pop()
            if cursor == 0:
                order[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack[node] = True
            advanced = False
            while cursor < len(adjacency[node]):
                neighbour = adjacency[node][cursor]
                cursor += 1
                if order[neighbour] == -1:
                    work.append((node, cursor))
                    work.append((neighbour, 0))
                    advanced = True
                    break
                if on_stack[neighbour]:
                    low[node] = min(low[node], order[neighbour])
            if advanced:
                continue
            if low[node] == order[node]:
                component: list[int] = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                result.append(sorted(component))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return result


def analyze(steps: Sequence[Step]) -> GraphAnalysis:
    size = len(steps)
    if size == 0:
        return GraphAnalysis((), ())
    edges = build_edges(steps)
    unbounded: list[UnboundedLoop] = []
    for component in _components(size, edges):
        members = set(component)
        inside = [edge for edge in edges if edge.source in members and edge.target in members]
        has_cycle = len(component) > 1 or any(edge.source == edge.target for edge in inside)
        if not has_cycle or any(edge.kind == "goto" and edge.bounded for edge in inside):
            continue
        jumps = [edge for edge in inside if edge.kind in ("next", "goto") and edge.target <= edge.source]
        jumps = jumps or [edge for edge in inside if edge.kind in ("next", "goto")]
        if not jumps:
            continue
        pick = max(jumps, key=lambda edge: (edge.kind == "next", edge.source))
        unbounded.append(UnboundedLoop(pick.source, pick.target, pick.kind, tuple(component)))
    reachable = {0}
    frontier = [0]
    adjacency: dict[int, list[int]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    while frontier:
        for neighbour in adjacency.get(frontier.pop(), ()):
            if neighbour not in reachable:
                reachable.add(neighbour)
                frontier.append(neighbour)
    unreachable = tuple(position for position in range(size) if position not in reachable)
    return GraphAnalysis(tuple(unbounded), unreachable)


__all__ = ["Edge", "GraphAnalysis", "UnboundedLoop", "analyze", "build_edges"]
