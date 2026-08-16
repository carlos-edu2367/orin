# Agendamentos: horário local e fuso selecionável

- O formulário de agendamento usa `America/Sao_Paulo` como fuso padrão, mas permite selecionar outros fusos IANA em `SchedulesPage.tsx`.
- Para recorrências, `time_of_day` e `timezone` continuam sendo a autoridade do calendário local.
- Para tarefas de uma vez, o frontend envia o valor civil de `datetime-local` sem conversão pelo fuso do navegador; o backend interpreta esse valor no `timezone` selecionado e persiste o instante em UTC.
- A API serializa `next_fire_at` com `Z`, inclusive quando o SQLite devolve `DateTime(timezone=True)` sem tzinfo. Isso evita que o frontend trate um UTC como horário local e exiba, por exemplo, 02:50 em vez de 23:50 em São Paulo.
- Validação realizada: testes unitários do scheduler/API, teste da tela de agendas e build do frontend.
