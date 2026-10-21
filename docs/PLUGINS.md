# Plugins

O Orin aceita plugins no formato declarativo compatível com o Claude Code e com o formato nativo do Orin: um manifesto `.orin-plugin/plugin.json` ou `.claude-plugin/plugin.json` acompanhado de componentes opcionais:

```text
.orin-plugin/plugin.json      # ou .claude-plugin/plugin.json
skills/<nome>/SKILL.md        # skills reutilizáveis
commands/<nome>.md            # slash commands de plugin
agents/<nome>.md              # subagentes declarativos
hooks/hooks.json              # hooks de ciclo de vida (command, http, prompt, agent)
.mcp.json                     # servidores MCP do plugin
.lsp.json                     # servidores LSP por linguagem para diagnósticos pós-edição
output-styles/<nome>.md       # estilos de saída customizados
settings.json                 # configurações padrão sugeridas pelo plugin
```

O manifesto declara `name`, `version` (SemVer) e, opcionalmente, `description` e `userConfig`.

## Suporte, segurança e limites

- **Inspeção sem execução prévia:** baixar ou inspecionar um pacote nunca executa código do plugin. O pacote é verificado contra limites de tamanho e quantidade de arquivos, recusa *symlinks* ou *path traversal* e calcula um digest SHA-256 determinístico sobre todos os caminhos e conteúdos ordenados em `data/plugins/<plugin_id>/<version>/`.
- **Aprovação explícita:** servidores MCP incluídos em `.mcp.json` entram em `pending_approval` e exigem aprovação própria. Hooks e servidores MCP de escopo de projeto/repositório também respeitam o **Workspace Trust**.
- **Hooks confinados (`hooks/hooks.json`):** os hooks declarados por um plugin executam no motor unificado de hooks do Orin (com isolamento de árvore de processos via Windows Job Object / POSIX process group e *timeout* estrito de 1 a 30 segundos), com caminhos resolvidos dentro da raiz do plugin (`${ORIN_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_ROOT}`).
- **LSP (`.lsp.json`):** plugins podem declarar servidores Language Server Protocol por extensão/linguagem. Após chamadas `Edit` ou `Write`, o cliente LSP coleta diagnósticos e os injeta como `<system-reminder>` no turno; se o servidor LSP falhar, os diagnósticos são desativados sem bloquear a edição.
- **Configuração de usuário e segredos (`userConfig`):** campos declarados em `userConfig` no manifesto podem ser preenchidos na interface ou via CLI. Campos marcados com `"sensitive": true` são armazenados criptografados no cofre Fernet do Orin (`AGENTOS_PROVIDER_ENCRYPTION_KEY`) e injetados via `${user_config.<chave>}` apenas em tempo de execução, sem nunca aparecer em respostas de API ou logs.

## Fluxo de instalação (UI, Chat e CLI)

1. **Pelo Chat ou Settings → Plugins:** o agente (via `search_plugin` / `install_plugin`) ou o usuário inspeciona uma referência (URL HTTPS do GitHub, `owner/repo`, marketplace configurado ou diretório local). O Orin exibe o `PluginApprovalCard` detalhando skills, comandos, agentes, hooks, MCPs e LSPs. Só o clique em **Instalar** ativa as contribuições.
2. **Pelo terminal (`orin code plugin`):**
   ```bash
   orin code plugin list
   orin code plugin validate ./meu-plugin
   orin code plugin install owner/repo
   orin code plugin details <plugin_id>
   orin code plugin configure <plugin_id> '{"api_endpoint":"https://..."}'
   orin code plugin enable <plugin_id> | disable <plugin_id>
   orin code plugin uninstall <plugin_id>
   ```

## Escrevendo um plugin

Use frontmatter compatível nas skills (`name`, `description`, `argument-hint`, `allowed-tools`, `model`, `effort`, `context: fork`, `hooks`) e nos agentes (`name`, `description`, `tools`, `disallowedTools`, `model`, `effort`). Recursos adicionais de uma skill devem ficar em `references/`, `examples/`, `templates/` ou `resources/`. Nunca inclua segredos em texto claro dentro de `.mcp.json` ou `plugin.json`: declare variáveis em `userConfig` (`sensitive: true`) ou requisitos de credencial no fluxo de aprovação do MCP.

## Marketplaces e remoção

Um marketplace é um repositório ou índice `marketplace.json` contendo `name` e uma lista `plugins` (`name`, `source`, `description`). Ele pode ser adicionado em **Settings → Plugins** ou via `orin code plugin marketplace add <fonte>` (`orin code plugin marketplace list` lista os marketplaces configurados). Marketplaces declarados por um repositório em `extraKnownMarketplaces` só são consultados após o **Workspace Trust**.

Para remover um plugin, use **Remover** em **Settings → Plugins** ou `orin code plugin uninstall <plugin_id>`; todas as contribuições registradas pelo pacote são desativadas e removidas sem apagar o histórico de auditoria de turnos passados.
