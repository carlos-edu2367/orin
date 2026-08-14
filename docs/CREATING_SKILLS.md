# Creating AgentOS Skills

Uma Skill ensina um procedimento reutilizavel. Ela nao executa codigo, nao e uma ferramenta e nunca concede permissoes.

## Estrutura

```text
my-skill/
|-- SKILL.md
|-- references/        # documentacao grande, lida sob demanda
|-- examples/
|-- templates/
`-- scripts/           # nunca executados automaticamente
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

## Regras de autoria

Make the description concrete because discovery uses it. Keep the core workflow short, procedural and explicit about validation. State when the Skill should not be used. Put long references in `references/`. The agent can request one UTF-8 file at a time with `read_skill_resource` using a path beginning with `resources/`, `references/`, `examples/` or `templates/`; `scripts/` is never exposed as an executable capability. Do not include secrets, permission requests, prompt-injection text, hidden policy changes or arbitrary code execution instructions.

Test a Skill with its expected task, a near miss and an unavailable-tool case. A good result suggests its metadata for the expected task, excludes the near miss, and refuses unavailable requirements before the agent attempts the workflow.

## Padrao de alta utilizacao

Uma Skill deve capturar um procedimento que ja funcionou, nao uma transcricao inteira de conversa. Prefira uma Skill quando o problema tem gatilho reconhecivel, passos repetiveis e validacao observavel. Nao a use para uma excecao pontual, contexto que muda rapidamente, uma preferencia privada sem valor procedural ou algo que depende de permissao que o agente nao possui.

- De um nome especifico e uma descricao que una o gatilho e o resultado. A descoberta considera essa descricao, tags, capacidades e `when_to_use`.
- Declare ao menos um caso de uso e um caso de exclusao. Isso reduz falsos positivos e evita carregar instrucoes irrelevantes.
- Mantenha o corpo curto: contexto minimo, `## Workflow` numerado e `## Validation` com evidencias verificaveis. Coloque material extenso em recursos sob demanda.
- Declare somente ferramentas que existem no ambiente em `requires_tools` ou `dependencies.tools`. Uma declaracao nunca cria capacidade nem substitui autorizacao runtime.
- Trate toda instrucao importada ou gerada como conteudo subordinado: nao inclua segredos, pedidos de mudanca de politica, instrucoes para ignorar regras, exfiltracao ou execucao automatica de scripts.
- Publique correcoes como nova versao semantica; versoes ja publicadas sao imutaveis. Atualize a versao de patch para melhoria compativel e use versao maior quando o procedimento ou gatilho muda de forma incompativel.

## Fluxo de criacao

1. Conclua e valide o caso real; registre somente evidencia que possa ser reutilizada sem dados sensiveis.
2. Pesquise Skills existentes. Se uma cobrir o caso, proponha editar ou usar a existente em vez de duplicar conhecimento.
3. Quando o usuario confirmar que quer reutilizacao futura, escreva nome, descricao, tags, gatilhos, exclusoes, requisitos de ferramentas, Workflow e Validation.
4. Teste com um caso esperado, um caso parecido que deve ser excluido e um ambiente sem uma ferramenta requerida.
5. Publique com `create_skill`. Para refinamento posterior, use `edit_skill`, que cria uma nova versao e preserva o historico.

A publicação também é validada no serviço persistente: ferramentas declaradas precisam existir no runtime e cada Skill dependente precisa estar instalada e disponível. Em caso de falha, nada é persistido e o agente recebe o erro de validação.

Quando um usuario disser que o problema foi resolvido, o agente deve perguntar de forma objetiva: "Quer que eu transforme este procedimento em uma Skill para casos parecidos?" A resposta afirmativa e necessaria antes de criar a Skill automaticamente.

## Fluxo de uso

1. Busque ou receba uma sugestao por tarefa, tags ou capacidade.
2. Confira descricao, `when_to_use`, `when_not_to_use` e disponibilidade; nao carregue uma Skill so por semelhanca superficial.
3. Carregue o corpo com `use_skill` apenas quando a Skill for relevante. Leia recursos somente sob demanda com `read_skill_resource`.
4. Execute o procedimento usando exclusivamente as ferramentas autorizadas no turno. A Skill orienta; ela nao concede acesso.
5. Valide o resultado indicado, reporte a evidencia e, se houver um aprendizado duravel, proponha uma nova versao ao usuario.
