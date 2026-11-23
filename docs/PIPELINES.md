# Pipelines

Um pipeline é um arquivo YAML que descreve, etapa por etapa, um trabalho de vários agentes: planejar, implementar em paralelo, rodar testes, pedir revisão, corrigir e repetir até passar. O fluxo (ordem, paralelismo, repetição, parada) é decidido por um motor Python, nunca por um modelo. O motor grava um diário durável em SQLite, por isso uma execução interrompida pode ser retomada de onde parou, sem refazer o que já deu certo.

Quando usar: trabalho grande, repetível, com várias fases e uma condição objetiva de pronto (testes que passam, revisão sem achados). Quando não usar: tarefa pequena. Um pipeline multiplica o gasto de tokens (cada etapa é um subagente); para uma correção de uma linha, converse normalmente.

Nada roda sozinho. Um pipeline só começa quando você o inicia (interface ou API), quando você liga `pipelines.agentMayStart` e o agente chama `RunPipeline` para um pipeline já aprovado, ou quando você digita `/goal`.

## Onde ficam os arquivos e a precedência

| Nível | Pasta | Observação |
|---|---|---|
| Projeto | `<projeto>/.orin/pipelines/*.yaml` | Só é lido se a pasta for confiável. Em pasta não confiável o Orin só conta quantos arquivos pulou, sem mostrar nome nem conteúdo. |
| Usuário | `~/.orin/pipelines/*.yaml` (ou a pasta de `ORIN_USER_DIR`) | Vale para todos os projetos. |
| Embutido | `src/agentos/pipelines/builtin/*.yaml` | Os quatro prontos, abaixo. Só leitura. |

Regras de leitura:

- Vale o de maior precedência por `id`: projeto, depois usuário, depois embutido. Os de menor precedência com o mesmo `id` ficam ocultos.
- O `id` precisa ser igual ao nome do arquivo sem a extensão (`E_ID` se diferir). Extensões aceitas: `.yaml` e `.yml`.
- Cada arquivo tem no máximo 256 KB e deve estar em UTF-8 (`E_TOO_LARGE`, `E_YAML_SYNTAX`). Atalhos (links) que apontam para fora da pasta de pipelines são ignorados.
- O YAML é lido em modo restrito: sem tags, sem chaves de mesclagem (`<<`), sem chave duplicada, um documento só (`E_YAML_FORBIDDEN`).
- `pipelines.enabled = false` esconde tudo e responde `E_DISABLED`.

## Confiança da pasta e aprovação por hash

O YAML de projeto vem de um repositório que pode não ser seu. Por isso:

1. Só aparece se a pasta estiver marcada como confiável (a mesma confiança usada para hooks).
2. Antes da primeira execução, a interface mostra os comandos dos gates (comando, `expect`, tempo limite, entradas usadas) e pede aprovação. A aprovação guarda o hash do arquivo (`definition_hash`) em `pipeline-approvals.json`, na pasta de usuário do Orin.
3. Mudar um byte do arquivo invalida a aprovação: o Orin pede de novo. Aprovar exige enviar o hash que você revisou; se o arquivo mudou nesse meio tempo, a API responde 409.

Quem precisa de aprovação:

| Quem inicia | Embutido | Usuário | Projeto |
|---|---|---|---|
| Você | não | não | sim, por hash |
| Agente (`RunPipeline`) | não | sim, por hash | sim, por hash |

O motivo da segunda linha: um agente com permissão de escrita poderia ter escrito o arquivo de usuário. O token do `orin code` e a CLI nunca gravam nem aprovam pipelines (403).

O comando de cada gate ainda passa pelas regras de permissão da conversa (a ferramenta `Bash`), e o turno do pipeline usa o modo de permissão da conversa, nunca um modo elevado. No modo de planejamento (`plan_mode`) o pipeline não inicia; em `ask_permissions` o motor avisa com `pipeline.degraded`.

## Esquema v1, campo a campo

Todo arquivo começa com `version: 1`. Campo desconhecido é erro (`E_UNKNOWN_FIELD`, com sugestão do nome mais parecido). As tabelas abaixo são as mesmas de `src/agentos/pipelines/schema_v1.py`; `tests/unit/pipelines/test_docs_in_sync.py` confere que cada campo está aqui.

Exemplo mínimo completo:

```yaml pipeline
version: 1
id: exemplo-guia
name: Exemplo do guia
description: Planeja, implementa por tarefa, roda os testes e corrige até passar.
inputs:
  objetivo: {type: string, required: true, max_length: 8000}
  testes: {type: string, default: "pytest -q"}
budget:
  max_tokens: 1000000
  max_cost_usd: 8
  max_minutes: 60
defaults:
  effort: medium
  retries: {max: 1, on: [failed, stalled]}
  stall_minutes: 10
steps:
  - id: plano
    agent: plan
    prompt: |
      Divida em tarefas independentes: ${{ inputs.objetivo }}
    output_schema:
      type: object
      required: [tarefas]
      properties:
        tarefas:
          type: array
          maxItems: 8
          items: {type: string}

  - id: implementar
    agent: general
    map:
      over: ${{ steps.plano.output.tarefas }}
      as: tarefa
      max_parallel: 3
    budget: {max_tokens: 200000}
    prompt: |
      Implemente apenas esta tarefa: ${{ tarefa }}

  - id: verificar
    gate:
      all:
        - command: ${{ inputs.testes }}
          timeout_seconds: 600
    on_fail:
      goto: corrigir
      max_loops: 2

  - id: corrigir
    agent: general
    when: ${{ steps.verificar.failed }}
    prompt: |
      Corrija as falhas: ${{ steps.verificar.findings }}
    next: verificar
```

