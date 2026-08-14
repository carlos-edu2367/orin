# Detalhe de Skill indisponível para execução

- O catálogo de Skills pode listar uma Skill customizada com `available: false` quando ela declara uma ferramenta que não está exposta ao runtime atual.
- O detalhe não deve falhar nesse caso: `SkillLibraryService.get` e `PostgresSkillLibraryService.get` agora calculam a disponibilidade separadamente e leem as instruções sem executar a validação de disponibilidade.
- `SkillRegistry.read_instructions` aplica a resolução e a integridade normal do pacote, enquanto `SkillRegistry.load` continua sendo o caminho estrito usado para execução e bloqueia ferramentas ausentes.
- A regressão foi coberta no registry, no serviço em memória e no store persistente; a suíte relacionada passou em 31 testes.
