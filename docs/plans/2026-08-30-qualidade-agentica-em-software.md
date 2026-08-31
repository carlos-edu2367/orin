# Plano de correção — qualidade dos agentes do Orin em tarefas de software

**Data:** 2026-08-30
**Escopo:** `agentos.agentic`, `agentos.code_mode`, `agentos.skills`, workspace de ferramentas.
**Motivação:** com modelos pagos baratos (GPT Luna, DeepSeek V4 Flash), o Orin entregou um projeto React
visualmente bom, mas fora dos padrões do ecossistema e não executável. Os mesmos modelos entregam bem em
outros harnesses.

## Status de implementação

- **Fase 0 (terminal utilizável):** implementada em 2026-08-30. `run_command` ganhou `timeout_seconds`/`cwd`,
  ambiente não interativo, `stdin` fechado, preservação de head+tail na saída, e rastreamento de processos em
  background (`read_process_output`, `stop_process`) com log em `.orin/logs/`. Diff de mudanças agora usa
  `git status --porcelain` quando o workspace é um repositório git.
- **Fase 1 (workspace legível):** implementada em 2026-08-30. Política de ignore extraída para
  [`agentos/ignore.py`](../../src/agentos/ignore.py), aplicada em `list_files`, `search_files`, o diff de
  mudanças e a árvore do prompt; `file_snapshot` trocou `rglob` por `os.walk` com poda de diretório;
  `list_files` ganhou parâmetro `pattern` (glob); manifesto de raiz sempre visível na árvore do prompt.
- **Fase 2 (realimentação mecânica):** implementada em 2026-08-30 em
  [`diagnostics.py`](../../src/agentos/agentic/diagnostics.py) — detecção de projeto (node/python/go/rust),
  diagnóstico automático (ruff/eslint) após `write_file`/`edit_file` com debounce por caminho, ferramenta
  `verify_project` (install→typecheck→lint→build→test com relatório estruturado) e `verify_frontend`
  (navega, tira screenshot e observa a estrutura de cada rota declarada no navegador isolado). Ressalva
  honesta: `verify_frontend` **não lê o console do navegador** — essa capacidade não existe hoje em
  `agentos.browser` (não há `BrowserOperationKind` para isso) e adicioná-la é uma mudança maior, de ponta a
  ponta pelos domínios de modelo/segurança/persistência do browser, deixada fora deste plano.
- **Fase 3 (ciclo de reparo):** implementada em 2026-08-30. VERIFY passou a ser obrigatória sempre que o
  turno mutou o workspace e ainda não foi verificado (gate geral, independente do Modo Code); a nova
  ferramenta `report_verification` é como o agente conclui a verificação, e uma reprovação devolve o turno a
  EXECUTE por até 3 rodadas de reparo antes de desistir e responder com ressalvas. O orçamento de EXECUTE
  agora cresce com o número de entregáveis do contrato (com teto). Iterações que produziram uma escrita
  bem-sucedida ou um resultado de verificação deixam de consumir o orçamento da fase — só flailing consome.
  O prompt não lista mais ferramentas de forma estática; cada requisição informa exatamente o que foi
  publicado naquela chamada.
- **Fase 4 (conhecimento de stack):** implementada em 2026-08-30. Há skills built-in para React SPA,
  Next.js App Router, FastAPI e acessibilidade de frontend; `verify_project(scaffold=...)` oferece as
  receitas curadas sem acrescentar uma ferramenta à fase; contratos de software exigem evidência mecânica
  e contratos de frontend também declaram browser; instruções raiz em `AGENTS.md`, `CLAUDE.md` e
  `CONVENTIONS.md` entram no prompt volátil.
- **Fase 5 (Modo Code confiável):** implementada em 2026-08-30. Workspaces gerenciados novos têm
  autonomia de código na primeira execução (pastas locais continuam exigindo aprovação); o gate aceita
  somente payload estruturado e aprovado de `verify_project` e `verify_frontend`, nunca regex sobre texto
  de comando; no limite da iteração a entrega fecha com ressalvas explícitas, não como erro duro.