Convenção das tabelas: "obrigatório" significa que a falta gera `E_REQUIRED`; tipos errados geram `E_TYPE`; fora da faixa gera o código do assunto (`E_BUDGET`, `E_RETRY`, `E_INPUT`, `E_MAP_RANGE`, `E_LOOP_NO_MAX`).

### Nível do arquivo

<!-- schema:top -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `version` | inteiro | sim | Sempre 1 (`E_VERSION`). |
| `id` | texto | sim | Letras minúsculas, números e hífen; até 64 caracteres; igual ao nome do arquivo (`E_ID`). |
| `name` | texto | não | Nome mostrado na interface. |
| `description` | texto | não | Descrição curta. |
| `inputs` | mapa | não | Entradas digitadas ao iniciar. |
| `budget` | mapa | não | Teto do pipeline inteiro. Sem ele vale o padrão e sai `W_BUDGET_DEFAULT`. |
| `defaults` | mapa | não | Valores padrão das etapas. |
| `steps` | lista | sim | Etapas, na ordem de execução. |
<!-- /schema -->

### Entradas (`inputs.<nome>`)

O nome da entrada usa letras minúsculas, números e `_`.

<!-- schema:input -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `type` | texto | não | `string`, `integer`, `boolean` ou `models`. |
| `required` | booleano | não | Entrada obrigatória ao iniciar. |
| `default` | qualquer | não | Valor padrão; precisa combinar com `type` e com `max_length`. |
| `max_length` | inteiro | não | De 1 a 100000; vale só para `string`. |
<!-- /schema -->

O tipo `models` é uma lista de até 3 modelos no formato `provedor/modelo`, ou o texto `auto`. Antes de criar a execução o Orin resolve `auto` (modelos favoritos de famílias diferentes, sem os de cota recusada, sempre diferentes do autor quando possível) e grava a lista resolvida nas entradas, então a retomada é estável. Se houver menos famílias que o pedido, sai `pipeline.degraded` com `reason="models_auto_partial"`; sem nenhum modelo a API responde 422 `PIPELINE_MODELS_UNAVAILABLE`. Uma lista explícita de `models`, digitada ou vinda do `default` do arquivo, só aceita modelos favoritos (`PIPELINE_INPUT_INVALID`). Uma entrada `integer` aceita no máximo 15 dígitos, em texto ou como número JSON; fora isso, a faixa não é validada pelo v1.

### Orçamento (`budget`)

<!-- schema:budget -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `max_tokens` | inteiro | não | Mínimo 1. |
| `max_cost_usd` | número | não | Mínimo 0. Só conta custo conhecido. |
| `max_minutes` | número | não | Mínimo 0. O motor converte para segundos ao montar o orçamento do agente; `max_seconds` não é aceito no YAML. |
<!-- /schema -->

### Padrões (`defaults`)

<!-- schema:defaults -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `provider` | texto | não | Provedor padrão das etapas de agente. |
| `model` | texto | não | Modelo padrão. |
| `effort` | texto | não | Esforço padrão (`low`, `medium`, `high`, `xhigh`, `max`, `ultra`; o Orin ajusta ao nível aceito pelo modelo e avisa com `W_EFFORT_ADJUSTED`). |
| `retries` | mapa | não | Política de repetição padrão. |
| `stall_minutes` | inteiro | não | De 1 a 1440. |
<!-- /schema -->

Sem `provider` e `model`, a etapa usa os da conversa que iniciou o pipeline (é o caso dos quatro prontos).

### Repetições (`retries`)

<!-- schema:retries -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `max` | inteiro | não | De 0 a 3 repetições. |
| `on` | lista | não | Causas repetidas: `failed`, `stalled`, `schema_invalid`, `gate_failed`. |
<!-- /schema -->

### Etapa (`steps[]`)

Uma etapa tem `agent` (etapa de agente) ou `gate` (etapa só de checagem), nunca os dois (`E_STEP_KIND`). Etapa de agente exige `prompt`. Etapa de gate não aceita campos de agente. Os ids de etapa são únicos (`E_STEP_ID_DUP`).

<!-- schema:step -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `id` | texto | sim | Identificador único da etapa. |
| `name` | texto | não | Nome mostrado. |
| `description` | texto | não | Descrição curta. |
| `agent` | texto | não | Agente embutido ou customizado. Só aceita origem confiável. |
| `gate` | mapa | não | Etapa de checagem, sem agente. |
| `prompt` | texto | não | Tarefa do agente; aceita `${{ }}`. |
| `provider` | texto | não | Sobrescreve o provedor. Só aceita origem confiável. |
| `model` | texto | não | Sobrescreve o modelo. Só aceita origem confiável. |
| `effort` | texto | não | Sobrescreve o esforço. Só aceita origem confiável. |
| `isolation` | texto | não | `none` ou `worktree` (literal, sem `${{ }}`). |
| `map` | mapa | não | Repete a etapa por item de uma lista. |
| `output_schema` | mapa | não | JSON Schema da saída, até 16 KB. |
| `budget` | mapa | não | Teto por agente lançado pela etapa. |
| `when` | texto | não | Expressão booleana; falsa marca a etapa como pulada. |
| `on_fail` | texto ou mapa | não | `stop`, `continue` ou `{goto, max_loops}`. |
| `next` | texto | não | Id da etapa seguinte. |
| `retries` | mapa | não | Política de repetição da etapa. |
| `stall_minutes` | inteiro | não | De 1 a 1440. |
<!-- /schema -->

