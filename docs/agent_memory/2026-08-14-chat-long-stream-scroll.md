# Chat: respostas longas e rolagem durante streaming

- O feed de atividades do frontend é bounded em 500 eventos para manter a timeline controlada.
- Antes da correção, `ChatPage` remontava o texto parcial exclusivamente desses eventos; como o backend divide deltas em chunks de 480 caracteres, respostas muito longas podiam perder o prefixo durante o streaming.
- A correção mantém um buffer cumulativo por `message_id`, deduplicado por `event_id`, separado do feed visual bounded.
- `turnTimelineFold` reconcilia o texto cumulativo/durável com os deltas ainda visíveis, preservando atividades intercaladas e recolocando o prefixo que saiu da janela.
- O auto-scroll durante streaming passou a usar atribuição imediata de `scrollTop`; a animação suave permanece apenas na ação explícita de retornar ao fim. Isso evita que várias animações concorram com a rolagem manual.
- Cobertura adicionada em `ChatPage.test.tsx` para rollover de 500 eventos e para viewport fixado, e em `turnTimelineFold.test.ts` para texto fora da janela. Frontend completo validado com 268 testes e build de produção.
