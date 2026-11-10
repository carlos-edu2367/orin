# Harness: trabalhos longos que dependem de processos demorados

Data: 2026-11-10 · Caso de estudo: conversa `chat_91efd7f7d3814269acd2fedfa1ec5820` (produção do vídeo
"Space Mercenary", projeto `pessoal/coyotin`), 4 turnos em 09/11/2026.

## O que aconteceu (evidência: orin.db, worker.log, .orin/logs do projeto)

| Turno | Provedor | Fim | Causa real |
|---|---|---|---|
| 1 (17:47–18:03 UTC) | Claude Opus | `cancelled` pelo usuário | O principal esperou 6 subagentes com `sleep 540` e depois `sleep 240` em primeiro plano. 5 relatórios ficaram prontos às 17:59:29, mas o `sleep` não acorda quando um subagente termina, e o usuário cancelou. O `general-5` caiu com `PROVIDER_STREAM_INTERRUPTED` depois de 515 s (`worker.log` 14:58:11 local, `exit=1`, `stderr_tail=''`) e não foi retomado. |
| 3 (18:45–19:05) | agy / gemini-4-argon-high | `paused` / `EFFECT_RECONCILIATION_REQUIRED` | 78 efeitos de ferramenta `APPLIED`. O TTS rodava em background (`pid 3144`). O agente acompanhava o log com `read_file` e, na iteração 79, ficou **exatamente 180 s** sem emitir nada ("pensando há 20…180 s"). O `IDLE_TIMEOUT_SECONDS=180` do Claude, reaproveitado pelo agy, matou o processo (`native_loop.py:669-678`). O efeito PROVIDER virou `UNKNOWN` e o turno ficou pausado para sempre. |
| 4 (19:05–19:08) "travou?" | agy | `paused` / `EFFECT_RECONCILIATION_REQUIRED` | A compactação automática removeu 200 mensagens (65k → 10k tokens). Sem estado de trabalho, o agente releu o roteiro 3 vezes, o manifesto 4 vezes e o script várias vezes em 2 min. Depois o agy saiu com `exit=1` (106 s) **1 s depois** de uma ferramenta, ou seja, não foi o idle timeout. O motivo não ficou registrado: `stderr_tail=''` e o log não diz se foi EOF, erro no `result` ou outra coisa. |
| depois | — | — | O TTS (`pid 3144`) continuou sozinho, terminou as 15 levas e quebrou no QC (`ValueError: Soundfile ... malformed`). `processes.json` marca `notified_exit: false`: ninguém foi avisado e nenhum turno acordou. |

Na tela, o usuário viu "Aguardando reconciliação de efeito externo · 1 falha" e nenhuma ação disponível.

## Causas no código

1. **Idle timeout fixo e emprestado.** O agy roda no loop nativo do Claude (`claude_cli/native_loop.py:624`)
   com `IDLE_TIMEOUT_SECONDS = 180` (`claude_cli/command.py:15`). O timer só pausa com uma ferramenta MCP em
   andamento (`in_flight`), não enquanto o modelo pensa sem emitir. O Gemini "high" passou de 100 s várias vezes
   no mesmo turno.
2. **Stream cortado = pausa definitiva.** Em `native_turn.py:2600-2621` e `:2642-2663`, se houve saída visível ou
   alguma ação, o efeito PROVIDER vai para `UNKNOWN` e o turno retorna `reconciliation_required`, **antes** de
   `_recover_provider_failure` (`:2671`). Retry e fallback só existem para falhas sem saída.
   Isso é conservador demais: cada ferramenta já tem efeito próprio no ledger (`execution_effects`), e o efeito
   PROVIDER em si não tem efeito externo.
3. **Ninguém reconcilia.** `pause_for_reconciliation` (`conversations/chat.py:2870`) não tem reconciliador,
   endpoint nem botão. A próxima mensagem abre um turno novo, sem `--conversation` e com o histórico em texto.
   O agy até sabe retomar (`antigravity_cli/command.py:723-738`), mas `supports_session_resume` sai `False`
   porque `persistent_stdin=True` (`native_turn.py:2018-2022`).
4. **Não existe espera boa por processo longo.**
   - O aviso "Background process X has exited" só vai junto do resultado da **próxima** ferramenta e é pulado
     justamente em `read_process_output` (`agent_tools.py:1095`). Nunca é empurrado ao modelo ocioso. Só
     subagentes e mensagens na fila são empurrados (`native_turn.py:2454-2515`).
   - No schema legado (padrão do modo Code), `read_process_output` só expõe `pid` e `tail_chars`, sem
     `block`/`wait_seconds` (`tools/shell/tasks.py:270-276`). O modelo só consegue fazer polling ou `sleep`.
   - Um processo que sobrevive ao turno fica sem dono.
