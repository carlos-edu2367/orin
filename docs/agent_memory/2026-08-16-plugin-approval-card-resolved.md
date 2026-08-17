# Cartao de aprovacao de plugin resolvido

- O backend encerrava a aprovacao do plugin corretamente, mas o componente React `PluginApprovalCard` continuava montado quando o turno deixava `waiting_user`.
- Nesse estado, os botoes eram ocultados, porem a lista completa de contribuicoes permanecia no historico e parecia um modal pendente.
- A regra visual agora retorna `null` quando `active` e falso; a solicitacao continua registrada no historico de atividade, sem manter o dialogo de aprovacao.
- Validacao: testes focados do frontend passaram (14 testes), `npm run build` passou e `/healthz`/`/readyz` responderam `ok`/`ready` apos reiniciar o backend em `8000`.