### Fan-out (`map`)

<!-- schema:map -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `over` | lista ou referência | sim | Lista literal ou `${{ steps.x.output.campo }}`; no máximo 50 itens (`E_MAP_RANGE`). |
| `as` | texto | sim | Nome da variável do item; letras minúsculas, números e `_`, diferente de `inputs` e `steps`. |
| `max_parallel` | inteiro | não | De 1 a 8; padrão 2. O motor também respeita o limite de subagentes da sessão. |
| `fail_fast` | booleano | não | Padrão `true`: a primeira falha cancela os outros itens. |
<!-- /schema -->

Cada item vira uma linha do diário com uma `item_key` no formato `<indice>-<hash8 do item>`.

### Laço (`on_fail`)

<!-- schema:on_fail -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `goto` | texto | não | Etapa para a qual voltar. Exige `max_loops` (`E_LOOP_NO_MAX`). |
| `max_loops` | inteiro | não | De 1 a 5 voltas. |
<!-- /schema -->

### Gate (`gate`)

O gate tem exatamente uma lista, `all` (todos passam) ou `any` (basta um). A avaliação para no primeiro item que decide o resultado.

<!-- schema:gate -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `all` | lista | não | Todos os itens precisam passar. |
| `any` | lista | não | Basta um item passar. |
<!-- /schema -->

Cada item da lista tem exatamente uma destas chaves: `command`, `schema` ou `verifier`.

<!-- schema:command_item -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `command` | texto | sim | Comando de shell. Só `${{ inputs.* }}` pode entrar nele. |
| `expect` | texto | não | `zero` (padrão) ou `nonzero`. |
| `timeout_seconds` | inteiro | não | De 1 a 600; padrão 600. |
<!-- /schema -->

<!-- schema:schema_item -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `step` | texto | sim | Etapa anterior cuja saída é validada. |
| `json_schema` | mapa | sim | JSON Schema, até 16 KB, sem `$ref` remoto. |
<!-- /schema -->

<!-- schema:verifier_item -->
| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `policy` | texto | não | `cross_family`, `same_model` ou `explicit`. |
| `effort` | texto | não | `low`, `medium`, `high`, `xhigh`, `max` ou `ultra`. |
| `provider` | texto | não | Provedor do verificador (com `explicit`). |
| `model` | texto | não | Modelo do verificador (com `explicit`). |
| `pass_on` | lista | não | Vereditos que passam: `pass`, `fail`, `inconclusive`. |
<!-- /schema -->

## Expressões, `${{ }}` e a regra de origem dos dados

`${{ }}` aceita só caminhos e, em `when` e em `map.over`, uma expressão pequena. O analisador é próprio, sem `eval` de qualquer tipo.

Caminhos possíveis:

- `inputs.nome`: entrada digitada por você.
- `steps.<id>.output` e `steps.<id>.output.campo`: saída validada de uma etapa anterior.
- `steps.<id>.failed`: verdadeiro se a etapa falhou.
- `steps.<id>.findings`: falhas de comando e achados provados `blocker` ou `major` do verificador (achado `unproven` fica de fora).
- a variável do `map` (`${{ tarefa.titulo }}`).

Referência a etapa inexistente é `E_REF_UNKNOWN`; a etapa que roda depois é `E_REF_FORWARD`; sintaxe inválida é `E_EXPR_SYNTAX`. Dentro de um texto (prompt, comando), `${{ }}` aceita só um caminho; condições vão em `when`. Caminho ausente vale `null`. Valores compostos entram no texto como JSON compacto.

Gramática (a mesma de `src/agentos/pipelines/expressions.py`; o teste de sincronia compara os dois):

```text
expr    := or
or      := and ("or" and)*
and     := not ("and" not)*
not     := "not" not | cmp
cmp     := atom (("==" | "!=") atom)?
atom    := literal | path | "len" "(" path ")" | "(" expr ")"
path    := ident ("." ident)*        # at most 8 segments; ident = [a-z][a-z0-9_]*
literal := STRING | INT | "true" | "false" | "null"
```

Limites: 500 caracteres por expressão e 8 segmentos por caminho.

### Regra de taint (origem)

- `inputs.*` é confiável: foi digitado por você.
- `steps.*` é saída de modelo: não confiável.
- A variável do `map` herda a origem do `over` (lista literal é confiável).

Os campos que viram comando ou escolhem o que roda (`gate.command`, `agent`, `provider`, `model`, `effort`) só aceitam origem confiável. Uma referência não confiável ali é `E_REF_TAINT` na carga. Resultado: saída de modelo nunca vira comando de shell.

## Citação de `inputs` em gates

Entradas entram no comando já citadas pelo Orin, nunca cruas:

- Dialeto `posix` (Git Bash no Windows, `/bin/sh` nos demais): `shlex.quote`.
- Referência no meio do texto do comando: um único argumento citado.
- Referência que é o valor inteiro de `command` (`command: ${{ inputs.testes }}` com `testes = "pytest -q"`): o valor é separado em palavras (`shlex.split`) e cada palavra é citada. Assim `pytest -q` funciona e `pytest -q; rm -rf x` nunca vira um segundo comando. Espaços e quebras de linha nas bordas não contam (`command: |` com só a referência também é o comando inteiro): a carga apara as bordas de `command`.
- Entrada que **seleciona o programa**: a referência é o comando inteiro ou está na primeira palavra de um comando (`command: ${{ inputs.prog }} --flag`, `./${{ inputs.prog }}`, depois de `;`, `&&`, `|`, de `NOME=valor` ou de `env`/`sudo`/`exec`/`bash -c`). Quem preenche essa entrada escolhe o que roda: ela aparece com ATENÇÃO na revisão de aprovação (`program_inputs` em cada comando, `selects_program` na entrada) e o agente não pode preenchê-la. A classificação vem de uma única função (`command_quoting.classify_references`), usada pela execução, pela revisão e pela regra do agente.
- Dialeto `cmd` (sem Git Bash no Windows): não há citação segura geral. Gate com entrada citada é recusado com `GATE_SHELL_UNSUPPORTED` e a mensagem manda instalar o Git for Windows. Comando sem `${{ inputs.* }}` roda normalmente.
- Referência já entre aspas (`'${{ inputs.x }}'`, `"${{ inputs.x }}"`) ou depois de barra invertida: recusada na carga com `E_EXPR_SYNTAX` (aponta a linha do `command`) e de novo na execução (`GATE_INPUT_INVALID`), porque as aspas do autor quebrariam a citação do Orin. Escreva a referência sem aspas.
- Antes de rodar, o motor confere que as palavras do comando final são iguais às do modelo com cada referência trocada pelo valor bruto, e que o comando está no conjunto permitido calculado a partir das entradas (`GATE_REF_UNTRUSTED` fora dele).
- `timeout_seconds` vai de 1 a 600, que é o teto da ferramenta `Bash` (por onde o gate roda); o padrão é 600. O gate nunca vira tarefa em segundo plano.
- `expect: nonzero` exige falha real: só vale código de saída inteiro diferente de 0, 126 e 127. Tempo esgotado, cancelamento, permissão negada e comando inexistente ou não executável não contam como "o teste falhou". Sem isso, um erro de digitação "reproduziria" o bug.

## Gates

Um gate é uma etapa sem agente. Resultado: `succeeded` ou `gate_failed`.

- `command`: roda pela ferramenta `Bash`, sob as permissões da conversa; passa com saída 0 (ou diferente de 0 com `expect: nonzero`). A falha gera um achado `command_failed` com o fim da saída.
- `schema`: valida a saída de uma etapa anterior contra um JSON Schema (`E_REF_UNKNOWN` ou `E_REF_FORWARD` se a etapa não existe ou vem depois).
- `verifier`: pede uma revisão independente (pacote A). A política `cross_family` escolhe um modelo de outra família; `same_model` e `explicit` existem para casos controlados. Passa se o veredito está em `pass_on`. O gate roda porque o pipeline o declara: não depende de `orchestration.verifier.enabled` nem de `orchestration.selfVerify.enabled`. Sem verificador disponível a validação de contexto recusa o pipeline, e em execução o gate falha com `GATE_VERIFIER_UNAVAILABLE`; nunca é pulado em silêncio.

## Controle de fluxo

- `map`: um agente por item, até `max_parallel` ao mesmo tempo. Limite de 50 itens (`E_MAP_RANGE`). Com `fail_fast: false` os itens que falham ficam `null` na lista da etapa seguinte e o resultado ganha ressalva.
- `when`: expressão booleana; falsa marca a etapa como `skipped` e o fluxo segue para a próxima.
- `on_fail`: `stop` (padrão) para a execução com `failed`; `continue` segue adiante (a etapa fica marcada como falha); `{goto: <etapa>, max_loops: N}` volta para a etapa indicada, de 1 a 5 vezes. Sem `max_loops` é `E_LOOP_NO_MAX`. O motor conta as voltas por etapa de origem e para com `loop_limit` ao esgotá-las.
- `next`: salta para outra etapa. Só vale quando a etapa rodou; etapa pulada por `when` segue para a seguinte. Voltar para trás exige que o destino tenha `max_loops`. Etapa inalcançável gera `W_UNREACHABLE_STEP`.
- Laço sem progresso: se uma etapa falha de novo com a mesma impressão digital da falha anterior (código de saída e fim normalizado da saída; para agentes, a mensagem e os erros de esquema), a execução para antes de gastar mais (`pipeline.stalled` com `action="no_progress"`).
- `retries`: `max` de 0 a 3, `on` com as causas `failed`, `stalled`, `schema_invalid`, `gate_failed`. Espera exponencial de 5 s a 60 s (5, 10, 20, 40, 60). Nunca repetem: cancelamento, estouro de orçamento, `GATE_PERMISSION_DENIED`, `GATE_INPUT_INVALID`, `GATE_SHELL_UNSUPPORTED`, `GATE_REF_UNTRUSTED`.
- `stall_minutes`: sem atividade do filho nesse tempo, ele é cancelado e a etapa fica `stalled`. A nova tentativa só acontece se `retries.on` incluir `stalled`, e leva uma nota para mudar de abordagem (o prompt e o `input_hash` não mudam).
- `output_schema`: a saída do agente é validada; se não encaixa, volta ao mesmo agente com o erro, até 5 correções. Na sexta falha a etapa fica `failed` com `schema_invalid`.

Limites por execução: 200 agentes (`AGENT_CAP`) e 500 execuções de etapa (`STEP_CAP`).

## Orçamento

Padrão quando o arquivo não declara `budget`: 2.000.000 de tokens, US$ 15 e 120 minutos, com aviso `W_BUDGET_DEFAULT` na carga. O custo em dólares só conta custo conhecido; em assinatura sem preço, o motor conta tokens e marca `cost_known=false`.

