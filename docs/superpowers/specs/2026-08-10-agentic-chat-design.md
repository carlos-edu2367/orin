# Agentic chat em tempo real — Design

## Decisão

O chat é o agregado de produto. Cada envio cria (ou continua) uma conversa,
uma mensagem do usuário, uma mensagem assistente em progresso, um turn e a
execution correspondente. O navegador recebe apenas projeções públicas da
conversa; provider key, referências de tarefa e IDs internos ficam no servidor.

O dispatcher persiste um trabalho por turn antes de publicar seu identificador
no Redis/ARQ. O worker independente adquire o trabalho, move a execution por
`QUEUED → STARTING → RUNNING → COMPLETED/FAILED`, faz streaming do provider
configurado e persiste cada delta como evento de conversa. Um watchdog falha
trabalhos não adquiridos com um erro recuperável. Reconnect lê o histórico e
retoma eventos a partir de cursor, portanto não duplica mensagens.

## Superfícies públicas

- `POST /v1/conversations` cria conversa e primeiro turn; retorna somente
  `conversation_id`, título, estado e message/turn públicos.
- `GET /v1/conversations`, `GET /v1/conversations/{id}` e
  `POST /v1/conversations/{id}/messages` suportam sidebar, hidratação e
  continuidade idempotente.
- `GET /v1/conversations/{id}/events` é SSE com cursor; a UI reidrata antes de
  reconectar.
- `GET /v1/conversations/{id}/overview` apresenta turnos/executions sem expor
  identificadores técnicos; `POST .../turns/{turn_id}/retry` cria novo turn.

## Segurança e operação

Somente o worker lê a credencial do provider e nunca a registra. Eventos de
chat carregam apenas conteúdo autorizado e estados sanitizados. Health inclui
heartbeat do worker, backlog e idade do outbox/dispatch para identificar fila
parada sem divulgar infraestrutura ou segredos.
