# Cena: visualização animada em pixel art dos chats do Orin

Data: 2026-11-09 · Status: Fases 1, 2 e 3 implementadas

## Objetivo

Oferecer uma segunda forma de ver uma conversa: uma **cena em pixel art** onde o Orin (agente principal) e seus subagentes aparecem como personagens 2D num escritório/oficina, animados pelo que estão fazendo e mostrando visualmente a interação entre eles. O chat comum continua sendo a visualização padrão.

## Decisões de escopo (tomadas com o usuário)

| Tema | Decisão |
|---|---|
| Onde aparece | **Substitui a `TeamActivityView`**. O botão de alternância (`chat__team-toggle-btn` em `ChatPage.tsx`) passa a ser **Chat / Cena**. |
| Cenário | **Escritório/oficina com estações**. O personagem anda até a estação da ferramenta que está usando. |
| Personagens | Orin = mascote roxo (base: `components/OrinMascot.tsx`, cor `#B4A4EF`) em pixel art. Subagentes = **avatar sorteado, único por agente**, determinístico a partir do `agentId` (mesmo agente sempre com a mesma cara). |
| Interações | Clicar no personagem → painel com tarefa, ferramenta atual, último pensamento. Aprovar permissões / responder perguntas na cena (personagem levanta a mão / placa). Mandar mensagem ao Orin ou a um subagente. Parar/cancelar um subagente. |
| Tempo | **Só ao vivo.** Sem turno em execução, a equipe aparece descansando. Sem replay. |
| Arte | **Sprites definidos em código**: matrizes de pixels + paleta, em TypeScript. Nenhum PNG/binário novo. *(Nota: a arte final usa webp pintado em `scene/assets/` com fallback para os sprites em código.)* |
| Modos | Funciona no modo **Chat** e no modo **Code**. |
| MVP inclui | Balões de fala/pensamento (trechos curtos), animação de delegação (Orin entrega um papel e o subagente entra em cena), animação de retorno (subagente entrega o resultado ao Orin), animação de erro/falha de ferramenta, efeitos sonoros **desligados por padrão**. |
| Referência visual | Nenhuma imposta. Proposta: tiles 16×16, personagens 16×24, escala inteira (×3/×4), paleta curta derivada do Orin (roxos) + o fundo `--ink`. |

### O que a `TeamActivityView` faz hoje e não pode se perder

A Cena herda as responsabilidades da view atual: **decisão pendente** (`onResolveDecision`), **entregáveis** (`onOpenDeliverable` → prévia), **pausar a equipe** (`onPauseTeam`), **voltar ao chat** e a **lista de tarefas** (`todos`). Esses elementos ficam numa faixa de HUD sobre a cena. Os testes atuais de `TeamActivityView.test.tsx` devem ser migrados (comportamento equivalente), não simplesmente apagados.

## Fonte de dados (sem backend no MVP de visualização)

A Cena é uma **projeção pura** de `activity.events` (já disponível no `ChatPage`), da mesma forma que `turnTimelineFold.ts` e a `TeamActivityView` já fazem. Campos úteis já normalizados em `api/conversations.ts`: `agentId` (default `agent:main`), `parentAgentId`, `agentName`, `subagentType`, `path`/`filePath`, `toolPermission`, `questions`, `planApproval`. A árvore de delegação vem de `agentTree.ts` (`orderAgentTree`).

Arquitetura proposta:

```
activity.events ──► sceneReducer (puro, testável) ──► SceneState
                                                       ├─ agents: Map<agentId, {avatarSeed, station, pose, bubble, status, parentId}>
                                                       ├─ transfers: entregas em trânsito (delegação/retorno)
                                                       └─ pending: permissão/pergunta por agente
SceneState ──► SceneCanvas (Canvas 2D, requestAnimationFrame, pixel-perfect)
           └─► SceneHud (DOM: painel do personagem, cartões de aprovação/pergunta, todos, entregáveis)
```

