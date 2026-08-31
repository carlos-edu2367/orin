# Handoff — Fases 4, 5 e 6 do plano de qualidade agêntica em software

**De:** sessão que implementou as Fases 0–3 (release v0.2.25).
**Para:** o agente que continuar o trabalho.
**Leitura obrigatória antes de tocar em código:** [2026-08-30-qualidade-agentica-em-software.md](2026-08-30-qualidade-agentica-em-software.md)
— este documento não repete o diagnóstico nem o "porquê" de cada fase, só o estado atual do código e o que
fazer a seguir. Leia a seção "Status de implementação" no topo daquele arquivo primeiro.

## Onde as coisas estão agora (pós Fases 0–3, release v0.2.25)

Resumo de uma frase: o agente agora tem terminal utilizável, workspace legível, ferramentas de diagnóstico
mecânico (`verify_project`, `verify_frontend`, diagnóstico automático pós-escrita) e um ciclo de
verificação obrigatório com reparo (`report_verification` + VERIFY↔EXECUTE). O que falta é ensinar o
agente a **começar** um projeto certo (Fase 4) e fazer o Modo Code confiar em evidência real em vez de
regex (Fase 5), além de medir se tudo isso realmente ajuda (Fase 6).

Arquivos que mudaram e você vai precisar conhecer:

- [`src/agentos/agentic/diagnostics.py`](../../src/agentos/agentic/diagnostics.py) — `detect_recipe(root)`
  retorna um `ProjectRecipe` (node/python/go/rust) com `command_for(step)` para
  install/typecheck/lint/build/test. `file_diagnostic_command(path, project_root)` decide o lint de um
  arquivo isolado. **Esta é a base que a Fase 4 estende** — não recrie detecção de projeto do zero.
- [`src/agentos/agentic/agent_tools.py`](../../src/agentos/agentic/agent_tools.py) — tem agora
  `verify_project` (~linha 1666), `report_verification` (~linha 1730), `read_process_output`,
  `stop_process`, e o rastreamento geral de mudanças `_changed_paths`/`_change_events`/`change_events()`
  (usado pelo gate de verificação obrigatória, não confundir com `_code_mode_checks`, que é *só* do Modo
  Code).
- [`src/agentos/agentic/phases.py`](../../src/agentos/agentic/phases.py) — `PhaseController.force_verify()`
  / `force_respond()` / `force_execute()`; `note_iteration(actions, productive=...)`; `PHASE_TOOLS`,
  `TOOLKIT_TOOLS`.
- [`src/agentos/agentic/runtime.py`](../../src/agentos/agentic/runtime.py) — `_needs_verification()`,
  `_advance_phase()` (ciclo de reparo), `_elastic_execute_budget()`, `MAX_VERIFY_REPAIR_ROUNDS = 3`.
- [`src/agentos/ignore.py`](../../src/agentos/ignore.py) — política de ignore compartilhada
  (`PathIgnorePolicy`, `GitignoreFilter`, `DENIED_SEGMENTS`).

## ⚠️ A restrição que vai te morder: teto de 16 ferramentas por fase

`tests/unit/agentic/test_phase_controller.py::test_every_phase_publishes_a_set_a_small_model_can_navigate`
garante que nenhuma fase publica mais de 16 ferramentas quando o contrato declara `{"files", "terminal"}`.
**ORIENT e EXECUTE já estão exatamente em 16/16 hoje.** Rode isto para confirmar antes de mexer:

```bash
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from agentos.agentic.phases import tools_for, Phase
for phase in Phase:
    print(phase.value, len(tools_for(phase, frozenset({'files','terminal'}))))
"
```

Se a Fase 4 adicionar `scaffold_project` (ou qualquer ferramenta nova) a `_READ_TOOLS`/`_WRITE_TOOLS` ou a
`TOOLKIT_TOOLS["files"]`/`["terminal"]` em `phases.py`, o teste quebra imediatamente. Nas Fases 0/1 isso já
aconteceu e a solução foi **consolidar em vez de adicionar**: `glob` virou parâmetro de `list_files`,
`list_processes` virou modo de `read_process_output`. Faça o mesmo raciocínio antes de criar uma ferramenta
nova — pergunte primeiro se ela pode ser um parâmetro de uma ferramenta que já existe. Se genuinamente
precisar de uma ferramenta nova, terá que tirar alguma coisa de `_READ_TOOLS`/`_WRITE_TOOLS` para abrir
espaço, ou publicá-la apenas via um toolkit que a tarefa raramente declara junto de `files`+`terminal`.

