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

Componentes recebem projections tipadas, não envelopes SSE crus. O normalizer é a única camada que conhece nomes de eventos; isso protege o design system de mudanças no backend.
