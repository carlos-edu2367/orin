# Plano de Implementação (Blindado via Red-Team) — Fase 4: Palco Orin Friend Ao Vivo (`ref-04.png`) & Personalização do Friend (`ref-06.png`)

> [!IMPORTANT]
> **Etapa do Fluxo Obrigatório (`superpowers` -> `writing-plans` + Auditoria Red-Team Aplicada):**
> Este plano divide a **Fase 4 de 7** em tarefas com escopos de arquivos **100% isolados** e **contratos TypeScript explícitos**, prontas para execução via **`subagent-driven-development`** (ou `executing-plans`):
> - **Subagentes Implementadores independentes** escrevem os testes `[RED]` e a implementação `[GREEN]` + casos adversários (*Red-Team*).
> - **Subagente Revisor independente (sem contexto de decisões)** audita código, tipos, testes e screenshots do Playwright ao final.

---

## Visão Geral das Tarefas e Isolamento de Arquivos

| Tarefa | Escopo Funcional | Arquivos Exclusivos | Modo de Execução |
| :--- | :--- | :--- | :--- |
| **Tarefa 1** | Evolução do **Palco Orin Friend Ao Vivo** (`LiveVoiceStage.tsx` — `ref-04.png`): Header com breadcrumb `Friend / Ao vivo` + pill `Microfone ativo` + `<OrinMascot />`, palco central com `<OrinMascot variant="soft3d" />` reativo + `<LiveOrb />`, headline `"Estou ouvindo, {userNickname}."`, waveform horizontal lilás de 31 barras, dock inferior de 4 controles (`Silenciar microfone`, `Legendas ao vivo`, seletor `Alto-falantes` e `Encerrar`) + `<LevelMeter />` preservado e coluna lateral direita **`"Nesta conversa"`** (diálogo recente sanitizado + card de tarefa ativa com checklist e botão `"↗ Abrir no Code"`). | `frontend/src/features/conversations/LiveVoiceStage.tsx`<br>`frontend/src/features/conversations/__tests__/LiveVoiceStageRef04.test.tsx` (novo) | **Paralelo** (com Tarefa 2) |
| **Tarefa 2** | Evolução da visão **"Seu Friend" em 3 Abas** (`FriendSoulDrawer.tsx` — `ref-06.png`): Header `<h1>Seu Friend</h1>` + botão `"🎙️ Conversar por voz"` + 3 abas (`✨ Aparência`, `🎙️ Voz`, `👤 Personalidade`), coluna esquerda com card de preview do `<OrinMascot size={160} variant="soft3d" />`, único `<h2>{displayName}</h2>`, subtítulo `"Seu parceiro de trabalho."` e botão `"▷ Ouvir uma prévia"` (com cleanup ao fechar `isOpen=false`), coluna direita com campos de identidade com ícones, **5 sliders bipolares visíveis** (`Calor humano`, `Bom humor`, `Formalidade`, `Diretividade`, `Energia`), `<details data-testid="friend-more-settings">` nativo e rodapé com `"↺ Restaurar padrão"` + `"Salvar alterações"`. | `frontend/src/features/conversations/FriendSoulDrawer.tsx`<br>`frontend/src/features/conversations/__tests__/FriendSoulDrawerRef06.test.tsx` (novo) | **Paralelo** (com Tarefa 1) |
| **Tarefa 3** | Integração em `ChatPage.tsx` e estilização completa em `live.css` (`ref-04.png` + `ref-06.png`): passagem de `transcriptItems`, `todos`, `delegatedCodeSession`, `onOpenCodeSession` e `userNickname` para `LiveVoiceStage`, `onStartVoice` para `FriendSoulDrawer`, e layout CSS integrado à sidebar esquerda (`.chat .live-stage` e `.chat .friend-drawer` com `left: 264px` no desktop). | `frontend/src/features/conversations/ChatPage.tsx`<br>`frontend/src/styles/live.css`<br>`frontend/src/features/conversations/__tests__/ChatPageFriendPhase4.test.tsx` (novo) | **Sequencial** (após Tarefas 1 e 2) |
| **Tarefa 4** | Verificação Visual no Playwright + E2E (`orin-friend-live.spec.ts`) + Auditoria por Subagente Revisor Independente (sem contexto de decisões) + Commit & Push na branch `refactor/interface`. | Toda a Fase 4 | **Gate Final** |

---

## Contratos TypeScript Compartilhados entre as Tarefas 1, 2 e 3

