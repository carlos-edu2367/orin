# Handoff — Implementar o plano de Settings Shell Refactor

Data: 2026-08-15
Branch: `main`
HEAD no handoff: `c67c8d7`
Plano a implementar: `docs/superpowers/plans/2026-08-14-settings-shell-refactor.md`

## Estado atual

O plano de MCP foi concluído e o plano de Plugins foi implementado. O último
plano pendente é a refatoração do shell de Settings.

Suítes verificadas após Plugins:

- Backend completo no momento da entrega do plano de Plugins: `1463 passed,
  68 skipped`.
- Frontend: `301 passed`, typecheck limpo e build aprovado.
- Migração `0035_plugins` validada com `upgrade()`/`downgrade()` em SQLite em
  memória.
- O lint dos arquivos novos passa. A execução global ainda acusa a violação
  pré-existente em `frontend/src/features/settings/RuntimeSettingsPage.tsx:41`
  (`react-hooks/set-state-in-effect`) e o warning de dependência associado.

Há uma alteração não rastreada pré-existente em `data/`; não apagar, resetar ou
adicionar ao commit sem autorização explícita.

## Direção autorizada

O objetivo é implementar integralmente o plano de Settings Shell Refactor, sem
iniciar outro plano. O dono autorizou o trabalho direto no `main` para a sessão
de Plugins; para esta nova sessão, confirme novamente se o trabalho deve ser
direto no branch ou em worktree antes de editar.

Use TDD RED→GREEN, um commit por task e verificação incremental. Se a skill
`superpowers:executing-plans` estiver disponível, use-a; caso contrário, siga o
checklist do próprio plano manualmente. Não use subagentes para implementação
sem autorização; uma revisão independente de acessibilidade/layout pode ser
feita se houver suporte disponível.

## O que existe hoje e precisa ser preservado

### Shell e rotas

- `frontend/src/features/settings/SettingsPage.tsx` ainda declara manualmente
  `SECTIONS` e renderiza topbar, navegação e conteúdo.
- `frontend/src/app/routes.tsx` ainda aponta diretamente para:
  - `/settings/general` → `RuntimeSettingsPage`
  - `/settings/memory` → `MemoryPage`
  - `/settings/providers` e `/settings/omniroute` → `ProviderSettingsPage`
  - `/settings/skills` → `SkillsPage`
  - `/settings/mcp` → `McpSection`
  - `/settings/plugins` → `PluginsSection`
  - placeholders para `/settings/agents`, `/settings/workspace` e
    `/settings/advanced`
  - aliases de nível raiz `/providers`, `/skills` e `/schedules`.
- O plano novo deve transformar `SettingsPage` em redirect fino ou compatibilidade
  controlada e fazer `SettingsShell` ser o layout único das rotas `/settings/*`.

### Conteúdo que tem shells próprios

- `frontend/src/features/providers/ProviderSettingsPage.tsx` tem cerca de 650
  linhas, renderiza `app-shell`/`topbar` próprios e contém `ProviderPanel`,
  formulários de configure/revoke/refresh/test/favorite, OmniRoute, Ollama e
  `VisionModelSetting`.
- `frontend/src/features/skills/SkillsPage.tsx` tem cerca de 356 linhas,
  renderiza `app-shell`/`topbar` próprios e mistura biblioteca, criação,
  detalhe, edição e associações de agentes.
- `frontend/src/features/mcp/McpSection.tsx` e
  `frontend/src/features/plugins/PluginsSection.tsx` atualmente envolvem o
  conteúdo em `SettingsPage`. Depois do shell, devem virar conteúdo de seção
  sem topbar/shell próprio.
- `frontend/src/features/memory/MemoryPage.tsx` também usa `SettingsPage` para
  memória global e outro layout para memória de projeto; não quebrar o fluxo de
  projeto ao mover a parte global.
- `frontend/src/features/settings/RuntimeSettingsPage.tsx` mistura limite de
  interações e estado/remoção da instalação. O plano separa isso entre General
  e About; preserve o contrato dos APIs existentes.

### APIs e comportamento que não podem regredir

- O chat deve continuar sendo acessível pelo link `Voltar ao chat`.
- Configuração de provider continua write-only para chaves: nunca reexibir,
  persistir no frontend ou inserir segredo em logs/props públicos.
- A navegação de MCP/Plugins deve preservar cards, estados pending/active/
  disabled, aprovação e ações existentes.
- A gaveta de provider deve abrir em `/settings/providers/:provider`, manter a
  grade visível atrás e fechar com `Esc`, botão de retorno e restauração de foco.
