# Identidade das skills de plugins

Skills contribuídas por plugins devem ter uma identidade pública namespaced no formato
`plugin-id:skill-id`, em que ambos os componentes são derivados e normalizados dos nomes
declarados. O campo opcional `id` dentro de um `SKILL.md` não deve alterar esse contrato.

O inspector agora monta o ID como `f"{manifest.plugin_id}:{plugin_id_from_name(skill.name)}"`.
Isso mantém referências estáveis entre plugins e evita que um ID local legado ou arbitrário
quebre a resolução por nome.

Verificação: 14 testes de plugins passaram; o checkout real do `superpowers` produziu IDs como
`superpowers:brainstorming`, `superpowers:dispatching-parallel-agents` e
`superpowers:executing-plans`.