- **Fase 6 (medição):** a matriz de 10 cenários e o método de pontuação mecânica estão em
  `tests/eval/`; a execução da baseline pós-v0.2.25 continua pendente de credenciais/configuração para os
  dois modelos alvo e deve ser registrada antes de comparar ganhos das Fases 4–5.

## Tese

O gargalo não é o prompt nem a inteligência do modelo. É que **o Orin abstraiu o ambiente de
desenvolvimento a ponto de o agente não conseguir usá-lo**. Um modelo fraco não acerta de primeira; ele
converge quando um compilador, um instalador, um linter e um console de browser dizem, de forma mecânica,
o que está errado. Hoje o Orin não fecha nenhum desses laços: ele escreve arquivos e responde.

Todo o resto deste plano é consequência disso.

---

## 1. Diagnóstico — cadeia causal do caso "trilhas de estudo"

### 1.1 O agente não consegue usar o toolchain do ecossistema

`COMMAND_TIMEOUT_SECONDS = 45` em `src/agentos/agentic/agent_tools.py:47` é um teto fixo, sem parâmetro
por chamada.

- `npm install` de um projeto React costuma passar de 45 s → `AgentToolError` de timeout.
- `npm create vite@latest` é interativo → trava esperando stdin → morre no timeout.
- `npm run build` em projeto médio → borderline.
- Não há variáveis não interativas no ambiente (`CI`, `npm_config_yes`), então qualquer generator moderno
  pergunta algo e trava.

**Consequência direta:** o agente não escafolda com a ferramenta oficial do ecossistema. Ele escreve React
"de cabeça", a partir dos priors do modelo — que é exatamente o que um modelo barato tem de pior. Daí sai
código que não segue padrão, sem `package.json` coerente, sem dependências instaladas, que nunca rodou.

### 1.2 Nada nunca verifica se o projeto existe de verdade

Não há instalação, build, typecheck, lint ou execução. `write_file` retorna `"Wrote N bytes"` e o turno
segue. O agente não recebe **nenhum** sinal de erro mecânico. Ele não tem contra o que convergir.

### 1.3 O workspace fica ilegível assim que houver dependências

`src/agentos/agentic/workspace.py` não aplica nenhum filtro de ignore. O filtro existe e está pronto em
`src/agentos/retrieval/filters.py` (`DENIED_SEGMENTS`, `GitignoreFilter`), mas só é usado pelo índice
semântico. Consequências:

- `list_entries` (`workspace.py:167`) tem teto de 500 entradas e varre `node_modules` — o `list_files` do
  agente devolve lixo.
- O `workspace_tree` do system prompt (`session.py:1008`, `depth=3`, primeiras 60 entradas) passa a
  descrever `node_modules` em vez do projeto. **O agente perde a visão do próprio trabalho.**
- `search` (`workspace.py:187`) faz `root.glob("**/*")` ordenado sobre `node_modules`.
- `file_snapshot` (`workspace.py:224`) roda `rglob("*")` com teto de 1000 arquivos, **duas vezes por
  `run_command`** (`agent_tools.py:1251` + `changed_files`). Com `node_modules` presente: lento e, pior,
  incorreto — os 1000 primeiros arquivos podem ser todos de dependência, então `changed_files` deixa de
  reportar as alterações reais e o Modo Code perde a evidência do que mudou.

### 1.4 A fase VERIFY é inalcançável no caminho feliz

`PhaseController.observe` (`agentic/phases.py`) só avança de fase quando **o orçamento estoura**. E
`AgenticTurnRuntime.run` (`agentic/runtime.py`, ramo `if (finish is not None or text_parts)`) retorna
`completed` assim que o modelo emite texto sem tool call.

Ou seja: um turno que "termina bem" nunca entra em VERIFY. O contrato é escrito, os critérios de aceite
são definidos — e **nunca são conferidos**. Todo o mecanismo de contrato vira decoração no caso normal.

