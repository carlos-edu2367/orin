# Orin Clean/Premium UI Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refatorar a interface do Orin para um padrão clean/premium anti-slop ("Inset Studio Shell"), introduzindo um motor de temas tridimensional (`ThemeProvider`), reduzindo a sobrecarga de informações na Home/Chat/Code via Progressive Disclosure (`ModelPicker` no topo do palco + `SessionContextPopover` no Composer) e desacoplando a antiga tela de Settings em um `PreferencesModal` rápido e três Hubs na Sidebar (`/library`, `/integrations`, `/schedules`).

**Architecture:**
1. **Motor de Temas & Design System (`ThemeContext.tsx` + `theme.css`):** Gerencia `mode` (`dark`, `carbon`, `light`, `system`), `accent` (`violet`, `sapphire`, `emerald`, `amber`, `mono`) e `ambient` (`on`, `off`), aplicando-os sincronamente em `document.documentElement` (`data-theme`, `data-accent`, `data-ambient`) sem FOUC e removendo cores hardcoded dos componentes.
2. **App Shell "Inset Canvas" (`WorkspaceShell.tsx` + `WorkspaceNavigation.tsx` + `ModelPicker.tsx`):** Envolve as views principais em um grid persistente com Sidebar de altura total à esquerda (com suporte a colapso em Icon Rail `64px`, links para os Hubs e rodapé com `ProfileMenu` + `ThemeQuickToggle` + botão `Preferências`) e um palco enquadrado (`.workspace-shell__canvas`) com cabeçalho limpo onde vive o `ModelPicker` em formato Pill Dropdown Unificado.
3. **Progressive Disclosure (`SessionContextPopover.tsx` + `Composer.tsx` + `Home.tsx` + `ChatPage.tsx`) & Settings Desacoplada (`PreferencesModal.tsx` + `LibraryHubPage.tsx` + `IntegrationsHubPage.tsx`):** Condensa pasta local, modo de permissão (`Shift+Tab`), worktree e vínculo de projeto em um único chip/popover no Composer; limpa os elementos duplicados da Home e do Header do Chat; substitui as 12 rotas de Settings por um Modal de 4 abas (`PreferencesModal`) e Hubs na Sidebar com Slide-Over real para `ProviderDetail`.

