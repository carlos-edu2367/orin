# Orçamento ilimitado de ações no runtime agentic

## Achado

A configuração de runtime persistia somente `max_iterations`. Quando o usuário selecionava `null` (sem limite), o worker ainda criava `AgenticLimits` com `max_actions=24` para o agente principal, e `TurnSession` criava subagentes com `max_actions=12`. Isso podia produzir `ACTION_LIMIT` mesmo com “Sem limite de interações” marcado.

## Decisão

`AgenticLimits.max_actions` aceita `None` como sem limite. O worker converte a configuração `max_iterations=None` para `max_actions=None`; esse valor é preservado no agente principal e propagado aos subagentes. Configurações numéricas continuam usando os tetos existentes: 24 para o principal e 12 para cada subagente.

## Validação

Testes cobrem a propagação no `ChatWorker`, mais de 12 ações em subagente ilimitado e execução do runtime com ações ilimitadas.