- **Renderização:** Canvas 2D próprio, `imageSmoothingEnabled = false`, sem dependência nova (PixiJS não é necessário nesta escala). Sprites são pré-rasterizados uma vez em `OffscreenCanvas`/`ImageBitmap` a partir das matrizes.
- **Acessibilidade:** o canvas tem `role="img"` + rótulo resumido; o HUD em DOM traz o estado **em texto** (regra do design system: estado nunca só por cor/animação). Personagens clicáveis também acessíveis por teclado (lista oculta de botões / tab order).
- **`prefers-reduced-motion`:** personagens se teletransportam para a estação (sem caminhada), sem partículas; balões continuam.
- **Desempenho:** pausar o loop quando a aba/ view não está visível; suportar ≥ 12 agentes simultâneos a 60 fps num notebook comum. Agentes concluídos saem de cena após alguns segundos (ou vão para o "sofá" se o turno acabou).

## Estações do escritório (ferramenta → estação)

| Estação | Ferramentas (por nome ou `toolKind`) |
|---|---|
| Mesa com terminal | `run_command`, `powershell`, `verify_project`, shell tasks |
| Estante de arquivos | `read_file`, `list_files`, `glob_files`, `search_files`, `search_code`, `view_file`, `transcribe_pdf` |
| Bancada de escrita | `write_file`, `edit_file`, `notebook_edit` |
| Janela/monitor de navegador | `fetch_url`, ferramentas de browser, `preview_*` |
| Quadro de planos | `write_contract`, `todo_write`, `task_*`, plan mode, `exit_plan_mode` |
| Mesa de reunião (centro) | delegação, `ask_agent(s)`, `send_message`, `run_subagent` |
| Painel de extensões | MCP, plugins, skills |
| Sofá / copa | ocioso, turno concluído |

Ferramenta desconhecida → mesa genérica. O mapeamento fica numa tabela única (`sceneStations.ts`) para ser fácil de ajustar.

## Evento → animação

| Evento | Efeito na cena |
|---|---|
| `turn.started` | Orin acorda, vai à mesa de reunião; luz do escritório acende. |
| `agent.thought_delta` | Balão de pensamento ("…" + últimas ~60 chars) sobre o agente; pose pensando. |
| `tool.started` / `tool.requested` | Agente caminha até a estação; pose de trabalho específica da estação. |
| `tool.progressed` | Animação de trabalho continua (ex.: digitação, folhear). |
| `tool.finished` / `tool.completed` | Pequeno "✓" pixel; agente volta à posição de espera. |
| `tool.failed` | Faísca/fumacinha na estação, agente com pose de susto, balão curto com o erro. |
| `tool.permission_required` | Agente levanta a mão com placa "?"; cartão de aprovação no HUD ancorado ao personagem. |
| `tool.permission_resolved` | Placa some; aprovado → segue para a estação; negado → balão "ok". |
| `turn.waiting_user` (com perguntas) | Orin vira para a "câmera" segurando placa; perguntas no HUD. |
| `delegation.created` / `agent.created` | Orin vai à mesa, entrega um papel; o novo personagem entra pela porta (ou "nasce" com poof) e recebe o papel. |
| `delegation.waiting` | Pai fica esperando (pose de braços cruzados / relógio). |
| `delegation.completed` | Subagente leva o papel de volta ao pai (animação de retorno) e sai/descansa. |
| `delegation.failed` | Papel amassado, balão de erro. |
| `agent.message_sent` / `agent.message_received` | Envelope voando entre os dois personagens. |
| `turn.completed` | Todos vão para o sofá/copa (estado "descansando"). |
| `turn.failed` / `turn.cancelled` | Luz pisca, todos param e voltam ao descanso. |

Sons (opt-in, desligados por padrão, preferência persistida): passos, teclado, "ding" de conclusão, "bonk" de erro, envelope. Gerados via WebAudio (sem arquivos binários).

## Avatares sorteados

