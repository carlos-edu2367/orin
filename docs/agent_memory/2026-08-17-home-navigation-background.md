# Fundo das ações fixas da navegação na Home

## Diagnóstico

O rodapé `.project-navigation__actions` tinha `background: linear-gradient(var(--surface), var(--surface) 70%)`. Como a Home usa um fundo atmosférico no shell e a sidebar não define um painel opaco, esse gradiente criava um retângulo escuro atrás de “Novo projeto” e “Ações agendadas”.

## Correção

O contêiner de ações agora usa `background: transparent`. Os backgrounds individuais dos botões continuam controlando o destaque primário e os estados de hover, enquanto o entorno acompanha o fundo da Home.

## Validação

O teste de regressão de layout verifica que as ações continuam fora da área rolável e que o contêiner permanece transparente.
