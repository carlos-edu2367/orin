# Scheduler de chats agendados

- `ScheduledChatService` persiste a tarefa em `scheduled_chat_tasks` e usa as
  tabelas duráveis `schedules`/`schedule_occurrences` como autoridade temporal.
- O processo `agentos.workers.scheduler` apenas cria turnos no
  `PostgresChatStore`; o publisher e `ChatWorker` existentes continuam sendo
  os únicos componentes que enfileiram e executam o modelo.
- Recorrências reutilizam a mesma conversa, portanto preservam o workspace e
  usam o histórico limitado e as memórias do runtime. Há somente uma ocorrência
  ativa por agenda para evitar concorrência nesse contexto compartilhado.
- A seleção de provider/modelo e o projeto são validados no create e
  revalidados no disparo. A tarefa nunca persiste credenciais.
- A origem é registrada em `conversation_turns.scheduled_by_schedule_id` e
  devolvida à interface para sinalização da conversa.