5. **Subagente que cai não é retomado** (`session.py:3690-3728`): a regra de `had_visible_output` é a mesma.
6. **A compactação perde o estado operacional**: processos em background (pid, log), progresso da todo list,
   arquivos já gerados. Daí o loop de releitura no turno 4.
7. **Observabilidade**: `log_cli_run` (`claude_cli/provider.py:279`) não grava o motivo do término (idle,
   EOF, `result` de erro, deadline). O agy não recebe `tool_timeout_ms` (`antigravity_cli/adapter.py:119-126`).

## Plano (em ordem de prioridade)

### Fase 1: motivo de término e mensagem legível (pequena, destrava o resto)
- `NativeTurnHandle` registra `end_reason` ∈ {`idle_timeout`, `stdout_eof`, `error_result`, `deadline`,
  `cancelled`} e as últimas ~20 linhas de stdout não-JSON. Vai para `log_cli_run` e para o payload de
  `turn.failed`.
- Na UI, trocar "Aguardando reconciliação de efeito externo" por algo como "O modelo parou de responder
  (sem saída por 180 s). O trabalho feito até aqui está salvo." e mostrar o botão **Retomar** (Fase 2).
- Testes: fake CLI que (a) fica mudo por mais que o idle, (b) fecha stdout e (c) devolve `result` de erro.
  O teste confere o `end_reason` de cada caso.

### Fase 2: retomada automática segura após `PROVIDER_STREAM_INTERRUPTED`
- Regra: se **nenhum** efeito TOOL do turno está em `STARTED`/`UNKNOWN`, o efeito PROVIDER é reconciliado como
  `NOT_APPLIED` (o texto parcial já está salvo) e o turno continua. O runtime abre um processo novo com o
  histórico atual, incluindo os resultados de ferramenta já aplicados e uma nota "a sessão anterior caiu, continue
  de onde parou". Limite: `max_stream_resumes` (ex.: 2) por turno, com backoff.
- Se há ferramenta em `STARTED`/`UNKNOWN`, a pausa continua, mas ganha o botão **Retomar**: um endpoint
  `POST /v1/conversations/{id}/turns/{turn_id}/resume`, classificado em `test_profile_isolation.py`, que marca
  a ferramenta pendente como `UNKNOWN_ACKED` e informa isso ao modelo.
- `recover_stale` (`workers/chat.py:899-920`) aplica a mesma regra aos turnos pausados quando o worker reinicia.
- Avaliar o `--conversation <id>` do agy na retomada (precisa confirmar se a sessão sobrevive ao kill). Se não
  sobreviver, a retomada por histórico basta.
  **Avaliado em 10/11/2026 (agy 1.5.5):** a sessão sobrevive ao `taskkill /T /F`, e `--conversation` lembra o
  histórico e aceita várias mensagens no stdin persistente. Mas o agy grava a URL/porta e o token do servidor MCP
  do turno em `~/.gemini/antigravity-cli/conversations/<id>.db` (`trajectory_metadata_blob`). Um processo novo,
  com o servidor em outra porta, falha nas ferramentas do Orin (`unknown tool: "mcp_orin_..."`), e o botão Retomar
  e o `recover_stale` nunca têm a porta antiga. Decisão: manter a retomada por histórico.
  Evidência em `C:/tmp/agy-resume-eval/`.
- Subagentes (causa 5) usam a mesma rotina. Um subagente que cai retoma uma vez antes de o pai receber
  `delegation.failed`.

### Fase 3: idle timeout por adapter e ciente do processo
- `AdapterCapabilities.idle_timeout_seconds` (Claude 180 s; agy 600 s, a calibrar com os dados de "pensando há").
- Antes de matar, o loop confere se o processo ainda está vivo. Vivo e dentro do deadline do turno, ele emite
  `waiting_model` e estende uma vez. O kill só vem quando o processo está mudo **e** estourou o teto.
- Investigar se o agy aceita timeout de ferramenta MCP (env/flag) e repassar `tool_timeout_ms`.

### Fase 4: esperar processos longos sem polling
- `read_process_output`/`TaskOutput` no schema legado ganham `block`, `wait_seconds` (≤ 600) e
  `until_pattern` (regex no log). Durante a espera, `in_flight > 0` mantém o idle e a UI mostra o tail do log
  como progresso.
- O aviso de saída de processo também passa a vir em `read_process_output`. Quando o modelo devolve `result` com
  um processo do turno ainda vivo, o runtime faz o que já faz com subagentes: espera com
  `wait_any(processos ∪ subagentes)` e reinjeta "processo X terminou (exit N), tail: ...".
