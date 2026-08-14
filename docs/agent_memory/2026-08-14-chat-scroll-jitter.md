# Chat: eliminar stutter do auto-scroll durante streaming

- O container `.chat__scroll` recebe mensagens, deltas e atividades de tools no mesmo fluxo enquanto a resposta está em andamento.
- O auto-scroll de uma viewport fixada não deve esperar o `useEffect`: isso permite que o navegador pinte um frame com o offset antigo antes de reposicionar o fim, tornando visíveis resets durante a segunda mensagem e durante a montagem da timeline.
- A correção usa `useLayoutEffect` para aplicar `scrollTop = scrollHeight` antes do paint e define `overflow-anchor: none` em `.chat__scroll`, evitando disputa entre o posicionamento controlado pelo React e a ancoragem automática do navegador.
- O scroll suave continua reservado ao botão explícito de retorno ao fim; quem saiu do fim continua sem ser forçado a acompanhar novos eventos.
- Validação de 2026-08-14: `npm test -- --run` passou com 268 testes em 43 arquivos e `npm run build` passou.
