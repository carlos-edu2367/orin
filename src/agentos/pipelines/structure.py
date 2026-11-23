"""Structural validation: plain YAML data -> ``PipelineDefinition`` (schema v1 tables).

Never executes or resolves anything; it only checks shape, types, ranges and
vocabularies, collecting every problem with its YAML position. Reference, taint and
graph checks live in ``references.py``.
"""
from __future__ import annotations

import difflib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from . import schema_v1 as sv
from .errors import ReportBuilder
from .expressions import ExpressionError, PathRef, parse_expression, unwrap_expression
from .model import (
    AgentStep,
    Budget,
    CommandGate,
    Defaults,
    Gate,
    GateItem,
    GateStep,
    InputSpec,
    MapSpec,
    OnFail,
    RetryPolicy,
    SchemaGate,
    Step,
    VerifierGate,
)
from .yaml_loader import LoadedYaml

_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_IDENT_FIRST = frozenset("abcdefghijklmnopqrstuvwxyz")
_IDENT_REST = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")
_RESERVED_VARS = frozenset({"inputs", "steps"})
_AGENT_ONLY_FIELDS = ("prompt", "map", "output_schema", "isolation", "provider", "model", "effort")

KIND_LABEL = {
    "str": "texto",
    "text": "texto",
    "int": "número inteiro",
    "number": "número",
    "bool": "verdadeiro ou falso (true/false)",
    "list": "lista",
    "map": "mapa (chave: valor)",
}


def is_ident(text: str) -> bool:
    return bool(text) and text[0] in _IDENT_FIRST and all(char in _IDENT_REST for char in text)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