- Teto do pipeline: é o limite duro. Estourou qualquer um dos três, o motor cancela todos os filhos em voo, não lança mais nenhum e a execução termina `budget_exceeded` (`stop_reason="budget"`). Estouro na última etapa mantém o resultado: a execução termina `succeeded` com ressalva.
- Teto por agente: o `budget` de uma etapa vale para cada agente lançado (cada item de `map` e cada tentativa), sempre limitado ao que resta do pipeline. Um filho que estoura o próprio teto falha a etapa com `SUBAGENT_BUDGET_EXCEEDED`, sem repetição. Se o teto por agente vezes os itens passa do total do pipeline, sai `W_BUDGET_FANOUT`.
- Contador: soma o uso do filho e dos netos, incluindo o verificador, uma vez só.
- Teto do usuário contra o projeto: `orchestration.budget.subagent.maxTokens` e `.maxCostUsd` (vazios = sem teto) limitam o `budget` que o modelo pede ao chamar `Agent` e o de um YAML de projeto. O projeto só pode reduzir o teto, nunca aumentar. Para pipeline de projeto o Orin corta o `budget` do pipeline (inclusive o padrão) e o de cada etapa no teto do usuário; a API mostra o orçamento efetivo (`budget.lowered_by_user_ceiling`). YAML de usuário e embutido valem como escritos.

## Retomada

O diário grava, antes de cada etapa, uma linha com `input_hash` (prompt resolvido, agente e seu `digest`, modelo, esforço e item) e, ao fim, estado, saída e uso. Uma execução que o processo perdeu (queda do worker, reinício) vira `interrupted`; ao reencontrá-la, o worker a retoma sozinho. Você também pode retomar pela interface, por `orin pipeline resume` ou pela API.

- Reaproveitado: etapa `succeeded` com o mesmo `input_hash` e o mesmo número de volta do laço. Nenhuma linha nova entra no diário e o uso não é contado duas vezes (evento `pipeline.step_reused`).
- Gate: um gate terminal só é reaproveitado se o hash for igual, `rerun_gates` for falso e nenhuma etapa de agente tiver rodado de novo antes dele nesta execução. Um gate que falhou e encerrou a execução é refeito (retomar é tentar de novo).
- `rerun_gates`: refaz todos os gates em vez de reaproveitar o resultado do diário.
- Definição alterada: a execução guarda um instantâneo da definição (até 256 KB). Se o arquivo mudou, a API responde 409 `PIPELINE_DEFINITION_CHANGED` com as opções `original` (retoma com o instantâneo) e `new_run` (inicia uma execução nova com as mesmas entradas, se o arquivo ainda existe). Pipeline de projeto retomado exige pasta confiável e aprovação do hash do instantâneo.
- `budget_override`: uma execução `budget_exceeded` só retoma com ele, e o contador recomeça do zero. Sem ele: 409 `PIPELINE_BUDGET_OVERRIDE_REQUIRED`. Fora disso, a retomada soma o que a execução já gastou.
- Worktree: uma etapa com worktree já integrada e hash diferente é `RESUME_CONFLICT` (execução `failed`, `stop_reason="resume_conflict"`). Artefato de saída ausente ou corrompido não é reaproveitado: a etapa roda de novo e sai `pipeline.degraded` com `reason="artifact_unreadable"`.
- Pausar drena as etapas em curso e deixa a execução `interrupted` com `stop_reason="paused"`; cancelar cancela os filhos e termina `cancelled`.
- Retenção: execuções terminadas (`succeeded`, `failed`, `cancelled`, `budget_exceeded`) e seus artefatos são apagadas após 90 dias.

Estados da execução: `running`, `succeeded`, `failed`, `cancelled`, `interrupted`, `budget_exceeded`. Estados da etapa: `pending`, `running`, `succeeded`, `failed`, `stalled`, `skipped`, `interrupted`, `gate_failed`.

## Os quatro pipelines prontos

Nenhum fixa provedor nem modelo: cada etapa usa os da conversa que iniciou a execução.

| id | Fluxo | Entradas | Teto |
|---|---|---|---|
| `implementar-verificar` | plano, implementação em paralelo (até 8 tarefas, 3 por vez), gate (comando de testes e verificador `cross_family`), correção em até 2 voltas | `objetivo`, `testes` (padrão `pytest -q`) | 2.000.000 de tokens, US$ 15, 120 min |
| `revisao-multimodelo` | até 3 revisores de famílias diferentes, cegos entre si, e uma consolidação que lista cada achado com os revisores que o provaram | `alvo`, `revisores` (`models`, padrão `auto`), `esforco` | 1.000.000 de tokens, US$ 10, 60 min |
| `corrigir-bug` | teste que reproduz (o gate exige que ele falhe), correção, gate (o teste novo e a suíte passam) e verificador; até 2 voltas na reprodução e 2 na correção | `descricao`, `comando_reproducao`, `testes` | 1.500.000 de tokens, US$ 10, 90 min |
| `pesquisa` | divide a pergunta, exploradores em paralelo (esforço baixo, 4 por vez) e um redutor que cruza e cita fontes | `pergunta`, `max_exploradores` (padrão 4) | 500.000 tokens, 30 min |

Notas de comportamento real:

