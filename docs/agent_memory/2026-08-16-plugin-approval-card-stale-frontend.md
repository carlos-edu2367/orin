# Card de aprovação de plugin ausente por bundle frontend stale

## Diagnóstico

Na tentativa real de instalação, o backend persistiu corretamente o evento `tool.finished` de
`install_plugin` com `plugin_approval: true`, `wait_for_user: true` e o objeto do plugin contendo
14 skills. O parser de eventos e o componente `PluginApprovalCard` existem no código-fonte do
frontend.

A instância, contudo, serve o bundle estático `frontend/dist/assets/index-BFAVItWm.js`, gerado às
03:01, anterior ao código do card. A busca no bundle não encontrou `plugin_approval`,
`PluginApprovalCard` ou o texto de aprovação, embora esses elementos existam em `frontend/src`.

## Conclusão

O problema não é a inspeção nem a persistência da aprovação: é um build frontend desatualizado.
É necessário executar o build do frontend e reiniciar o servidor que serve `frontend/dist` para
que o cartão apareça.
