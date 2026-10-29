# Especificação de Design (Spec) — Fase 1: Design System, Mascote `OrinMascot` e Shell Global Unificado

> [!IMPORTANT]
> **Fluxo Obrigatório (`superpowers`):** Esta é a **Etapa 1 (Spec)** da **Fase 1**. Nenhum código será alterado antes da sua aprovação explícita desta especificação. Após aprovada, criaremos o Plano de Implementação (`writing-plans`) da Fase 1.

---

## 1. Objetivo da Fase 1

Construir a fundação visual e estrutural de toda a nova interface do Orin conforme as referências (`orin.svg` e estrutura global de `ref-01.png` a `ref-16.png`), garantindo:
1. **Tema Padrão idêntico às referências (*Orin Dark + Lavanda `#B4A4EF`*)** + **Catálogo de Temas e Acentos Personalizados** para o usuário escolher (em `Configurações / Aparência` e `Seu Friend`).
2. **Componente vetorial dinâmico [`OrinMascot.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/components/OrinMascot.tsx)** a partir de [`orin.svg`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/.cache/orin-ref/orin.svg) (com variantes `flat`, `soft3d`, `waving` e indicador `statusDot="online"`) e atualização do [`Brand.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/components/Brand.tsx) (`orin` em minúsculas).
3. **Unificação do [`WorkspaceShell.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/app/WorkspaceShell.tsx) e [`WorkspaceNavigation.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/WorkspaceNavigation.tsx)** com a nova hierarquia de navegação lateral, topbar de `56px` (com botão `"Conversar por voz"` e avatar do Orin) e perfil local/autenticado sempre visível no rodapé.

---

## 2. Sistema de Temas: Padrão das Referências + Temas Personalizados

### 2.1. Superfícies Base (`ThemeMode` em [`ThemeContext.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/app/ThemeContext.tsx) e [`theme.css`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/styles/theme.css))
Expandiremos `ThemeMode` mantendo total retrocompatibilidade com os modos existentes e adicionando novas superfícies personalizadas:
`export type ThemeMode = 'dark' | 'carbon' | 'midnight' | 'forest' | 'light' | 'system'`

| ID (`data-theme`) | Nome na UI | Papel | Sidebar (`--orin-page-bg`) | Canvas (`--orin-surface-1`) | Card (`--orin-surface-2`) | Input (`--orin-surface-3`) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`dark`** *(Padrão)* | **Orin Escuro (Padrão)** | **Exato das referências `ref-01`..`ref-16`** | `#101014` | `#141419` | `#1A1A21` | `#1E1E26` |
| **`carbon`** | **Carbon OLED** | Preto puro de alto contraste | `#050506` | `#0B0B0D` | `#121215` | `#18181C` |
| **`midnight`** *(Novo)* | **Noite Profunda** | Azul-marinho espacial sereno | `#0B0F17` | `#0F1521` | `#161E2E` | `#1C263B` |
| **`forest`** *(Novo)* | **Floresta Noturna** | Grafite esverdeado orgânico | `#0C1210` | `#101815` | `#16211D` | `#1C2B25` |
| **`light`** | **Papel Quente (Claro)** | Editorial claro confortável | `#F2F0EC` | `#FAF9F6` | `#FFFFFF` | `#FFFFFF` |
| **`system`** | **Automático (Sistema)** | Segue `prefers-color-scheme` do SO (`dark` ↔ `light`) | Dinâmico | Dinâmico | Dinâmico | Dinâmico |

### 2.2. Cores de Destaque / Acento (`ThemeAccent`)
O acento padrão `violet` passa a ser o **Lavanda Orin (`#B4A4EF`)** com texto sobre botão escuro (`--orin-accent-ink: #181523`), e adicionamos `rose` ao lado das opções existentes:
`export type ThemeAccent = 'violet' | 'sapphire' | 'emerald' | 'amber' | 'rose' | 'mono'`

