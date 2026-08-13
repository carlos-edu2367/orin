# Scheduler de chat — plano de implementação

## Objetivo

Permitir que uma pessoa agende um prompt com provider/modelo e, opcionalmente,
um projeto. Cada ocorrência deve entrar no mesmo pipeline durável de um chat
normal, mas manter a origem agendada visível na conversa.

## Decisões

1. Reutilizar as tabelas `schedules` e `schedule_occurrences` já existentes;
   o Scheduler apenas materializa turnos e nunca executa o runtime do agente.
2. Persistir o prompt e a seleção de modelo em uma tabela de tarefas
   agendadas, sem credenciais. A cada disparo o provider/modelo e o projeto
   são revalidados no banco antes de enfileirar o turno.
3. Uma agenda recorrente usa uma conversa contínua. Isso compartilha o
   workspace e permite ao runtime reaproveitar o histórico já limitado pela
   janela de contexto e as memórias duráveis relevantes. Disparos da mesma
   agenda são serializados para não disputar esse contexto.
4. Cobrir quatro regras explícitas, no timezone IANA informado: uma vez,
   a cada hora, diariamente em horário civil e semanalmente em dia/horário
   civil. O serviço armazena os próximos instantes em UTC.

## Entregas

- migração para tarefas agendadas e marcação de turnos;
- serviço e worker periódico duráveis;
- API para criar/listar/cancelar agendas;
- UI para criar uma agenda e uma indicação na conversa;
- testes unitários do calendário, materialização, recorrência e isolamento.
