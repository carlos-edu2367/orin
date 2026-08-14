# Validação de publicação de Skills

- A validação de ferramentas e dependências não pode ficar somente em `AgentToolset._skill_command`, porque Skills também podem ser criadas pela API ou UI.
- `SkillLibraryService` e `PostgresSkillLibraryService` agora validam antes de persistir: ferramentas declaradas contra `SKILL_RUNTIME_TOOLS`, dependências de Skill instaladas, disponibilidade transitiva e auto-dependência.
- O agente recebe o `ValueError` do serviço como falha da ferramenta com mensagem acionável; as rotas de criação e edição convertem o mesmo erro em `ApplicationValidationError`/HTTP 422.
- Skills inválidas já existentes continuam podendo ser abertas para diagnóstico, mas novas criações e versões não entram mais nesse estado.