class StructureBuilder:
    def __init__(self, loaded: LoadedYaml, report: ReportBuilder) -> None:
        self.loaded = loaded
        self.rb = report

    # ------------------------------------------------------------------ helpers
    def _at(self, path: str, key: bool = False) -> tuple[int | None, int | None]:
        return self.loaded.key_where(path) if key else self.loaded.where(path)

    def error(self, code: str, message: str, path: str, key: bool = False) -> None:
        self.rb.error(code, message, self._at(path, key), path)

    def warn(self, code: str, message: str, path: str, key: bool = False) -> None:
        self.rb.warn(code, message, self._at(path, key), path)

    def kind_ok(self, value: object, kind: str, path: str, code: str = "E_TYPE", label: str | None = None) -> bool:
        checks = {
            "str": lambda v: isinstance(v, str),
            "text": lambda v: isinstance(v, str),
            "int": _is_int,
            "number": _is_number,
            "bool": lambda v: isinstance(v, bool),
            "list": lambda v: isinstance(v, list),
            "map": lambda v: isinstance(v, dict),
            "any": lambda v: True,
        }
        if checks[kind](value):
            return True
        name = label or path.rsplit(".", 1)[-1]
        self.error(code, f"`{name}` deve ser {KIND_LABEL.get(kind, kind)}", path)
        return False

    def check_keys(self, data: Mapping[str, Any], table: str, path: str) -> None:
        known = sv.names(table)
        for key in data:
            if key in known:
                continue
            hint = difflib.get_close_matches(str(key), known, n=1, cutoff=0.6)
            suggestion = f" (você quis dizer `{hint[0]}`?)" if hint else ""
            self.error("E_UNKNOWN_FIELD", f"campo desconhecido `{key}`{suggestion}", _join(path, str(key)), key=True)

    def field(self, data: Mapping[str, Any], table: str, name: str, path: str, code: str = "E_TYPE") -> object:
        """Read a table field, applying its type, range and choices (E_TYPE unless told otherwise); None if absent/invalid."""
        spec = sv.spec_of(table, name)
        assert spec is not None, (table, name)
        if name not in data:
            if spec.required:
                self.error("E_REQUIRED", f"falta o campo obrigatório `{name}`", path or name)
            return None
        value = data[name]
        here = _join(path, name)
        if not self.kind_ok(value, spec.kind if spec.kind != "step_on_fail" else "any", here, code, name):
            return None
        if spec.choices and value not in spec.choices:
            self.error(code, f"`{name}` deve ser um de: {', '.join(spec.choices)}", here)
            return None
        if spec.kind in ("int", "number") and (spec.minimum is not None or spec.maximum is not None):
            low = spec.minimum if spec.minimum is not None else float("-inf")
            high = spec.maximum if spec.maximum is not None else float("inf")
            if not low <= value <= high:  # type: ignore[operator]
                if spec.maximum is not None:
                    self.error(code, f"`{name}` deve ficar entre {spec.minimum:g} e {spec.maximum:g}", here)
                else:
                    self.error(code, f"`{name}` deve ser maior ou igual a {spec.minimum:g}", here)
                return None
        return value

    # ---------------------------------------------------------------- top level
    def build(self) -> dict[str, Any] | None:
        data = self.loaded.data
        if not isinstance(data, dict):
            self.error("E_TYPE", "o arquivo deve ser um mapa com `version`, `id` e `steps`", "")
            return None
        self.check_keys(data, "top", "")
        version = data.get("version")
        if version is None or isinstance(version, bool) or version != sv.SCHEMA_VERSION or not isinstance(version, int):
            self.error("E_VERSION", f"`version` é obrigatório e precisa ser {sv.SCHEMA_VERSION}", "version" if "version" in data else "")
        pipeline_id = self._pipeline_id(data)
        name = data.get("name")
        if "name" in data and not self.kind_ok(name, "str", "name"):
            name = None
        description = data.get("description")
        if "description" in data and not self.kind_ok(description, "str", "description"):
            description = None
        inputs = self._inputs(data.get("inputs"), "inputs") if "inputs" in data else ()
        budget = self._budget(data.get("budget"), "budget") if "budget" in data else None
        if "budget" not in data or (budget is not None and budget.is_empty):
            self.warn(
                "W_BUDGET_DEFAULT",
                "o pipeline não declara `budget`: vale o teto padrão (2.000.000 de tokens, US$ 15 e 120 minutos)",
                "",
            )
        defaults = self._defaults(data.get("defaults"), "defaults") if "defaults" in data else Defaults()
        steps = self._steps(data.get("steps"), inputs)
        return {
            "id": pipeline_id,
            "name": str(name or pipeline_id or ""),
            "description": str(description or ""),
            "inputs": inputs,
            "budget": budget,
            "defaults": defaults,
            "steps": steps,
        }

    def _pipeline_id(self, data: Mapping[str, Any]) -> str:
        if "id" not in data:
            self.error("E_REQUIRED", "falta o campo obrigatório `id`", "")
            return ""
        value = data["id"]
        if not isinstance(value, str) or not value or len(value) > sv.PIPELINE_ID_MAX or any(c not in _ID_CHARS for c in value):
            self.error(
                "E_ID",
                f"`id` deve ter de 1 a {sv.PIPELINE_ID_MAX} caracteres entre letras minúsculas, números e hífen",
                "id",
            )
            return value if isinstance(value, str) else ""
        return value

    # ------------------------------------------------------------------- inputs
    def _inputs(self, raw: object, path: str) -> tuple[InputSpec, ...]:
        if not self.kind_ok(raw, "map", path):
            return ()
        result: list[InputSpec] = []
        for name, body in raw.items():  # type: ignore[union-attr]
            here = _join(path, name)
            if not is_ident(name):
                self.error("E_INPUT", f"nome de entrada inválido `{name}` (use letras minúsculas, números e _)", here, key=True)
            if not isinstance(body, dict):
                self.error("E_TYPE", f"a entrada `{name}` deve ser um mapa com `type`, `required`, `default`", here)
                continue
            self.check_keys(body, "input", here)
            kind = body.get("type", "string")
            if kind not in sv.INPUT_TYPES:
                self.error("E_INPUT", f"tipo de entrada desconhecido `{kind}` (use {', '.join(sv.INPUT_TYPES)})", _join(here, "type"))
                kind = "string"
            required = body.get("required", False)
            if not isinstance(required, bool):
                self.error("E_TYPE", "`required` deve ser verdadeiro ou falso (true/false)", _join(here, "required"))
                required = False
            max_length = body.get("max_length")
            if max_length is not None:
                low, high = sv.INPUT_MAX_LENGTH_RANGE
                if kind != "string" or not _is_int(max_length) or not low <= max_length <= high:
                    self.error("E_INPUT", f"`max_length` vale só para texto e deve ficar entre {low} e {high}", _join(here, "max_length"))
                    max_length = None
            default = body.get("default")
            if "default" in body and not self._input_default_ok(kind, default, max_length, _join(here, "default")):
                default = None
            result.append(InputSpec(name, kind, bool(required), default, max_length))
        return tuple(result)

    def _input_default_ok(self, kind: str, value: object, max_length: int | None, path: str) -> bool:
        ok = {
            "string": isinstance(value, str),
            "integer": _is_int(value),
            "boolean": isinstance(value, bool),
            "models": value == "auto"
            or (isinstance(value, list) and len(value) <= sv.MODELS_INPUT_MAX and all(isinstance(i, str) and i for i in value)),
        }[kind]
        if not ok:
            hint = f" (até {sv.MODELS_INPUT_MAX} itens `provider/modelo`, ou `auto`)" if kind == "models" else ""
            self.error("E_INPUT", f"o valor padrão não combina com o tipo `{kind}`{hint}", path)
            return False
        if kind == "string" and max_length is not None and len(value) > max_length:  # type: ignore[arg-type]
            self.error("E_INPUT", f"o valor padrão passa de `max_length` ({max_length})", path)
            return False
        return True

    # ------------------------------------------------------------------ budgets
    def _budget(self, raw: object, path: str) -> Budget | None:
        if raw is None:
            return None
        if not self.kind_ok(raw, "map", path, "E_BUDGET"):
            return None
        self.check_keys(raw, "budget", path)  # type: ignore[arg-type]
        values: dict[str, Any] = {}
        for name in sv.names("budget"):
            if name not in raw:  # type: ignore[operator]
                continue
            value = raw[name]  # type: ignore[index]
            here = _join(path, name)
            if name == "max_tokens":
                good = _is_int(value) and value > 0
                hint = "um número inteiro maior que zero"
            else:
                good = _is_number(value) and value > 0
                hint = "um número maior que zero"
            if not good:
                self.error("E_BUDGET", f"`{name}` deve ser {hint}", here)
                continue
            values[name] = value
        return Budget(
            max_tokens=values.get("max_tokens"),
            max_cost_usd=float(values["max_cost_usd"]) if "max_cost_usd" in values else None,
            max_minutes=float(values["max_minutes"]) if "max_minutes" in values else None,
        )

    def _retries(self, raw: object, path: str) -> RetryPolicy | None:
        if raw is None:
            return None
        if not self.kind_ok(raw, "map", path, "E_RETRY"):
            return None
        self.check_keys(raw, "retries", path)  # type: ignore[arg-type]
        attempts = raw.get("max", 0)  # type: ignore[union-attr]
        low, high = sv.MAX_RETRIES_RANGE
        if not _is_int(attempts) or not low <= attempts <= high:
            self.error("E_RETRY", f"`retries.max` deve ser um inteiro de {low} a {high}", _join(path, "max"))
            attempts = 0
        causes = raw.get("on", [])  # type: ignore[union-attr]
        kept: list[str] = []
        if not isinstance(causes, list):
            self.error("E_RETRY", f"`retries.on` deve ser uma lista com valores de: {', '.join(sv.RETRY_CAUSES)}", _join(path, "on"))
        else:
            for index, cause in enumerate(causes):
                if cause not in sv.RETRY_CAUSES:
                    self.error("E_RETRY", f"causa de retry desconhecida `{cause}` (use {', '.join(sv.RETRY_CAUSES)})", f"{_join(path, 'on')}[{index}]")
                else:
                    kept.append(cause)
        return RetryPolicy(attempts, tuple(kept))

    def _defaults(self, raw: object, path: str) -> Defaults:
        if not self.kind_ok(raw, "map", path):
            return Defaults()
        self.check_keys(raw, "defaults", path)  # type: ignore[arg-type]
        data = raw  # type: ignore[assignment]
        return Defaults(
            provider=self.field(data, "defaults", "provider", path),  # type: ignore[arg-type]
            model=self.field(data, "defaults", "model", path),  # type: ignore[arg-type]
            effort=self.field(data, "defaults", "effort", path),  # type: ignore[arg-type]
            retries=self._retries(data.get("retries"), _join(path, "retries")),  # type: ignore[union-attr]
            stall_minutes=self.field(data, "defaults", "stall_minutes", path),  # type: ignore[arg-type]
        )

    # -------------------------------------------------------------------- steps
    def _steps(self, raw: object, inputs: tuple[InputSpec, ...]) -> tuple[Step, ...]:
        if raw is None:
            self.error("E_REQUIRED", "falta o campo obrigatório `steps`", "")
            return ()
        if not self.kind_ok(raw, "list", "steps"):
            return ()
        if not raw:
            self.error("E_REQUIRED", "o pipeline precisa de ao menos uma etapa em `steps`", "steps")
            return ()
        seen: dict[str, int] = {}
        built: list[Step] = []
        for index, body in enumerate(raw):  # type: ignore[arg-type]
            here = f"steps[{index}]"
            if not isinstance(body, dict):
                self.error("E_TYPE", "cada etapa deve ser um mapa (id, agent ou gate, ...)", here)
                continue
            step = self._step(body, index, here, seen)
            if step is not None:
                built.append(step)
        return tuple(built)

    def _step_id(self, body: Mapping[str, Any], here: str, seen: dict[str, int], index: int) -> str | None:
        if "id" not in body:
            self.error("E_REQUIRED", "falta o campo obrigatório `id`", here)
            return None
        value = body["id"]
        path = _join(here, "id")
        if not isinstance(value, str) or not is_ident(value) or len(value) > sv.PIPELINE_ID_MAX:
            self.error(
                "E_ID",
                "o `id` da etapa deve começar com letra minúscula e usar só letras minúsculas, números e _ (é assim que `steps.<id>` o referencia)",
                path,
            )
            return None
        if value in seen:
            self.error("E_STEP_ID_DUP", f"já existe uma etapa com o id `{value}` (linha {self._at(f'steps[{seen[value]}].id')[0]})", path)
            return None
        seen[value] = index
        return value

    def _step(self, body: dict[str, Any], index: int, here: str, seen: dict[str, int]) -> Step | None:
        self.check_keys(body, "step", here)
        step_id = self._step_id(body, here, seen, index)
        has_agent, has_gate = "agent" in body, "gate" in body
        if has_agent == has_gate:
            self.error(
                "E_STEP_KIND",
                "a etapa precisa ter `agent` ou `gate`, e nunca os dois" if has_agent else "a etapa precisa ter `agent` (etapa de agente) ou `gate` (etapa de checagem)",
                here,
            )
        if step_id is None:
            return None
        common: dict[str, Any] = {
            "id": step_id,
            "name": self.field(body, "step", "name", here),
            "description": self.field(body, "step", "description", here),
            "when": self.field(body, "step", "when", here),
            "next": self.field(body, "step", "next", here),
            "stall_minutes": self.field(body, "step", "stall_minutes", here),
            "retries": self._retries(body.get("retries"), _join(here, "retries")),
            "budget": self._budget(body.get("budget"), _join(here, "budget")),
            "on_fail": self._on_fail(body.get("on_fail"), _join(here, "on_fail")) if "on_fail" in body else OnFail(),
            "index": index,
        }
        if has_gate and not has_agent:
            present = [name for name in _AGENT_ONLY_FIELDS if name in body]
            if present:
                self.error("E_STEP_KIND", f"etapa de gate não aceita {', '.join(f'`{n}`' for n in present)} (campos de etapa de agente)", here)
            return GateStep(gate=self._gate(body["gate"], _join(here, "gate")), **common)
        return self._agent_step(body, here, common)

    def _agent_step(self, body: dict[str, Any], here: str, common: dict[str, Any]) -> AgentStep:
        agent = self.field(body, "step", "agent", here) or ""
        if "prompt" not in body:
            self.error("E_REQUIRED", "a etapa de agente precisa de `prompt`", here)
        prompt = self.field(body, "step", "prompt", here) or ""
        output_schema = None
        if "output_schema" in body and self.kind_ok(body["output_schema"], "map", _join(here, "output_schema")):
            output_schema = body["output_schema"]
            self.check_json_schema(output_schema, _join(here, "output_schema"))
        return AgentStep(
            agent=agent,  # type: ignore[arg-type]
            prompt=prompt,  # type: ignore[arg-type]
            provider=self.field(body, "step", "provider", here),  # type: ignore[arg-type]
            model=self.field(body, "step", "model", here),  # type: ignore[arg-type]
            effort=self.field(body, "step", "effort", here),  # type: ignore[arg-type]
            isolation=self.field(body, "step", "isolation", here),  # type: ignore[arg-type]
            map=self._map(body["map"], _join(here, "map")) if "map" in body else None,
            output_schema=output_schema,
            **common,
        )

    def _map(self, raw: object, path: str) -> MapSpec | None:
        if not self.kind_ok(raw, "map", path):
            return None
        data: dict[str, Any] = raw  # type: ignore[assignment]
        self.check_keys(data, "map", path)
        if "over" not in data:
            self.error("E_REQUIRED", "falta o campo obrigatório `over`", path)
        if "as" not in data:
            self.error("E_REQUIRED", "falta o campo obrigatório `as`", path)
        var = data.get("as")
        if var is not None and (not isinstance(var, str) or not is_ident(var) or var in _RESERVED_VARS):
            self.error("E_ID", "`as` deve ser um nome simples (letras minúsculas, números e _), diferente de `inputs` e `steps`", _join(path, "as"))
            var = None
        parallel = data.get("max_parallel", sv.DEFAULT_MAP_PARALLEL)
        low, high = sv.MAX_PARALLEL_RANGE
        if not _is_int(parallel) or not low <= parallel <= high:
            self.error("E_MAP_RANGE", f"`max_parallel` deve ser um inteiro de {low} a {high}", _join(path, "max_parallel"))
            parallel = sv.DEFAULT_MAP_PARALLEL
        fail_fast = data.get("fail_fast", True)
        if not isinstance(fail_fast, bool):
            self.error("E_TYPE", "`fail_fast` deve ser verdadeiro ou falso (true/false)", _join(path, "fail_fast"))
            fail_fast = True
        over = data.get("over")
        literal: tuple[object, ...] | None = None
        over_text: str | None = None
        if isinstance(over, list):
            if len(over) > sv.MAX_MAP_ITEMS:
                self.error("E_MAP_RANGE", f"`over` tem {len(over)} itens; o máximo é {sv.MAX_MAP_ITEMS}", _join(path, "over"))
            literal = tuple(over)
        elif isinstance(over, str):
            try:
                expr = parse_expression(unwrap_expression(over))
            except ExpressionError as exc:
                self.error("E_EXPR_SYNTAX", f"`over` inválido: {exc.message}", _join(path, "over"))
            else:
                if not isinstance(expr, PathRef):
                    self.error("E_EXPR_SYNTAX", "`over` deve ser uma lista literal ou um caminho como ${{ steps.plano.output.tarefas }}", _join(path, "over"))
            over_text = over
        elif over is not None:
            self.error("E_TYPE", "`over` deve ser uma lista literal ou uma referência `${{ ... }}`", _join(path, "over"))
        return MapSpec(over=over_text, var=var or "item", max_parallel=parallel, fail_fast=fail_fast, literal=literal)

    def _on_fail(self, raw: object, path: str) -> OnFail:
        if raw in ("stop", "continue"):
            return OnFail(action=raw)  # type: ignore[arg-type]
        if not isinstance(raw, dict):
            self.error("E_TYPE", "`on_fail` deve ser `stop`, `continue` ou {goto: <etapa>, max_loops: N}", path)
            return OnFail()
        self.check_keys(raw, "on_fail", path)
        goto = raw.get("goto")
        if goto is None:
            self.error("E_REQUIRED", "`on_fail` em forma de mapa precisa de `goto`", path)
            return OnFail()
        if not isinstance(goto, str):
            self.error("E_TYPE", "`goto` deve ser o id de uma etapa", _join(path, "goto"))
            return OnFail()
        low, high = sv.MAX_LOOPS_RANGE
        loops = raw.get("max_loops")
        if loops is None:
            self.error("E_LOOP_NO_MAX", f"`goto` exige `max_loops` de {low} a {high}: sem ele o laço não tem condição de parada", path)
        elif not _is_int(loops) or not low <= loops <= high:
            self.error("E_LOOP_NO_MAX", f"`max_loops` deve ser um inteiro de {low} a {high}", _join(path, "max_loops"))
            loops = None
        return OnFail(action="goto", goto=goto, max_loops=loops)

    # -------------------------------------------------------------------- gates
    def _gate(self, raw: object, path: str) -> Gate:
        empty = Gate("all", ())
        if not self.kind_ok(raw, "map", path):
            return empty
        data: dict[str, Any] = raw  # type: ignore[assignment]
        self.check_keys(data, "gate", path)
        modes = [mode for mode in ("all", "any") if mode in data]
        if len(modes) != 1:
            self.error("E_TYPE", "o `gate` deve ter exatamente uma lista: `all` ou `any`", path)
            return empty
        mode = modes[0]
        items_raw = data[mode]
        if not self.kind_ok(items_raw, "list", _join(path, mode)) or not items_raw:
            if isinstance(items_raw, list):
                self.error("E_TYPE", f"`{mode}` precisa de ao menos um item", _join(path, mode))
            return Gate(mode, ())  # type: ignore[arg-type]
        items: list[GateItem] = []
        for index, item in enumerate(items_raw):
            parsed = self._gate_item(item, f"{_join(path, mode)}[{index}]")
            if parsed is not None:
                items.append(parsed)
        return Gate(mode, tuple(items))  # type: ignore[arg-type]

    def _gate_item(self, raw: object, path: str) -> GateItem | None:
        if not isinstance(raw, dict):
            self.error("E_TYPE", "cada item do gate deve ser um mapa com `command`, `schema` ou `verifier`", path)
            return None
        main = [kind for kind in sv.GATE_ITEM_KINDS if kind in raw]
        if len(main) != 1:
            self.error("E_TYPE", "cada item do gate deve ter exatamente um de `command`, `schema` ou `verifier`", path)
            return None
        kind = main[0]
        allowed = {"command": sv.names("command_item"), "schema": ("schema",), "verifier": ("verifier",)}[kind]
        for key in raw:
            if key not in allowed:
                hint = difflib.get_close_matches(str(key), allowed, n=1, cutoff=0.6)
                tip = f" (você quis dizer `{hint[0]}`?)" if hint else ""
                self.error("E_UNKNOWN_FIELD", f"campo desconhecido `{key}`{tip}", _join(path, str(key)), key=True)
        if kind == "command":
            command = self.field(raw, "command_item", "command", path)
            if command is not None and not str(command).strip():
                self.error("E_TYPE", "`command` não pode ser vazio", _join(path, "command"))
            timeout = self.field(raw, "command_item", "timeout_seconds", path)
            expect = self.field(raw, "command_item", "expect", path)
            return CommandGate(
                command=str(command or "").strip(),  # a block scalar (`|`) leaves a new line around the command
                expect=expect or "zero",  # type: ignore[arg-type]
                timeout_seconds=timeout if timeout is not None else sv.DEFAULT_TIMEOUT_SECONDS,  # type: ignore[arg-type]
            )
        body = raw[kind]
        here = _join(path, kind)
        if not self.kind_ok(body, "map", here):
            return None
        self.check_keys(body, "schema_item" if kind == "schema" else "verifier_item", here)
        if kind == "schema":
            step = self.field(body, "schema_item", "step", here)
            schema = self.field(body, "schema_item", "json_schema", here)
            if isinstance(schema, dict):
                self.check_json_schema(schema, _join(here, "json_schema"))
            return SchemaGate(step=str(step or ""), json_schema=schema if isinstance(schema, dict) else {})
        pass_on = ("pass",)
        if "pass_on" in body:
            raw_pass = body["pass_on"]
            if not isinstance(raw_pass, list) or not raw_pass or any(v not in sv.VERDICTS for v in raw_pass):
                self.error("E_TYPE", f"`pass_on` deve ser uma lista com valores de: {', '.join(sv.VERDICTS)}", _join(here, "pass_on"))
            else:
                pass_on = tuple(raw_pass)
        return VerifierGate(
            policy=self.field(body, "verifier_item", "policy", here),  # type: ignore[arg-type]
            effort=self.field(body, "verifier_item", "effort", here),  # type: ignore[arg-type]
            provider=self.field(body, "verifier_item", "provider", here),  # type: ignore[arg-type]
            model=self.field(body, "verifier_item", "model", here),  # type: ignore[arg-type]
            pass_on=pass_on,
        )

    # -------------------------------------------------------------- json schema
    def check_json_schema(self, schema: Mapping[str, Any], path: str) -> None:
        try:
            size = len(json.dumps(schema, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            self.error("E_SCHEMA_INVALID", "o JSON Schema não é serializável", path)
            return
        if size > sv.OUTPUT_SCHEMA_MAX_BYTES:
            self.error("E_SCHEMA_INVALID", f"o JSON Schema tem {size} bytes; o máximo é {sv.OUTPUT_SCHEMA_MAX_BYTES // 1024} KB", path)
            return
        for ref in _remote_refs(schema):
            self.error("E_SCHEMA_INVALID", f"`$ref` remoto não é permitido (`{ref}`); use só referências locais `#/...`", path)
            return
        try:
            Draft202012Validator.check_schema(dict(schema))
        except SchemaError as exc:
            self.error("E_SCHEMA_INVALID", f"JSON Schema inválido: {str(exc.message)[:200]}", path)


def _remote_refs(node: object) -> Sequence[str]:
    found: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in ("$ref", "$dynamicRef") and isinstance(value, str) and not value.startswith("#"):
                    found.append(value)
                else:
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return found


__all__ = ["StructureBuilder", "is_ident"]
