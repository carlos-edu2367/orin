# Settings Shell Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Colocar **todas** as configurações no mesmo fluxo — uma barra lateral com seções agrupadas à esquerda e o conteúdo à direita — e transformar a lista de providers em uma grade de cards com logo, onde clicar abre um painel de configuração no mesmo lugar em vez de navegar para outra tela.

**Architecture:** Um único `SettingsShell` passa a ser o layout de todas as rotas `/settings/*`. Cada seção vira um componente de conteúdo puro que o shell renderiza — não uma página com `app-shell` e `topbar` próprios. Providers e Skills, que hoje são telas independentes de 650 e 356 linhas, são quebradas em componentes focados e reencaixadas dentro do shell. O detalhe de um provider vira um painel roteado (`/settings/providers/:provider`) que desliza sobre o conteúdo, preservando a grade atrás dele.

**Tech Stack:** React 18, TypeScript, react-router-dom, CSS escrito à mão em `agentos.css` com os tokens de `theme.css`, Vitest + Testing Library, Playwright para regressão visual e axe para acessibilidade.

---

## Direção de design

O Orin já tem uma identidade declarada no topo de `agentos.css`: *uma sala de grafite quase preta com um único sinal violeta dentro*. Violeta é racionado — marca o agente e a ação primária, nunca uma borda ou um fundo. Texto técnico vai em mono para separar o que a máquina disse do que uma pessoa escreveu. Este plano **não inventa uma estética nova**; ele estende essa para uma superfície que hoje está inconsistente.

O conceito da tela: **índice, sala e gaveta.**

- **Índice** (barra lateral): a lista completa do que existe para configurar, agrupada por assunto, com um status em mono na direita de cada item. O índice nunca some — é como você sabe onde está.
- **Sala** (painel de conteúdo): uma coluna de 860px com um cabeçalho consistente (eyebrow mono, título, lede) e o conteúdo da seção. Toda seção usa o mesmo cabeçalho, então mudar de seção não parece mudar de aplicativo.
- **Gaveta** (painel de detalhe): configurar um provider não te leva embora. A gaveta desliza sobre a sala, a grade continua visível na borda, e `Esc` ou o botão voltar te devolve exatamente onde você estava.

Três decisões que dão caráter à tela:

1. **O status em mono na barra lateral.** `Providers · 2 ativos`, `MCP · 1 pendente`. É informação real, densa, e no tipo certo — e faz o índice virar um painel de estado, não só navegação.
2. **O ponto violeta de pendência.** Um único ponto violeta ao lado do item da barra lateral quando algo espera por você (uma conexão MCP proposta pelo agente, um plugin aguardando aprovação). É o único violeta da barra. Racionamento é o que faz ele funcionar.
3. **A revelação escalonada da grade.** Ao entrar em Providers, os cards aparecem com 40ms de atraso entre si. Uma vez, na entrada, não em cada hover. Respeita `prefers-reduced-motion`.

### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ ◈ Orin        Settings                              Voltar ao chat   │  58px
├───────────────────┬──────────────────────────────────────────────────┤
│ SESSÃO            │  PROVIDERS / CONEXÕES                            │
│  General      ·   │  Provedores de modelo                            │
│  Memory      12   │  Configure ou revogue o acesso de cada provider.  │
│                   │                                                  │
│ INTELIGÊNCIA      │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐    │
│  Providers  2 on  │  │  ◐     │ │  ✳     │ │  ◇     │ │  ▣     │    │
│  Leitura visual   │  │ OpenAI │ │Anthropi│ │OpenRout│ │OmniRout│    │
│                   │  │ 42 mod │ │ 9 mod  │ │não conf│ │ ativo  │    │
│ EXTENSÕES         │  └────────┘ └────────┘ └────────┘ └────────┘    │
│  Skills      18   │                                                  │
│  MCP       1 •    │  ┌────────┐                                      │
│  Plugins      3   │  │  ◍     │                                      │
│                   │  │ Ollama │                                      │
│ SISTEMA           │  │ local  │                                      │
│  Workspace        │  └────────┘                                      │
│  Agendamentos 4   │                                                  │
│  Sobre            │                                                  │
└───────────────────┴──────────────────────────────────────────────────┘
      216px                        860px máx
