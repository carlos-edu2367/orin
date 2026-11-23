"""docs/PIPELINES.md stays in step with the code: schema tables, error codes, settings, built-ins, grammar, examples."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentos.pipelines import errors, expressions, schema_v1
from agentos.pipelines.builtin_loader import BUILTIN_IDS
from agentos.pipelines.validator import validate_structure
from agentos.settings.keys_pipelines import PIPELINE_KEYS

DOC = Path(__file__).resolve().parents[3] / "docs" / "PIPELINES.md"
TEXT = DOC.read_text(encoding="utf-8")


def _section(name: str) -> str:
    match = re.search(rf"<!-- {re.escape(name)} -->\n(.*?)<!-- /[a-z]+ -->", TEXT, re.DOTALL)
    assert match, f"missing marker <!-- {name} --> in docs/PIPELINES.md"
    return match.group(1)


def _rows(section: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in section.splitlines():
        found = re.match(r"\|\s*`([^`]+)`\s*\|(.*)\|\s*$", line)
        if found:
            rows[found.group(1)] = found.group(2)
    return rows


@pytest.mark.parametrize("table", sorted(schema_v1.TABLES))
def test_every_schema_field_is_documented_and_nothing_else(table: str) -> None:
    rows = _rows(_section(f"schema:{table}"))
    assert set(rows) == set(schema_v1.names(table)), table


@pytest.mark.parametrize("table", sorted(schema_v1.TABLES))
def test_documented_ranges_and_choices_match_the_table(table: str) -> None:
    rows = _rows(_section(f"schema:{table}"))
    for spec in schema_v1.TABLES[table]:
        row = re.sub(r"[ ._,]", "", rows[spec.name])
        for choice in spec.choices:
            assert f"`{choice}`" in rows[spec.name], (table, spec.name, choice)
        for bound in (spec.minimum, spec.maximum):
            if bound is not None and float(bound).is_integer():
                assert str(int(bound)) in row, (table, spec.name, bound)
        assert ("sim" in rows[spec.name].split("|")[1]) == spec.required, (table, spec.name)


def test_every_error_and_warning_code_is_documented_and_nothing_else() -> None:
    rows = _rows(_section("codes"))
    assert set(rows) == set(errors.ERROR_CODES) | set(errors.WARNING_CODES)


def test_every_pipeline_setting_is_documented() -> None:
    rows = _rows(_section("settings"))
    assert set(rows) == {key.path for key in PIPELINE_KEYS}
    assert len(rows) == 5


def test_builtin_pipelines_are_documented() -> None:
    for pipeline_id in BUILTIN_IDS:
        assert f"`{pipeline_id}`" in TEXT


def test_documented_limits_match_the_code() -> None:
    assert "2.000.000" in TEXT and schema_v1.DEFAULT_BUDGET_TOKENS == 2_000_000
    assert "US$ 15" in TEXT and schema_v1.DEFAULT_BUDGET_COST_USD == 15.0
    assert "120 minutos" in TEXT and schema_v1.DEFAULT_BUDGET_MINUTES == 120
    assert schema_v1.TIMEOUT_RANGE == (1, 600)
    assert schema_v1.MAX_MAP_ITEMS == 50 and "50 itens" in TEXT


def test_grammar_block_equals_the_module_grammar() -> None:
    doc = expressions.__doc__ or ""
    block = doc.split("::", 1)[1]
    source = [line.strip() for line in block.splitlines() if line.strip()]
    match = re.search(r"```text\n(expr .*?)```", TEXT, re.DOTALL)
    assert match, "grammar block missing"
    documented = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    assert documented == source


def test_yaml_examples_pass_structural_validation() -> None:
    blocks = re.findall(r"```yaml pipeline\n(.*?)```", TEXT, re.DOTALL)
    assert blocks
    for block in blocks:
        definition, report = validate_structure(block, "user")
        assert report.ok, report.errors
        assert definition is not None


def test_the_program_selecting_rule_is_documented_where_the_agent_and_the_quoting_are() -> None:
    """C10 security review (P1, S2): the first word of a command selects the program, the agent may not fill it."""
    from agentos.pipelines import command_quoting

    quoting = TEXT.split("## Citação de `inputs` em gates", 1)[1].split("\n## ", 1)[0]
    tool = TEXT.split("## A ferramenta `RunPipeline`", 1)[1].split("\n## ", 1)[0]
    assert "seleciona o programa" in quoting and "primeira palavra" in quoting and "classify_references" in quoting
    assert hasattr(command_quoting, "classify_references")
    assert "seleciona o programa" in tool and "PIPELINE_INPUT_NOT_ALLOWED" in tool
    assert "15 dígitos" in TEXT