### 1.5 Quando VERIFY roda, ela é um beco sem saída

`PHASE_TOOLS[Phase.VERIFY]` é somente leitura. Não há transição VERIFY → EXECUTE. O agente pode descobrir
que o build quebra e ser **estruturalmente incapaz de corrigir**: a próxima fase é RESPOND, sem
ferramentas. Isso é o pior defeito de desenho do ciclo atual.

### 1.6 O orçamento de fase impede um projeto inteiro num turno

`DEFAULT_PHASE_BUDGETS`: ORIENT 6/20, PLAN 2/3, EXECUTE 20/60, VERIFY 4/10, RESPOND 1/0. Total útil
≈ 80 ações, e depois disso o agente cai, sem volta, em modo leitura.

Um SPA React de verdade (scaffold, deps, router, layout, ~10 componentes, conteúdo da trilha, estilos,
testes, build, correção) não cabe. O limite do worker (`workers/chat.py:917`) é generoso —
`max_iterations=None` por padrão, deadline de 1 h — mas o orçamento de fase é o teto real e ele é fixo,
independente do tamanho do contrato.

### 1.7 O contrato sintetizado não tem critério de engenharia

`contract.synthesize` (`agentic/contract.py`) gera um único critério: *"O pedido foi atendido: <pedido>"*,
`how="inspection"`, `toolkits={"files"}`. Para uma tarefa de software isso é vazio, e como `toolkits` não
inclui `browser`, **a validação visual fica indisponível pelo resto do turno**.

### 1.8 O prompt mente sobre as ferramentas

`session.py:1037` publica `tool_names` com **todas as 46** definições do toolset, enquanto
`runtime._tool_schemas` expõe só as ~14 da fase atual. O bloco "## Tools available now" lista ferramentas
que o modelo não pode chamar. Há uma linha de ressalva, mas para um modelo fraco isso é fonte direta de
tool call inválido e de plano impossível.

### 1.9 O Modo Code é evitável e o portão é frouxo

- Padrão é `approval_required`: a primeira escrita é bloqueada esperando aprovação
  (`agent_tools.py:706`), o que empurra o usuário de volta ao chat normal — onde **não há portão nenhum**.
- `_observe_code_mode_outcome` detecta "verificação" por regex sobre a **string do comando**:
  `\b(...|test|build)\b` casa com `echo test`. Um modelo fraco satisfaz o portão sem verificar nada.
- O portão é ignorado na iteração final (`runtime.py`, ramo `final_iteration`).

---

## 2. Princípio da correção

> Para um modelo barato entregar padrão de mercado, o harness precisa transformar "acertar" em "reagir a
> um erro". Todo investimento deve ir para **laços de realimentação mecânicos**, não para mais prompt.

Ordem de prioridade: **ambiente real → realimentação → ciclo de reparo → conhecimento de stack → prompt**.

---

## 3. Fases da correção

### Fase 0 — Terminal utilizável (bloqueia todo o resto)

Arquivo principal: `src/agentos/agentic/agent_tools.py`.

1. `run_command` ganha `timeout_seconds` (padrão 120, máximo 600) e `cwd` relativo ao workspace.
2. Ambiente não interativo por padrão: `CI=1`, `npm_config_yes=true`, `npm_config_fund=false`,
   `npm_config_audit=false`, `PIP_DISABLE_PIP_VERSION_CHECK=1`, `DEBIAN_FRONTEND=noninteractive`,
   `stdin` fechado (`subprocess.DEVNULL`) para que um generator interativo **falhe rápido com mensagem**
   em vez de travar até o timeout.
3. Saída longa: manter o head e o tail (hoje o corte cego em 12 000 chars descarta justamente o erro final
   de um build). Erro de compilador vem no fim — preservar o fim é obrigatório.
4. Processos em background: registrar pid + arquivo de log; novas ferramentas `list_processes`,
   `read_process_output(pid, tail)` e `stop_process(pid)`. Hoje um dev server é iniciado e o agente nunca
   consegue ler o que ele imprimiu.
