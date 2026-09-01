# Memória e aprendizado contínuo do Orin — design

**Data:** 2026-09-01
**Estado:** design aprovado, aguardando plano de implementação
**Motivação:** um usuário real passou uma semana usando o Orin e nenhuma memória foi criada.

## 1. Problema

Duas queixas que compartilham uma causa:

1. o Orin não aprende com os próprios erros nem com o usuário;
2. o Orin praticamente nunca cria memórias.

A segunda não é o modelo sendo preguiçoso. É o sistema nunca pedir.

### 1.1 Evidência

- **A instrução é um bullet órfão.** `session.py:323` acrescenta uma única linha —
  "Use `remember` when the user states a durable preference or fact worth keeping; do not store
  transient chatter." — **depois** do bloco `## Subagents`, sem cabeçalho próprio. Para o modelo,
  ela lê como instrução sobre subagentes. Metade da frase é freio, sem acelerador correspondente.
- **Nada nunca pede uma memória.** `remember` é 1 de 16 ferramentas publicadas em ORIENT/EXECUTE
  (`phases.py:64`), competindo com ferramentas que avançam a tarefa visível. Salvar memória não
  avança nada que o usuário veja. Não existe ponto do turno em que o runtime pergunte
  "aprendeu algo aqui?".
- **O modelo nunca sente falta.** `recent(limit=12)` já é injetado no prompt (`session.py:1046`),
  então não há lacuna percebida que motive `recall` — nem, por consequência, `remember`.
- **Correções do usuário são descartadas.** "não, é assim" é o sinal de aprendizado mais rico
  disponível e não é capturado em lugar nenhum.
- **Erros morrem no fim do turno.** `_failed_signatures` (`runtime.py:210`) é in-memory e por turno.
  `turn_quality_metrics` grava contadores que nada nunca lê de volta. `report_verification`
  gera rodadas de reparo dentro do turno e depois é esquecido.
- **O store é uma lista plana.** `agent_memories` tem só `fact` + `tags`. Sem tipo, confiança,
  origem ou decaimento. `search()` é sobreposição de bag-of-words: memória escrita com palavras
  diferentes da consulta nunca casa. E a injeção real é `recent(12)` ordenado por `updated_at` —
  recência pura, zero relevância. A 13ª memória é invisível para sempre.

### 1.2 Restrição herdada

`tests/unit/agentic/test_phase_controller.py::test_every_phase_publishes_a_set_a_small_model_can_navigate`
garante no máximo 16 ferramentas por fase com o contrato `{"files","terminal"}`. **ORIENT e EXECUTE
estão hoje exatamente em 16/16.** Este design **não cria nenhuma ferramenta nova**; `remember` e
`recall` já ocupam duas dessas vagas e permanecem como estão.

## 2. Decisões tomadas

| Questão | Decisão |
|---|---|
| De onde aprender | Correção do usuário, falha de ferramenta/verificação, fatos ditos em conversa, e métrica de turno |
| Como extrair | **Híbrido**: sinais mecânicos viram memória sem chamar modelo; só correção e fatos passam por uma reflexão curta pós-turno, condicionada a gatilho |
| Visibilidade | **Salva e mostra um card** na atividade do turno, com desfazer/editar |
| Skills | **Fora de escopo.** Memória é o que é verdade; Skill é como fazer. A fronteira permanece |
| Embeddings | **Não.** Instalação pessoal tem centenas de fatos, não milhões; a varredura léxica mantém a recuperação explicável e sem dependência nova |

## 3. Arquitetura

### 3.1 `src/agentos/agentic/learning.py` (novo)

Irmão direto de `quality.py`, mesma disciplina: nunca levanta exceção, nunca influencia o turno,
só observa.

`TurnLearningLedger` acumula durante o turno:

| Sinal | Detecção (mecânica) | Destino |
|---|---|---|
| `tool_failure_resolved` | assinatura em `_failed_signatures` cujo mesmo tool depois teve sucesso com argumentos diferentes | memória **direta** |
| `verification_failed` | `report_verification` com resultado negativo | memória **direta** |
| `contract_rewritten` | `write_contract` chamado 2ª vez no mesmo turno | só sinaliza |
| `user_correction` | mensagem do usuário após turno `completed`, casando com gatilho léxico barato (`não`, `errado`, `na verdade`, `prefiro`, `sempre que`, `nunca`) | **reflexão** |
| `user_answered_question` | `ask_user` respondido | **reflexão** |
| `budget_exhausted` | fase saiu por `exhausted` sem `productive` | só sinaliza |

Os sinais **diretos** produzem memória sem chamada de modelo, porque o fato já é estruturado.
Exemplo: `run_command("npm install")` falha, `run_command("pnpm install")` sucede →
`kind=operational`, `scope=project`, "Neste projeto o gerenciador de pacotes é pnpm; npm install falha."

### 3.2 Reflexão condicional

`reflect_on_turn` roda dentro de `_settle_quality`, **depois** de `store.finish()` — a resposta
já foi entregue, latência percebida é zero. Dispara apenas se o ledger tiver ao menos um sinal
de reflexão.

- Entrada: digest de no máximo ~1.5k tokens (sinais + as 2 últimas mensagens do usuário +
  objetivo do contrato, se houver). **Nunca o transcript inteiro.**
- Saída: JSON estrito `[{kind, scope, fact, confidence}]`, no máximo 3 itens, `fact` ≤ 200 caracteres.
- Fora do schema → descarta em silêncio.

Custo: turno sem sinal, **zero**. Turno com sinal, uma chamada de ~2k in / ~150 out.

### 3.3 Correção do prompt (causa raiz)