**Tech Stack:** React 19, TypeScript, React Router v7, Motion (`motion/react`), Tailwind CSS v3 + CSS Custom Properties (`--orin-*`), Vite, Vitest, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-17-orin-ui-refactoring-design.md`

## Global Constraints

- **Zero Hardcoded Dark Colors:** Proibido usar `text-white`, `text-white/*`, `bg-black/*`, `border-white/*` em superfícies temáticas; usar exclusivamente variáveis CSS `--orin-*`.
- **Preservação de `data-testid` e Acessibilidade:** Manter todos os `data-testid` existentes (`home-mode-chat`, `home-mode-code`, `code-permission-mode-pill`, `code-permission-mode-select`, `code-worktree-toggle`, `workspace-files-drawer-toggle`, `orin-code-badge`, `orin-code-workspace-chip`) para zero regressão na suíte de testes existente.
- **Anti-Slop Typography & Surfaces (`taste-skill`):** Fonte principal `'Plus Jakarta Sans', 'Geist', sans-serif`, monoespaçada `'JetBrains Mono'` com `tabular-nums`, e `Escape` contido dentro de popovers/modais (`event.stopPropagation()`) para nunca cancelar um turno em execução por acidente.
- **Retrocompatibilidade de Rotas:** Todas as rotas `/settings/*` devem continuar funcionando (abrindo o `PreferencesModal` na aba correspondente ou redirecionando para `/library/*`, `/integrations/*`, `/schedules`).

---

## Mapa de Arquivos (File Structure)

| Arquivo | Ação | Responsabilidade Única |
| :--- | :--- | :--- |
| `frontend/src/app/ThemeContext.tsx` | **Create** | Contexto React, hook `useTheme()`, persistência em `localStorage` e sincronização de `data-theme`, `data-accent`, `data-ambient` no `<html>`. |
| `frontend/src/styles/theme.css` | **Modify** | Definição dos tokens de superfície para `dark`, `carbon`, `light` e `system`, e das 5 paletas de `data-accent` (`violet`, `sapphire`, `emerald`, `amber`, `mono`). |
| `frontend/src/styles/index.css` & `agentos.css` | **Modify** | Troca de `Inter` por `Plus Jakarta Sans`/`Geist`, estilos do `.workspace-shell`, `.session-context-popover`, `.preferences-modal`, `.slide-over-drawer` e remoção de duplicatas de `.settings-shell`. |
| `frontend/src/main.tsx` | **Modify** | Chamada síncrona `initThemeDom()` antes do render e envolvimento de `<App />` com `<ThemeProvider>`. |
| `frontend/src/components/ModelPicker.tsx` | **Modify** | Pill Dropdown unificada para o canto superior esquerdo do palco (com abas de Provider + busca de Modelo + Favoritos no mesmo popover, mantendo compatibilidade de props). |
| `frontend/src/app/WorkspaceShell.tsx` | **Create** | Layout persistente (Sidebar Full-Height + Inset Canvas + `CanvasHeader` + host global do `PreferencesModal`). |
| `frontend/src/features/projects/WorkspaceNavigation.tsx` | **Modify** | Sidebar com suporte a Rail colapsável (`64px`), links para os Hubs (`Início`, `Biblioteca`, `Integrações`, `Automações`) e rodapé com `ProfileMenu`, `ThemeQuickToggle` e botão `Preferências`. |
| `frontend/src/features/conversations/SessionContextPopover.tsx` | **Create** | Chip resumido + Popover unificado para Pasta/Repositório, Permissão do Code (`Shift+Tab`), Git Worktree e Projeto. |
| `frontend/src/features/conversations/Composer.tsx` | **Modify** | Barra enxuta com 4 controles (Anexar, Pill `Chat \| >_ Code`, `SessionContextPopover`, Enviar/Parar). |
| `frontend/src/app/Home.tsx` | **Modify** | Palco limpo dentro do `WorkspaceShell` (`OrinOrb` + Saudação + `Composer` + 3 Quick Action Chips), sem blocos duplicados e sem cores hardcoded. |
| `frontend/src/features/conversations/ChatPage.tsx` | **Modify** | Integração com `WorkspaceShell` / `CanvasHeader`, remoção dos 11 itens amontoados no topo e delegação da gestão de projeto/pasta/permissão para o `SessionContextPopover`. |
| `frontend/src/features/settings/PreferencesModal.tsx` | **Create** | Modal rápido com 4 abas: `Aparência`, `Geral & Modo Code`, `Sistema & Ambiente`, `Conta & Sobre`. |
| `frontend/src/features/settings/LibraryHubPage.tsx` | **Create** | Hub na Sidebar (`/library`) unificando `SkillsSection`, `PluginsSection` e `MemoryPage`. |
| `frontend/src/features/settings/IntegrationsHubPage.tsx` | **Create** | Hub na Sidebar (`/integrations`) unificando `ProvidersSection` + `VisionModelSetting` + `McpSection`, com Slide-Over lateral real para `ProviderDetail`. |
| `frontend/src/features/settings/useSettingsBadges.ts` | **Modify** | Otimização para evitar 7 chamadas simultâneas no mount global, expondo badges sob demanda com cache. |
| `frontend/src/app/routes.tsx` | **Modify** | Registra `/library/*`, `/integrations/*`, `/schedules` no `WorkspaceShell` e mapeia rotas `/settings/*` com retrocompatibilidade. |

---

## Chunk 1: Design System Anti-Slop & Motor de Temas (`ThemeContext` + `theme.css`)

### Task 1: Criar `ThemeContext.tsx` e expandir tokens em `theme.css` (`dark`, `carbon`, `light`, `system` + 5 `accents` + `ambient`)

**Files:**
- Create: `frontend/src/app/ThemeContext.tsx`
- Modify: `frontend/src/styles/theme.css`
- Modify: `frontend/src/styles/index.css:1-18`
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/app/__tests__/ThemeContext.test.tsx`

**Interfaces:**
- Produces:
  - `export type ThemeMode = 'dark' | 'carbon' | 'light' | 'system'`
  - `export type ThemeAccent = 'violet' | 'sapphire' | 'emerald' | 'amber' | 'mono'`
  - `export type ThemeAmbient = 'on' | 'off'`
  - `export function initThemeDom(): void`
  - `export function ThemeProvider({ children }: { children: ReactNode }): JSX.Element`
  - `export function useTheme(): { mode: ThemeMode; resolvedTheme: 'dark' | 'carbon' | 'light'; accent: ThemeAccent; ambient: ThemeAmbient; setMode: (m: ThemeMode) => void; setAccent: (a: ThemeAccent) => void; setAmbient: (amb: ThemeAmbient) => void; cycleMode: () => void }`

- [ ] **Step 1 (RED): Write the failing test `frontend/src/app/__tests__/ThemeContext.test.tsx`**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { ThemeProvider, initThemeDom, useTheme } from '../ThemeContext'

function ThemeHarness() {
  const { mode, resolvedTheme, accent, ambient, setMode, setAccent, setAmbient, cycleMode } = useTheme()
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <span data-testid="resolved">{resolvedTheme}</span>
      <span data-testid="accent">{accent}</span>
      <span data-testid="ambient">{ambient}</span>
      <button type="button" onClick={() => setMode('light')}>Light</button>
      <button type="button" onClick={() => setMode('carbon')}>Carbon</button>
      <button type="button" onClick={() => setAccent('sapphire')}>Sapphire</button>
      <button type="button" onClick={() => setAmbient('off')}>Ambient Off</button>
      <button type="button" onClick={cycleMode}>Cycle</button>
    </div>
  )
}

describe('ThemeContext & initThemeDom', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-accent')
    document.documentElement.removeAttribute('data-ambient')
  })

  it('applies default dark/violet/on attributes to documentElement and persists changes', async () => {
    const user = userEvent.setup()
    initThemeDom()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(document.documentElement.getAttribute('data-accent')).toBe('violet')
    expect(document.documentElement.getAttribute('data-ambient')).toBe('on')

    render(
      <ThemeProvider>
        <ThemeHarness />
      </ThemeProvider>,
    )

    await user.click(screen.getByRole('button', { name: 'Light' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(window.localStorage.getItem('orin.theme.mode')).toBe('light')

    await user.click(screen.getByRole('button', { name: 'Sapphire' }))
    expect(document.documentElement.getAttribute('data-accent')).toBe('sapphire')
    expect(window.localStorage.getItem('orin.theme.accent')).toBe('sapphire')

    await user.click(screen.getByRole('button', { name: 'Ambient Off' }))
    expect(document.documentElement.getAttribute('data-ambient')).toBe('off')
    expect(window.localStorage.getItem('orin.theme.ambient')).toBe('off')
  })

  it('cycles through dark -> light -> carbon and falls back safely on invalid storage values', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('orin.theme.mode', 'invalid-mode')
    window.localStorage.setItem('orin.theme.accent', 'invalid-accent')

    render(
      <ThemeProvider>
        <ThemeHarness />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(screen.getByTestId('accent')).toHaveTextContent('violet')

    await user.click(screen.getByRole('button', { name: 'Cycle' }))
    expect(screen.getByTestId('mode')).toHaveTextContent('light')

    await user.click(screen.getByRole('button', { name: 'Cycle' }))
    expect(screen.getByTestId('mode')).toHaveTextContent('carbon')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- --run src/app/__tests__/ThemeContext.test.tsx`
Expected: FAIL (`Cannot find module '../ThemeContext'`)

- [ ] **Step 3 (GREEN): Implement `ThemeContext.tsx`, update `theme.css`, `index.css` and `main.tsx`**

Create `frontend/src/app/ThemeContext.tsx`:

```tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemeMode = 'dark' | 'carbon' | 'light' | 'system'
export type ResolvedTheme = 'dark' | 'carbon' | 'light'
export type ThemeAccent = 'violet' | 'sapphire' | 'emerald' | 'amber' | 'mono'
export type ThemeAmbient = 'on' | 'off'

const MODE_KEY = 'orin.theme.mode'
const ACCENT_KEY = 'orin.theme.accent'
const AMBIENT_KEY = 'orin.theme.ambient'

const VALID_MODES: readonly ThemeMode[] = ['dark', 'carbon', 'light', 'system']
const VALID_ACCENTS: readonly ThemeAccent[] = ['violet', 'sapphire', 'emerald', 'amber', 'mono']
const VALID_AMBIENTS: readonly ThemeAmbient[] = ['on', 'off']

function readValid<T extends string>(key: string, valid: readonly T[], fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key)
    return raw && (valid as readonly string[]).includes(raw) ? (raw as T) : fallback
  } catch {
    return fallback
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Ignore storage errors in restricted modes
  }
}

function resolveSystemTheme(): 'dark' | 'light' {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark'
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function resolveThemeMode(mode: ThemeMode): ResolvedTheme {
  return mode === 'system' ? resolveSystemTheme() : mode
}

export function applyThemeAttributes(mode: ThemeMode, accent: ThemeAccent, ambient: ThemeAmbient): ResolvedTheme {
  const resolved = resolveThemeMode(mode)
  if (typeof document !== 'undefined') {
    const root = document.documentElement
    root.setAttribute('data-theme', resolved)
    root.setAttribute('data-theme-Preference', mode)
    root.setAttribute('data-accent', accent)
    root.setAttribute('data-ambient', ambient)
  }
  return resolved
}

export function initThemeDom(): void {
  if (typeof window === 'undefined') return
  const mode = readValid(MODE_KEY, VALID_MODES, 'dark')
  const accent = readValid(ACCENT_KEY, VALID_ACCENTS, 'violet')
  const ambient = readValid(AMBIENT_KEY, VALID_AMBIENTS, 'on')
  applyThemeAttributes(mode, accent, ambient)
}

export type ThemeContextValue = {
  mode: ThemeMode
  resolvedTheme: ResolvedTheme
  accent: ThemeAccent
  ambient: ThemeAmbient
  setMode: (next: ThemeMode) => void
  setAccent: (next: ThemeAccent) => void
  setAmbient: (next: ThemeAmbient) => void
  cycleMode: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readValid(MODE_KEY, VALID_MODES, 'dark'))
  const [accent, setAccentState] = useState<ThemeAccent>(() => readValid(ACCENT_KEY, VALID_ACCENTS, 'violet'))
  const [ambient, setAmbientState] = useState<ThemeAmbient>(() => readValid(AMBIENT_KEY, VALID_AMBIENTS, 'on'))
  const [systemTheme, setSystemTheme] = useState<'dark' | 'light'>(() => resolveSystemTheme())

  const resolvedTheme: ResolvedTheme = mode === 'system' ? systemTheme : mode

  useEffect(() => {
    applyThemeAttributes(mode, accent, ambient)
  }, [mode, accent, ambient, systemTheme])

  useEffect(() => {
    if (mode !== 'system' || typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(prefers-color-scheme: light)')
    const onChange = () => setSystemTheme(media.matches ? 'light' : 'dark')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [mode])

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next)
    writeStorage(MODE_KEY, next)
    applyThemeAttributes(next, accent, ambient)
  }, [accent, ambient])

  const setAccent = useCallback((next: ThemeAccent) => {
    setAccentState(next)
    writeStorage(ACCENT_KEY, next)
    applyThemeAttributes(mode, next, ambient)
  }, [mode, ambient])

  const setAmbient = useCallback((next: ThemeAmbient) => {
    setAmbientState(next)
    writeStorage(AMBIENT_KEY, next)
    applyThemeAttributes(mode, accent, next)
  }, [mode, accent])

  const cycleMode = useCallback(() => {
    const order: readonly ThemeMode[] = ['dark', 'light', 'carbon']
    const currentIdx = order.indexOf(resolvedTheme)
    const next = order[(currentIdx + 1) % order.length]
    setMode(next)
  }, [resolvedTheme, setMode])

  const value = useMemo<ThemeContextValue>(() => ({
    mode,
    resolvedTheme,
    accent,
    ambient,
    setMode,
    setAccent,
    setAmbient,
    cycleMode,
  }), [mode, resolvedTheme, accent, ambient, setMode, setAccent, setAmbient, cycleMode])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    return {
      mode: 'dark',
      resolvedTheme: 'dark',
      accent: 'violet',
      ambient: 'on',
      setMode: () => {},
      setAccent: () => {},
      setAmbient: () => {},
      cycleMode: () => {},
    }
  }
  return ctx
}
```

Update `frontend/src/styles/theme.css` to add `[data-theme="carbon"]` and `[data-accent="sapphire" | "emerald" | "amber" | "mono"]` (for both dark/carbon and light themes), plus `--orin-font-sans: 'Plus Jakarta Sans', 'Geist', -apple-system, BlinkMacSystemFont, sans-serif;`.
Update `frontend/src/main.tsx` to call `initThemeDom()` before `ReactDOM.createRoot` and wrap `<App />` in `<ThemeProvider>`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- --run src/app/__tests__/ThemeContext.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/ThemeContext.tsx frontend/src/app/__tests__/ThemeContext.test.tsx frontend/src/styles/theme.css frontend/src/styles/index.css frontend/src/main.tsx
git commit -m "feat(theme): add 3D ThemeContext (dark/carbon/light, 5 accents, ambient toggle) and anti-slop typography"
```

---

## Chunk 2: `ModelPicker` Unificado (Top-Left Pill) & `SessionContextPopover` (Progressive Disclosure)

### Task 2: Evoluir `ModelPicker.tsx` para suportar modo Pill Unificado no Header e criar `SessionContextPopover.tsx`

**Files:**
- Modify: `frontend/src/components/ModelPicker.tsx`
- Create: `frontend/src/features/conversations/SessionContextPopover.tsx`
- Modify: `frontend/src/features/conversations/Composer.tsx`
- Test: `frontend/src/features/conversations/__tests__/SessionContextPopover.test.tsx`

**Interfaces:**
- Produces:
  - `export function SessionContextPopover(props: SessionContextPopoverProps): JSX.Element`
  - `ModelPicker` keeps existing `providers`, `provider`, `onProviderChange`, `models`, `modelId`, `onModelChange`, `loading`, `failed`, `disabled` props, and adds optional `variant?: 'split' | 'unified'` (default `'unified'` in `CanvasHeader`, rendering a single sleek pill with provider tabs + model search inside one popover, while still exposing `role="option"` for providers and models).

- [ ] **Step 1 (RED): Write the failing test `frontend/src/features/conversations/__tests__/SessionContextPopover.test.tsx`**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { SessionContextPopover } from '../SessionContextPopover'

describe('SessionContextPopover', () => {
  it('renders a compact summary chip and opens unified popover with workspace, permission, worktree, and project controls', async () => {
    const user = userEvent.setup()
    const onPermissionChange = vi.fn()
    const onWorktreeChange = vi.fn()
    const onMoveToProject = vi.fn()

    render(
      <MemoryRouter>
        <SessionContextPopover
          mode="code"
          permissionMode="ask_permissions"
          onPermissionModeChange={onPermissionChange}
          worktreeIsolated={false}
          onWorktreeIsolatedChange={onWorktreeChange}
          worktreeBranch={null}
          workspaceState={{
            kind: 'local',
            path: 'C:/projetos/orin',
            folderName: 'orin',
            scope: 'chat',
            projectName: null,
          }}
          onInspectWorkspace={vi.fn()}
          onAttachWorkspace={vi.fn()}
          onDetachWorkspace={vi.fn()}
          onWorkspaceChange={vi.fn()}
          projects={[{ project_id: 'proj_1', name: 'Orin Core', description: '' }]}
          activeProjectId={null}
          activeProjectName={null}
          onMoveToProject={onMoveToProject}
          onCreateProjectFromSession={vi.fn()}
        />
      </MemoryRouter>,
    )

    // Summary pill shows folder name and permission mode
    expect(screen.getByTestId('session-context-trigger')).toHaveTextContent('orin')
    expect(screen.getByTestId('code-permission-mode-pill')).toHaveTextContent('🔒 Pedir permissão')

    // Open popover
    await user.click(screen.getByTestId('session-context-trigger'))
    expect(screen.getByRole('dialog', { name: /Contexto da sessão/i })).toBeInTheDocument()

    // Change permission mode inside popover
    await user.click(screen.getByRole('button', { name: /Aceitar edições/i }))
    expect(onPermissionChange).toHaveBeenCalledWith('accept_edits')

    // Toggle worktree inside popover
    await user.click(screen.getByTestId('code-worktree-toggle'))
    expect(onWorktreeChange).toHaveBeenCalledWith(true)

    // Move to project inside popover
    await user.click(screen.getByRole('button', { name: 'Orin Core' }))
    expect(onMoveToProject).toHaveBeenCalledWith('proj_1')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/SessionContextPopover.test.tsx`
Expected: FAIL (`Cannot find module '../SessionContextPopover'`)

- [ ] **Step 3 (GREEN): Implement `SessionContextPopover.tsx` and update `Composer.tsx` & `ModelPicker.tsx`**

Create `frontend/src/features/conversations/SessionContextPopover.tsx`:
- Renders:
  1. Compact summary chip (`data-testid="session-context-trigger"`) displaying folder name (`📁 orin` or `📁 Workspace padrão`) and active project badge if any.
  2. In `code` mode, keeps the quick-cycle `data-testid="code-permission-mode-pill"` and the hidden `<select data-testid="code-permission-mode-select">` accessible right next to or inside the summary bar so `Shift+Tab` and existing tests work seamlessly.
  3. When clicked, opens a clean floating popover (`role="dialog" aria-label="Contexto da sessão"`) with:
     - **Repositório / Pasta de Trabalho:** embeds `WorkspaceFolderButton` (or its inline directory picker/inspector).
     - **Modo de Permissão (Code):** 4 selectable buttons (`Pedir permissão`, `Aceitar edições`, `Modo Plano`, `Sem restrições`).
     - **Isolamento Git Worktree (Code):** `data-testid="code-worktree-toggle"` switch/button showing active branch.
     - **Organizar em Projeto (quando aplicável):** list of `projects` to move session (`onMoveToProject`), `data-testid="remove-from-project-button"`, and `data-testid="create-project-from-session-form"`.
  4. Handles `Escape` with `event.stopPropagation()` so closing the popover never stops a running turn.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/SessionContextPopover.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/conversations/SessionContextPopover.tsx frontend/src/features/conversations/__tests__/SessionContextPopover.test.tsx frontend/src/features/conversations/Composer.tsx frontend/src/components/ModelPicker.tsx
git commit -m "feat(conversations): add SessionContextPopover progressive disclosure and unified ModelPicker"
```

---

## Chunk 3: Desacoplamento de Settings — `PreferencesModal` + Hubs (`LibraryHubPage` & `IntegrationsHubPage`)

### Task 3: Criar `PreferencesModal.tsx`, `LibraryHubPage.tsx` e `IntegrationsHubPage.tsx` (com Slide-Over real para `ProviderDetail`)

**Files:**
- Create: `frontend/src/features/settings/PreferencesModal.tsx`
- Create: `frontend/src/features/settings/LibraryHubPage.tsx`
- Create: `frontend/src/features/settings/IntegrationsHubPage.tsx`
- Modify: `frontend/src/features/settings/useSettingsBadges.ts`
- Test: `frontend/src/features/settings/__tests__/PreferencesAndHubs.test.tsx`

**Interfaces:**
- Produces:
  - `export type PreferencesTab = 'appearance' | 'general' | 'system' | 'about'`
  - `export function PreferencesModal({ open, initialTab, onClose, client }: PreferencesModalProps): JSX.Element | null`
  - `export function LibraryHubPage({ activeTab, client }: { activeTab?: 'skills' | 'plugins' | 'memory'; client?: ApiClient }): JSX.Element`
  - `export function IntegrationsHubPage({ activeTab, selectedProvider, client }: { activeTab?: 'providers' | 'mcp'; selectedProvider?: ProviderName | null; client?: ApiClient }): JSX.Element`

- [ ] **Step 1 (RED): Write the failing test `frontend/src/features/settings/__tests__/PreferencesAndHubs.test.tsx`**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { PreferencesModal } from '../PreferencesModal'
import { LibraryHubPage } from '../LibraryHubPage'

describe('PreferencesModal & Sidebar Hubs', () => {
  it('switches theme mode, accent, and ambient in real time inside PreferencesModal', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()

    render(
      <MemoryRouter>
        <ThemeProvider>
          <PreferencesModal open={true} initialTab="appearance" onClose={onClose} />
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(screen.getByRole('dialog', { name: /Preferências/i })).toBeInTheDocument()

    // Select Warm Paper (Light) theme
    await user.click(screen.getByTestId('theme-mode-light'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')

    // Select Emerald accent
    await user.click(screen.getByTestId('theme-accent-emerald'))
    expect(document.documentElement.getAttribute('data-accent')).toBe('emerald')

    // Toggle ambient off
    await user.click(screen.getByTestId('theme-ambient-toggle'))
    expect(document.documentElement.getAttribute('data-ambient')).toBe('off')
  })

  it('renders LibraryHubPage with Skills, Plugins, and Memory tabs', async () => {
    render(
      <MemoryRouter initialEntries={['/library/skills']}>
        <LibraryHubPage activeTab="skills" />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: /Biblioteca/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Skills/i })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /Plugins/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Memória/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- --run src/features/settings/__tests__/PreferencesAndHubs.test.tsx`
Expected: FAIL (`Cannot find module '../PreferencesModal'`)

- [ ] **Step 3 (GREEN): Implement `PreferencesModal.tsx`, `LibraryHubPage.tsx`, `IntegrationsHubPage.tsx` and optimize `useSettingsBadges.ts`**

1. **`PreferencesModal.tsx`**:
   - Renders an accessible modal (`role="dialog" aria-modal="true" aria-label="Preferências"`) with 4 vertical tabs:
     - `appearance`: visual cards for `dark` (`Space Dark`), `carbon` (`Carbon OLED`), `light` (`Warm Paper`), `system` (`Sistema`); 5 accent swatches (`violet`, `sapphire`, `emerald`, `amber`, `mono`); and `data-testid="theme-ambient-toggle"` switch.
     - `general`: embeds `<RuntimeSettingsPage embedded client={client} />`.
     - `system`: embeds `<WorkspaceSection />`, `<BackgroundSection />`, and `<BrowserSection />` in clean stacked sections.
     - `about`: embeds `<AboutSection />` and, if `session.isAdmin && session.capabilities.user_admin`, `<UsersSection />`.
2. **`LibraryHubPage.tsx`**:
   - Renders inside the Inset Canvas with tabs for `Skills` (`/library/skills` → `<SkillsSection />`), `Plugins` (`/library/plugins` → `<PluginsSection />`), and `Memória` (`/library/memory` → `<MemoryPage embedded />`).
3. **`IntegrationsHubPage.tsx`**:
   - Renders inside the Inset Canvas with tabs for `Providers & Modelos` (`/integrations/providers` → `<ProvidersSection client={apiClient} />` + `<VisionModelSetting client={apiClient} />`) and `Servidores MCP` (`/integrations/mcp` → `<McpSection />`).
   - When `selectedProvider` is present, renders `<ProviderDetail>` inside a fixed/absolute **Slide-Over Drawer** on the right side of the canvas (`data-testid="settings-drawer-slot"` preserved for test compatibility), with a backdrop and close button so the grid never jumps vertically.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- --run src/features/settings/__tests__/PreferencesAndHubs.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/settings/PreferencesModal.tsx frontend/src/features/settings/LibraryHubPage.tsx frontend/src/features/settings/IntegrationsHubPage.tsx frontend/src/features/settings/useSettingsBadges.ts frontend/src/features/settings/__tests__/PreferencesAndHubs.test.tsx
git commit -m "feat(settings): add PreferencesModal, LibraryHubPage, and IntegrationsHubPage with slide-over drawer"
```

---

## Chunk 4: App Shell "Inset Studio Canvas" (`WorkspaceShell.tsx` & `WorkspaceNavigation.tsx`)

### Task 4: Criar `WorkspaceShell.tsx` e atualizar `WorkspaceNavigation.tsx` com Icon Rail, Hubs na Sidebar e Rodapé com Tema/Preferências

**Files:**
- Create: `frontend/src/app/WorkspaceShell.tsx`
- Modify: `frontend/src/features/projects/WorkspaceNavigation.tsx`
- Modify: `frontend/src/styles/agentos.css`
- Test: `frontend/src/app/__tests__/WorkspaceShell.test.tsx`

**Interfaces:**
- Produces:
  - `export function WorkspaceShell({ headerLeft, headerCenter, headerRight, children, client, chats, onChatsChange, onNewConversation, onNewCodeConversation, initialPreferencesTab }: WorkspaceShellProps): JSX.Element`

- [ ] **Step 1 (RED): Write the failing test `frontend/src/app/__tests__/WorkspaceShell.test.tsx`**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ThemeProvider } from '../ThemeContext'
import { WorkspaceShell } from '../WorkspaceShell'

describe('WorkspaceShell & Sidebar Hubs', () => {
  it('renders full-height sidebar with hubs, collapses to rail, and opens PreferencesModal from footer', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <ThemeProvider>
          <WorkspaceShell
            headerLeft={<div data-testid="slot-left">Model Pill</div>}
            headerCenter={<div data-testid="slot-center">Title</div>}
            headerRight={<div data-testid="slot-right">Actions</div>}
          >
            <div data-testid="canvas-content">Main Stage</div>
          </WorkspaceShell>
        </ThemeProvider>
      </MemoryRouter>,
    )

    expect(screen.getByTestId('slot-left')).toBeInTheDocument()
    expect(screen.getByTestId('canvas-content')).toBeInTheDocument()

    // Sidebar hubs links exist
    expect(screen.getByRole('link', { name: /Biblioteca/i })).toHaveAttribute('href', '/library/skills')
    expect(screen.getByRole('link', { name: /Integrações/i })).toHaveAttribute('href', '/integrations/providers')
    expect(screen.getByRole('link', { name: /Automações/i })).toHaveAttribute('href', '/schedules')

    // Toggle sidebar rail collapse
    const railToggle = screen.getByTestId('sidebar-rail-toggle')
    await user.click(railToggle)
    expect(railToggle).toHaveAttribute('aria-expanded', 'false')

    // Open PreferencesModal from sidebar footer
    await user.click(screen.getByTestId('open-preferences-modal'))
    expect(screen.getByRole('dialog', { name: /Preferências/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- --run src/app/__tests__/WorkspaceShell.test.tsx`
Expected: FAIL (`Cannot find module '../WorkspaceShell'`)

- [ ] **Step 3 (GREEN): Implement `WorkspaceShell.tsx` and update `WorkspaceNavigation.tsx` & `agentos.css`**

1. **`WorkspaceNavigation.tsx`**:
   - Adds Brand + `data-testid="sidebar-rail-toggle"` (`aria-expanded={!railCollapsed}`) at the top of the sidebar.
   - Adds the **Workspace Hubs** navigation block (`Biblioteca` → `/library/skills`, `Integrações` → `/integrations/providers`, `Automações` → `/schedules`).
   - Adds footer bar with `<ProfileMenu />`, `data-testid="theme-quick-toggle"` (calls `cycleMode()`), and `data-testid="open-preferences-modal"` (calls `onOpenPreferences()`), while also keeping an accessible `Settings` link/trigger for tests that query `aria-label="Abrir Settings"` or `Settings`.
2. **`WorkspaceShell.tsx`**:
   - Renders `<div className="workspace-shell">` with `<aside className="workspace-navigation">` on the left and `<div className="workspace-shell__canvas">` (Inset Canvas with `margin: 8px 8px 8px 0`, `border-radius: 18px`, `border: 1px solid var(--orin-border-subtle)`) on the right.
   - Inside `.workspace-shell__canvas`, renders `<header className="canvas-header">` (`headerLeft`, `headerCenter`, `headerRight`) and `<div className="canvas-stage">{children}</div>`.
   - Hosts `<PreferencesModal open={preferencesOpen} ... />` and listens to keyboard shortcut `Ctrl+,` / `⌘,`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- --run src/app/__tests__/WorkspaceShell.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/WorkspaceShell.tsx frontend/src/app/__tests__/WorkspaceShell.test.tsx frontend/src/features/projects/WorkspaceNavigation.tsx frontend/src/styles/agentos.css
git commit -m "feat(shell): add Inset Studio WorkspaceShell, collapsible sidebar rail, and hub navigation"
```

---

## Chunk 5: Refatoração de `Home.tsx`, `ChatPage.tsx` e `routes.tsx`

### Task 5: Integrar `Home.tsx`, `ChatPage.tsx` e `routes.tsx` ao novo `WorkspaceShell`, `ModelPicker` no topo e `SessionContextPopover`

**Files:**
- Modify: `frontend/src/app/Home.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Test: `frontend/src/app/__tests__/HomeAndChatRefactored.test.tsx`

**Interfaces:**
- Consumes: `WorkspaceShell`, `SessionContextPopover`, `ModelPicker`, `LibraryHubPage`, `IntegrationsHubPage`, `PreferencesModal`, `useTheme`.
- Produces: Updated `Home`, `ChatPage`, and `routes` with zero hardcoded dark colors and full backwards compatibility.

- [ ] **Step 1 (RED): Write the failing test `frontend/src/app/__tests__/HomeAndChatRefactored.test.tsx`**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ThemeProvider } from '../ThemeContext'
import { Home } from '../Home'

describe('Refactored Home Stage', () => {
  it('places ModelPicker in the top canvas header, removes duplicate workspace bars, and switches Chat/Code mode from the compact pill', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter initialEntries={['/?mode=chat']}>
        <ThemeProvider>
          <Routes>
            <Route path="/" element={<Home bootstrap={{ status: 'ready', csrfToken: 'test-token' }} />} />
          </Routes>
        </ThemeProvider>
      </MemoryRouter>,
    )

    // ModelPicker is inside the top canvas header
    const header = screen.getByTestId('canvas-header')
    expect(header.querySelector('.model-picker')).not.toBeNull()

    // Compact mode switch works and updates heading
    const codeToggle = screen.getByTestId('home-mode-code')
    await user.click(codeToggle)
    expect(codeToggle).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Orin Code/i)

    // SessionContextPopover trigger is inside the composer
    expect(screen.getByTestId('session-context-trigger')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- --run src/app/__tests__/HomeAndChatRefactored.test.tsx`
Expected: FAIL (`Unable to find an element by: [data-testid="canvas-header"]`)

- [ ] **Step 3 (GREEN): Refactor `Home.tsx`, `ChatPage.tsx`, and `routes.tsx`**

1. **`Home.tsx`**:
   - Uses `<WorkspaceShell>` (or shares its structure with `data-testid="canvas-header"`), placing `<ModelPicker>` in the top-left header slot.
   - Removes the giant 2-card `.home__mode-switch`, removes the duplicate yellow `.home__code-workspace-bar`, removes `.home__recent` below the Composer, and removes `.home__foot`.
   - Places the compact Segmented Mode Pill (`data-testid="home-mode-chat"` / `data-testid="home-mode-code"`) and `<SessionContextPopover>` inside `<Composer>`, plus 3 contextual Quick Action pills below the Composer.
   - Respects `ambient === 'on'` from `useTheme()` before rendering `<AmbientField />` and `<div className="home__glow" />`.
   - Replaces all `text-white/*`, `bg-black/*`, `border-white/*` classes with semantic `--orin-*` tokens.
2. **`ChatPage.tsx`**:
   - Cleans `.chat__bar` / `CanvasHeader`:
     - Left: `<ModelPicker>` (when conversation is loaded) + `<span className="chat__code-badge">` (if `code` mode).
     - Center: `<h1>{conversation?.title}</h1>` + clickable project badge (`data-testid="conversation-project-badge"`).
     - Right: `<ContextIndicator usage={contextUsage} />`, `Arquivos` button (`workspace-files-drawer-toggle`), and `Visão geral` button.
   - Moves the inline project form (`conversation-project-trigger` / `conversation-project-menu`), `WorkspaceFolderButton`, permission mode pill, and worktree toggle into `<SessionContextPopover>` inside the `<Composer>` bar (keeping a quiet trigger in the header or composer so all existing `ProjectSessionManagement.test.tsx` assertions pass!).
3. **`routes.tsx`**:
   - Adds `/library`, `/library/:tab`, `/integrations`, `/integrations/:tab`, `/integrations/providers/:provider`.
   - Preserves `/settings/*` routes so direct navigation to `/settings/general`, `/settings/providers`, `/settings/skills`, etc. continues to render seamlessly and pass existing route tests.

- [ ] **Step 4: Run test and full frontend test suite to verify everything passes**

Run:
```bash
npm --prefix frontend test -- --run src/app/__tests__/HomeAndChatRefactored.test.tsx
npm --prefix frontend test -- --run
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/Home.tsx frontend/src/features/conversations/ChatPage.tsx frontend/src/app/routes.tsx frontend/src/app/__tests__/HomeAndChatRefactored.test.tsx
git commit -m "feat(ui): refactor Home, ChatPage, and routes to Inset Studio Shell and Progressive Disclosure"
```

---

## Chunk 6: Verificação Visual Obrigatória no Navegador Real (Playwright + `taste-skill` Gate)

### Task 6: Validação visual em navegador real (Desktop `1440x900` e Mobile `390x844`) nos 3 temas e lint/build final

**Files:**
- Modify: `frontend/src/styles/agentos.css` (ajustes finos ópticos se detectados na inspeção real)
- Test: Execução real via Playwright / Vite Dev Server + `npm --prefix frontend run build` + `npm --prefix frontend run lint`

- [ ] **Step 1: Subir o servidor de desenvolvimento e inspecionar no navegador real (Playwright)**

Run:
```bash
npm --prefix frontend run build
npm --prefix frontend run lint
```
Subir `npm --prefix frontend run dev:local` e abrir no navegador Playwright em `1440x900` e `390x844`:
1. Inspecionar a `Home` em `Space Dark`, `Carbon OLED` e `Warm Paper (Light)` com diferentes cores de acento (`Violet`, `Sapphire`, `Emerald`).
2. Abrir o `SessionContextPopover` no Composer e verificar alinhamento, bordas, foco e ausência de overflow.
3. Abrir o `PreferencesModal` e os Hubs de `Biblioteca` (`/library/skills`) e `Integrações` (`/integrations/providers` com o Slide-Over de um provider aberto).
4. Inspecionar uma conversa no modo `Chat` e no modo `Orin Code` (verificando `ModelPicker` no topo, `ContextIndicator`, drawer de `Arquivos` e `SessionContextPopover`).

- [ ] **Step 2: Rodar suíte completa de testes unitários e de integração**

Run:
```bash
npm --prefix frontend test -- --run
```
Expected: 100% PASS, zero warnings no ESLint e zero erros de TypeScript no `tsc -b`.

- [ ] **Step 3: Commit final (se houver ajustes ópticos)**

```bash
git status -s
```
