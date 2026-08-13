# Auditoria UX/UI do chat — 2026-08-13

## Escopo e método

Foi analisado o frontend local-first do Orin a partir do `README.md`, rotas, componentes React, reducers/SSE, estilos e testes frontend. O backend local não estava em execução durante a auditoria (`http://127.0.0.1:8000/healthz` não respondeu), portanto a avaliação visual ao vivo ficou para a validação por testes mockados e build; não foram inferidos comportamentos de uma sessão real.

## Mapa encontrado

```text
App (BrowserRouter)
├── /: Home
│   ├── WorkspaceNavigation (histórico e projetos)
│   ├── CommandPalette
│   └── Composer + picker de provider/modelo
├── /chats/:conversationId: ChatPage
│   ├── header (título, visão geral, command palette, settings)
│   ├── WorkspaceNavigation
│   ├── chat__scroll
│   │   ├── mensagens do usuário e assistente
│   │   ├── TurnTimeline intercalada (texto + atividade)
│   │   ├── ActivityStream para eventos ainda sem resposta
│   │   └── AgentPulse durante turno ativo
│   ├── OverviewPanel opcional
│   └── Composer (Stop, anexos e workspace)
├── /projects/:projectId/chats/:conversationId: mesma ChatPage contextual
└── settings, providers, memory, skills e projetos: superfícies de gestão
```

O snapshot durável de conversa é conciliado com SSE em `ChatPage`; `activityReducer` deduplica por `eventId`, mantém cursor e permite ressincronização. `TurnTimeline` intercala `assistant.delta` e ações do turno, e `ActivityCard` dá acesso progressivo aos detalhes. Essa base já está alinhada ao princípio do produto: conversa primeiro, execução visível e detalhes sob demanda.

## Achados priorizados

| Prioridade | Evidência | Impacto de UX |
| --- | --- | --- |
| P1 | Ao sair do fim de `.chat__scroll`, `pinnedRef` impede autoscroll corretamente, mas não há contador, aviso ou ação para alcançar novas mensagens/atividades. O composer também deixa de estar visível até hover/foco. | Em uma execução com muitas ferramentas, a pessoa pode não perceber que houve progresso nem ter um caminho explícito de volta ao presente. |
| P1 | `ProjectNavigation` renderiza todos os chats como `Link`, sem estado ativo. | Em listas longas, perde-se a referência visual de qual conversa está aberta. |
| P1 | O botão de visão geral em `ChatPage` sempre navega para `/chats/:conversationId[/overview]`, mesmo quando a rota atual é `/projects/:projectId/chats/:conversationId`. | Abrir ou fechar a visão geral descaracteriza o contexto do projeto e muda a URL sem intenção do usuário. |
| P2 | Falhas ao carregar uma conversa recebem uma mensagem genérica no thread e o wildcard retorna para a Home. | Deep link inválido tem recuperação pouco orientada; requer uma rodada específica para diferenciar indisponibilidade local e conversa ausente, com contrato de erro confirmado. |
| P2 | A command palette limita conversas a 12 itens recebidos pela superfície atual. | Continua útil para atalhos globais, mas não escala como descoberta completa de histórico sem busca paginada no backend. |
| P3 | O overview é uma lateral contextual e não compete com o chat; a camada funciona, mas não informa por si só por que a pessoa deveria abri-la. | O acesso atual por botão é suficiente nesta rodada; uma recomendação contextual exigiria sinais de uso a validar. |

## Decisão desta rodada

Implementar uma melhoria incremental de **continuidade de conversa**:

1. Quando novos itens chegam enquanto a leitura está acima do fim, mostrar uma ação discreta, acessível e contada para ir ao conteúdo mais recente. Não realizar autoscroll forçado.
2. Indicar a conversa ativa na navegação sem alterar a estrutura de histórico/projetos.
3. Preservar a rota base de chats de projeto ao abrir e fechar a visão geral.

Essas mudanças atacam fluxo, navegação e feedback antes de polimento visual. Elas não alteram o contrato de SSE, o reducer, streaming, Stop, anexos, command palette, modelo, overview ou backend.

## Implementação e verificação

- Implementação delegada a um subagente frontend e revisada pelo agente principal.
- `ChatPage` mostra uma ação contada para retornar ao fim sem sequestrar o scroll; a contagem usa identidades de conteúdo e não conta `assistant.delta` duas vezes.
- `ProjectNavigation` passou a usar `NavLink` para expor o chat ativo, inclusive na rota descendente de visão geral.
- A rota contextual `/projects/:projectId/chats/:conversationId` é preservada ao abrir e fechar a visão geral.
- Testes unitários específicos, a suíte unitária completa e build foram executados. E2E/visual não iniciou porque o Chromium do Playwright não está instalado neste ambiente.

## Adendo de implementação — prévias e recuperação (2026-08-13)

Os achados P2 de recuperação e descobribilidade local foram resolvidos sem mudar contratos do backend:

- Conversa inexistente ou backend local indisponível agora recebe estado dedicado, com explicação, nova tentativa e retorno para nova conversa. A chave da rota também remonta a página de chat ao trocar de conversa, evitando conteúdo anterior momentaneamente visível.
- A command palette não descarta mais conversas depois das 12 primeiras: sem consulta mostra os primeiros 32 itens e, com texto, filtra toda a lista fornecida pela navegação.
- A prévia de arquivos deixou de delegar todos os formatos ao `iframe` do navegador. Markdown é renderizado com a mesma pilha do chat; JSON é formatado; scripts e arquivos de configuração são apresentados em bloco de código; imagens são exibidas como mídia; PDFs preservam o visualizador nativo isolado. Formatos sem visualização segura recebem orientação explícita para abrir ou baixar.

## Itens conscientemente adiados

- Estado dedicado para conversa inexistente e mensagens de indisponibilidade do backend: depende de diferenciar com segurança o envelope de erro da API.
- Busca completa e paginada de conversas na command palette: exige capacidade de listagem/pesquisa além da lista local atual.
- Mudanças de densidade nas ActivityCards: a hierarquia atual já apresenta resumo, expansão e detalhes; não há evidência de que uma alteração estrutural seja necessária nesta rodada.