- Rotas raiz `/providers`, `/skills` e `/schedules` devem redirecionar para
  `/settings/providers`, `/settings/skills` e `/settings/schedules`.
- `/settings/omniroute` deve redirecionar para Providers; Agents continua no
  contexto do agente, não deve virar uma tela global inventada.
- Não usar `role="tab"` para alternadores simples; o padrão do repo é botão
  normal com `aria-pressed` quando aplicável.
- O lint `react-hooks/set-state-in-effect` é sensível: evitar `setState`
  síncrono disparado diretamente por `useEffect`; usar estado inicial correto e
  atualizar em callbacks assíncronos.

## Ordem recomendada

1. Tasks 1–4: `sections.ts`, `SettingsNav`, `SettingsShell`/`SettingsSection`
   e `SettingsDrawer`.
2. Tasks 5–8: marcas, `ProviderCard`/`ProviderGrid`, hook de estado e detalhe
   de provider.
3. Tasks 9–10: Skills/Memory e General/Workspace/Schedules/Leitura visual/
   About dentro do shell.
4. Task 11: rotas, redirects e Command Palette usando a mesma fonte declarativa.
5. Tasks 12–14: CSS, acessibilidade e regressão visual.
6. Task 15: documentação.

## Pontos de atenção por task

- `sections.ts` deve ser a única fonte de verdade da navegação, incluindo
  badges de Memory, Providers, Skills, MCP, Plugins, Schedules e version.
- `SettingsNav` precisa expor `aria-current="page"`; o marcador de pending deve
  ter nome acessível (`Aguardando sua ação`) e nunca depender apenas de cor.
- `SettingsDrawer` é `role="region"`, não modal: `aria-modal="true"` não deve
  ser usado. O foco inicial e a restauração precisam ser testados.
- As marcas de provider são SVGs locais, inline/build-time, sem URL remota.
  `dangerouslySetInnerHTML` só pode receber esses assets estáticos, nunca API ou
  input do usuário.
- Ao extrair `ProviderPanel`, preserve exatamente as chamadas existentes de
  `configureProvider`, `revokeProvider`, `refreshProviderModels`, favoritos,
  testes OmniRoute/Ollama e polling de instalação.
- A grade deve usar os nomes reais de `PROVIDER_NAMES` em
  `frontend/src/api/providers.ts`, não uma lista duplicada.
- O drawer deve manter o provider aberto ao atualizar estado; não causar
  remount/reset de formulário por cada atualização da grade.
- `SkillsSection` deve manter busca, filtro por source, paginação, criação,
  edição, detalhe, remoção de versões e associações de agente.
- `useSettingsBadges` deve fazer chamadas limitadas, cacheadas por sessão e
  falhar de forma degradada; badge desconhecido não deve aparecer como zero.
- O plano cita dez seções, mas a fonte declarativa deve refletir exatamente os
  itens definidos no próprio plano: General, Memory, Providers, Leitura visual,
  Skills, MCP, Plugins, Workspace, Agendamentos e Sobre.

## Verificação obrigatória

Rodar após cada componente novo:

```powershell
cd frontend
npx tsc -b --noEmit
npx eslint . --max-warnings=0
npx vitest run
```

Ao final:

```powershell
python -m pytest tests/unit tests/integration -q
cd frontend
npm run test
npm run lint
npm run build
npm run test:e2e
```

O lint global deve ser comparado com o baseline conhecido em
`RuntimeSettingsPage.tsx:41`; qualquer nova violação deve ser corrigida.
Playwright/E2E que dependa do backend local só conta como verificado se o
backend realmente estiver disponível.

Verificação manual final: abrir Settings, percorrer todas as seções, confirmar
que topbar/cabeçalho/largura/espaçamento permanecem consistentes, abrir e fechar
três providers seguidos, testar `Esc` e restauração de foco e confirmar que a
grade não pisca nem perde estado.

## Riscos conhecidos

- O lint global pré-existente de `RuntimeSettingsPage.tsx` permanece até o plano
  decidir se essa tela será corrigida durante a extração para General/About.
- A rota `/settings/vision` e os redirects precisam ser adicionados sem quebrar
  a API existente `/v1/settings/vision-model`.
- A regressão visual depende de renderizar a aplicação real; fixtures isoladas
  não provam o ciclo completo com backend.
- Não alterar o plano de Plugins nem remover o conteúdo de MCP/Plugins durante a
  refatoração: apenas reencaixá-los no shell comum.

## Entrega esperada

Ao terminar, informar:

- tasks e commits realizados;
- arquivos principais criados/alterados;
- resultados de typecheck, lint, testes, build e E2E;
- qualquer divergência inevitável do plano;
- riscos restantes.