Para garantir zero conflito de compilação durante a execução paralela das Tarefas 1 e 2 e a integração na Tarefa 3:

1. **Em `frontend/src/features/conversations/LiveVoiceStage.tsx` (Tarefa 1):**
   ```ts
   import type { DelegatedCodeSessionInfo, LiveVoicePhase, TodoItem } from './activityTypes'

   export type LiveVoiceTranscriptItem = {
     id: string
     role: 'user' | 'assistant'
     content: string
     timestamp?: string
   }

   // Novas props 100% opcionais adicionadas a LiveVoiceStageProps:
   export type LiveVoiceStageProps = {
     // ...props existentes inalteradas...
     transcriptItems?: ReadonlyArray<LiveVoiceTranscriptItem>
     todos?: ReadonlyArray<TodoItem>
     delegatedCodeSession?: DelegatedCodeSessionInfo | null
     onOpenCodeSession?: (codeConversationId?: string) => void
   }
   ```
2. **Em `frontend/src/features/conversations/FriendSoulDrawer.tsx` (Tarefa 2):**
   ```ts
   // Nova prop 100% opcional adicionada a FriendSoulDrawerProps:
   export type FriendSoulDrawerProps = {
     // ...props existentes inalteradas...
     onStartVoice?: () => void
   }
   ```

---

## Tarefa 1: Palco `"Friend / Ao vivo"` (`LiveVoiceStage.tsx` — `ref-04.png`)

### Passo 1.1 — Escrever o teste falhando (`[RED]`)
Criar [`frontend/src/features/conversations/__tests__/LiveVoiceStageRef04.test.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/__tests__/LiveVoiceStageRef04.test.tsx) verificando:
1. **Header e Estado Personalizado (`stateLabel`):**
   - `stateLabel('listening')` continua retornando `'Estou ouvindo'` quando `userNickname` não é informado/vazio, e `stateLabel('listening', 'Orin', null, 'Carlos')` retorna `'Estou ouvindo, Carlos.'`.
   - Renderiza `data-testid="live-voice-breadcrumb"` com `Friend` e o nome do companion (`live-voice-companion-name`), além do pill `data-testid="live-voice-mic-status-pill"` exibindo `"Microfone ativo"` (e `"Microfone silenciado"` quando silenciado).
   - Mantém `live-voice-active-profile-pill`, `live-voice-open-settings-button` e `live-voice-close-button` visíveis e clicáveis.
2. **Palco Central, Mascote `soft3d`, Waveform Lilás (`live-voice-waveform`) e Legendas:**
   - Renderiza `data-testid="live-voice-waveform"` com `31` barras verticais (`.live-waveform__bar`) e `<OrinMascot variant="soft3d" />` + `<LiveOrb />` dentro de `data-testid="live-voice-orb"`.
   - Exibe `"Estou ouvindo, Carlos."` em `data-testid="live-voice-state-label"` quando `phaseOverride="listening"` e `userNickname="Carlos"`.
   - Em `data-testid="live-voice-captions"`, mantém `<span className="visually-hidden">{greeting} </span>` antes de `"Pode falar naturalmente e me interromper."` para preservar a asserção `toContain('Oi, Carlos!')` de `OrinFriendAndLiveMode.test.tsx`.
3. **Dock Inferior de 4 Controles + `<LevelMeter />`:**
   - Renderiza `data-testid="live-voice-mute-button"` com rótulo `"Silenciar microfone"`, `data-testid="live-voice-captions-toggle"` com rótulo `"Legendas ao vivo"` (iniciando com `aria-pressed="true"` e alternando ao clicar), `data-testid="live-voice-speaker-picker"` (exibindo `"Alto-falantes (Padrão)"` ou dispositivos `audiooutput` enumerados) e `data-testid="live-voice-end-call-button"` com rótulo `"Encerrar"` que aciona `onClose`.
   - Preserva `<LevelMeter />` (`data-testid="live-mic-meter"`), `live-voice-barge-in-button` e `live-voice-ptt-button` montados e acessíveis no dock.
4. **Coluna Lateral Direita `"Nesta conversa"` (`live-voice-side-panel`):**
   - Renderiza `data-testid="live-voice-transcript-list"` com os turnos passados via `transcriptItems` (ou turnos demonstrativos contextuais quando vazio) exibindo autor (`Você` / `Orin`), horário e texto sanitizado (sem tags `<speak>` ou blocos de código).
   - Renderiza `data-testid="live-voice-task-card"` com título da tarefa (`delegatedCodeSession?.goal` ou `"Página da Auryly"`), contagem `"X de Y etapas concluídas"`, barra de progresso protegida contra divisão por zero (`total === 0`), checklist de `todos` (`✓` concluídas e `○` pendentes) e botão `"↗ Abrir no Code"` (`data-testid="live-voice-open-in-code"`) que chama `onOpenCodeSession`.
5. **Casos Adversários (*Red-Team*):**
   - Quando `navigator.mediaDevices` é `undefined` ou `enumerateDevices()` rejeita com `NotAllowedError`, `live-voice-speaker-picker` mantém o fallback `"Alto-falantes (Padrão)"` sem quebrar a renderização.
   - Quando `userNickname` contém caracteres proibidos (`< > { } \``), pontuação final duplicada ou mais de 40 caracteres, `stateLabel` higieniza e trunca com segurança.

