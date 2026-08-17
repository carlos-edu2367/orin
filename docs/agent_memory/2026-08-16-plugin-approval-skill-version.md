# Aprovação de plugins Claude Code sem versão por skill

## Fato encontrado

Plugins reais do ecossistema Claude Code, incluindo `superpowers`, declaram a versão em
`plugin.json`, mas normalmente não repetem `version` no frontmatter de cada `SKILL.md`.
O inspector de plugins já aceitava esse formato e sintetizava a versão do manifesto, porém
o `PluginActivator` relia as skills usando o parser estrito do Orin. A aprovação falhava com
`SkillParseError: missing required skill frontmatter field: version` depois de uma inspeção
bem-sucedida.

## Decisão implementada

O ativador usa o mesmo contrato tolerante do inspector para skills de plugins: exige apenas
`name` e `description` e usa `inspection.ref.version` como `default_version`. O parser padrão
das skills nativas continua estrito.

A rota `POST /v1/plugins/{plugin_id}/approve` também executa a aprovação via threadpool, pois
a ativação faz parsing, persistência e pode propor servidores MCP. Isso impede que uma aprovação
lenta congele outras requisições e streams da API.

## Verificação

- Checkout real de `superpowers`: 14 skills inspecionadas e 14 ativadas; a primeira recebeu a
  versão do plugin (`5.1.0`).
- Testes focados de plugins/API: 90 passaram.
- Teste frontend `PluginApprovalCard`: 1 passou.
- Suíte completa: 1513 passaram, 68 ignorados e 1 falhou por divergência preexistente de versão
  do launcher (`0.2.1` esperada contra `0.2.2` exposta), sem relação com plugins.