- `implementar-verificar` roda sem `isolation: worktree` (as tarefas do plano não dividem arquivos); a variante com worktree é uma cópia sua em `~/.orin/pipelines`. Emite `W_BUDGET_FANOUT` por desenho: o teto do pipeline continua sendo o limite duro.
- `corrigir-bug`: os comandos dos gates vêm só das entradas. Um teste que falha por outro motivo (erro de sintaxe) também "reproduz"; a falha esperada e o verificador reduzem o risco, não o eliminam.
- `pesquisa`: `max_parallel` é literal (4) e `max_exploradores` só entra no prompt de `dividir`; o limite duro é o `maxItems: 8` do esquema. O v1 não valida a faixa da entrada inteira.
- `revisao-multimodelo` e `pesquisa` usam `fail_fast: false` e `on_fail: continue`: se um item falha, o relatório diz o que ficou sem resposta.

## `/goal`

`/goal <objetivo> [--max-tokens N]` continua o trabalho em turnos sucessivos até um avaliador pequeno dizer que terminou (`{done, reason}`) ou até o teto de tokens (`goal.defaultMaxTokens`, padrão 300.000). Só roda quando você o pede. A execução é uma linha de `pipeline_runs` com `pipeline_id="goal"`.

Subcomandos: `/goal clear` encerra o objetivo (não cancela o turno em curso; pela API, `{"cancel_turn": true}` também o cancela) e `/goal resume [--max-tokens N]` reabre um objetivo parado com uma janela nova de tokens, turnos e falhas.

Ordem de parada ao fim de cada turno: turno do usuário na fila ou outro turno vivo adia a avaliação; teto de tokens para sem avaliar; o avaliador diz `done` e o objetivo termina; duas falhas seguidas do avaliador param (`evaluator_failed`); 3 turnos seguidos sem ferramenta nem arquivo alterado param (`no_progress`); 50 turnos param (`max_turns`), depois de avaliar. Os turnos `compact`, `shell` e `pipeline` não contam.

O avaliador é um favorito de nível 1 ou 2 do provedor do turno (senão o modelo do turno com esforço `low`), chamado sem ferramentas, com a transcrição entre delimitadores sorteados a cada chamada. Retomáveis: `interrupted` e `failed` ou `budget_exceeded` com motivo `budget`, `max_turns`, `no_progress`, `evaluator_failed` ou `send_failed`. Um objetivo `succeeded` ou limpo não volta.

A API de `/goal` é descrita abaixo. Na interface, o comando de texto depende da tela de conversa (WS-C11).

## A ferramenta `RunPipeline`

Com `pipelines.agentMayStart` ligada (padrão desligada, só você pode ligar), o agente principal ganha a ferramenta `RunPipeline(pipeline_id, inputs)`. Sem `pipeline_id` ela lista os pipelines que o agente pode iniciar.

- Só inicia pipelines aprovados para agente (tabela de aprovação acima).
- Nunca roda dentro do turno do agente: valida, registra um início pendente (até 3 por turno, `PIPELINE_PENDING_LIMIT`) e responde. O gancho pós-turno inicia os pendentes se o turno terminou bem e descarta os demais. O registro é do processo do worker: perdê-lo numa queda significa não gastar nada.
- Filhos de um pipeline nunca recebem `RunPipeline`.
- O agente não fornece valor para uma entrada que seleciona o programa de um gate (o comando inteiro ou a primeira palavra dele, ver "Citação de `inputs` em gates"): vale o `default` do arquivo (`PIPELINE_INPUT_NOT_ALLOWED`).
- O início pendente guarda as entradas que o agente digitou, não as resolvidas. O gancho pós-turno resolve de novo: o `default` das entradas que selecionam o programa e o aviso `pipeline.degraded` de `auto` continuam valendo.
- O pedido de permissão do broker mostra apenas o nome da ferramenta; id, entradas e orçamento aparecem nos argumentos da chamada e no resultado.

## API

Todas as rotas exigem pessoa autenticada e uma das ações `pipeline.read`, `pipeline.run` ou `pipeline.write`. As que tocam arquivos exigem a capacidade `host_folders` (404 em servidor compartilhado). As respostas trazem referências e resumos, nunca saída de modelo, exceto a rota de saída de etapa (um artefato, até 256 KB). Erros vêm como `{"error": {"code", "category", "message", ...}}`.

