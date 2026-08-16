# Seleção de provider e modelo no chat

## Decisão

- O chat reutiliza o `ModelPicker` existente dentro do `Composer`, permitindo trocar provider e modelo sem sair da conversa.
- A seleção é carregada pelo catálogo autorizado do usuário em `/v1/providers/{provider}/models` e o picker fica desabilitado durante a execução ou parada de um turno.
- Cada novo envio pode incluir `selection`; a API valida o par provider/modelo contra o catálogo do usuário antes de encaminhar a mensagem.
- No `PostgresChatStore`, uma seleção válida atualiza o provider/modelo da conversa e identifica o novo turno com essa seleção. Sem seleção, o turno continua herdando a seleção atual.

## Compatibilidade e segurança

- Não foi necessária migração: as colunas de provider/modelo já existem na conversa.
- A validação não confia no valor vindo da UI: a autoridade continua sendo o catálogo de provider com escopo do usuário.
- O fluxo foi aplicado também a respostas de perguntas, aprovações MCP/plugins e mensagens com anexos, preservando o pipeline normal da conversa.

## Validação

- Frontend: testes unitários de `ChatPage` e `ModelPicker` passaram.
- Backend: testes de API, `ChatApplication` e `ChatStore` passaram.
- Build TypeScript/Vite passou; permanece apenas o aviso preexistente de chunks grandes.
