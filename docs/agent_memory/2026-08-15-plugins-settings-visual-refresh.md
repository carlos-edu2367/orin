# Plugins Settings visual refresh

- `PluginsSection` agora ocupa uma superfície própria no Settings Shell: resumo de registry, contadores de instalados/ativos/avisos, loading skeleton, erro recuperável e empty state com ação primária.
- `PluginCard` mantém expand/collapse, ativação/desativação e remoção, mas apresenta estados em português, autor/versão, contribuições, homepage, warnings, identificador e ações com hierarquia visual consistente.
- `PluginInstallDialog` segue o fluxo existente de inspecionar antes de aprovar e ganhou overlay, foco inicial, Escape, descrição de segurança, preview da inspeção e avisos.
- O snapshot visual de Plugins foi atualizado; os snapshots de outras telas não devem depender de badges carregados de um backend local em execução.