## Fase 4 — Conhecimento de stack que o modelo fraco não tem

Objetivo inalterado do plano original. Ordem sugerida, do menor para o maior risco:

### 4.1 Skills built-in por stack (comece por aqui — não esbarra no teto de ferramentas)

`src/agentos/skills/builtin/` tem hoje 4 skills (`code-review`, `systematic-debugging`,
`technical-research`, `testing`) — nenhuma de stack. Leia uma delas e
[`docs/CREATING_SKILLS.md`](../CREATING_SKILLS.md) para o formato exato, depois crie:

- `react-spa` — Vite + React + TS: estrutura de pastas, roteamento, convenção de componentes, comandos de
  verificação (`npm run build`, `npm run lint`, `npx tsc --noEmit`), armadilhas comuns (esquecer
  `npm install` antes de rodar, não usar `create vite` e escrever `package.json` à mão).
- `nextjs-app`, `python-api` (FastAPI), `frontend-a11y`.

Skills carregadas automaticamente quando o contrato declara entregáveis daquele tipo já funcionam via
`skills/retrieval.py` — não precisa mexer no runtime para isso funcionar, só escrever os `SKILL.md`.

### 4.2 `scaffold_project(recipe)` — cuidado com o teto de 16

Receitas curadas com versões fixadas: `vite-react-ts`, `next-app`, `fastapi-service`, `express-api`. Cada
receita é essencialmente uma sequência de comandos (`npm create vite@latest . -- --template react-ts`,
depois `npm install`) executada com a MESMA infraestrutura que `run_command`/`verify_project` já usam
(timeout, ambiente não interativo, stdin fechado — reaproveite, não duplique).

Antes de registrar como ferramenta nova, primeiro tente: isso pode ser um **modo de `verify_project`**?
Por exemplo `verify_project(steps=["scaffold:vite-react-ts"])`, ou um parâmetro separado
`scaffold: str | None` na mesma ferramenta. Isso evita gastar mais uma vaga do teto de 16.

Política de prompt (em `code_mode/prompt.py` ou na instrução de fase EXECUTE): projeto novo começa pelo
generator oficial da stack; escrever `package.json`/estrutura à mão é último recurso. Isso resolve na
origem o "não segue os padrões do React" que motivou o plano inteiro.

### 4.3 Contrato de software com critérios mecânicos

Em [`contract.py`](../../src/agentos/agentic/contract.py), `synthesize()` hoje gera um único critério
`how="inspection"` — vazio para trabalho de código. Para contratos que declaram toolkit `"terminal"` (ou
que o texto do pedido cheira a código — reaproveite a detecção de `code_mode/models.py:detect_code_request`
se fizer sentido), `synthesize`/a validação de `write_contract` devem **exigir** pelo menos um critério
`how="tool"` cobrindo instalação/build/typecheck, e para frontend, um cobrindo renderização de rota. Isso
faz o `report_verification` da Fase 3 ter contra o que checar de verdade.

### 4.4 Carregar `AGENTS.md`/`CLAUDE.md`/`CONVENTIONS.md` do workspace

Em `session.py:build_runtime()`, ao lado de onde `workspace_tree`/`skill_catalog` já são montados
(~linha 1000), ler esses arquivos da raiz do workspace (se existirem) e adicionar ao prompt volátil. Sem
custo de ferramenta, sem risco de teto.

## Fase 5 — Modo Code confiável

### 5.1 Autonomia padrão para projeto novo

