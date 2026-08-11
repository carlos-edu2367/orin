# Sessão web, OpenRouter e Home Imersiva — Design

**Data:** 2026-08-10  
**Status:** aprovado para planejamento de implementação

## Objetivo

Permitir que uma pessoa autenticada grave uma chave do OpenRouter e comece uma
conversa a partir de uma home imersiva inspirada na referência visual fornecida,
sem expor credenciais, modelos arbitrários ou identificadores técnicos.

## Diagnóstico

O `ApiClient` criado pelo browser não recebe `Authorization` e o HTML não
publica um token CSRF. Já as rotas mutáveis do gateway exigem um principal
autenticado e, para sessão por cookie, CSRF e Origin válidos. Assim, o
`PUT /v1/providers/openrouter` é bloqueado antes de chegar ao adaptador
`PostgresProviderConfigurationAdapter`; a UI agrupa 401 e 403 na mensagem
"Sessão não autorizada para esta ação".

O adaptador persistente de configuração, o catálogo OpenRouter server-side e
`POST /v1/conversations` já existem. O defeito é a ausência de uma ponte
utilizável entre a sessão web emitida pelo host/autenticador e o frontend,
somada a cobertura E2E que usa mocks HTTP no lugar dessa integração.

## Decisão de autenticação

A web usará uma sessão opaca `HttpOnly` (`agentos_session`) emitida por um
autenticador ou host já confiável. O HTML entregue para essa sessão conterá um
token CSRF de vida curta numa meta tag; o cliente o envia somente em mutações.
O gateway continua sendo a fonte de `user_id`, escopo e autorização.

Esta entrega **não** cria um formulário de PAT nem armazena PAT, chave de
provider ou CSRF em localStorage, URL, logs ou estado React. Um login/IdP novo
não faz parte do escopo. Caso o ambiente ainda não tenha um emissor de sessão,
ele é pré-requisito de release: a implementação deve prover uma porta/contrato
de integração testável, mas não contornar a autenticação em produção.

Estados na UI:

1. sessão válida: salvar, atualizar catálogo e iniciar conversa funcionam;
2. sessão ausente/expirada: alerta localizado pede nova autenticação e preserva
   a chave digitada apenas em memória até a pessoa sair/trocar de página;
3. CSRF recusado: alerta pede recarregar/autenticar novamente, sem mostrar
   detalhes sensíveis;
4. erro do OpenRouter: estado separado de autenticação, com mensagem sanitizada
   e correlation ID copiável.

## Design da home

A rota `/` passa a usar a composição da imagem de referência como tela de
repouso, sem converter a imagem em fundo estático:

- topo compacto com marca AgentOS à esquerda e botão de configurações à direita;
- fundo quase preto com rede de pontos, névoa verde muito sutil e órbitas
  vetoriais decorativas; todos os ornamentos são `aria-hidden`;
- núcleo luminoso central no plano médio; CSS/SVG e Motion produzem a estética,
  preservando escala e responsividade sem depender de canvas;
- composer central de uma coluna: textarea grande como foco, botão circular de
  envio no canto e controles compactos de Provider/Modelo abaixo;
- o seletor de provider mostra apenas configurações habilitadas; modelo mostra
  somente catálogo autorizado e pesquisável; catálogo vazio apresenta um link
  claro para configurações;
- rodapé discreto com versão e frase de produto; nenhuma fixture, raw `agent_id`
  ou `task_ref` aparece na jornada normal.

A home deve funcionar de 320px a desktop grande. No mobile, o composer ocupa a
largura disponível, os seletores quebram para uma coluna e o elemento orbital
reduz a densidade visual sem perder o contexto da marca.

## Transição de envio

Ao enviar uma mensagem válida, o composer bloqueia para impedir duplicidade,
mantendo o texto localmente. Em até 220 ms, conteúdo secundário e controles
fazem fade para baixa opacidade enquanto núcleo/órbitas recebem uma breve
convergência. A navegação para `/execution/:executionId` só ocorre após o
recibo 201 de `POST /v1/conversations`.

Se a criação falhar, a animação reverte, a mensagem continua preenchida, o foco
retorna ao controle apropriado e o erro aparece em texto. Com
`prefers-reduced-motion`, a interface troca apenas opacidade sem movimento de
órbita, zoom, flashes ou animação contínua.

## Limites e segurança

- A chave é enviada uma única vez por `PUT`, mascarada no campo e apagada do
  estado React após sucesso; nunca volta em resposta, catálogo ou telemetria.
- O browser não chama OpenRouter diretamente; refresh e catálogo usam somente o
  adaptador server-side já existente.
- Provider + `model_id` continua sendo a identidade da seleção; o nome de
  exibição não é usado como identificador.
- O `Idempotency-Key` é preservado ao repetir a mesma intenção de salvar ou
  criar conversa.
- Mensagens públicas de falha não expõem token, chave, payload upstream,
  referência de tarefa, `agent_id` ou stack trace.

## Estratégia de testes

1. Backend: teste de contrato para sessão válida, sessão ausente, CSRF inválido
   e autorização contra `PUT /v1/providers/openrouter` e
   `POST /v1/conversations`.
2. Frontend unitário: `createBrowserApiClient` lê apenas o CSRF bootstrap;
   telas preservam a chave/mensagem em falha e não renderizam segredos.
3. Playwright E2E: fixture de sessão realista (cookie + meta CSRF) percorre
   salvar OpenRouter, atualizar catálogo, selecionar modelo e criar conversa;
   cenários sem sessão e CSRF inválido usam erros distintos.
4. Playwright visual: snapshots desktop e mobile da home em repouso e em estado
   de envio; baseline em movimento reduzido.
5. Acessibilidade: labels, ordem de tabulação, foco após erro, contraste,
   `aria-live` para estados e ausência de violações críticas/graves com axe.

## Arquivos com impacto esperado

- `frontend/index.html`, `frontend/src/api/client.ts` e uma nova pequena camada
  de bootstrap de sessão;
- `src/agentos/api/gateway.py`, contratos de segurança e bootstrap/ASGI apenas
  para integrar a sessão emitida pelo host, sem fallback in-memory;
- `frontend/src/app/Home.tsx`, `frontend/src/features/conversations/ConversationComposer.tsx`,
  estilos e rota de configurações;
- testes API, unitários, E2E, acessibilidade e snapshots visuais;
- `.env.example`, `README.md` e documentação frontend para remover o requisito
  obsoleto de um modelo na configuração de credenciais.

## Critérios de aceite

- Uma sessão válida consegue salvar a chave OpenRouter e atualizar o catálogo;
  uma sessão inválida nunca grava a chave.
- A página inicial reproduz a hierarquia, atmosfera e interação da referência,
  mantendo Provider/Modelo autorizados e acessíveis.
- Enviar uma mensagem produz um fade curto e navega somente após confirmação
  do backend; erro recupera o formulário sem perder a intenção.
- As suítes de contrato, unitárias, E2E, visuais, acessibilidade, lint e build
  passam; a revisão confirma que nenhum segredo aparece no navegador ou logs.
