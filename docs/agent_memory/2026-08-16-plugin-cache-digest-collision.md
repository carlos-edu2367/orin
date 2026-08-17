# Conflito de digest no cache de plugins

## Fato encontrado

O cache local tinha `superpowers/6.3.0`, mas o repositório remoto entregava o mesmo número de
versão com outro `package_digest`. O `PluginFetcher` tratava isso como uma colisão inválida e o
`PluginService` convertia a falha em `plugin could not be inspected`, impedindo até a inspeção.

## Decisão implementada

O pacote antigo continua imutável. Quando a mesma versão chega com digest diferente, a nova
cópia é armazenada em `plugin-id/version-digest`, permitindo inspeção e aprovação explícita sem
sobrescrever uma instalação/cache anterior. Um digest já existente continua sendo reutilizado;
um conflito improvável no caminho completo ainda falha fechado.

## Verificação

Com uma cópia do cache stale que reproduzia o problema, `PluginService.inspect("obra/superpowers")`
passou a retornar `pending_approval` com 14 skills e um caminho versionado pelo digest. Os testes
de plugins, ferramentas de agente e integração passaram: 16 testes.
