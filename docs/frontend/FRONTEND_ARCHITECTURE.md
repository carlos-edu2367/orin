# Frontend Architecture

## Proposta

Adotar **React + TypeScript + Vite**, Tailwind, Motion, TanStack Query, Zustand, Three.js/R3F/Drei. Vite é a escolha inicial porque o backend é uma API independente e não apresenta requisito de SSR, SEO ou rotas server-side; Next.js só se justifica se autenticação/BFF ou SSR passarem a ser requisitos explícitos.

## Limites de estado

| Estado | Dono | Exemplos |
| --- | --- | --- |
| Server | TanStack Query | agents, executions, providers, resources autorizados. |
| Realtime/projection | Zustand + reducer puro | cursor, eventos deduplicados, activity groups, graph. |
| UI | Zustand/local React | sidebar, inspector, accordions, command palette. |
| Animation | Motion values / refs | springs, fade, pulse lifetime. |
| Scene | R3F refs/useFrame | partículas, instancing, posição interpolada. |

## Estrutura futura

```text
src/
  api/                 typed HTTP + SSE clients, errors, idempotency
  features/executions/ projection reducer, transcript, controls
  features/activities/ normalizer and semantic groups
  features/agents/     rail, graph projection, R3F scene
  features/providers/  safe configuration flow
  components/          primitives and composed disclosure controls
  stores/              UI and realtime bindings
  routes/              home, execution, settings, inspector views
```

## Performance

Lazy-load R3F; render 2D first. Adaptar DPR, usar geometries/materials compartilhados e instancing para pulsos; `useFrame` escreve refs, não estado React. Pausar canvas em aba oculta, viewport fora da tela e reduced motion. Impor particle budget por grafo e code-split inspector/settings. Medir frame time e duração de commits antes de elevar densidade da cena.

## Segurança

Sessão cookie usa CSRF/Origin em mutações; PAT fica fora de URL/localStorage por padrão. Gerar uma `Idempotency-Key` por intenção e reutilizá-la em retry. Tratar refs/cursors como opacos. Não logar payload SSE bruto, resultado, chave de provider ou headers de autenticação no analytics cliente.
