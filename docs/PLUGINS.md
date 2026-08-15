# Plugins

O Orin aceita plugins no formato declarativo usado pelo Claude Code: um
`.claude-plugin/plugin.json`, diretórios `skills/<nome>/SKILL.md`, arquivos
`agents/*.md` e um `.mcp.json` opcional. O manifesto precisa declarar `name` e
uma versão SemVer. Campos desconhecidos permanecem inertes.

## Suporte e limites

O v1 suporta skills, subagentes declarativos e servidores MCP stdio/HTTPS. A
instalação nunca executa arquivos do pacote. Servidores MCP entram primeiro em
`pending_approval` e exigem aprovação própria. `hooks/` é detectado e mostrado
como aviso, mas não é ativado; comandos com scripts também não são executados.

Pacotes podem vir de URL HTTPS pública do GitHub, `owner/repo`, marketplace
configurado ou diretório local. O conteúdo é limitado por tamanho e número de
arquivos, não aceita symlinks e é instalado em
`data/plugins/<plugin_id>/<version>/`. O digest SHA-256 é calculado sobre todos
os caminhos e conteúdos ordenados; ele garante que uma versão publicada não
seja substituída silenciosamente, mas não prova a identidade do publicador.

## Fluxo de instalação

O agente busca ou recebe uma referência, inspeciona o pacote e apresenta um
card com skills, MCPs, subagentes e avisos. Só o clique em **Instalar** ativa as
contribuições. A recusa deixa o pacote sem ativação. Em Settings → Plugins é
possível inspecionar, ativar/desativar e remover plugins; remoção também remove
as contribuições registradas, sem apagar evidência de execuções passadas.

## Escrevendo um plugin

Exemplo mínimo:

```text
.claude-plugin/plugin.json
skills/review/SKILL.md
agents/reviewer.md
.mcp.json
```

Use frontmatter compatível com o parser de skills do Orin (`name`, `version`,
`description` e, opcionalmente, tags, dependências e ferramentas). Recursos de
uma skill devem ficar em `references/`, `examples/`, `templates/` ou
`resources/`. Nunca inclua credenciais em `.mcp.json`: apenas os nomes das
variáveis são preservados e os valores são fornecidos no fluxo de aprovação do
MCP.

## Marketplaces e remoção

Um marketplace é um repositório com um índice `marketplace.json` contendo
`name` e uma lista `plugins`, cada item com `name` e `source` (URL HTTPS ou
`owner/repo`). Depois de adicionado em Settings → Plugins, o nome pode ser
usado pelo agente para busca. Para remover, use **Remover** no card do plugin e
confirme explicitamente.