### Passo 1.2 — Implementar o código mínimo (`[GREEN]`)
- Atualizar [`frontend/src/features/conversations/LiveVoiceStage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/LiveVoiceStage.tsx) seguindo todos os contratos acima.
- Rodar: `npm --prefix frontend test -- --run src/features/conversations/__tests__/LiveVoiceStageRef04.test.tsx tests/unit/OrinFriendAndLiveMode.test.tsx`

---

## Tarefa 2: Visão `"Seu Friend"` em 3 Abas (`FriendSoulDrawer.tsx` — `ref-06.png`)

### Passo 2.1 — Escrever o teste falhando (`[RED]`)
Criar [`frontend/src/features/conversations/__tests__/FriendSoulDrawerRef06.test.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/__tests__/FriendSoulDrawerRef06.test.tsx) verificando:
1. **Cabeçalho (`<h1>`) e Abas (`friend-personalize-tabs`):**
   - Exibe título `<h1>Seu Friend</h1>`, subtítulo `"Personalize como o Orin conversa e te acompanha."` e botão `"Conversar por voz"` (`data-testid="friend-start-voice-button"`) que dispara `onStartVoice`.
   - Renderiza as 3 abas `data-testid="friend-tab-appearance"` (`Aparência`), `data-testid="friend-tab-voice"` (`Voz`) e `data-testid="friend-tab-personality"` (`Personalidade`, selecionada por padrão com `aria-selected="true"`).
2. **Card Esquerdo de Identidade e Prévia (`friend-preview-card`) e Regra do Único `<h2>`:**
   - Exibe `<OrinMascot size={160} variant="soft3d" />` (mais `<LiveOrb />`), o **único `<h2>` do componente** (`getByRole('heading', { level: 2 })` com texto `{displayName}`), o subtítulo `"Seu parceiro de trabalho."` e o botão `"▷ Ouvir uma prévia"` (`data-testid="friend-listen-preview-button"`).
   - Teste adversário (*Red-Team*):
     - Clicar em `"▷ Ouvir uma prévia"` coloca imediatamente o botão em estado ocupado (`disabled` e `data-busy="true"`), impedindo cliques concorrentes mesmo em ambientes sem `window.speechSynthesis` (JSDOM).
     - Fechar a gaveta (`rerender` com `isOpen={false}`) ou clicar em `"Conversar por voz"` interrompe imediatamente a prévia e limpa timers/síntese ativos.
3. **Campos de Identidade e 5 Sliders Bipolares Visíveis (`ref-06.png`):**
   - Exibe `Nome do Friend` (`Como você quer chamar seu Friend?`) e `Como te chamar` (`Qual nome o Orin deve usar para se referir a você?`).
   - Exibe diretamente (fora do `<details>`) os 5 sliders bipolares principais com seus pólos exatos:
     - `Calor humano` (`friend-trait-slider-warmth`): `Mais reservado` ↔ `Mais caloroso`
     - `Bom humor` (`friend-trait-slider-humor`): `Mais sério` ↔ `Mais bem-humorado`
     - `Formalidade` (`friend-trait-slider-formality`): `Mais casual` ↔ `Mais formal`
     - `Diretividade` (`friend-trait-slider-candor`): `Mais exploratório` ↔ `Mais direto`
     - `Energia` (`friend-trait-slider-energy`): `Mais tranquilo` ↔ `Mais energético`