O bullet órfão de `session.py:323` vira uma seção `## Memória` própria, com gatilhos concretos
("quando a pessoa corrige você", "quando você descobre uma convenção do projeto") em vez de
apenas o freio atual.

### 3.4 Store — migração de `agent_memories`

| Coluna | Razão |
|---|---|
| `kind` | `preference` \| `fact` \| `operational` \| `correction`. Decide como o texto entra no prompt e como se contradiz |
| `confidence` | `0.0–1.0`. Sinal mecânico entra alto; reflexão declara o seu. Governa o corte de injeção |
| `source` | `user_explicit` \| `reflection` \| `mechanical`. Sem isso, memória errada é irrastreável |
| `hit_count`, `last_used_at` | Memória com `confidence < 0.5` e sem recuperação há mais de 30 dias sai da injeção — **não** é apagada, continua visível na página Memory. As colunas entram na Fase 1; a política de decaimento que as consome é da Fase 3 |
| `superseded_by` | Contradição encadeia em vez de deletar; histórico auditável |

**Correção de bug latente:** `UniqueConstraint("user_id", "fact")` (`schema.py:791`) nunca foi
relaxada quando `0028_projects` adicionou escopo de projeto. `save()` deduplica considerando
escopo, mas o banco não — gravar o mesmo fato em dois projetos estoura `IntegrityError`.
Passa a ser `(user_id, scope_type, project_id, fact)`.

**Supersessão** é mecânica, sem modelo: memória nova com mesmo `kind`, mesmo escopo e alta
sobreposição de termos com uma existente marca a antiga como `superseded_by`. "Prefiro npm" →
"prefiro pnpm" não acumula duas verdades contraditórias no prompt.

### 3.5 Recuperação

`recent(limit=12)` é substituído por `relevant(task, limit=12)`, com orçamento repartido:

- **4 vagas fixas** para `kind=preference` de maior confiança — preferência do usuário vale em
  todo turno, independente do assunto;
- **8 vagas** por relevância léxica contra a tarefa atual, reusando o `_terms` que o `recall`
  já usa (não reescrever), com escopo de projeto vencendo empate contra escopo global.

Seguro para cache: as memórias já vivem no bloco **volátil** do prompt (`session.py:357`), não no
prefixo cacheado. Trocar recência por relevância não invalida cache algum.

### 3.6 Superfície

- Novo `AgentActivityEventType.MEMORY_LEARNED` em `agentic/events.py`, payload
  `{memory_id, fact, kind, source}`.
- Consumido por `frontend/src/features/conversations/activityTypes.ts`, `activitySummary.ts`
  e `ActivityCard.tsx`. Render discreto: "Aprendi: …" + **Desfazer**.
- Desfazer reusa `DELETE /v1/memories/{id}` (`gateway.py:723`).
- Editar exige um `PATCH /v1/memories/{id}` novo, que `MemoryPage.tsx` também passa a usar —
  hoje a página só lista e exclui.

### 3.7 Privacidade

A chamada de reflexão vai para o **mesmo** provedor que já processou o turno, com um digest de
mensagens que aquele provedor já viu. Nenhum dado novo sai da máquina, e nada sai em turno que
não acendeu gatilho. A promessa local-first do README permanece intacta.

## 4. Tratamento de falhas

Mesma disciplina de `_settle_quality`: todo o caminho de aprendizado é `try/except` largo.
Provedor fora do ar no fim do turno → pula. JSON fora do schema → descarta. Store sem o método →
não grava. **Nenhuma falha de aprendizado pode falhar um turno** — o turno já entregou a resposta
antes disso rodar.

## 5. Testes

- Detecção de cada sinal do ledger.
- Supersessão por contradição.
- Ranking de relevância: preferência sempre entra; projeto vence global no empate.
- Validação do JSON da reflexão, incluindo lixo do modelo.
- Teste explícito de que nenhum caminho de aprendizado propaga exceção.
- O teste do teto de 16 permanece intocado e deve continuar passando sem alteração.

## 6. Faseamento

**Fase 1 — custo zero por turno.** Seção `## Memória` no prompt; migração do schema (incluindo a
correção da constraint); sinais mecânicos → memória direta; `relevant()` no lugar de `recent()`;
card `MEMORY_LEARNED` + `PATCH /v1/memories/{id}`.
*Critério de sucesso:* numa semana de uso normal com trabalho de código, ao menos uma memória
`kind=operational` gravada sem intervenção do usuário.

**Fase 2 — reflexão condicional.** `user_correction` e `user_answered_question` passam pela
chamada pós-turno.
*Critério de sucesso:* uma correção explícita do usuário ("prefiro X") aparece como memória
`kind=preference` no turno seguinte, e influencia a resposta.

**Fase 3 — telemetria de volta e decaimento.** Ler `turn_quality_metrics` (turnos redundantes,
orçamento estourado) e aplicar decaimento por desuso.
*Critério de sucesso:* queda mensurável em `redundant_tool_calls` nos turnos que carregam uma
memória derivada de telemetria.

**Ressalva sobre a Fase 3.** É a de retorno mais incerto. `turn_quality_metrics` mede o
comportamento do agente, não o mundo; transformar "gastei 40 tool calls" numa memória acionável é
muito mais difícil do que "o build aqui é pnpm". Se o critério de sucesso não se mover, a Fase 3
deve ser cortada em vez de expandida.

## 7. Fora de escopo

- Criação automática de Skills (permanece deliberada, com confirmação explícita do usuário).
- Embeddings ou busca vetorial para memória.
- Qualquer ferramenta nova publicada nas fases ORIENT/EXECUTE.
- Substituir o `AgenticTurnRuntime` pelo Kernel genérico de `Execution` (assunto do plano de 2026-08-26).