| ID (`data-accent`) | Nome na UI | Cor Principal (Escuro) | Cor Principal (Claro) | Texto sobre Botão Primário (`--orin-accent-ink`) |
| :--- | :--- | :--- | :--- | :--- |
| **`violet`** *(Padrão)* | **Lavanda Orin (Padrão)** | `#B4A4EF` | `#6D28D9` | `#181523` (escuro) / `#FFFFFF` (claro) |
| **`sapphire`** | **Safira** | `#7AA2F7` | `#2563EB` | `#0F172A` / `#FFFFFF` |
| **`emerald`** | **Esmeralda** | `#4ADE80` | `#059669` | `#0D2016` / `#FFFFFF` |
| **`amber`** | **Âmbar Solar** | `#FBBF24` | `#D97706` | `#231805` / `#FFFFFF` |
| **`rose`** *(Novo)* | **Quartzo Rosa** | `#F4A2B8` | `#E11D48` | `#261118` / `#FFFFFF` |
| **`mono`** | **Monocromático** | `#E4E4E7` | `#18181B` | `#141418` / `#FFFFFF` |

> [!TIP]
> **Sincronia entre Tema e Mascote:** O componente `OrinMascot` usará `var(--orin-accent, #B4A4EF)` como cor base do corpo por padrão (com opção de override na aba `Aparência` do Friend na Fase 4), permitindo que o mascote reflita o tema escolhido pelo usuário ou permaneça no lavanda clássico.

---

## 3. Componente `OrinMascot.tsx` e `Brand.tsx`

### 3.1. `frontend/src/components/OrinMascot.tsx`
Componente React/SVG baseado fielmente na geometria de [`orin.svg`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/.cache/orin-ref/orin.svg) (`viewBox="0 0 160 160"`):
- **Props:**
  ```tsx
  export type OrinMascotProps = {
    size?: number | string
    variant?: 'flat' | 'soft3d' | 'waving'
    statusDot?: 'online' | 'busy' | 'none'
    color?: string // default: 'var(--orin-accent, #B4A4EF)'
    animated?: boolean
    className?: string
    ariaLabel?: string
  }
  ```
- **Variantes:**
  - `flat`: silhueta vetorial limpa (`orin.svg`) para logo da sidebar (`28px`), ícone do item `Friend` (`20px`) e avatares de chat (`28px`).
  - `soft3d`: iluminação volumétrica suave via `<radialGradient>` + sombra elíptica na base, usada no Hero da Home (`88px`), Palco de Voz (`112px`) e Card `Seu Friend` (`120px`).
  - `waving`: inclui o bracinho acenando e os 3 traços de brilho (`sparkles`) no topo direito (`ref-02.png`).
  - `statusDot="online"`: renderiza o ponto verde (`#4ADE80` com anel da cor do fundo) no canto superior direito, usado no avatar da Topbar (`ref-01` a `ref-16`).

### 3.2. `frontend/src/components/Brand.tsx` e `frontend/src/config/brand.ts`
- Substitui a imagem `orin-logo.png` enquadrada em borda por `<OrinMascot size={28} variant="flat" />`.
- Renderiza o wordmark `<span className="brand__word">orin</span>` em minúsculas (`font-size: 20px; font-weight: 700; letter-spacing: -0.03em; color: var(--orin-text-primary)`), mantendo `aria-label="Orin, início"` para acessibilidade e compatibilidade com testes.

---

## 4. Arquitetura do Shell Global (`WorkspaceShell.tsx` e `WorkspaceNavigation.tsx`)

### 4.1. Layout Full-Bleed e Topbar Unificada (`WorkspaceShell.tsx`)
- **Remoção do "Inset Card":** `.workspace-shell__canvas` deixa de ter `margin: 8px` e `border-radius: 18px`, ocupando 100% da altura com fundo `--orin-surface-1` (`#141419`) e separado da sidebar (`#101014`) apenas por `border-right: 1px solid var(--orin-border-subtle)`.
- **Topbar (`56px`):**
  - **Esquerda (`headerLeft`):** Título ou Breadcrumb da página atual (ex.: `"Início"`, `"Projetos"`, `"Auryly › Pesquisa de oportunidades"`).
  - **Direita (`headerRight`):** Renderiza as ações contextuais da página + o botão global **`"Conversar por voz"`** (ícone de ondas de áudio em lavanda + label, acionando o Modo Ao Vivo / Friend) + `<OrinMascot size={30} variant="flat" statusDot="online" />`.