```

Com a gaveta aberta em `/settings/providers/openai`:

```
│ ...               │  ← Providers    ┌─────────────────────────────┐ │
│  Providers  2 on  │                 │ ◐  OpenAI                   │ │
│ ...               │  ┌────────┐ ┌───│ Chave de API                │ │
│                   │  │  ◐     │ │  ✳│ [·······················]   │ │
│                   │  │ OpenAI │ │Ant│                             │ │
│                   │  └────────┘ └───│ 42 modelos · atualizado hoje│ │
│                   │                 │ [Salvar]      [Revogar]     │ │
│                   │                 └─────────────────────────────┘ │
```

### Seções da barra lateral

| Grupo | Item | Rota | Status |
| --- | --- | --- | --- |
| Sessão | General | `/settings/general` | — |
| Sessão | Memory | `/settings/memory` | contagem de memórias |
| Inteligência | Providers | `/settings/providers` | `N ativos` |
| Inteligência | Leitura visual | `/settings/vision` | modelo escolhido |
| Extensões | Skills | `/settings/skills` | contagem |
| Extensões | MCP | `/settings/mcp` | `N ativos` + ponto se houver pendente |
| Extensões | Plugins | `/settings/plugins` | contagem + ponto se houver pendente |
| Sistema | Workspace | `/settings/workspace` | — |
| Sistema | Agendamentos | `/settings/schedules` | contagem |
| Sistema | Sobre | `/settings/about` | versão instalada |

`/settings/omniroute` e `/settings/agents`, que hoje existem como rotas separadas, passam a redirecionar: OmniRoute é um card dentro de Providers; Agents continua no contexto de cada agente. `/providers`, `/skills` e `/schedules` (rotas de nível raiz que hoje duplicam Settings) redirecionam para o equivalente em `/settings/*`.

## Estrutura de arquivos

**Criar:**

| Arquivo | Responsabilidade |
| --- | --- |
| `frontend/src/features/settings/SettingsShell.tsx` | Layout: topbar, barra lateral, painel de conteúdo, slot de gaveta. |
| `frontend/src/features/settings/SettingsNav.tsx` | Barra lateral: grupos, itens, status, ponto de pendência, `aria-current`. |
| `frontend/src/features/settings/SettingsSection.tsx` | Cabeçalho padrão (eyebrow + h1 + lede) + slot de conteúdo. |
| `frontend/src/features/settings/SettingsDrawer.tsx` | Gaveta: foco, `Esc`, retorno de foco, `aria-modal="false"` (é um painel, não um modal). |
| `frontend/src/features/settings/sections.ts` | Definição declarativa dos grupos e itens. Única fonte de verdade da navegação. |
| `frontend/src/features/settings/useSettingsBadges.ts` | Busca as contagens de status da barra lateral, com cache por sessão. |
| `frontend/src/features/providers/ProviderGrid.tsx` | Grade de cards. |
| `frontend/src/features/providers/ProviderCard.tsx` | Um card: marca, nome, status em mono. |
| `frontend/src/features/providers/ProviderDetail.tsx` | Conteúdo da gaveta de um provider (extraído de `ProviderPanel`). |
| `frontend/src/features/providers/providerBrand.ts` | Marca e cor de acento por provider. |
| `frontend/src/features/providers/OmniRouteSetup.tsx` | Extraído de `ProviderSettingsPage`. |
| `frontend/src/features/providers/OllamaSetup.tsx` | Extraído de `ProviderSettingsPage`. |
| `frontend/src/features/providers/useProviderState.ts` | O hook de carregamento/ação, extraído de `ProviderPanel`. |
| `frontend/src/features/skills/SkillsSection.tsx` | Biblioteca de skills como conteúdo de seção. |
| `frontend/src/features/skills/SkillRow.tsx` | Uma linha de skill. |
| `frontend/src/features/skills/AgentSkillsPanel.tsx` | Extraído de `SkillsPage`. |
| `frontend/src/features/settings/AboutSection.tsx` | Versão, atualização, desinstalar (hoje dentro de `RuntimeSettingsPage`). |
| `frontend/src/assets/providers/*.svg` | Marcas dos providers, inlined em build. |

**Modificar:** `frontend/src/app/routes.tsx`, `frontend/src/features/settings/SettingsPage.tsx` (vira um redirect fino), `RuntimeSettingsPage.tsx`, `MemoryPage.tsx`, `SchedulesPage.tsx`, `ProviderSettingsPage.tsx` (vira `ProvidersSection.tsx`), `SkillsPage.tsx`, `CommandPalette.tsx`, `frontend/src/styles/agentos.css`.

**Remover ao final:** nada é deletado sem substituto. `ProviderSettingsPage.tsx` e `SkillsPage.tsx` são renomeados/quebrados, não apagados às cegas.

---

### Task 1: Definição declarativa das seções

**Files:**
- Create: `frontend/src/features/settings/sections.ts`
- Test: `frontend/tests/unit/settingsSections.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/tests/unit/settingsSections.test.ts
import { describe, expect, it } from 'vitest'
import { SETTINGS_GROUPS, findSettingsItem, settingsItems } from '../../src/features/settings/sections'

describe('settings sections', () => {
  it('groups every item under a titled group', () => {
    for (const group of SETTINGS_GROUPS) {
      expect(group.title.length).toBeGreaterThan(0)
      expect(group.items.length).toBeGreaterThan(0)
    }
  })

  it('gives every item a unique id, path and label', () => {
    const ids = settingsItems().map((item) => item.id)
    const paths = settingsItems().map((item) => item.path)
    expect(new Set(ids).size).toBe(ids.length)
    expect(new Set(paths).size).toBe(paths.length)
    for (const item of settingsItems()) {
      expect(item.label.length).toBeGreaterThan(0)
      expect(item.path.startsWith('/settings/')).toBe(true)
    }
  })

  it('resolves an item from an exact path', () => {
    expect(findSettingsItem('/settings/providers')?.id).toBe('providers')
  })

  it('resolves an item from a nested detail path', () => {
    expect(findSettingsItem('/settings/providers/openai')?.id).toBe('providers')
  })

  it('returns undefined for a path outside settings', () => {
    expect(findSettingsItem('/chats/abc')).toBeUndefined()
  })

  it('declares which items carry a status badge', () => {
    expect(findSettingsItem('/settings/mcp')?.badge).toBe('mcp')
    expect(findSettingsItem('/settings/general')?.badge).toBeUndefined()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- settingsSections`
Expected: FAIL — módulo inexistente.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/features/settings/sections.ts

/**
 * The single source of truth for settings navigation.
 *
 * The sidebar, the command palette and the route table all read this, so a new
 * section is added in exactly one place and cannot appear in one surface while
 * missing from another.
 */
export type SettingsBadge = 'memory' | 'providers' | 'skills' | 'mcp' | 'plugins' | 'schedules' | 'version'

export type SettingsItem = {
  id: string
  label: string
  path: string
  /** Shown under the title in the content header. */
  lede: string
  badge?: SettingsBadge
}

export type SettingsGroup = {
  title: string
  items: SettingsItem[]
}

export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    title: 'Sessão',
    items: [
      { id: 'general', label: 'General', path: '/settings/general', lede: 'Preferências locais e limites de execução do runtime.' },
      { id: 'memory', label: 'Memory', path: '/settings/memory', lede: 'Fatos que o agente lembra entre conversas.', badge: 'memory' },
    ],
  },
  {
    title: 'Inteligência',
    items: [
      { id: 'providers', label: 'Providers', path: '/settings/providers', lede: 'Configure ou revogue o acesso de cada provider. A chave nunca é reexibida depois do envio.', badge: 'providers' },
      { id: 'vision', label: 'Leitura visual', path: '/settings/vision', lede: 'Qual modelo lê imagens e páginas escaneadas quando o modelo do turno não enxerga.' },
    ],
  },
  {
    title: 'Extensões',
    items: [
      { id: 'skills', label: 'Skills', path: '/settings/skills', lede: 'Procedimentos que o agente carrega quando a tarefa pede.', badge: 'skills' },
      { id: 'mcp', label: 'MCP', path: '/settings/mcp', lede: 'Servidores MCP conectados e as tools que cada um publica.', badge: 'mcp' },
      { id: 'plugins', label: 'Plugins', path: '/settings/plugins', lede: 'Pacotes instalados e o que cada um contribui.', badge: 'plugins' },
    ],
  },
  {
    title: 'Sistema',
    items: [
      { id: 'workspace', label: 'Workspace', path: '/settings/workspace', lede: 'Onde os arquivos de cada conversa são gravados.' },
      { id: 'schedules', label: 'Agendamentos', path: '/settings/schedules', lede: 'Conversas que começam sozinhas em um horário.', badge: 'schedules' },
      { id: 'about', label: 'Sobre', path: '/settings/about', lede: 'Versão instalada, atualização e remoção.', badge: 'version' },
    ],
  },
]

