# Fluxo unificado de novo chat em projeto

## Decisão

O botão `Novo chat em <projeto>` não deve abrir um modal de criação separado. Ele navega para `/projects/:projectId/new`, que renderiza o mesmo `Home` e `Composer` usados para iniciar uma conversa normal. A criação envia `project_id` no endpoint comum `/v1/conversations`.

## Contrato relevante

O `Composer` da tela inicial já suporta texto, anexos e seleção de provider/modelo, mas a pasta de workspace dependia de um `conversation_id`, por isso não funcionava antes da primeira mensagem. Foi adicionado o endpoint autenticado `POST /v1/workspaces/inspect` para validar a pasta antes da conversa existir. A criação aceita `workspace_path` e `workspace_acknowledged_risk`; o backend valida novamente o caminho, verifica o projeto pertencente ao usuário e salva o root antes de enfileirar o primeiro turno.

Para conversas independentes, o root usa o `conversation_id` alocado. Para conversas de projeto, usa o `project.workspace_id`, preservando o escopo compartilhado entre chats do projeto. Em falha da criação, o root anterior é restaurado ou removido.

## Cobertura

Os testes cobrem o novo chat de projeto com pasta e primeira mensagem, a inspeção pré-conversa e a persistência do root no workspace do projeto. A suíte frontend completa passou; o build TypeScript/Vite passou; os testes de API focados passaram.

## Risco restante

O lint global ainda acusa violações preexistentes em `ChatPage` e componentes de plugins. Os arquivos alterados neste fluxo passam no lint direcionado.