### 4.2. Nova Hierarquia da Sidebar (`WorkspaceNavigation.tsx`)
Seguindo a estrutura idêntica de `ref-01.png` a `ref-16.png`:
1. **Header da Sidebar:** `<Brand to="/" />` à esquerda + botão discreto de recolher/expandir rail à direita (`sidebar-rail-toggle`).
2. **CTA Primário Único:** Botão de largura total **`+ Nova conversa`** (`background: var(--orin-accent)`, `color: var(--orin-accent-ink)`, `height: 40px`, `border-radius: 10px`, `font-weight: 600`, `data-testid="sidebar-new-chat"`).
3. **Navegação Principal Superior (3 itens):**
   - `🏠 Início` (`NavLink` para `/`)
   - `👾 Friend ›` (`button` / `NavLink` com mini `OrinMascot` e seta `›` à direita, `data-testid="sidebar-orin-friend-button"`)
   - `📅 Ações agendadas` (`NavLink` para `/schedules`)
4. **Abas `Conversas` e `Projetos`:**
   - Alinhadas à esquerda com indicador ativo inferior de `2px` em `var(--orin-accent)`.
   - Na aba **Conversas**: lista de conversas recentes com ícone contextual (documento, lupa ou balão), título truncado em 1 linha e tempo relativo curto à direita (`há 2h`, `há 1d`, ou dot verde se estiver rodando agora).
   - Na aba **Projetos**: lista de projetos com ícone de pasta colorido, nome do projeto e contagem à direita (`8 conversas`), mais botão `+ Novo projeto`.
5. **Rodapé Fixo da Sidebar (3 Links + Barra de Perfil):**
   - `⊞ Habilidades` (`/library/skills`)
   - `🔗 Conexões` (`/integrations/providers`)
   - `⚙ Configurações` (`/settings/general` + atalho para abrir `PreferencesModal` / temas)
   - **Barra de Perfil (`ProfileMenu.tsx`):** Sempre visível (tanto em modo `session` quanto em modo `local`, lendo o nome do usuário de `session.user?.displayName` ou `localStorage.getItem('orin.user.displayName') ?? 'Você'`), exibindo avatar circular com inicial, nome, botão rápido de tema (`theme-quick-toggle`) e menu/chevron `›`.

---

## 5. Estratégia de Testes e Segurança (TDD + Red-Team + Playwright)

1. **Testes Unitários e de Integração (Vitest — `[RED]` → `[GREEN]`):**
   - `ThemeContext.test.tsx`: validar o novo tema padrão (`#101014` / `#141419` / `#B4A4EF`), os novos modos (`midnight`, `forest`) e o novo acento (`rose`), além de fallback seguro contra valores inválidos ou falhas de `localStorage`.
   - `OrinMascot.test.tsx`: validar renderização acessível (`role="img"`, `aria-label`), variantes (`flat`, `soft3d`, `waving`), `statusDot` e respeito a `prefers-reduced-motion`.
   - `WorkspaceShell.test.tsx` e `HomeAndChatRefactored.test.tsx`: validar a nova hierarquia da sidebar (`+ Nova conversa`, `Início`, `Friend`, `Ações agendadas`, abas `Conversas`/`Projetos`, rodapé `Habilidades`/`Conexões`/`Configurações` e `ProfileMenu` visível em modo local).
2. **Verificação Visual no Navegador (Playwright):**
   - Subir o servidor de desenvolvimento local, inspecionar a Sidebar, Topbar, `Brand`, `OrinMascot` e o seletor de Temas Personalizados no navegador via Playwright e comparar lado a lado com `ref-01.png`.