5. Trocar o `file_snapshot()` duplo por diff incremental com ignore (ver Fase 1); em repositório git,
   preferir `git status --porcelain`.

**Critério de aceite da fase:** `npm create vite@latest app -- --template react-ts`, `npm install`,
`npm run build` e `npm run dev` (background) executam e o agente lê a saída de todos.

### Fase 1 — Workspace legível

Arquivos: `src/agentos/agentic/workspace.py`, `src/agentos/agentic/session.py`.

1. Extrair a política de ignore de `retrieval/filters.py` para um módulo compartilhado e aplicá-la em
   `list_entries`, `search`, `file_snapshot` e no `workspace_tree` do prompt.
2. `workspace_tree` passa a ser uma árvore de projeto de verdade (com ignore, arquivos-raiz de manifesto
   sempre presentes: `package.json`, `pyproject.toml`, `tsconfig.json`, `README.md`).
3. Nova ferramenta `glob(pattern)` — hoje só existe busca por conteúdo; o agente não tem como perguntar
   "quais arquivos `.tsx` existem".
4. Elevar os tetos (500 entradas / 1000 no snapshot) depois que o ignore os tornar significativos.

### Fase 2 — Realimentação mecânica (o verdadeiro multiplicador)

Novo módulo: `src/agentos/agentic/diagnostics.py`.

1. **Detecção de projeto**: ler `package.json` / `pyproject.toml` / `go.mod` / `Cargo.toml` e derivar os
   comandos canônicos (install, build, test, lint, typecheck, dev).
2. **Diagnóstico automático pós-escrita**: após `write_file`/`edit_file` num arquivo de código, anexar ao
   resultado da ferramenta o erro de typecheck/lint daquele arquivo, quando barato de obter
   (`tsc --noEmit`, `eslint --format json`, `ruff`, `pyright`). Com debounce e teto de tempo.
   *Este é o item de maior impacto isolado do plano.*
3. **Ferramenta `verify_project`**: uma chamada que roda install → typecheck → lint → build → test e
   devolve resultado estruturado (o que passou, o que falhou, primeiro erro de cada etapa). Substitui a
   dependência de o modelo saber qual comando rodar.
4. **Frontend**: `verify_frontend` sobe o dev server, abre a URL no browser isolado, lê o console
   (`console.error` conta como falha), tira screenshot e navega pelas rotas declaradas nos entregáveis do
   contrato. O suporte a loopback já existe (commit `afec17b`).

### Fase 3 — Ciclo de reparo (consertar a máquina de fases)

Arquivos: `src/agentos/agentic/phases.py`, `src/agentos/agentic/runtime.py`.

1. **VERIFY obrigatória**: se o turno mutou o workspace e ainda não passou por VERIFY, a primeira resposta
   final não encerra o turno — o runtime injeta a transição para VERIFY em vez de retornar `completed`.
   (Mesmo mecanismo já usado pelo `code_completion_gate`, generalizado e não dependente do Modo Code.)
2. **Transição VERIFY → EXECUTE**: um critério de aceite reprovado devolve o agente para EXECUTE com
   ferramentas de escrita, com um limite explícito de rodadas de reparo (sugestão: 3). Sem isso, verificar
   não serve para nada.
3. **Orçamento elástico**: derivar `PhaseBudget` de EXECUTE do tamanho do contrato
   (ex.: `20 + 8 × nº de entregáveis`, com teto), em vez de 20/60 fixos. E não contar como gasto a
   iteração que produziu escrita bem-sucedida ou verificação nova — o orçamento existe para conter
   *flailing*, não trabalho produtivo.
4. Corrigir a incoerência do prompt: `tool_names` passa a refletir exatamente os schemas publicados na
   fase corrente.

### Fase 4 — Conhecimento de stack que o modelo fraco não tem

