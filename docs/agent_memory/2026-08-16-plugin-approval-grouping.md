# Card de aprovação agrupado com a busca do plugin

## Diagnóstico

O backend persistia corretamente o evento `install_plugin` com `plugin_approval` e o frontend
recebia esse payload. Porém, `search_plugin` e `install_plugin` compartilhavam o mesmo
`toolKind = plugin`; `activitySummary` os agrupava em uma única linha. Como o renderer escolhe o
primeiro evento do grupo, ele renderizava o card genérico de busca e ocultava o cartão interativo
de aprovação.

## Correção

Eventos com aprovação de plugin, aprovação MCP ou perguntas estruturadas agora recebem uma chave
de agrupamento própria. A compactação de ferramentas comuns continua funcionando.

## Verificação

- Testes frontend focados: 13 passaram.
- Build frontend concluído com o bundle `index-B97Qa-td.js`.
- Orin reiniciado e saudável em `http://127.0.0.1:8000` (`/healthz` e `/readyz` OK).
