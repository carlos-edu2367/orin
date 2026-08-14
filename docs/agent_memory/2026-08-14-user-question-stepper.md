# Fluxo sequencial para perguntas do agente

- `ask_user` continua aceitando perguntas em lote e a retomada do turno `WAITING_USER` continua acontecendo por uma única mensagem normal. O frontend agora trata o lote como um wizard local para não disparar o agente a cada pergunta.
- `UserQuestionCard` mostra apenas a pergunta atual expandida, mantém perguntas anteriores como resumos editáveis e oferece progresso, voltar, continuar e envio final. Respostas vazias continuam permitidas para preservar o contrato de perguntas opcionais.
- A visualização foi validada com cinco perguntas em desktop; o fluxo avançou da primeira para a segunda, exibiu o resumo editável anterior e manteve o botão de edição. Testes frontend: 269 passaram; lint e build passaram.
