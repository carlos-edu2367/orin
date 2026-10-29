# Plano de Implementação — Fase 1: Design System, Mascote `OrinMascot` e Shell Global Unificado

**Goal:** Implementar a fundação visual e estrutural da nova interface do Orin (Tema Padrão das referências `#101014`/`#141419`/`#B4A4EF` + catálogo de temas personalizados, componente vetorial `OrinMascot` em 3 variantes + `Brand` minúsculo, e unificação do `WorkspaceShell` + `WorkspaceNavigation` + `ProfileMenu`) usando TDD (`[RED]` -> `[GREEN]` -> `[REFACTOR]`) e validação visual no navegador com Playwright.

**Architecture:**
1. **`ThemeContext.tsx` & `theme.css`**: Atualiza o tema `dark` + `violet` padrão para as cores exatas das referências (`#101014` sidebar, `#141419` canvas, `#1A1A21` cards, `#1E1E26` inputs, `#B4A4EF` acento lavanda com `--orin-accent-ink: #181523`), adiciona os novos modos personalizados (`midnight`, `forest`) e o novo acento (`rose`), e expõe todos no `PreferencesModal.tsx`.
2. **`OrinMascot.tsx` & `Brand.tsx`**: Cria o componente SVG parametrizado do mascote Orin (`flat`, `soft3d`, `waving` e `statusDot`) sincronizado com `--orin-accent` e atualiza o `Brand.tsx` para usar o mascote flat + wordmark `"orin"` em minúsculas.
3. **`WorkspaceShell.tsx`, `WorkspaceNavigation.tsx`, `ProfileMenu.tsx` & `agentos.css`**: Remove a moldura "inset" arredondada do `.workspace-shell__canvas`, adiciona as ações globais padrão na topbar (`Conversar por voz` + `OrinMascot` online), reestrutura a sidebar (`+ Nova conversa`, menu `Início` / `Friend` / `Ações agendadas`, abas `Conversas` / `Projetos`, rodapé `Habilidades` / `Conexões` / `Configurações`) e torna o `ProfileMenu` visível também em modo local.

---

## Task 1: Sistema de Temas — Padrão das Referências + Temas Personalizados (TDD)

**Files:**
- Modify: `frontend/src/app/__tests__/ThemeContext.test.tsx`
- Modify: `frontend/src/features/settings/__tests__/PreferencesAndHubs.test.tsx`
- Modify: `frontend/src/app/ThemeContext.tsx`
- Modify: `frontend/src/styles/theme.css`
- Modify: `frontend/src/features/settings/PreferencesModal.tsx`

### Steps:
1. **[RED]** Atualizar `frontend/src/app/__tests__/ThemeContext.test.tsx` e `PreferencesAndHubs.test.tsx` para testar:
   - Os novos modos `midnight` e `forest` em `setMode` e no grid de `PreferencesModal` (`theme-mode-midnight`, `theme-mode-forest`).
   - O novo acento `rose` em `setAccent` e na lista de swatches (`theme-accent-rose`).
   - A presença dos novos tokens canônicos (`#101014`, `#141419`, `#B4A4EF`, `--orin-accent-ink`) em `theme.css`.
2. **Run test (expect FAIL):**
   `npm --prefix frontend test -- src/app/__tests__/ThemeContext.test.tsx src/features/settings/__tests__/PreferencesAndHubs.test.tsx`
3. **[GREEN]** Atualizar `ThemeContext.tsx`, `theme.css` e `PreferencesModal.tsx` com os novos modos (`dark`, `carbon`, `midnight`, `forest`, `light`, `system`), acentos (`violet`, `sapphire`, `emerald`, `amber`, `rose`, `mono`) e tokens das referências.
4. **Run test (expect PASS):**
   `npm --prefix frontend test -- src/app/__tests__/ThemeContext.test.tsx src/features/settings/__tests__/PreferencesAndHubs.test.tsx`

---

## Task 2: Componente `OrinMascot.tsx` e Refatoração de `Brand.tsx` (TDD)

**Files:**
- Create: `frontend/src/components/__tests__/OrinMascotAndBrand.test.tsx`
- Create: `frontend/src/components/OrinMascot.tsx`
- Modify: `frontend/src/components/Brand.tsx`
- Modify: `frontend/src/config/brand.ts`
- Modify: `frontend/src/styles/agentos.css`