- Processo que sobrevive ao turno (turno pausado ou concluído) registra um evento na conversa ao terminar
  ("TTS terminou com erro, exit 1") e grava `notified_exit` de forma persistente. Isso pode virar um "acordar"
  opcional via `ScheduleWakeup`.
- `sleep N` em primeiro plano acima de ~60 s no `run_command` vira uma espera interrompível, que acorda quando
  um subagente ou processo em background termina. O guia de ferramentas passa a dizer para esperar com
  `check_subagent`/`TaskOutput block`, e não com `sleep`.

### Fase 5a: autocompact só perto do limite da janela

Aprovado em 10/11/2026: 90% da janela, com teto padrão de 400k tokens (`AUTOCOMPACT_MAX_TOKENS`) para janelas
grandes, ou seja, `limiar = min(90% da janela, janela − reserva − 13k, 400k)`. Ordem de execução das fases:
1+2 → 3 → 5a/5b → 4.

Hoje (fatos no código):
- Turno nativo (agy, Claude CLI, Codex): resume o histórico anterior sempre que ele passa de
  `MAX_NATIVE_PRIOR_HISTORY_CHARS = 120_000` caracteres (~30k tokens), seja qual for a janela
  (`native_turn.py:75`, `:1979`). Foi isso que compactou o turno 4 com 65k de 1M tokens (6,5%), e o resumo levou 54 s.
- Na montagem da primeira mensagem, o histórico acima do mesmo orçamento perde os blocos de ferramenta antigos
  **sem resumo** (`native_turn.py:1834-1859`).
- No início de cada turno nativo, todo resultado de ferramenta de turnos anteriores encolhe para 1.600 caracteres
  (`native_turn.py:2062`, `keep_recent=0`). No loop clássico, a cada iteração, encolhe tudo o que não está entre os
  8 mais recentes (`runtime.py:1367`).
- Loop clássico: microcompact a 65% (`runtime.py:60`) e resumo em `janela − (saída ou 20k) − 13k`
  (`runtime.py:1797-1810`), que é a fórmula do Claude Code. Sem janela conhecida, o resumo dispara a 82%.
- Subagentes herdam `max_context_tokens` do pai e não recebem `context_window_tokens` (`session.py:3378-3385`).
  Por isso caem nos 82% do limite do pai, mesmo rodando num modelo com outra janela.

Proposta:
- Gatilho único para principal e subagentes:
  `limiar = min(90% da janela, janela − reserva de saída − 13k)`, com piso para janelas pequenas.
  Exemplos: 200k → 167k (83%); 400k → 360k (90%); 1M → 900k (90%).
- Medir com os tokens reais da última chamada (`context_window_used_tokens`) quando o provedor informa, e
  usar chars/4 só como fallback.
- Remover o gatilho de 120k caracteres. O corte da primeira mensagem passa a ser em tokens e relativo à janela, e
  vale só como rede de segurança acima do limiar.
- Microcompact e envelhecimento de resultados passam a rodar só no limiar, como primeiro passo antes do resumo.
  Se liberarem espaço suficiente, o resumo não roda.
- Subagente usa a janela do próprio modelo.
- `--autocompact` do Claude CLI recebe o mesmo limiar em tokens. Verificar se o agy compacta dentro do processo;
  se não compactar, um turno muito longo depende do `_force_compact` reativo.
- A configuração `autoCompact` existente (número de tokens) continua servindo de teto opcional por usuário.
- Pré-requisito: Fase 3 (idle timeout por adapter). Com mais contexto, o modelo demora mais para começar a responder.

### Fase 5b: compactação que preserva o estado de trabalho
- O resumo de compactação ganha uma seção fixa gerada pelo runtime, e não pelo modelo: todo list atual,
  processos em background vivos (pid, comando, log, última linha), arquivos escritos no turno e as 5 últimas
  ações.
- Releitura do mesmo arquivo e trecho sem mudança de sha (o `read_file reaproveitado` já detecta) mais de 2 vezes
  seguidas gera um aviso ao modelo ("você já leu isso; o conteúdo está acima").

## Verificação proposta
- Unitários com fake adapter para cada fase: idle, EOF, retomada com ledger limpo e com ferramenta pendente,
  espera bloqueante com `until_pattern`, reinjeção da saída de processo e resumo de compactação.
- Integração: rota nova classificada em `tests/integration/api/test_profile_isolation.py`. Rodar
  `tests/integration` e `tests/unit` com as variáveis do app removidas (ver memórias).
- E2E manual: turno no agy que dispara um `python -c "import time; time.sleep(400)"` em background e espera por
  ele. O turno precisa terminar sozinho, sem pausa.
