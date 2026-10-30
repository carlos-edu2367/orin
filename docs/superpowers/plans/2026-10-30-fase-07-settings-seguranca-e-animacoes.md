# Plano de Implementação (Pós-Auditoria Red-Team) — Fase 7: Modelos & Provedores (`ref-14.png`), Segurança & Controle (`ref-15.png`), Menu de Configurações e Coreografia Interativa Premium (`orin-home-interativa.html`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruir as superfícies de **Modelos e provedores (`ref-14.png`)**, **Segurança e controle (`ref-15.png`)** e o menu de **Configurações** em 6 categorias principais, além de implementar a coreografia de **animações e micro-interações premium** baseada em `c:\Users\User\Downloads\orin-home-interativa.html` (mascote vivo com flutuação, piscar de olhos e explosão de 9 partículas lavanda, física de mola em botões/pílulas/cartões, entrada de painéis/mensagens e suporte a `Movimento reduzido`), mantendo 100% de compatibilidade com todos os testes unitários e E2E existentes.

---

## Correções Incorporadas da Auditoria Red-Team (`0fc309ca-3ac5-4af6-acef-ed2369bc459c`)

1. **[CRÍTICO] Zero Colisão de Strict Mode em `ProvidersSection.tsx` (`getByRole('link', { name: /OpenAI/ })`):**
   - Em `settings-a11y.spec.ts` e `visual/settings.spec.ts`, o Playwright executa `page.getByRole('link', { name: /OpenAI/ })` em strict mode e valida o ciclo de foco `Enter -> Drawer -> Escape -> card.toBeFocused()`.
   - Portanto, no Card 3 (`Provedores conectados` de `.providers-ref14`), a ação **`Gerenciar ›`** será um **`<button type="button" onClick={() => navigate(\`/settings/providers/\${provider}\`)}>`** (role `button`, não `link`), enquanto `<ProviderGrid states={states} />` permanece visível logo abaixo como a **única superfície de `role="link"`** para cada provedor (`OpenAI`, `Anthropic`, etc.).
2. **[CRÍTICO] Ordem de `useEffect` e Único `role="alert"` em `PermissionsSection.tsx` (`w7SettingsEditors.test.tsx`):**
   - `w7SettingsEditors.test.tsx` (linha 71) exige que `calls[0].url` contenha `conversation_id=chat_1` (`/v1/settings/effective`) e na linha 92 exige um único `role="alert"` quando o servidor retorna 404.
   - Portanto, `const settings = useLoaded(load, 'ler as permissões')` permanece como o **primeiro** hook com efeito em `PermissionsSection.tsx`. As leituras de `getCodeModeSettings` e `getTrustedFolders` vêm **depois** de `useLoaded` e fazem fallback silencioso em caso de erro (sem renderizar `role="alert"` extra). O bloco `#security-advanced-rules` permanece visível no DOM por padrão.
3. **[CRÍTICO] Preservação do `<h1>` Nativo de `SettingsSection`:**
   - `w7-settings.spec.ts` e `SettingsShell.test.tsx` buscam `getByRole('heading', { level: 1, name: 'Permissões' })` e `getByRole('heading', { level: 1, name: 'Providers' })`. Mantemos o `<h1>` nativo de `<SettingsSection>` intacto e renderizamos os títulos editoriais da Fase 7 (`Modelos e provedores` e `Segurança e controle`) como **`<h2>`**.
4. **[ALTO] Rota `/settings/voice` Integrada ao `SettingsShell` (Sem Modal Fullscreen nem Auto-Mic):**
   - Em `/settings/voice`, renderizamos uma seção integrada `<VoiceSettingsSection />` dentro de `<SettingsRoute><SettingsSection eyebrow="VOZ / ÁUDIO" title="Voz" lede="Configure microfone, reconhecimento de fala e a voz do Orin.">...</SettingsSection></SettingsRoute>`, lendo/gravando `loadLiveVoicePrefs` / `saveLiveVoicePrefs` sem abrir `role="dialog"` fullscreen e sem disparar `MicCapture.open()` automaticamente no mount.