### Steps:
1. **[RED]** Criar `frontend/src/components/__tests__/OrinMascotAndBrand.test.tsx` testando:
   - Renderização do `OrinMascot` nas variantes `flat`, `soft3d` e `waving`.
   - Renderização do indicador `statusDot="online"` e `statusDot="busy"`.
   - Sincronia com `color` customizado ou fallback para `var(--orin-accent, #B4A4EF)`.
   - `Brand` renderizando o `OrinMascot` e o wordmark `"orin"` mantendo `aria-label="Orin, início"`.
2. **Run test (expect FAIL):**
   `npm --prefix frontend test -- src/components/__tests__/OrinMascotAndBrand.test.tsx`
3. **[GREEN]** Implementar `OrinMascot.tsx` (baseado na geometria de `orin.svg`), atualizar `Brand.tsx` e estilizar `.brand` / `.orin-mascot` em `agentos.css`.
4. **Run test (expect PASS):**
   `npm --prefix frontend test -- src/components/__tests__/OrinMascotAndBrand.test.tsx`

---

## Task 3: Reestruturação da Sidebar (`WorkspaceNavigation.tsx`), `ProfileMenu.tsx` e `WorkspaceShell.tsx` (TDD)

**Files:**
- Modify: `frontend/src/app/__tests__/WorkspaceShell.test.tsx`
- Modify: `frontend/src/app/__tests__/UiPolishFixesRound2.test.tsx`
- Modify: `frontend/src/features/auth/ProfileMenu.tsx`
- Modify: `frontend/src/features/projects/WorkspaceNavigation.tsx`
- Modify: `frontend/src/app/WorkspaceShell.tsx`
- Modify: `frontend/src/styles/agentos.css`

### Steps:
1. **[RED]** Atualizar `WorkspaceShell.test.tsx` para exigir:
   - CTA primário `+ Nova conversa` (`data-testid="sidebar-new-chat"`).
   - Menu principal superior com `Início`, `Friend` (`data-testid="sidebar-orin-friend-button"`) e `Ações agendadas` (`/schedules`).
   - Abas `Conversas` e `Projetos` com sublinhado ativo e tempo relativo nos itens de conversa.
   - Rodapé fixo com `Habilidades` (`/library/skills`), `Conexões` (`/integrations/providers`) e `Configurações` (`/settings/general`), além do `ProfileMenu` visível mesmo em modo local (exibindo inicial + nome do usuário + acesso rápido a tema/preferências).
   - Topbar do `WorkspaceShell` com suporte ao botão `"Conversar por voz"` e avatar `OrinMascot` online.
2. **Run test (expect FAIL):**
   `npm --prefix frontend test -- src/app/__tests__/WorkspaceShell.test.tsx`
3. **[GREEN]** Implementar as mudanças em `ProfileMenu.tsx`, `WorkspaceNavigation.tsx`, `WorkspaceShell.tsx` e `agentos.css` (removendo a margem/borda "inset" de `.workspace-shell__canvas` e aplicando a estética full-bleed das referências).
4. **Run test (expect PASS):**
   `npm --prefix frontend test -- src/app/__tests__/WorkspaceShell.test.tsx src/app/__tests__/UiPolishFixes.test.tsx src/app/__tests__/UiPolishFixesRound2.test.tsx src/features/projects/__tests__/ProjectSessionManagement.test.tsx`

---

## Task 4: Suíte Completa de Testes, Build TypeScript e Verificação Visual no Playwright

**Files:**
- Verify: Todos os testes em `frontend/src/**/__tests__/*.test.tsx`

### Steps:
1. Rodar toda a suíte de testes unitários e de integração do frontend (`npm --prefix frontend test`) e checagem de tipos (`npm --prefix frontend run build` / `tsc --noEmit`).
2. Subir o frontend local e inspecionar visualmente no navegador usando **Playwright** (`browser_navigate`, `browser_take_screenshot`):
   - Tema padrão (*Orin Escuro* `#101014`/`#141419` + *Lavanda* `#B4A4EF`).
   - Troca de temas personalizados no `PreferencesModal` (`Noite Profunda`, `Floresta Noturna`, `Carbon OLED`, `Papel Quente` + acentos).
   - Nova Sidebar (`Brand` com mascote + `orin`, `+ Nova conversa`, `Início`, `Friend ›`, `Ações agendadas`, abas `Conversas` | `Projetos`, rodapé `Habilidades`, `Conexões`, `Configurações` e barra de perfil).
