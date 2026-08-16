# Correções de tarefas agendadas e Home

- A criação de uma tarefa agendada falhava no runtime local porque `scheduled_chat_tasks.schedule_id` possui uma chave estrangeira para `schedules.schedule_id`, mas o serviço inseria a tarefa antes da agenda. A ordem correta é inserir `schedules` e depois `scheduled_chat_tasks` na mesma transação.
- A rota `POST /v1/schedules` deve converter `ValueError` de validação do domínio em `ApplicationValidationError`, preservando o envelope HTTP 422 para data, fuso, modelo ou provider inválidos.
- A Home precisa usar `height: 100dvh` e `overflow: hidden`, como o Chat, com o histórico da navegação em sua própria região rolável. O conteúdo central pode rolar internamente quando necessário.
- Cobertura adicionada: o teste do scheduler usa o engine SQLite local com `PRAGMA foreign_keys=ON`, e o teste de layout verifica o shell da Home com altura fixa da viewport.
- A cobertura HTTP verifica criar, listar e cancelar pelo endpoint; a cobertura de componente verifica o submit da UI e o cancelamento após a tarefa aparecer na lista.