5. **[ALTO] Nomes Reais em `api/settings.ts` e Zero Colisão com Botão `'Adicionar'`:**
   - Usamos `getTrustedFolders` e `revokeTrustedFolder` de `../../api/settings` + persistência complementar em `localStorage` (`orin.security.authorizedFolders`, com fallback inicial `[{ path: 'C:/Projetos/auryly', scope: 'folder_only' }]`).
   - O botão de confirmação para adicionar pasta chama-se **`"Autorizar pasta"`** (e o botão de abrir chama-se **`"+ Adicionar pasta"`**), nunca `"Adicionar"` isolado (que colidiria com `getByRole('button', { name: 'Adicionar' })` de `w7SettingsEditors.test.tsx`). O badge `+ Adicionar` em `src/app.ts` é um `<span>`.
6. **[ALTO] Preservação de Substrings CSS (`settingsStyles.test.ts` e `OrinMascotAndBrand.test.tsx`):**
   - Preservamos todas as regras CSS exigidas por `settingsStyles.test.ts` e `OrinMascotAndBrand.test.tsx` (incluindo `@media (prefers-reduced-motion: reduce)` com `.orin-mascot.is-animated`) e mantemos `size={88} variant="soft3d"` no mascote hero de `Home.tsx`.
7. **[ALTO] Zero Overflow Horizontal em `375px` (`settings-a11y.spec.ts`):**
   - Em `@media (max-width: 900px)`, `.providers-ref14__grid` e `.security-ref15__grid` colapsam para `grid-template-columns: 1fr` e `.settings-nav__primary` / `.settings-nav__advanced` usam `display: flex; align-items: center; gap: 6px;` dentro do container rolável `.settings-nav`.
8. **[MÉDIO] Limpeza de Timers (`celebrationTimerRef`) e `submit()` Não-Bloqueante em `Home.tsx`:**
   - `triggerCelebration()` é síncrono (acionado ao clicar em chips rápidos, clicar no mascote ou iniciar `submit()`), sem atrasar `createConversation`/`navigate`, e limpa `celebrationTimerRef` no `unmount`.