`avatarFromSeed(agentId)`: hash estável → escolhe partes (corpo, cabeça/cabelo, acessório, cor de roupa dentro da paleta). Todas as partes são matrizes em `sceneSprites.ts`. Garantir contraste com o fundo e que dois agentes simultâneos não fiquem idênticos (re-sortear com salt em colisão). O Orin é fixo.

## Ações da Cena: o que já tem API

Conferido em `frontend/src/api/*.ts` e `src/agentos/api/conversation_routes.py`:

| Ação | API hoje | Situação |
|---|---|---|
| Aprovar/negar permissão de ferramenta | `submitToolPermission` → `POST /turns/{turn_id}/tool-permission` | ✅ existe |
| Responder perguntas do agente | mesmo fluxo do `UserQuestionCard` (envio da mensagem de respostas) | ✅ existe |
| Mandar mensagem ao Orin | `sendConversationMessage` (+ fila `/queue` com turno em andamento) | ✅ existe |
| Aprovar plano / MCP / plugin | cartões existentes (`PlanApprovalCard`, `McpApprovalCard`, `PluginApprovalCard`) | ✅ reutilizar |
| Mandar mensagem **a um subagente específico** | `sendConversationAgentMessage` → `POST /conversations/{conversation_id}/agents/{agent_id}/messages` (entregue no próximo passo do subagente; se ele terminar antes de ler, vai ao Orin principal; 409 `AGENT_NOT_RUNNING` se não está ativo) | ✅ Fase 3 |
| Parar/cancelar **um subagente específico** | `stopConversationAgent` → `POST /conversations/{conversation_id}/agents/{agent_id}/stop` (para o subagente e os que ele iniciou; o pai recebe `STOPPED_BY_USER` e o turno continua) | ✅ Fase 3 |

## Fases

**Fase 1: Cena básica (só frontend)**
1. `sceneReducer.ts` puro + testes (sequências de eventos → estado; agentes paralelos intercalados; árvore de delegação).
2. Sprites em código + `avatarFromSeed` + rasterização; Orin em pixel art.
3. `SceneCanvas` (escritório, estações, caminhada, poses, balões, envelopes, erro, delegação/retorno).
4. `SceneHud`: painel do personagem ao clicar, todos, entregáveis, pausar equipe, voltar ao chat.
5. Troca da `TeamActivityView` pela Cena no `ChatPage` (Chat e Code); migração dos testes.
6. Reduced motion, pausa fora da tela, sons opt-in.

**Fase 2: Ações na Cena com API existente**
7. Permissões, perguntas, aprovação de plano/MCP/plugin ancoradas ao personagem (reaproveitando os cartões).
8. Caixa de mensagem ao Orin dentro da cena.

**Fase 3: Ações por subagente (backend + frontend)**
9. Rota `POST /v1/conversations/{id}/agents/{agent_id}/stop` ligada a `_stop_background_subagent` (autorização igual a `/cancel`), mais testes.
10. Rota para mensagem dirigida a um subagente (avaliar reuso de `send_message`/reativação de agente), mais testes.
11. Botões "Parar" e "Mensagem" no painel do personagem.

## Critérios de aceite

- Chat comum continua padrão e intocado; o toggle mostra **Chat / Cena** nos modos Chat e Code.
- Com 2+ subagentes em paralelo emitindo eventos intercalados, cada personagem reflete só os próprios eventos (testes no reducer).
- Cada `tool.*` leva o personagem certo à estação certa (teste da tabela de mapeamento).
- Delegação, retorno, mensagem entre agentes e falha têm animação visível; estado também aparece em texto no HUD.
- Sem turno ativo, todos descansando; nada de replay.
- Nenhum arquivo binário novo; sprites e sons em código.
- `prefers-reduced-motion` respeitado; loop pausado quando invisível; 12 agentes sem queda perceptível.
- Funcionalidades da antiga `TeamActivityView` preservadas e testadas.
- `npm --prefix frontend run lint`, `typecheck` e `test` verdes; na Fase 3, `uv run pytest` dos testes novos verde.
