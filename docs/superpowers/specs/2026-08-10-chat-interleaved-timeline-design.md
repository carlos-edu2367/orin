# Timeline intercalada no chat — Design

## Problema

`ChatPage` renderiza dois blocos sequenciais e independentes: primeiro todas
as bolhas de `messages` (texto final, já pronto), depois todo o
`<ActivityStream>` (ações da conversa inteira) — ver
[ChatPage.tsx:221-236](../../../frontend/src/features/conversations/ChatPage.tsx).
Isso é estrutural, não um bug de estado: mesmo que cada `ConversationActivityEvent`
já carregue `cursor` e `turn_id` (obrigatório em todo evento, ver
[events.py:52](../../../src/agentos/agentic/events.py)), o frontend descarta essa
ordem — agrupa o texto de um turno numa bolha só e filtra os eventos de tipo
`message` (`assistant.delta`/`assistant.completed`) para fora do
`ActivityStream` ([activitySummary.ts:48](../../../frontend/src/features/conversations/activitySummary.ts)).
Resultado: o usuário só vê "o que o agente fez" depois de toda a resposta,
nunca no meio dela.

## Decisão

Cada mensagem do assistente passa a renderizar uma **timeline de turno**
intercalada — texto e cards de ação na ordem real de `cursor` — em vez de uma
bolha única seguida por um `ActivityStream` global. Isso vale tanto para
turnos ao vivo quanto para conversas reabertas do histórico, porque os dois
casos leem a mesma estrutura (`activity.events`, já com `cursor` e `turn_id`).

### Algoritmo (`buildTurnTimeline`)

Para uma mensagem do assistente com `message_id`:

1. Achar seu `turn_id`: primeiro evento em `activity.events` com
   `kind === 'message'` e esse `messageId`.
2. Filtrar `activity.events` para esse `turn_id`, já vêm ordenados por
   `cursor` (ordem de chegada/snapshot).
3. Dobrar (fold) a lista: eventos `message` do mesmo `messageId` acumulam
   texto num segmento corrente; qualquer evento não-`message` fecha o
   segmento de texto aberto (se houver conteúdo), aplica as mesmas regras de
   agrupamento que `summarizeActivities` já usa hoje (pular
   `tool.requested`, colapsar `tool.started`→`tool.finished`, juntar
   ferramentas consecutivas da mesma família) e produz um item de atividade;
   depois disso um novo segmento de texto pode começar.
4. Resultado: `TimelineItem[]` = `{ kind: 'text', content: string } | { kind: 'activity', group: ActivityGroup }`, na ordem em que aconteceu.

A lógica de agrupamento (`isRenderable`, `groupingKey`, `groupLabel`) sai de
`activitySummary.ts` como está — ela é reaproveitada pelo fold, não
reescrita, para não regredir rótulos/estados já testados.

### Pareamento turno ↔ mensagem do usuário

`ConversationMessage` não carrega `turn_id`. O pareamento usa a ordem
posicional: a invariante do sistema é uma conversa linear (composer trava
durante a execução, então não há dois turnos concorrentes) — a n-ésima
mensagem de usuário corresponde ao n-ésimo turno em `conversation.turns`.
Isso só é usado para decidir *onde* a timeline do turno é ancorada no fluxo
(logo após aquela mensagem de usuário); a timeline em si é construída a
partir do `turn_id` resolvido no passo 1 acima, não da posição.

### Fallback

Se o `turn_id` de uma mensagem não for resolvível (dado antigo sem essa
granularidade, evento ausente, contagem turnos≠mensagens de usuário), essa
mensagem renderiza exatamente como hoje: bolha única com `item.content`,
sem timeline. Nunca perde dado, nunca quebra a tela — só perde a
intercalação para aquele caso específico.

### Renderização

`ChatPage.tsx` troca o par
`messages.map(...) + <ActivityStream events={activity.events} />` por: para
cada mensagem, se for `assistant` e tiver timeline resolvível, renderizar a
sequência de `MarkdownMessage` (por segmento de texto) e
`ActivityCard`/`AgentBirth`/`AgentExchange` (por item de atividade) — os
mesmos componentes que `ActivityStream` já usa hoje, na mesma ordem de
import, sem alteração visual. Mensagens de usuário continuam bolhas simples,
sem mudança. `AgentPulse` continua ancorado no fim de tudo (representa
"trabalhando agora", não pertence a um turno específico).

`ActivityStream` deixa de ser chamado no nível da página; sua função de
agrupamento (`summarizeActivities`) é reaproveitada por `buildTurnTimeline`,
então o componente em si — e seus testes — continuam existindo para reforço,
mas passam a operar sobre a fatia de eventos de um turno, não da conversa
inteira.

## Não-objetivos

- Não muda o backend, o formato dos eventos SSE, nem a paginação de
  histórico.
- Não intercala eventos entre turnos diferentes nem tenta ordenar mensagens
  de usuário fora da ordem em que já chegam de `conversation.messages`.
- Não adiciona componente visual novo — reordena os existentes.

## Testes

- Unitário para `buildTurnTimeline`: dado uma sequência de eventos com
  `assistant.delta` intercalado com `tool.finished`/`agent.created`, o
  resultado alterna `text`/`activity` na ordem certa; eventos não-renderáveis
  (`tool.requested`, `tool.started` já resolvido) continuam pulados.
- Unitário para o pareamento: turno sem `turn_id` resolvível cai no
  fallback (bolha única).
- Integração (`ChatPage.test.tsx`): estender o teste existente de snapshot
  finalizado para afirmar a *ordem* no DOM (texto antes do card de ação
  quando o evento de atividade tem cursor maior que o delta, e vice-versa),
  não só a presença dos textos.
- Atualizar snapshots visuais (`chat.png`, `chat-activity-open.png`) já que a
  posição do card de atividade muda.
