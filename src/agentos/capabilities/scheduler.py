from __future__ import annotations

from .models import CapabilityProgram


class ProgramValidationError(ValueError):
    """The typed program cannot be scheduled safely."""


class DeterministicStepScheduler:
    def __init__(self, maximum_parallel_steps: int) -> None:
        if maximum_parallel_steps < 1:
            raise ValueError("maximum_parallel_steps must be positive")
        self.maximum_parallel_steps = maximum_parallel_steps

    def validate(self, program: CapabilityProgram) -> None:
        graph = {str(step.step_id): {str(dep) for dep in step.dependencies} for step in program.steps}
        pending = {name: set(dependencies) for name, dependencies in graph.items()}
        while pending:
            ready = sorted(name for name, deps in pending.items() if not deps)
            if not ready:
                raise ProgramValidationError("program dependency graph contains a cycle")
            for name in ready:
                pending.pop(name)
            for deps in pending.values():
                deps.difference_update(ready)

    def ready(
        self,
        program: CapabilityProgram,
        *,
        completed: tuple[str, ...] | list[str],
        active: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        self.validate(program)
        completed_set = set(completed)
        active_set = set(active)
        values = []
        for step in sorted(program.steps, key=lambda item: str(item.step_id)):
            step_id = str(step.step_id)
            if step_id in completed_set or step_id in active_set:
                continue
            if all(str(dep) in completed_set for dep in step.dependencies):
                values.append(step_id)
        return tuple(values[: self.maximum_parallel_steps - len(active_set)])


__all__ = ["DeterministicStepScheduler", "ProgramValidationError"]