| Método e rota | Ação | O que faz |
|---|---|---|
| `GET /v1/pipelines` | read | Lista os pipelines visíveis (`conversation_id` opcional para incluir os do projeto). |
| `POST /v1/pipelines/validate` | read | Valida um texto YAML (`text`, `source`, `conversation_id`). |
| `GET /v1/pipelines/{pipeline_id}` | read | Definição, erros, avisos e hash. `text` é o texto que a descoberta validou: vem vazio quando o arquivo passa de 256 KB, não é UTF-8 ou é um atalho para fora da pasta. |
| `PUT /v1/pipelines/{pipeline_id}` | write | Grava o arquivo (`scope` `user` ou `project`; `If-Match` com o hash atual evita sobrescrever; a comparação e a troca rodam sob trava de arquivo). Sobre um arquivo atual acima de 256 KB ou fora de UTF-8 responde 422 `PIPELINE_FILE_UNREADABLE`; sobre um atalho para fora da pasta, 422 `PIPELINE_PATH_INVALID`. Não aceita o token do CLI. |
| `DELETE /v1/pipelines/{pipeline_id}` | write | Apaga o arquivo (`scope`, `If-Match`). Recusa atalho para fora da pasta (`PIPELINE_PATH_INVALID`). Não aceita o token do CLI. |
| `POST /v1/pipelines/{pipeline_id}/approve` | write | Aprova o hash revisado (`definition_hash`). 409 se o arquivo mudou. |
| `POST /v1/pipelines/{pipeline_id}/revoke` | write | Revoga a aprovação. |
| `POST /v1/pipelines/{pipeline_id}/estimate` | read | Estimativa: assinaturas envolvidas e orçamento efetivo. |
| `POST /v1/pipeline-runs` | run | Inicia (202; `pipeline_id`, `conversation_id`, `inputs`). Com limite de taxa. |
| `GET /v1/pipeline-runs` | read | Lista execuções (`conversation_id`, `state`, `limit` até 200). |
| `GET /v1/pipeline-runs/{run_id}` | read | Execução, etapas e telemetria de uso. |
| `GET /v1/pipeline-runs/{run_id}/steps/{step_id}/output` | read | Saída de uma etapa (`item_key`, `attempt`). 410 se o artefato sumiu. |
| `POST /v1/pipeline-runs/{run_id}/pause` | run | Pausa (202). |
| `POST /v1/pipeline-runs/{run_id}/cancel` | run | Cancela (202). |
| `POST /v1/pipeline-runs/{run_id}/resume` | run | Retoma (202; `definition`, `rerun_gates`, `budget_override`). |
| `GET /v1/conversations/{id}/goal` | read | Estado do objetivo. |
| `POST /v1/conversations/{id}/goal` | run | Inicia (`objective`, `max_tokens`). |
| `POST /v1/conversations/{id}/goal/clear` | run | Encerra (`cancel_turn`). |
| `POST /v1/conversations/{id}/goal/resume` | run | Retoma (`max_tokens`). |

Pausar e cancelar funcionam entre processos: a API grava `stop_reason="pause_requested"` e o vigia do driver, no worker, consulta o pedido a cada 0,5 s.

Erros do `/goal`: `GOAL_ACTIVE` (409), `GOAL_NOT_FOUND` (404), `GOAL_NOT_RESUMABLE` (409), `GOAL_OBJECTIVE_INVALID` e `GOAL_MAX_TOKENS_INVALID` (422). Erros comuns de pipeline: `PIPELINE_NOT_FOUND`, `PIPELINE_INVALID` (422, com a lista de erros), `PIPELINE_NOT_APPROVED`, `PIPELINE_CHANGED`, `PIPELINE_CONFLICT`, `PIPELINE_BUSY`, `PIPELINE_FILE_UNREADABLE`, `PIPELINE_PATH_INVALID`, `PIPELINE_PLAN_MODE`, `PIPELINE_RUN_NOT_FOUND`, `PIPELINE_NOT_RUNNING`, `PIPELINE_NOT_CANCELLABLE`, `PIPELINE_NOT_RESUMABLE`, `PIPELINE_SNAPSHOT_MISSING`, `PIPELINE_INPUT_REQUIRED`, `PIPELINE_INPUT_UNKNOWN`, `PIPELINE_INPUT_INVALID`, `PIPELINE_MODELS_UNAVAILABLE`.

## CLI `orin pipeline`

Chama a API local com o token da instalação (o mesmo caminho do `orin code`). Sem o Orin em execução responde "inicie com `orin start`" e sai com código 2; recusa do servidor sai com código 1. Não escreve no banco: sem Orin não haveria worker para executar o turno.

| Comando | O que faz |
|---|---|
| `orin pipeline list [--conversation ID] [--state ESTADO]` | Lista execuções recentes. |
| `orin pipeline status <run_id>` | Mostra a execução, etapa por etapa, e como continuar. |
| `orin pipeline resume <run_id> [--rerun-gates] [--budget-override] [--definition original\|new_run]` | Retoma a partir do diário. |
| `orin pipeline cancel <run_id>` | Cancela. |

## Configurações

As cinco chaves estão no registro de `src/agentos/settings/keys_pipelines.py` e aparecem em Configurações. A interface grava na camada do usuário.

<!-- settings -->
| Chave | Padrão | Camada | Efeito |
|---|---|---|---|
| `pipelines.enabled` | `true` | usuário; o projeto só desliga | Permite executar pipelines. |
| `pipelines.agentMayStart` | `false` | só usuário | Publica a ferramenta `RunPipeline`. |
| `orchestration.budget.subagent.maxTokens` | sem teto | usuário; o projeto só reduz | Teto de tokens por subagente, também sobre o orçamento pedido pelo agente. |
| `orchestration.budget.subagent.maxCostUsd` | sem teto | usuário; o projeto só reduz | Teto de custo (US$) por subagente; só conta custo conhecido. |
| `goal.defaultMaxTokens` | `300000` | só usuário | Teto de tokens do `/goal` quando não há `--max-tokens`. |
<!-- /settings -->

## Eventos

Os eventos entram no histórico da conversa que iniciou a execução: `pipeline.started`, `pipeline.step_started`, `pipeline.step_finished`, `pipeline.step_reused`, `pipeline.step_retrying`, `pipeline.gate_evaluated`, `pipeline.loop`, `pipeline.stalled`, `pipeline.degraded`, `pipeline.budget_exceeded`, `pipeline.finished`, `pipeline.resumed` e, do `/goal`, `goal.started`, `goal.evaluated`, `goal.continued`, `goal.finished`.

Privacidade: nenhum prompt nem saída de modelo vai em evento (grandes resultados viajam como referência), e nenhuma saída de modelo vai para comando. Entradas aparecem nos eventos só pelo nome e pelo hash, nunca pelo valor.