Hoje o padrão é `CodeAutonomy.APPROVAL_REQUIRED` (`code_mode/models.py:49`,
`agentic/settings.py:46-48`, `conversations/chat.py:635`), o que bloqueia a primeira escrita
(`agent_tools.py` — `_code_mode_requires_approval and name in {"write_file", "edit_file", "run_command",
"verify_project"}`) até aprovação do plano. Para um workspace **gerenciado** (não uma pasta local do
usuário) começando do zero, isso empurra o usuário de volta pro chat comum sem portão nenhum. Avalie mudar
o padrão para `CODE_AUTONOMY` especificamente nesse caso — não mexa no padrão para pasta local do usuário,
onde `approval_required` é a escolha certa.

### 5.2 Portão deixa de confiar em regex sobre a string do comando

Hoje, em `agent_tools.py::_observe_mutation_outcome` (linha ~1004), uma verificação "conta" quando o
comando bate no regex `\b(pytest|vitest|jest|...|test|build)\b` — `echo test` passa. Com `verify_project`
existindo (Fase 2), a correção natural é: `code_completion_gate()` (linha ~1017) deixa de checar
`self._code_mode_checks` (lista de strings de comando) e passa a checar uma evidência estruturada — por
exemplo, um novo `self._code_mode_verified: bool` setado quando `verify_project` retorna com sucesso
(`outcome.payload["all_passed"] is True`, ou pelo menos `outcome.payload["steps"]` não vazio com o(s)
step(s) relevante(s) tendo rodado). Trate isso em `_observe_mutation_outcome`, no mesmo lugar que hoje
faz `self._code_mode_checks.append(...)`.

Cuidado: `verify_project` já existe e é publicado em VERIFY e no toolkit `"terminal"` — confirme que ele
também está alcançável durante EXECUTE quando o Modo Code está ativo (deveria estar, via `"terminal"` no
`_WRITE_TOOLS`), senão o modelo não tem como gerar a evidência que o portão vai exigir.

### 5.3 Portão não deve virar falha dura na iteração final

Em `runtime.py` (~linha 495-512), quando o gate falha na `final_iteration`, hoje o turno retorna
`self._fail(turn, "CODE_VALIDATION_REQUIRED", ...)` — um estado de erro. O plano original pede que isso
vire uma conclusão `with_caveats` (o turno **completa**, mas declara explicitamente o que não foi
verificado), não uma falha. Veja `code_mode/models.py::CodeCompletionKind` (`VERIFIED`/`WITH_CAVEATS`) —
provavelmente já existe a modelagem para isso, só não está conectada nesse ponto do runtime.

## Fase 6 — Medição (faça isto em paralelo com a Fase 4, não depois)

O plano original pedia baseline **antes** da Fase 0. Isso não aconteceu — fomos direto para a correção.
A baseline útil agora é: meça o estado **pós v0.2.25** antes de começar a Fase 4/5, para poder atribuir a
elas (e não às Fases 0-3) qualquer ganho que aparecer depois.

1. Crie `tests/eval/` com ~10 pedidos reais de ponta a ponta contra os modelos baratos alvo (GPT Luna,
   DeepSeek V4 Flash) — inclua **o pedido literal da plataforma de trilhas de estudo** que motivou tudo
   isso (frontend só, uma trilha sobre SQL/SQLAlchemy).
2. Pontuação mecânica, não subjetiva: instalou? buildou? typecheck limpo? `npm run dev` sobe e a rota
   principal responde? Para isso, reaproveite `verify_project`/`verify_frontend` como o próprio mecanismo
   de pontuação (eles já retornam payload estruturado) em vez de escrever um avaliador paralelo.
3. Registre a pontuação atual (pós v0.2.25) como a baseline documentada. Cada fase daqui pra frente precisa
   mover o número; a que não mover deveria ser revertida ou repensada.

## O que NÃO fazer

- Não recriar detecção de projeto — `diagnostics.detect_recipe` já existe, estenda-o.
- Não adicionar ferramenta nova sem antes rodar o script do teto de 16 acima.
- Não mexer no `AgenticTurnRuntime` para trocá-lo pelo Kernel genérico de `Execution` — fora de escopo
  (assunto do plano de 2026-08-26, não deste).
- Não reintroduza checagem por regex de string de comando como sinal de verificação — é exatamente o que a
  Fase 5 existe para eliminar.
