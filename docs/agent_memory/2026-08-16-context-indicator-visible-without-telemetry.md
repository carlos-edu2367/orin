# Indicador de contexto sempre visível

## Descoberta

O `ContextIndicator` retornava `null` quando `context_usage` ainda não existia no snapshot ou nas atividades da conversa. Isso ocultava completamente o indicador em chats antigos e antes da primeira execução após a atualização.

## Decisão

O componente deve permanecer renderizado mesmo sem telemetria. Nesse estado, exibe `Contexto —` e um tooltip explicando que o cálculo detalhado será preenchido após a próxima execução. Quando a telemetria chega, o mesmo componente exibe o percentual, limite, categorias de tokens e estado da compactação automática.

## Validação

- Teste unitário do `ContextIndicator`: passou.
- Build de produção do frontend: passou.
- Validação visual com o backend local e a conversa `oi`: o chip apareceu no cabeçalho mesmo sem `context_usage`.
