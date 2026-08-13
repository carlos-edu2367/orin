# Perguntas estruturadas do agente

`ask_user` fica em `src/agentos/agentic/agent_tools.py` e recebe um lote de
até oito perguntas em modos `checkbox`, `single_choice` e `text`. A ferramenta
valida identificadores, opções e o orçamento do payload público antes de
publicar a atividade; o frontend nunca é a autoridade para essa validação.

Uma chamada encerra o turno com `WAITING_USER` em vez de manter o worker
bloqueado. A resposta do formulário é enviada como a próxima mensagem normal
da conversa, que fecha o turno anterior e cria o próximo. O cartão é montado a
partir do evento de atividade durável, portanto continua disponível após
recarregar a página enquanto o turno ainda estiver aguardando resposta.

Arquivos principais:

- `src/agentos/agentic/runtime.py`
- `src/agentos/conversations/chat.py`
- `frontend/src/features/conversations/UserQuestionCard.tsx`
