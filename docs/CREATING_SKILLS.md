# Creating Orin Skills

Uma **Skill** ensina um procedimento reutilizável ao agente. O Orin suporta tanto **Skills em disco** (compatíveis com `.orin/skills` e `.claude/skills`) quanto **Skills versionadas no banco de dados** (criadas pela interface ou pelas ferramentas `create_skill` / `edit_skill`), além das 8 Skills nativas embutidas (*builtins*).

## Fontes e precedência

As Skills são descobertas em 4 camadas de disco + banco/builtins (em caso de conflito de nome, a camada superior vence: `managed > user > project > plugin > db/builtin`):

1. **Managed:** `%ProgramData%\Orin\skills` (Windows) ou `/etc/orin/skills` (Linux);
2. **Usuário (`user`):** `<ORIN_CONFIG_DIR>/skills` e `~/.claude/skills`;
3. **Projeto (`project`):** `.orin/skills/<nome>/SKILL.md` e `.claude/skills/<nome>/SKILL.md` (além de comandos legados em `.orin/commands/*.md` e `.claude/commands/*.md`);
4. **Plugins (`plugin`):** diretórios `skills/<nome>/SKILL.md` dentro de plugins instalados e ativos;
5. **Banco e Builtins:** skills criadas com `create_skill` / `edit_skill` e skills embutidas (`systematic-debugging`, `testing`, `code-review`, `python-api`, `react-spa`, `nextjs-app`, `frontend-a11y`, `technical-research`).

## Estrutura de diretório e Frontmatter

```text
my-skill/
|-- SKILL.md
|-- references/        # documentação extensa, lida sob demanda com read_skill_resource
|-- examples/
|-- templates/
`-- resources/
```

O frontmatter YAML do `SKILL.md` suporta os campos clássicos do Orin e os campos de interoperabilidade do Orin Code / Claude Code:

```markdown
---
name: systematic-debugging
description: Investigate a software failure with a repeatable, evidence-led workflow.
version: 1.0.0
argument-hint: "[test-or-error]"
user-invocable: true
disable-model-invocation: false
allowed-tools: [Read, Grep, Glob, Bash(pytest:*)]
model: inherit
effort: high
context: fork
agent: general-purpose
tags: [debugging, testing, code]
capabilities: [diagnose_failure, create_regression_test]
when_to_use: [an error regressed, a test fails, unexpected production behaviour]
when_not_to_use: [a request is only for a code-style review]
requires_tools: [read_file, run_command]
dependencies:
  skills: [testing]
---

# Systematic debugging

Target: $ARGUMENTS (session ${ORIN_SESSION_ID}, skill dir ${ORIN_SKILL_DIR})

## Workflow

1. Reproduce and state the observed failure.
2. Gather the smallest relevant evidence.
3. Form one falsifiable hypothesis at a time.
4. Add a regression test before changing production code.
5. Verify the fix and report the evidence.

## Validation

Run the focused test, then the relevant suite.
```

### Campos avançados de execução

- **Substituições de variáveis:** o corpo da Skill pode usar `$ARGUMENTS`, `$ARGUMENTS[0]`, `$1`..`$9`, `${ORIN_SESSION_ID}` (ou `${CLAUDE_SESSION_ID}`) e `${ORIN_SKILL_DIR}` (ou `${CLAUDE_SKILL_DIR}`).
- **`allowed-tools`:** concede permissão `allow` temporária para as ferramentas/especificadores listados apenas enquanto a Skill está ativa (nunca sobrepõe uma regra `deny`).
- **`context: fork` e `model`:** quando `context: fork` é definido, a Skill executa isoladamente em um subagente (usando o `agent` e o `model` declarados, respeitando a política de modelos permitidos da sessão).
- **Comandos inline (`` !`cmd` ``):** trechos `` !`comando` `` no corpo de uma Skill passam obrigatoriamente pelo pipeline de permissões do `Bash` (incluindo análise AST `tree-sitter`); em Skills da camada de projeto (`.orin/skills` ou `.claude/skills`), só são avaliados se a pasta tiver **Workspace Trust** aprovado.
- **`hooks`:** uma Skill pode declarar hooks no frontmatter que ficam ativos exclusivamente durante a execução daquela Skill.

## Regras de autoria e padrão de alta utilização

- Escreva uma `description` concreta unindo o gatilho e o resultado esperado: o catálogo injetado no prompt lista apenas nome e descrição para economizar janela de contexto; o corpo completo só é carregado sob demanda quando a Skill é invocada via tool `Skill` (`use_skill`) ou comando `/nome-da-skill`.
- Mantenha o corpo enxuto: contexto mínimo, `## Workflow` numerado e `## Validation` com evidências verificáveis. Coloque material extenso em `references/`, `examples/`, `templates/` ou `resources/` para leitura sob demanda com `read_skill_resource`.
- Não inclua segredos, chaves de API nem instruções para contornar políticas de segurança.
- Ao publicar ou atualizar pelo banco (`create_skill` / `edit_skill`), versões já publicadas permanecem imutáveis: cada refinamento gera uma nova versão semântica validada contra as ferramentas e dependências existentes no runtime.
- Após editar Skills em disco durante uma sessão, o watcher de arquivos atualiza o catálogo automaticamente (ou use `/reload-skills`).
