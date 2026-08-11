# Creating AgentOS Skills

Uma Skill ensina um procedimento reutilizável. Ela não executa código, não é uma ferramenta e nunca concede permissões.

## Estrutura

```text
my-skill/
├── SKILL.md
├── references/        # documentação grande, lida sob demanda
├── examples/
├── templates/
└── scripts/           # nunca executados automaticamente
```

```markdown
---
name: systematic-debugging
description: Investigate a software failure with a repeatable, evidence-led workflow.
version: 1.0.0
tags: [debugging, testing, code]
capabilities: [diagnose_failure, create_regression_test]
when_to_use: [an error regressed, a test fails, unexpected production behaviour]
when_not_to_use: [a request is only for a code-style review]
requires_tools: [read_file, run_command]
dependencies:
  skills: [testing]
---

# Systematic debugging

## Workflow

1. Reproduce and state the observed failure.
2. Gather the smallest relevant evidence.
3. Form one falsifiable hypothesis at a time.
4. Add a regression test before changing production code.
5. Verify the fix and report the evidence.

## Validation

Run the focused test, then the relevant suite.
```

## Authoring rules

Make the description concrete because discovery uses it. Keep the core workflow short, procedural and explicit about validation. State when the Skill should not be used. Put long references in `references/`. The agent can request one UTF-8 file at a time with `read_skill_resource` using a path beginning with `resources/`, `references/`, `examples/` or `templates/`; `scripts/` is never exposed as an executable capability. Do not include secrets, permission requests, prompt-injection text, hidden policy changes or arbitrary code execution instructions.

Test a Skill with its expected task, a near miss and an unavailable-tool case. A good result suggests its metadata for the expected task, excludes the near miss, and refuses unavailable requirements before the agent attempts the workflow.