4. **Acordeão `"Mais ajustes"` (`<details data-testid="friend-more-settings">`) e `"↺ Restaurar padrão"` (`friend-restore-defaults-button`):**
   - Renderiza `<details data-testid="friend-more-settings">` nativo mantendo **sempre montados no DOM** (sem duplicar `data-testid` ao trocar de aba) todos os controles avançados (`friend-bond-card`, `friend-bond-stage`, `friend-affinity-score`, `friend-preset-*`, `friend-theme-*`, `friend-trait-slider-sarcasm`, `friend-trait-slider-verbosity`, `friend-mood-input`, `friend-style-notes-textarea`, `friend-memory-item-*`, `delete-friend-memory-*`, `friend-soul-path`, `sync-friend-soul-button`).
   - Clicar em `data-testid="friend-restore-defaults-button"` (`"↺ Restaurar padrão"`) restaura os traços padrão (`DEFAULT_TRAITS`), garante nome válido caso o campo estivesse vazio e habilita `"Salvar alterações"`.

### Passo 2.2 — Implementar o código mínimo (`[GREEN]`)
- Atualizar [`frontend/src/features/conversations/FriendSoulDrawer.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/FriendSoulDrawer.tsx) preservando 100% da compatibilidade com `frontend/tests/unit/OrinFriendAndLiveMode.test.tsx`.
- Rodar: `npm --prefix frontend test -- --run src/features/conversations/__tests__/FriendSoulDrawerRef06.test.tsx tests/unit/OrinFriendAndLiveMode.test.tsx`

---

## Tarefa 3: Integração em `ChatPage.tsx` e Estilização em `live.css`

### Passo 3.1 — Escrever o teste falhando (`[RED]`)
Criar [`frontend/src/features/conversations/__tests__/ChatPageFriendPhase4.test.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/__tests__/ChatPageFriendPhase4.test.tsx) verificando:
1. Em `ChatPage.tsx`, ao abrir o modo de voz (`chat-voice-button` ou `composer-live-voice-toggle`), `LiveVoiceStage` recebe as mensagens da conversa atual na coluna `"Nesta conversa"` (`live-voice-transcript-list`) e os itens de tarefa/delegação (`live-voice-task-card`), além do `userNickname` carregado em `friendPersona`.
2. Na visão `FriendSoulDrawer`, clicar em `"Conversar por voz"` (`friend-start-voice-button`) fecha a personalização (`friend-soul-drawer`) e abre o `LiveVoiceStage` (`live-voice-stage`).

### Passo 3.2 — Implementar o código mínimo (`[GREEN]`)
- Atualizar [`frontend/src/features/conversations/ChatPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx) e [`frontend/src/styles/live.css`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/styles/live.css):
  - Em `live.css`, posicionar `.chat .live-stage` e `.chat .friend-drawer` no desktop (`@media (min-width: 901px)`) com `left: 264px` (e `left: 64px` quando `.chat:has([data-rail-collapsed="true"])`), mantendo a sidebar `.workspace-navigation` visível à esquerda exatamente como em `ref-04.png` e `ref-06.png`.
  - Estilizar o grid de 2 colunas do palco de voz (`Palco Central` + coluna direita `"Nesta conversa"` de `360px`), o waveform lilás de 31 barras, o dock inferior de 4 controles com rótulos abaixo, e o layout de 2 colunas da visão `"Seu Friend"` com 3 abas, card esquerdo do mascote e 5 sliders bipolares horizontais.
- Rodar: `npm --prefix frontend test -- --run src/features/conversations/__tests__/ChatPageFriendPhase4.test.tsx src/features/conversations/__tests__/LiveVoiceStageRef04.test.tsx src/features/conversations/__tests__/FriendSoulDrawerRef06.test.tsx tests/unit/OrinFriendAndLiveMode.test.tsx`

---

## Tarefa 4: Verificação no Playwright + E2E + Auditoria por Subagente Revisor Independente

1. Rodar build (`npm --prefix frontend run build`), testes unitários (`OrinFriendAndLiveMode.test.tsx` + os 3 testes da Fase 4) e E2E (`npx --prefix frontend playwright test tests/e2e/orin-friend-live.spec.ts`).
2. Verificar visualmente no **Playwright** em `1440x900` o palco `"Friend / Ao vivo"` (`ref-04.png`) e a tela `"Seu Friend"` (`ref-06.png`) com a sidebar esquerda visível, capturando screenshots de evidência.
3. Disparar um **Subagente Revisor Independente (sem contexto de decisões)** para auditar toda a Fase 4 contra a Spec e as imagens `ref-04.png` e `ref-06.png`.
4. Após veredito `APROVADO`, fazer commit e push na branch `refactor/interface`.