## Códigos de erro e aviso da carga

Lista fechada de `src/agentos/pipelines/errors.py`. Mensagens saem em pt-BR no formato `linha N, coluna M: <mensagem> (código E_...)`. Erro impede a execução; aviso não.

<!-- codes -->
| Código | Quando |
|---|---|
| `E_YAML_SYNTAX` | YAML inválido, arquivo vazio, ilegível ou fora de UTF-8. |
| `E_YAML_FORBIDDEN` | Tag, mesclagem, chave duplicada, mais de um documento ou link para fora da pasta. |
| `E_TOO_LARGE` | Arquivo acima de 256 KB. |
| `E_VERSION` | `version` ausente ou diferente de 1. |
| `E_ID` | `id` fora do padrão, diferente do nome do arquivo ou `as` inválido. |
| `E_UNKNOWN_FIELD` | Campo desconhecido. |
| `E_TYPE` | Tipo errado em um campo. |
| `E_REQUIRED` | Falta um campo obrigatório. |
| `E_STEP_ID_DUP` | Dois passos com o mesmo id. |
| `E_STEP_KIND` | Etapa com `agent` e `gate`, ou com nenhum, ou gate com campos de agente. |
| `E_REF_UNKNOWN` | Referência a entrada, etapa ou campo que não existe. |
| `E_REF_FORWARD` | Referência a etapa que roda depois. |
| `E_REF_TAINT` | Saída de modelo em campo que só aceita origem confiável. |
| `E_EXPR_SYNTAX` | Expressão inválida, ou referência de gate entre aspas. |
| `E_LOOP_NO_MAX` | `goto` sem `max_loops` válido, ou ciclo sem parada. |
| `E_MAP_RANGE` | `max_parallel` fora de 1 a 8 ou `over` com mais de 50 itens. |
| `E_AGENT_UNKNOWN` | Agente inexistente. |
| `E_MODEL_UNKNOWN` | Provedor ou modelo indisponível. |
| `E_ISOLATION_DISABLED` | `isolation: worktree` com `orchestration.worktree.enabled` desligado. |
| `E_ISOLATION_UNAVAILABLE` | `isolation: worktree` com `map` paralelo fora de um repositório Git. |
| `E_SCHEMA_INVALID` | JSON Schema inválido, acima de 16 KB ou com `$ref` remoto. |
| `E_BUDGET` | Valor de `budget` inválido. |
| `E_RETRY` | Valor de `retries` inválido. |
| `E_INPUT` | Entrada com nome, tipo, `max_length` ou `default` inválido. |
| `E_DISABLED` | Pipelines desligados em `pipelines.enabled`. |
| `W_BUDGET_FANOUT` | Aviso: teto por agente vezes os itens passa do total do pipeline. |
| `W_BUDGET_DEFAULT` | Aviso: sem `budget`, vale o padrão. |
| `W_UNREACHABLE_STEP` | Aviso: etapa que nunca é alcançada. |
| `W_EFFORT_ADJUSTED` | Aviso: o esforço será ajustado ao que o modelo aceita. |
<!-- /codes -->

Códigos de execução (não fazem parte da lista fechada da carga): `E_RENDER` e `E_MAP_TYPE` (texto ou `over` que não resolve), `INPUT_INVALID` (entrada com tipo errado no início), `SCHEMA_INVALID`, `AGENT_CAP`, `STEP_CAP`, `SUBAGENT_BUDGET_EXCEEDED`, `RESUME_CONFLICT`, `GATE_INPUT_INVALID`, `GATE_SHELL_UNSUPPORTED`, `GATE_PERMISSION_DENIED`, `GATE_REF_UNTRUSTED`, `GATE_VERIFIER_UNAVAILABLE`.

## Limites conhecidos

- `proven_by` sem checagem cruzada: no `revisao-multimodelo`, o esquema só exige `proven_by` não vazio para severidade `major` ou `blocker`. Que os nomes citados pertençam mesmo aos revisores é pedido no prompt, não verificado; o v1 não tem forma limpa de expressar isso. Fica para a 0.14.
- O avaliador do `/goal` não consulta a cota do provedor que ele usa.
- Um gate em voo não é interrompido por estouro de orçamento: só por cancelamento do turno. Ele termina no próprio `timeout_seconds` (máximo 600 s).
- O aviso `pipeline.degraded` de "só há N famílias" vem da resolução de `auto` na API, não do motor.
- O cartão de permissão de `RunPipeline` mostra só o nome da ferramenta.
- Se o artefato de uma etapa com worktree já integrada se perde, o motor refaz a etapa sem saber que ela já tinha sido integrada.
- A correção de `output_schema` roda sem worktree: o agente corrige só o texto.
- Um teste de reprodução do `corrigir-bug` que falha por outro motivo também conta como "reproduziu".
- `RunPipeline` e o início pendente só existem em memória do processo do worker.
- A detecção de "seleciona o programa" cobre a primeira palavra, `NOME=valor`, `;`/`&&`/`|`/`$(...)` e os invólucros `env`, `sudo`, `exec`, `nohup`, `nice`, `time`, `command`, `builtin`, `eval` e `sh/bash -c`. Não segue invólucros com opções que levam valor (`sudo -u root ${{ inputs.x }}`, `xargs -n 1 ...`) nem intérpretes que leem código (`python -c ${{ inputs.x }}`): a pessoa vê o comando inteiro ao aprovar o arquivo.
