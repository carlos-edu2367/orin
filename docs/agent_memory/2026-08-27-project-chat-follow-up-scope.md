# Follow-up de chat em projeto e escopo de `projects`

## Correção

`PostgresChatStore.create()` usa a tabela `projects` tanto ao criar a conversa quanto ao adicionar um turno a uma conversa de projeto. Um import local dentro do ramo de criação tornava `projects` uma variável local para toda a função. No follow-up, esse ramo não é executado e a consulta do workspace do projeto levantava `UnboundLocalError`.

O módulo já importa `projects`; remover o import local preserva o mesmo contrato e permite que o segundo turno use o workspace do projeto.

## Cobertura

`tests/unit/conversations/test_chat_store.py` cobre a criação de uma conversa em projeto seguida de uma nova mensagem, garantindo que o turno seja enfileirado.
