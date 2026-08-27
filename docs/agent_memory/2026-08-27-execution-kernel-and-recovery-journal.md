# Kernel de execução e journal de recuperação — 2026-08-27

- O caminho padrão de `ChatWorker` agora entra em `RuntimeService`; `AgenticTurnRuntime` permanece somente como adaptador de streaming, formatos de provider e ferramentas. Quando está sob o Kernel, ele não altera lifecycle canônico nem finaliza a projeção de conversa.
- `execution_checkpoints` e `execution_effects` registram escopo completo, intenção, estado `PREPARED`/`IN_FLIGHT`/terminal, retryability e referências. Eles não alimentam SSE/outbox público e não registram argumentos, credenciais ou prompt.
- A recuperação de worker marca efeito em voo como `UNKNOWN` e pausa a `Execution`/projeção de chat. Somente uma execução sem fronteira externa registrada retorna à fila. Isto impede repetir efeitos cujo resultado não pode ser provado.
- Um follow-up de `waiting_user` emite `provide_input` para a execução canônica anterior, além de criar o novo turno de conversa.
- A migration `0040_execution_recovery_journal` precisa ser aplicada antes de ativar o novo worker em uma instalação existente.
