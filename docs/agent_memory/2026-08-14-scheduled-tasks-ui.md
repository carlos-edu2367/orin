# Redesign da tela de tarefas agendadas

- A tela `/schedules` reutilizava a casca e os componentes visuais de Skills, mas apresentava uma única coluna longa. O formulário ocupava toda a atenção e as tarefas existentes ficavam abaixo da dobra.
- A nova composição usa a casca padrão do app, um cabeçalho de automação, formulário principal, cartão explicativo e painel lateral de tarefas ativas. Em telas estreitas, o grid empilha e o botão primário ocupa a largura disponível.
- O contrato de agendamento não foi alterado: os mesmos endpoints, handlers, provider/model picker, recorrências, cancelamento e links de conversa continuam sendo usados. A captura visual foi validada com dados simulados em 1280x900 e 390x844; lint, build e 268 testes frontend passaram.