export function settingsItems(): SettingsItem[] {
  return SETTINGS_GROUPS.flatMap((group) => group.items)
}

export function findSettingsItem(pathname: string): SettingsItem | undefined {
  // The longest matching prefix wins so a detail route resolves to its section.
  return settingsItems()
    .filter((item) => pathname === item.path || pathname.startsWith(`${item.path}/`))
    .sort((left, right) => right.path.length - left.path.length)[0]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- settingsSections`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/settings/sections.ts frontend/tests/unit/settingsSections.test.ts
git commit -m "feat(settings): declare the settings navigation in one place"
```

---

### Task 2: Barra lateral

**Files:**
- Create: `frontend/src/features/settings/SettingsNav.tsx`
- Test: `frontend/tests/unit/SettingsNav.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/SettingsNav.test.tsx
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { SettingsNav } from '../../src/features/settings/SettingsNav'

function renderNav(pathname: string, badges = {}) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <SettingsNav badges={badges} />
    </MemoryRouter>,
  )
}

describe('SettingsNav', () => {
  it('renders every group as a labelled list', () => {
    renderNav('/settings/general')
    expect(screen.getByRole('navigation', { name: 'Settings' })).toBeInTheDocument()
    for (const title of ['Sessão', 'Inteligência', 'Extensões', 'Sistema']) {
      expect(screen.getByText(title)).toBeInTheDocument()
    }
  })

  it('marks the current item with aria-current', () => {
    renderNav('/settings/providers')
    expect(screen.getByRole('link', { name: /Providers/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /Skills/ })).not.toHaveAttribute('aria-current')
  })

  it('keeps the section current while a detail route is open', () => {
    renderNav('/settings/providers/openai')
    expect(screen.getByRole('link', { name: /Providers/ })).toHaveAttribute('aria-current', 'page')
  })

  it('renders a badge value next to the item that declares one', () => {
    renderNav('/settings/general', { skills: { value: '18' } })
    expect(within(screen.getByRole('link', { name: /Skills/ })).getByText('18')).toBeInTheDocument()
  })

  it('renders a pending marker with an accessible name, not colour alone', () => {
    renderNav('/settings/general', { mcp: { value: '2', pending: true } })
    expect(within(screen.getByRole('link', { name: /MCP/ })).getByLabelText('Aguardando sua ação')).toBeInTheDocument()
  })

  it('renders no badge element when the value is unknown', () => {
    renderNav('/settings/general')
    expect(within(screen.getByRole('link', { name: /Skills/ })).queryByTestId('settings-nav-badge')).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- SettingsNav`
Expected: FAIL — componente inexistente.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/features/settings/SettingsNav.tsx
import { NavLink, useLocation } from 'react-router-dom'
import { SETTINGS_GROUPS, findSettingsItem, type SettingsBadge } from './sections'

export type BadgeState = { value: string; pending?: boolean }
export type BadgeMap = Partial<Record<SettingsBadge, BadgeState>>

/**
 * The index of the room. It carries state, not only links: the mono value on
 * the right is the real count, and the single violet dot is the only violet in
 * this column — it means something is waiting for the user.
 */
export function SettingsNav({ badges }: { badges: BadgeMap }) {
  const current = findSettingsItem(useLocation().pathname)
  return (
    <nav className="settings-nav" aria-label="Settings">
      {SETTINGS_GROUPS.map((group) => (
        <div className="settings-nav__group" key={group.title}>
          <p className="settings-nav__group-title">{group.title}</p>
          {group.items.map((item) => {
            const badge = item.badge ? badges[item.badge] : undefined
            return (
              <NavLink
                key={item.id}
                to={item.path}
                className={current?.id === item.id ? 'settings-nav__item is-active' : 'settings-nav__item'}
                aria-current={current?.id === item.id ? 'page' : undefined}
              >
                <span className="settings-nav__label">{item.label}</span>
                {badge && (
                  <span className="settings-nav__badge" data-testid="settings-nav-badge">
                    {badge.pending && <span className="settings-nav__pending" role="img" aria-label="Aguardando sua ação" />}
                    {badge.value}
                  </span>
                )}
              </NavLink>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- SettingsNav`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/settings/SettingsNav.tsx frontend/tests/unit/SettingsNav.test.tsx
git commit -m "feat(settings): add the grouped settings sidebar with live status"
```

---

### Task 3: Shell e cabeçalho de seção

**Files:**
- Create: `frontend/src/features/settings/SettingsShell.tsx`, `frontend/src/features/settings/SettingsSection.tsx`
- Test: `frontend/tests/unit/SettingsShell.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/SettingsShell.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { SettingsShell } from '../../src/features/settings/SettingsShell'
import { SettingsSection } from '../../src/features/settings/SettingsSection'

function renderShell(pathname = '/settings/general', children = <p>conteúdo</p>, drawer?: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <SettingsShell badges={{}} drawer={drawer}>{children}</SettingsShell>
    </MemoryRouter>,
  )
}

describe('SettingsShell', () => {
  it('renders one main landmark with the sidebar and the content', () => {
    renderShell()
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText('conteúdo')).toBeInTheDocument()
  })

  it('offers a way back to the chat', () => {
    renderShell()
    expect(screen.getByRole('link', { name: /Voltar ao chat/ })).toHaveAttribute('href', '/')
  })

  it('renders the drawer slot only when a drawer is given', () => {
    const { rerender } = renderShell()
    expect(screen.queryByTestId('settings-drawer-slot')).toBeNull()
    rerender(
      <MemoryRouter initialEntries={['/settings/providers/openai']}>
        <SettingsShell badges={{}} drawer={<p>detalhe</p>}><p>conteúdo</p></SettingsShell>
      </MemoryRouter>,
    )
    expect(screen.getByText('detalhe')).toBeInTheDocument()
  })
})

describe('SettingsSection', () => {
  it('renders the standard header for the current route', () => {
    render(
      <MemoryRouter initialEntries={['/settings/providers']}>
        <SettingsSection eyebrow="PROVIDERS / CONEXÕES"><p>corpo</p></SettingsSection>
      </MemoryRouter>,
    )
    expect(screen.getByText('PROVIDERS / CONEXÕES')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Providers' })).toBeInTheDocument()
    expect(screen.getByText(/Configure ou revogue/)).toBeInTheDocument()
  })

  it('lets a caller override the title and the lede', () => {
    render(
      <MemoryRouter initialEntries={['/settings/providers']}>
        <SettingsSection eyebrow="X" title="Outro" lede="Outra descrição."><p>corpo</p></SettingsSection>
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Outro' })).toBeInTheDocument()
    expect(screen.getByText('Outra descrição.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- SettingsShell`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/features/settings/SettingsShell.tsx
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Brand } from '../../components/Brand'
import { SettingsNav, type BadgeMap } from './SettingsNav'

/**
 * Every settings route renders inside this shell. A section is content, never
 * a page with its own top bar: that is what makes moving between them feel like
 * one room instead of five applications.
 */
export function SettingsShell({ badges, drawer, children }: { badges: BadgeMap; drawer?: ReactNode; children: ReactNode }) {
  return (
    <main className="settings-shell">
      <header className="settings-shell__bar">
        <Brand to="/" />
        <span>Settings</span>
        <Link to="/">Voltar ao chat</Link>
      </header>
      <div className="settings-shell__body">
        <SettingsNav badges={badges} />
        <section className="settings-content">
          {children}
          {drawer && <div className="settings-content__drawer" data-testid="settings-drawer-slot">{drawer}</div>}
        </section>
      </div>
    </main>
  )
}
```

```tsx
// frontend/src/features/settings/SettingsSection.tsx
import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { findSettingsItem } from './sections'

/** The one header shape every section uses: eyebrow, title, lede, then body. */
export function SettingsSection({ eyebrow, title, lede, actions, children }: {
  eyebrow: string
  title?: string
  lede?: string
  actions?: ReactNode
  children: ReactNode
}) {
  const item = findSettingsItem(useLocation().pathname)
  return (
    <>
      <div className="settings-section__head">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title ?? item?.label ?? 'Settings'}</h1>
          <p className="settings-content__lede">{lede ?? item?.lede ?? ''}</p>
        </div>
        {actions && <div className="settings-section__actions">{actions}</div>}
      </div>
      {children}
    </>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- SettingsShell`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/settings/SettingsShell.tsx frontend/src/features/settings/SettingsSection.tsx frontend/tests/unit/SettingsShell.test.tsx
git commit -m "feat(settings): add the shared settings shell and section header"
```

---

### Task 4: Gaveta de detalhe

**Files:**
- Create: `frontend/src/features/settings/SettingsDrawer.tsx`
- Test: `frontend/tests/unit/SettingsDrawer.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/SettingsDrawer.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { SettingsDrawer } from '../../src/features/settings/SettingsDrawer'

describe('SettingsDrawer', () => {
  it('renders as a labelled region, not a modal dialog', () => {
    render(<SettingsDrawer title="OpenAI" onClose={() => {}}><p>corpo</p></SettingsDrawer>)
    const region = screen.getByRole('region', { name: 'OpenAI' })
    expect(region).toBeInTheDocument()
    expect(region).not.toHaveAttribute('aria-modal', 'true')
  })

  it('moves focus into the drawer when it opens', async () => {
    render(<SettingsDrawer title="OpenAI" onClose={() => {}}><button type="button">Salvar</button></SettingsDrawer>)
    expect(screen.getByRole('region', { name: 'OpenAI' })).toHaveFocus()
  })

  it('closes on Escape', async () => {
    const onClose = vi.fn()
    render(<SettingsDrawer title="OpenAI" onClose={onClose}><p>corpo</p></SettingsDrawer>)
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('closes from the back control', async () => {
    const onClose = vi.fn()
    render(<SettingsDrawer title="OpenAI" onClose={onClose}><p>corpo</p></SettingsDrawer>)
    await userEvent.click(screen.getByRole('button', { name: /Fechar/ }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('returns focus to the element that was focused before it opened', async () => {
    const opener = document.createElement('button')
    opener.textContent = 'abrir'
    document.body.append(opener)
    opener.focus()
    const { unmount } = render(<SettingsDrawer title="OpenAI" onClose={() => {}}><p>corpo</p></SettingsDrawer>)
    unmount()
    expect(opener).toHaveFocus()
    opener.remove()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- SettingsDrawer`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/features/settings/SettingsDrawer.tsx
import { useEffect, useRef, type ReactNode } from 'react'

/**
 * A panel, deliberately not a modal: the grid behind it stays visible and
 * readable, so opening a provider never costs the user their place. Escape and
 * the back control close it, and focus goes back where it came from.
 */
export function SettingsDrawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null)
  const restoreTo = useRef<HTMLElement | null>(null)

  useEffect(() => {
    restoreTo.current = document.activeElement as HTMLElement | null
    panel.current?.focus()
    return () => restoreTo.current?.focus?.()
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div className="settings-drawer" role="region" aria-label={title} tabIndex={-1} ref={panel}>
      <div className="settings-drawer__head">
        <h2>{title}</h2>
        <button type="button" className="button--quiet" onClick={onClose}>Fechar</button>
      </div>
      <div className="settings-drawer__body">{children}</div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- SettingsDrawer`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/settings/SettingsDrawer.tsx frontend/tests/unit/SettingsDrawer.test.tsx
git commit -m "feat(settings): add the detail drawer with keyboard and focus handling"
```

---

### Task 5: Marca de cada provider

**Files:**
- Create: `frontend/src/features/providers/providerBrand.ts`, `frontend/src/assets/providers/{openai,anthropic,openrouter,omniroute,ollama}.svg`
- Test: `frontend/tests/unit/providerBrand.test.ts`

> **Sobre as marcas:** os SVGs entregues por esta task são **marcas geométricas monocromáticas** desenhadas para o Orin, uma por provider, cada uma com sua cor de acento. Elas usam `currentColor` para o traço, então funcionam no tema escuro sem retoque, e são inlined em build (nenhuma requisição de rede, coerente com um app local). Se você quiser usar o logo oficial de um provider, basta substituir o arquivo em `frontend/src/assets/providers/<name>.svg` mantendo o `viewBox="0 0 24 24"` — nada em código muda.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/tests/unit/providerBrand.test.ts
import { describe, expect, it } from 'vitest'
import { PROVIDER_NAMES } from '../../src/api/providers'
import { providerBrand } from '../../src/features/providers/providerBrand'

describe('providerBrand', () => {
  it('gives every known provider a label, an accent and a mark', () => {
    for (const provider of PROVIDER_NAMES) {
      const brand = providerBrand(provider)
      expect(brand.label.length).toBeGreaterThan(0)
      expect(brand.accent).toMatch(/^#[0-9a-f]{6}$/i)
      expect(brand.mark).toContain('viewBox="0 0 24 24"')
    }
  })

  it('uses a distinct accent per provider so cards are told apart at a glance', () => {
    const accents = PROVIDER_NAMES.map((provider) => providerBrand(provider).accent)
    expect(new Set(accents).size).toBe(accents.length)
  })

  it('never embeds a remote reference in a mark', () => {
    for (const provider of PROVIDER_NAMES) {
      expect(providerBrand(provider).mark).not.toMatch(/https?:\/\//)
    }
  })

  it('falls back to a neutral brand for an unknown provider', () => {
    const brand = providerBrand('something-else' as never)
    expect(brand.label).toBe('something-else')
    expect(brand.mark).toContain('viewBox="0 0 24 24"')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- providerBrand`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Cada arquivo em `frontend/src/assets/providers/` segue este formato — traço em `currentColor`, sem `fill` fixo, sem referência externa:

```svg
<!-- frontend/src/assets/providers/openai.svg -->
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <circle cx="12" cy="12" r="8" />
  <path d="M12 4v16" />
  <path d="M4.8 8h14.4" />
</svg>
```

Desenhe as cinco marcas com formas distintas o bastante para serem reconhecidas em 28px: `openai` círculo dividido, `anthropic` asterisco de três traços, `openrouter` losango com dois nós, `omniroute` quadrado com trilho interno, `ollama` círculo cheio com órbita. Nenhuma passa de ~6 elementos.

```ts
// frontend/src/features/providers/providerBrand.ts
import anthropicMark from '../../assets/providers/anthropic.svg?raw'
import ollamaMark from '../../assets/providers/ollama.svg?raw'
import omnirouteMark from '../../assets/providers/omniroute.svg?raw'
import openaiMark from '../../assets/providers/openai.svg?raw'
import openrouterMark from '../../assets/providers/openrouter.svg?raw'
import type { ProviderName } from '../../api/providers'

const FALLBACK_MARK = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="4" /></svg>'

export type ProviderBrand = { label: string; accent: string; mark: string }

/**
 * Marks are inlined at build time: a local-first app must not fetch a logo.
 * The accent is used only on the mark itself — the card stays graphite, so the
 * product's single violet signal keeps meaning "the agent" and "primary action".
 */
const BRANDS: Record<ProviderName, ProviderBrand> = {
  openai: { label: 'OpenAI', accent: '#74d3b0', mark: openaiMark },
  anthropic: { label: 'Anthropic', accent: '#e8a06a', mark: anthropicMark },
  openrouter: { label: 'OpenRouter', accent: '#8fb8f0', mark: openrouterMark },
  omniroute: { label: 'OmniRoute', accent: '#c79cf5', mark: omnirouteMark },
  ollama: { label: 'Ollama', accent: '#e6e2f0', mark: ollamaMark },
}

export function providerBrand(provider: ProviderName): ProviderBrand {
  return BRANDS[provider] ?? { label: String(provider), accent: '#9a94ad', mark: FALLBACK_MARK }
}
```

Se o TypeScript reclamar do sufixo `?raw`, adicione `/// <reference types="vite/client" />` ao `frontend/src/vite-env.d.ts` (crie o arquivo se não existir).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- providerBrand`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/providers/providerBrand.ts frontend/src/assets/providers frontend/tests/unit/providerBrand.test.ts
git commit -m "feat(settings): add offline provider marks and accents"
```

---

### Task 6: Card e grade de providers

**Files:**
- Create: `frontend/src/features/providers/ProviderCard.tsx`, `frontend/src/features/providers/ProviderGrid.tsx`
- Test: `frontend/tests/unit/ProviderGrid.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/ProviderGrid.test.tsx
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ProviderGrid } from '../../src/features/providers/ProviderGrid'

const states = {
  openai: { status: 'configured' as const, detail: '42 modelos' },
  anthropic: { status: 'configured' as const, detail: '9 modelos' },
  openrouter: { status: 'unconfigured' as const, detail: '' },
  omniroute: { status: 'configured' as const, detail: 'gateway local' },
  ollama: { status: 'unavailable' as const, detail: 'não responde' },
}

function renderGrid(pathname = '/settings/providers') {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <ProviderGrid states={states} />
    </MemoryRouter>,
  )
}

describe('ProviderGrid', () => {
  it('renders one card per known provider', () => {
    renderGrid()
    expect(screen.getAllByRole('link')).toHaveLength(5)
  })

  it('shows the provider name and its status detail on the card', () => {
    renderGrid()
    const card = screen.getByRole('link', { name: /OpenAI/ })
    expect(within(card).getByText('42 modelos')).toBeInTheDocument()
  })

  it('links each card to its own detail route', () => {
    renderGrid()
    expect(screen.getByRole('link', { name: /Anthropic/ })).toHaveAttribute('href', '/settings/providers/anthropic')
  })

  it('states unconfigured in words, not only by colour', () => {
    renderGrid()
    expect(within(screen.getByRole('link', { name: /OpenRouter/ })).getByText('Não configurado')).toBeInTheDocument()
  })

  it('marks the open provider as current', () => {
    renderGrid('/settings/providers/openai')
    expect(screen.getByRole('link', { name: /OpenAI/ })).toHaveAttribute('aria-current', 'true')
  })

  it('gives each card an animation index for the staggered reveal', () => {
    renderGrid()
    expect(screen.getByRole('link', { name: /OpenAI/ })).toHaveStyle({ '--card-index': '0' })
    expect(screen.getByRole('link', { name: /Anthropic/ })).toHaveStyle({ '--card-index': '1' })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- ProviderGrid`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/features/providers/ProviderCard.tsx
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import type { ProviderName } from '../../api/providers'
import { providerBrand } from './providerBrand'

export type ProviderCardState = { status: 'configured' | 'unconfigured' | 'unavailable'; detail: string }

const STATUS_LABEL: Record<ProviderCardState['status'], string> = {
  configured: 'Configurado',
  unconfigured: 'Não configurado',
  unavailable: 'Indisponível',
}

export function ProviderCard({ provider, state, index, current }: {
  provider: ProviderName
  state: ProviderCardState
  index: number
  current: boolean
}) {
  const brand = providerBrand(provider)
  return (
    <Link
      to={`/settings/providers/${provider}`}
      className={`provider-card is-${state.status}`}
      style={{ '--card-index': index, '--card-accent': brand.accent } as CSSProperties}
      aria-current={current ? 'true' : undefined}
    >
      {/* The mark is a build-time import from this bundle, never network or
          user data — that is what makes this innerHTML safe. */}
      <span className="provider-card__mark" aria-hidden="true" dangerouslySetInnerHTML={{ __html: brand.mark }} />
      <strong className="provider-card__name">{brand.label}</strong>
      <span className="provider-card__status">{state.detail || STATUS_LABEL[state.status]}</span>
    </Link>
  )
}
```

`ProviderGrid.tsx` lê `useLocation()` para saber qual provider está aberto e mapeia `PROVIDER_NAMES` em cards.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- ProviderGrid`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/providers/ProviderCard.tsx frontend/src/features/providers/ProviderGrid.tsx frontend/tests/unit/ProviderGrid.test.tsx
git commit -m "feat(settings): show providers as a card grid"
```

---

### Task 7: Extrair o estado de um provider para um hook

`ProviderPanel` (`ProviderSettingsPage.tsx:59-373`) mistura carregamento, ações, formulário e três formas específicas. Esta task tira o estado do JSX sem mudar comportamento.

**Files:**
- Create: `frontend/src/features/providers/useProviderState.ts`
- Test: `frontend/tests/unit/useProviderState.test.ts`

- [ ] **Step 1: Write the failing test**

Cobertura, usando `renderHook` do Testing Library com um `ApiClient` falso:

```
- começa em loading e vai para loaded com o estado público
- um 404 vira loaded com enabled=null, não um erro de infraestrutura
- qualquer outro erro vira unavailable com o ApiError preservado
- configure envia a chave e limpa o campo depois de aceito
- configure reutiliza o mesmo Idempotency-Key enquanto a intenção não foi aceita
- revoke limpa o estado e o campo de chave
- refreshModels atualiza a contagem e propaga o erro sem derrubar o estado carregado
- abortar a montagem cancela a requisição sem setar estado
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- useProviderState`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Mova o corpo de estado de `ProviderPanel` para `useProviderState(client, provider)`, devolvendo `{ load, action, apiKey, setApiKey, baseUrl, setBaseUrl, enabled, setEnabled, models, catalog, configure, revoke, refreshModels, setFavorite }`. Nenhuma lógica nova — é movimentação com testes cobrindo o comportamento que já existia.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- useProviderState ProviderSettingsPage`
Expected: PASS — os testes existentes de `ProviderSettingsPage` continuam verdes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/providers/useProviderState.ts frontend/tests/unit/useProviderState.test.ts
git commit -m "refactor(settings): extract provider state into a hook"
```

---

### Task 8: Detalhe do provider na gaveta

**Files:**
- Create: `frontend/src/features/providers/ProviderDetail.tsx`, `OmniRouteSetup.tsx`, `OllamaSetup.tsx`
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx` → renomear para `ProvidersSection.tsx`
- Test: `frontend/tests/unit/ProviderDetail.test.tsx`

- [ ] **Step 1: Write the failing test**

```
- renderiza o nome e a marca do provider no topo
- o campo de chave é write-only: nunca preenchido a partir de uma resposta
- salvar chama configureProvider e mostra a confirmação
- revogar pede confirmação e depois chama revokeProvider
- um provider já configurado mostra a contagem de modelos e o botão de atualizar catálogo
- omniroute renderiza o OmniRouteSetup, e nenhum outro provider renderiza
- ollama renderiza o OllamaSetup com o seletor local/cloud
- um erro de ação aparece como alert sem apagar o formulário
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- ProviderDetail`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`ProviderDetail` consome `useProviderState` e renderiza o formulário comum; `OmniRouteSetup` e `OllamaSetup` saem inteiros de `ProviderSettingsPage.tsx` (linhas 420-612) para arquivos próprios com as mesmas props. `ProvidersSection.tsx` passa a ser apenas:

```tsx
export function ProvidersSection({ client = createBrowserApiClient() }: { client?: ApiClient }) {
  const states = useProviderCardStates(client)
  return (
    <SettingsSection eyebrow="PROVIDERS / CONEXÕES">
      <ProviderGrid states={states} />
      <VisionModelSetting client={client} />
    </SettingsSection>
  )
}
```

Alvo: nenhum dos arquivos novos passa de 220 linhas.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- Provider`
Expected: PASS em todos os testes de provider.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/providers frontend/tests/unit/ProviderDetail.test.tsx
git commit -m "refactor(settings): split the provider page into grid, detail and setups"
```

---

### Task 9: Skills e Memory dentro do shell

**Files:**
- Create: `frontend/src/features/skills/SkillsSection.tsx`, `SkillRow.tsx`, `AgentSkillsPanel.tsx`
- Modify: `frontend/src/features/skills/SkillsPage.tsx`, `frontend/src/features/memory/MemoryPage.tsx`
- Test: `frontend/tests/unit/SkillsSection.test.tsx`

- [ ] **Step 1: Write the failing test**

```
- SkillsSection renderiza dentro do SettingsShell, sem topbar própria
- a busca filtra a lista e mantém o foco no campo
- os filtros de origem incluem builtin, custom e plugin
- clicar numa skill abre a gaveta em /settings/skills/:skillId
- a gaveta mostra instruções, versões e os agentes que usam a skill
- AgentSkillsPanel continua salvando modo auto/pinned
- MemoryPage renderiza dentro do shell com o cabeçalho padrão
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- SkillsSection`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Quebre `SkillsPage.tsx` (356 linhas) em `SkillsSection` (dados + layout), `SkillRow` (uma linha) e `AgentSkillsPanel` (já é uma função separada hoje, linha 128). `MemoryPage` troca o `SettingsPage` atual pelo `SettingsShell` + `SettingsSection`, e a linha única gigante de JSX (`MemoryPage.tsx:14`) é quebrada em elementos legíveis.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- SkillsSection SkillsPage`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/skills frontend/src/features/memory frontend/tests/unit/SkillsSection.test.tsx
git commit -m "refactor(settings): move skills and memory into the settings shell"
```

---

### Task 10: General, Workspace, Agendamentos, Leitura visual e Sobre

**Files:**
- Modify: `frontend/src/features/settings/RuntimeSettingsPage.tsx`, `frontend/src/features/schedules/SchedulesPage.tsx`, `frontend/src/features/providers/VisionModelSetting.tsx`
- Create: `frontend/src/features/settings/AboutSection.tsx`, `frontend/src/features/settings/WorkspaceSection.tsx`
- Test: `frontend/tests/unit/settingsSectionsRender.test.tsx`

- [ ] **Step 1: Write the failing test**

```
- cada rota declarada em SETTINGS_GROUPS renderiza sem lançar
- cada rota renderiza exatamente um <h1> e uma navegação "Settings"
- nenhuma rota de settings renderiza a classe app-shell
- /settings/omniroute redireciona para /settings/providers/omniroute
- /settings/agents redireciona para /settings/general
- /providers, /skills e /schedules redirecionam para o equivalente em /settings
```

O primeiro caso é um teste tabular sobre `settingsItems()` — ele quebra automaticamente se alguém adicionar uma seção sem rota.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- settingsSectionsRender`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

- `RuntimeSettingsPage` perde o bloco de instalação/versão (vai para `AboutSection`) e passa a usar `SettingsSection`.
- `SchedulesPage` ganha uma variante de seção; a rota raiz `/schedules` vira `<Navigate to="/settings/schedules" replace />`.
- `VisionModelSetting` ganha sua própria rota `/settings/vision` além de continuar aparecendo abaixo da grade de providers.
- `WorkspaceSection` explica onde ficam os workspaces e lista as raízes configuradas — hoje esta rota é um placeholder vazio.
- `SettingsPage.tsx` vira `<Navigate to="/settings/general" replace />`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test`
Expected: toda a suíte verde.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/settings frontend/src/features/schedules frontend/src/features/providers/VisionModelSetting.tsx frontend/tests/unit/settingsSectionsRender.test.tsx
git commit -m "refactor(settings): bring every settings surface into one flow"
```

---

### Task 11: Rotas e paleta de comandos

**Files:**
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/components/CommandPalette.tsx`
- Test: `frontend/tests/unit/CommandPalette.test.tsx` (estender)

- [ ] **Step 1: Write the failing test**

```
- a tabela de rotas cobre toda entrada de settingsItems()
- /settings/providers/:provider renderiza a grade com a gaveta aberta
- /settings/skills/:skillId renderiza a lista com a gaveta aberta
- a paleta de comandos lista toda seção declarada em SETTINGS_GROUPS
- escolher uma seção na paleta navega para a rota correspondente
- a paleta não lista mais rotas de settings que já não existem
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- CommandPalette`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Gere as rotas de settings a partir de `settingsItems()` em vez de escrevê-las à mão, e faça a `CommandPalette` ler a mesma lista. Uma seção nova passa a aparecer nas três superfícies sem edição adicional.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test && npm --prefix frontend run lint && npm --prefix frontend run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/routes.tsx frontend/src/components/CommandPalette.tsx frontend/tests/unit/CommandPalette.test.tsx
git commit -m "refactor(settings): derive routes and the palette from one section list"
```

---

### Task 12: CSS do shell, da grade e da gaveta

**Files:**
- Modify: `frontend/src/styles/agentos.css:32-96`
- Test: `frontend/tests/unit/settingsStyles.test.ts`

- [ ] **Step 1: Write the failing test**

Siga o padrão de `frontend/tests/unit/scrollbarStyles.test.ts`, que lê o CSS como texto e afirma sobre ele:

```
- .settings-shell__body usa a barra lateral de 216px
- .provider-grid usa auto-fill com minmax de 200px
- .provider-card não usa a cor de sinal violeta no fundo nem na borda
- .settings-nav__pending é o único seletor da barra lateral que usa --signal
- a revelação escalonada dos cards está dentro de um bloco prefers-reduced-motion: no-preference
- existe um breakpoint abaixo de 900px que empilha a barra lateral
- a gaveta tem uma variante de largura total abaixo de 900px
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- settingsStyles`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Substitua o bloco `settings-*` atual por:

```css
/* Settings: index on the left, room on the right, drawer over the room.
   The sidebar carries state, so it stays graphite: the single violet in this
   column is the pending dot, and that is what makes it readable as urgent. */
.settings-shell { min-height: 100vh; background: var(--ink); color: var(--text); }
.settings-shell__bar { display: flex; align-items: center; gap: 18px; height: 58px; padding: 0 28px; border-bottom: 1px solid var(--line); font-size: 13px; }
.settings-shell__bar > span { flex: 1; color: var(--muted); }
.settings-shell__bar > a:last-child { color: var(--muted); }
.settings-shell__bar > a:last-child:hover { color: var(--text); }
.settings-shell__body { display: grid; grid-template-columns: 216px minmax(0, 1fr); max-width: 1180px; margin: 0 auto; min-height: calc(100vh - 58px); }

.settings-nav { padding: 26px 14px; border-right: 1px solid var(--line); display: grid; align-content: start; gap: 22px; }
.settings-nav__group { display: grid; gap: 2px; }
.settings-nav__group-title { margin: 0 0 6px 11px; color: var(--faint); font: 9px var(--mono); letter-spacing: .16em; text-transform: uppercase; }
.settings-nav__item { display: flex; align-items: center; gap: 8px; padding: 8px 11px; border-radius: 8px; color: var(--muted); font-size: 13px; transition: background .18s var(--ease), color .18s var(--ease); }
.settings-nav__item:hover { background: var(--raised); color: var(--text); }
.settings-nav__item.is-active { background: var(--raised-strong); color: var(--text); }
.settings-nav__label { flex: 1; min-width: 0; }
.settings-nav__badge { display: inline-flex; align-items: center; gap: 6px; color: var(--faint); font: 10px var(--mono); }
.settings-nav__pending { width: 5px; height: 5px; border-radius: 50%; background: var(--signal); box-shadow: 0 0 8px var(--signal-dim); }

.settings-content { position: relative; width: 100%; max-width: 860px; padding: 52px 48px 72px; }
.settings-section__head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 34px; }
.settings-content h1 { margin: 0 0 10px; font-size: 30px; letter-spacing: -.035em; }
.settings-content__lede { margin: 0; max-width: 62ch; color: var(--muted); line-height: 1.6; }

.provider-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; }
.provider-card { display: grid; gap: 9px; padding: 20px 18px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--raised); transition: border-color .2s var(--ease), transform .2s var(--ease), background .2s var(--ease); }
.provider-card:hover { border-color: var(--line-strong); background: var(--raised-strong); transform: translateY(-2px); }
.provider-card[aria-current="true"] { border-color: var(--card-accent); }
.provider-card__mark { display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--line); border-radius: 10px; color: var(--card-accent); }
.provider-card__mark svg { width: 20px; height: 20px; }
.provider-card__name { font-size: 14px; font-weight: 560; letter-spacing: -.01em; }
.provider-card__status { color: var(--faint); font: 10px var(--mono); letter-spacing: .04em; }
.provider-card.is-unconfigured .provider-card__mark { color: var(--faint); }
.provider-card.is-unavailable .provider-card__status { color: var(--warn); }

.settings-drawer { position: sticky; top: 24px; display: grid; align-content: start; gap: 18px; margin-top: 22px; padding: 22px; border: 1px solid var(--line-strong); border-radius: var(--radius); background: linear-gradient(150deg, var(--raised-strong), rgb(var(--orin-surface-rgb) / .9)); box-shadow: 0 24px 60px rgb(var(--orin-ink-rgb) / .55); }
.settings-drawer:focus-visible { outline: 2px solid var(--signal); outline-offset: 3px; }
.settings-drawer__head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.settings-drawer__head h2 { margin: 0; font-size: 18px; letter-spacing: -.02em; }
.settings-drawer__body { display: grid; gap: 16px; }

@media (prefers-reduced-motion: no-preference) {
  .provider-card { animation: settings-card-in .34s var(--ease) both; animation-delay: calc(var(--card-index, 0) * 40ms); }
  .settings-drawer { animation: settings-drawer-in .22s var(--ease) both; }
  @keyframes settings-card-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  @keyframes settings-drawer-in { from { opacity: 0; transform: translateX(14px); } to { opacity: 1; transform: none; } }
}

@media (max-width: 900px) {
  .settings-shell__body { grid-template-columns: 1fr; }
  .settings-nav { grid-auto-flow: column; grid-auto-columns: max-content; gap: 14px; overflow-x: auto; padding: 12px 16px; border-right: 0; border-bottom: 1px solid var(--line); }
  .settings-nav__group { grid-auto-flow: column; align-items: center; }
  .settings-nav__group-title { margin: 0 4px 0 0; }
  .settings-content { padding: 32px 20px 56px; }
  .settings-drawer { position: static; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- settingsStyles && npm --prefix frontend run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles/agentos.css frontend/tests/unit/settingsStyles.test.ts
git commit -m "feat(settings): style the shell, the provider grid and the drawer"
```

---

### Task 13: Acessibilidade

**Files:**
- Create: `frontend/tests/e2e/settings-a11y.spec.ts`

- [ ] **Step 1: Write the failing test**

Use `@axe-core/playwright`, que já é dependência. Cobertura:

```
- axe não reporta violação em /settings/general, /providers, /skills, /mcp e /plugins
- Tab a partir da topbar chega à barra lateral e depois ao conteúdo, nessa ordem
- abrir um provider pelo teclado move o foco para a gaveta
- Escape fecha a gaveta e devolve o foco ao card
- o texto do card tem contraste ≥ 4.5:1 contra o fundo do card
- a marca do provider é aria-hidden e o nome acessível vem do texto
- em 375px de largura a barra lateral continua alcançável e nada fica cortado
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test:e2e -- settings-a11y`
Expected: FAIL nos itens que ainda não foram tratados.

- [ ] **Step 3: Write minimal implementation**

Corrija o que o axe apontar. Ajustes previsíveis: `--faint` (`rgba(245,241,255,.34)`) **não** passa em 4.5:1 sobre `--raised` — o status do card precisa subir para `--muted` ou para um token novo `--mono-readable` com opacidade ≥ .62. Faça o ajuste no token, não caso a caso.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test:e2e -- settings-a11y`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/settings-a11y.spec.ts frontend/src/styles frontend/src/features/settings
git commit -m "test(settings): hold the settings shell to WCAG 2.1 AA"
```

---

### Task 14: Regressão visual

**Files:**
- Create: `frontend/tests/visual/settings.spec.ts`

- [ ] **Step 1: Write the failing test**

Snapshots, com o backend mockado no mesmo padrão dos specs visuais existentes:

```
- settings-general
- settings-providers-grid
- settings-providers-drawer-open
- settings-skills
- settings-mcp
- settings-plugins
- settings-providers-narrow (375px)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test:visual -- settings`
Expected: FAIL — sem baseline.

- [ ] **Step 3: Gravar as baselines**

Run: `npm --prefix frontend run test:visual -- settings --update-snapshots`

Olhe cada imagem antes de commitar. Uma baseline errada trava um bug visual no lugar.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test:visual -- settings`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/visual/settings.spec.ts frontend/tests/visual/settings.spec.ts-snapshots
git commit -m "test(settings): record visual baselines for the settings shell"
```

---

### Task 15: Documentação

**Files:**
- Modify: `docs/frontend/SCREEN_MAP.md`, `docs/frontend/UX_UI_SPEC.md`, `docs/frontend/COMPONENT_SYSTEM.md`, `README.md`

- [ ] **Step 1: `SCREEN_MAP.md`**

Substitua as entradas de Settings/Providers/Skills pelo mapa novo: uma tela, uma barra lateral, N seções, duas rotas de gaveta.

- [ ] **Step 2: `UX_UI_SPEC.md`**

Adicione "Settings: índice, sala e gaveta" com as três decisões de caráter (status em mono, ponto violeta de pendência, revelação escalonada) e a regra que as sustenta: violeta continua racionado.

- [ ] **Step 3: `COMPONENT_SYSTEM.md`**

Documente `SettingsShell`, `SettingsNav`, `SettingsSection`, `SettingsDrawer` e `ProviderCard`, com props e quando usar cada um. Registre a regra: **uma seção de settings nunca renderiza `app-shell` nem `topbar` própria.**

- [ ] **Step 4: `README.md`**

Atualize "Configure a provider" para o caminho novo (Settings → Providers → card do provider).

- [ ] **Step 5: Commit**

```bash
git add docs/frontend README.md
git commit -m "docs(settings): document the unified settings surface"
```

---

## Verificação final

```bash
npm --prefix frontend test && npm --prefix frontend run lint && npm --prefix frontend run build
```

```bash
npm --prefix frontend run test:e2e
```

Com o Orin rodando, verifique à mão o que teste não pega: entre em Settings, percorra as dez seções pela barra lateral e confirme que o cabeçalho, a largura da coluna e o espaçamento não mudam entre elas. Abra e feche três providers seguidos — a grade não deve piscar nem reposicionar.

## Relação com os outros planos

As seções **MCP** e **Plugins** aparecem em `sections.ts` desde a Task 1 deste plano, mas o conteúdo delas vem dos outros dois planos (`2026-08-14-mcp-connectors.md` Task 16, `2026-08-14-plugins.md` Task 15). Enquanto eles não existem, essas rotas renderizam um `SettingsSection` com um estado vazio explicando o que virá — não um placeholder genérico. Isso mantém os três planos executáveis em qualquer ordem.

## Follow-ups deliberadamente fora deste plano

1. **Busca dentro de Settings** (`Ctrl+F` na tela) — a paleta de comandos já cobre navegação entre seções; buscar dentro de um campo é outro trabalho.
2. **Tema claro** — o produto é dark-only hoje; os tokens já estão em `theme.css` para quando isso mudar.
3. **Configurações por projeto** — hoje memória de projeto vive no projeto. Trazer isso para dentro do shell exige decidir escopo, o que é uma discussão de produto, não de layout.