9. **[MÉDIO] Sanitização Contra XSS / Path Injection e Fallback de `useTheme()`:**
   - `useTheme()` fora do `ThemeProvider` inclui `reduceMotion: false` e `setReduceMotion: () => {}`.
   - Caminhos em `Pastas autorizadas` são sanitizados (remoção de caracteres de controle, `trim()`, rejeição de `< > " | ? *`, limite de `260` chars, normalização `\` -> `/` e bloqueio de duplicatas).
10. **[MÉDIO] Acessibilidade e Contraste WCAG AA em Dark + Light Mode:**
    - Todos os `<select>` e botões de ícone possuem `<label htmlFor>` ou `aria-label` explícitos, e `[data-theme="light"]` define cores de alto contraste (`>= 4.5:1`) para badges e botões tracejados.

---

## Task 1: Menu de Configurações em 6 Categorias, `Movimento reduzido` e Rotas (`/settings/voice`, `/settings/security`)

**Files:**
- Create: `frontend/tests/unit/SettingsPhase7NavAndMotion.test.tsx`
- Create: `frontend/src/features/settings/VoiceSettingsSection.tsx`
- Modify: `frontend/src/app/ThemeContext.tsx`
- Modify: `frontend/src/features/settings/SettingsNav.tsx`
- Modify: `frontend/src/features/settings/PreferencesModal.tsx`
- Modify: `frontend/src/features/settings/RuntimeSettingsPage.tsx`
- Modify: `frontend/src/app/routes.tsx`

- [ ] **Step 1 (RED): Criar `frontend/tests/unit/SettingsPhase7NavAndMotion.test.tsx`**
  - Testar as 6 categorias principais em `SettingsNav` (`data-testid="settings-primary-categories"`), `reduceMotion` em `ThemeContext` (incluindo fallback fora do provider e resiliência a erros de `localStorage`), toggle em `PreferencesModal` e `RuntimeSettingsPage`, e rota `/settings/voice`.
- [ ] **Step 2 (GREEN): Implementar `ThemeContext.tsx`, `SettingsNav.tsx`, `VoiceSettingsSection.tsx`, `PreferencesModal.tsx`, `RuntimeSettingsPage.tsx` e `routes.tsx`**
- [ ] **Step 3 (VERIFY): Rodar suíte de testes unitários de Settings e ThemeContext**

---

## Task 2: Superfície `Modelos e provedores` (`ref-14.png` — `ProvidersSection.tsx`)

**Files:**
- Create: `frontend/tests/unit/ProvidersPhase7Ref14.test.tsx`
- Modify: `frontend/src/features/providers/ProvidersSection.tsx`

- [ ] **Step 1 (RED): Criar `frontend/tests/unit/ProvidersPhase7Ref14.test.tsx`**
  - Testar cabeçalho `<h2 data-testid="providers-ref14-heading">Modelos e provedores</h2>`, Card 1 (`Modelo principal`), Card 2 (`Se houver uma falha`), Card 3 (`Provedores conectados` com `<button>` `Gerenciar ›` e `+ Conectar provedor`), Coluna Direita (`Uso e custos` + `Salvar alterações`), persistência em `localStorage` e preservação de `<ProviderGrid>` para `settings-a11y.spec.ts`.
- [ ] **Step 2 (GREEN): Implementar `ProvidersSection.tsx` (`ref-14.png`)**
- [ ] **Step 3 (VERIFY): Rodar Vitest (`ProvidersPhase7Ref14.test.tsx`, `ProviderSettingsPage.test.tsx`, `ProviderGrid.test.tsx`)**

---

## Task 3: Superfície `Segurança e controle` (`ref-15.png` — `PermissionsSection.tsx`)

**Files:**
- Create: `frontend/tests/unit/SecurityPhase7Ref15.test.tsx`
- Modify: `frontend/src/features/codeSettings/PermissionsSection.tsx`

- [ ] **Step 1 (RED): Criar `frontend/tests/unit/SecurityPhase7Ref15.test.tsx`**
  - Testar `<h2 data-testid="security-ref15-heading">Segurança e controle</h2>`, Card 1 (`Permissão padrão` com `Perguntar antes`, `Só planejar`, `Aceitar edições`), Card 2 (`Pastas autorizadas` com sanitização contra XSS/caracteres inválidos, seletor de escopo e botão `+ Adicionar pasta` / `Autorizar pasta`), Card 3 (`Ações em serviços externos`), Coluna Direita (`Aprovar alteração?` com diff expansível e botões `Recusar` / `Aprovar esta vez`) e rodapé (`Revisar atividades e permissões ›`, `Cancelar`, `Salvar alterações`).
- [ ] **Step 2 (GREEN): Implementar `PermissionsSection.tsx` (`ref-15.png`) preservando `useLoaded` como primeiro efeito e `#security-advanced-rules` visível**
- [ ] **Step 3 (VERIFY): Rodar Vitest (`SecurityPhase7Ref15.test.tsx`, `w7SettingsEditors.test.tsx`, `w7ReviewR5.test.tsx`)**

---

## Task 4: Coreografia Interativa Premium (`orin-home-interativa.html` — `OrinMascot.tsx`, `Home.tsx`, `agentos.css`) e Verificação E2E Playwright

**Files:**
- Modify: `frontend/src/components/OrinMascot.tsx`
- Modify: `frontend/src/app/Home.tsx`
- Modify: `frontend/src/styles/agentos.css`
- Create: `frontend/tests/e2e/phase7-settings-and-motion.spec.ts`

- [ ] **Step 1 (RED): Adicionar testes de partículas, celebração e limpeza de timer em `SettingsPhase7NavAndMotion.test.tsx`**
- [ ] **Step 2 (GREEN): Implementar `OrinMascot.tsx` (9 partículas `.orin-mascot__particle` + `celebrating`), `Home.tsx` (`triggerCelebration` com `celebrationTimerRef`) e `agentos.css` (`orin-floaty`, `orin-blink`, `orin-hero-celebrate`, `orin-little-celebrate`, `orin-particle-pop`, `orin-message-enter`, `orin-panel-in`, `.providers-ref14`, `.security-ref15`, mobile `@media (max-width: 900px)` e contraste `[data-theme="light"]`)**
- [ ] **Step 3 (VERIFY): Executar Vitest + Playwright E2E (`phase7-settings-and-motion.spec.ts` e `settings-a11y.spec.ts`) + Code Review Independente**
