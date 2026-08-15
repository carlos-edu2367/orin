# Plugins v1 implementation

- O plano de Plugins foi implementado diretamente no branch `main` em tarefas incrementais.
- O pacote `agentos.plugins` separa modelos, manifesto, fontes, fetch limitado, inspeção, marketplace, ativação e service.
- Fetch local/GitHub rejeita symlinks, caminhos inseguros, pacotes fora do orçamento e digest diferente para a mesma versão; inspeção nunca executa subprocessos.
- Skills de plugin usam `SkillSource.PLUGIN`, IDs namespaced (`plugin:skill`) e persistem `plugin_id`; `package_path` precisa apontar para o arquivo `SKILL.md` para `read_resource` funcionar.
- Servidores MCP contribuídos são sempre propostos pelo `McpServerService` em `pending_approval`; o agente não recebe valores de segredo.
- O frontend inclui API, card de aprovação no chat e Settings → Plugins, reaproveitando `approval-card*`.
- Pendências conhecidas do RFC 901: PluginHost executável isolado, assinatura/attestation, SBOM/quarentena, DRAINING e contribuições de Provider/Resource/Exporter.
