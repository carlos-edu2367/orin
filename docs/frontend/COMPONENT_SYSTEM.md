# Component System

## Primitivos

`Button`, `IconButton`, `Tooltip`, `Popover`, `Dialog`, `Sheet`, `Menu`, `CommandPalette`, `Toast`, `Skeleton`, `StatusLabel`, `Disclosure`, `CodeReference`, `ErrorNotice` e `EmptyState`. Todos devem ter foco visível, navegação por teclado, nomes acessíveis e estados pending/disabled.

## Compostos de domínio

| Componente | Contrato | Fonte |
| --- | --- | --- |
| `ExecutionShell` | `ExecutionProjection` | GET + SSE projection |
| `ExecutionControls` | estado, versão, permissões | control endpoint |
| `ActivityGroup` | activities semânticas | normalizer |
| `ToolActivityGroup` | invocation summary seguro | Tool projection futura |
| `AgentRail` | `AgentGraphProjection` compacto | delegations/messages |
| `OrchestrationScene` | graph + reduced motion | R3F, lazy |
| `InspectorSheet` | abas somente habilitadas | DTOs autorizados |
| `ProviderConnectionForm` | provider public state | provider endpoints |

## Settings

| Componente | Props principais | Quando usar |
| --- | --- | --- |
| `SettingsShell` | `badges`, `children`, `drawer?` | Layout único de toda rota `/settings/*`. |
| `SettingsNav` | `badges` | Índice agrupado; usa `aria-current="page"` e badges conhecidos. |
| `SettingsSection` | `eyebrow`, `title?`, `lede?`, `actions?`, `children` | Cabeçalho padrão de uma seção de conteúdo. |
| `SettingsDrawer` | `title`, `onClose`, `children` | Painel não-modal de detalhe; fecha com Esc e restaura foco. |
| `ProviderCard` | `provider`, `state`, `index`, `current` | Card da grade; marca local e status textual. |

Regra: uma seção de settings nunca renderiza `app-shell` nem `topbar` própria. A navegação, a paleta de comandos e a tabela de rotas leem `features/settings/sections.ts`.

Componentes recebem projections tipadas, não envelopes SSE crus. O normalizer é a única camada que conhece nomes de eventos; isso protege o design system de mudanças no backend.