1. **Scaffold primeiro, sempre**: nova ferramenta `scaffold_project(recipe)` com receitas curadas e
   versões fixadas (`vite-react-ts`, `next-app`, `fastapi-service`, `express-api`). Política no prompt do
   Modo Code: *projeto novo começa pelo generator oficial; escrever `package.json` à mão é o último
   recurso.* Isso sozinho resolve "não segue os padrões do React".
2. **Skills built-in por stack** em `src/agentos/skills/builtin/` (hoje há 4, nenhuma de stack):
   `react-spa`, `nextjs-app`, `python-api`, `frontend-a11y`. Cada uma com layout de pastas, convenções,
   comandos de verificação e armadilhas comuns. Recuperadas automaticamente quando o contrato declara
   entregáveis daquele tipo.
3. **Contrato de software com critérios mecânicos**: `contract.synthesize` e a validação de
   `write_contract` passam a exigir, para trabalho de código, critérios com `how="tool"` cobrindo
   *instala*, *builda*, *typecheck limpo*, *lint limpo* e, em frontend, *rota renderiza sem erro de
   console*. `toolkits` sintetizado para tarefa de código inclui `terminal` e `browser`.
4. Carregar `AGENTS.md`/`CLAUDE.md`/`CONVENTIONS.md` da raiz do workspace no prompt volátil, quando
   existirem.

### Fase 5 — Modo Code confiável

1. Padrão de autonomia para projeto novo em workspace gerenciado passa a `code_autonomy` (escrever é o
   ponto; `approval_required` faz sentido em pasta local do usuário, não numa pasta vazia da conversa).
2. O portão para de confiar em regex sobre a string do comando: passa a exigir **evidência estruturada**
   vinda de `verify_project`/`verify_frontend` (etapa executada, exit code, contagem de erros).
3. O portão deixa de ser ignorado na iteração final; se não houver evidência, o turno termina como
   `with_caveats` **declarando o que não foi verificado**, nunca como sucesso.

### Fase 6 — Medição (sem isso, nada acima é verificável)

`TurnQualityCounters` e a migração `0041_turn_quality_metrics` já dão a base.

1. Suíte de avaliação em `tests/eval/` com ~10 pedidos reais de ponta a ponta — incluindo **exatamente o
   pedido da plataforma de trilhas** — executados contra os modelos baratos alvo.
2. Pontuação **mecânica**, não subjetiva: instala? builda? typecheck limpo? dev server sobe? rota
   principal renderiza sem erro de console? navegação funciona?
3. Registrar a linha de base **antes** da Fase 0. Cada fase precisa mover a pontuação; a que não mover é
   revertida.

---

## 4. Ordem de execução e impacto esperado

| Ordem | Fase | Esforço | Impacto na qualidade com modelo barato |
| --- | --- | --- | --- |
| 1 | Fase 0 — terminal | Baixo | **Alto** — destrava o toolchain do ecossistema |
| 2 | Fase 1 — workspace legível | Baixo | Alto — o agente volta a enxergar o projeto |
| 3 | Fase 6 — linha de base | Médio | Nenhum direto; torna o resto mensurável |
| 4 | Fase 2 — realimentação | Alto | **Máximo** — troca "acertar" por "reagir" |
| 5 | Fase 3 — ciclo de reparo | Médio | Alto — faz o contrato valer alguma coisa |
| 6 | Fase 4 — stack/scaffold | Médio | Alto — resolve "fora do padrão" na origem |
| 7 | Fase 5 — Modo Code | Baixo | Médio — impede sucesso declarado sem evidência |

Fases 0, 1 e 6 podem ser feitas em paralelo. A Fase 2 depende da 0. A Fase 3 depende da 2 (verificar sem
ter o que verificar não muda nada).

## 5. O que este plano não faz

- Não muda o modelo de segurança do workspace (contenção de path, política de rede, browser isolado).
- Não troca o `AgenticTurnRuntime` pelo Kernel genérico de `Execution` (assunto do plano de 26/08).
- Não promete paridade com harness que roda no repositório real do usuário com LSP; promete que um
  projeto entregue **instala, builda e roda**, e que o que não foi verificado é declarado.
