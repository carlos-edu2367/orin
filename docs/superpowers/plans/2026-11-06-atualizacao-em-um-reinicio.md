# Atualização concluída num único "Reiniciar" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um clique em "Reiniciar para atualizar" basta para o Orin abrir na versão nova. Se a atualização falhar, o banner diz o motivo, em vez de oferecer de novo o mesmo "Reiniciar".

**Architecture:** O `orin update --apply --restart` hoje roda dentro do Job Object do launcher, que tem `KILL_ON_JOB_CLOSE`, e morre junto com o Orin que ele mesmo encerra. A correção tem três partes:
- o apply sai do job antes de parar o Orin, e o job passa a permitir só essa saída explícita (`BREAKAWAY_OK`);
- o apply grava log e resultado em disco, inclusive quando falha;
- o banner passa a ler esse resultado.

**Tech Stack:** Python (launcher, updater e `ctypes` com a API Win32), Electron (`desktop/electron/main.cjs`), React (`frontend/src/components/UpdateBanner.tsx`) e Vitest.

**Spec:** não há spec. A base é o diagnóstico abaixo, tirado do PC do Carlos em 2026-11-06.

## Diagnóstico (evidência)

`C:\Users\User\AppData\Local\Orin\logs\launcher.log` e `C:\Users\User\AppData\Local\Programs\Orin\` mostram esta sequência:

| Hora | Evento |
|---|---|
| 14:36 | `prepared.json` da 0.12.1 é gravado (download pronto) |
| 14:45:45 | 1º clique: o launcher recebe "stop requested" |
| 14:46:32 | O Orin abre **de novo na 0.11.6**. A ativação não aconteceu, e o banner "Versão 0.12.1 baixada" volta |
| 14:46:45 | 2º clique: stop |
| 14:46 | A junction `current` passa a apontar para `0.12.1` |
| 14:47:49 | O Orin abre na 0.12.1 |

Depois disso, ficaram resíduos:
- `prepared.json` (0.12.1) continua em disco;
- `update-state.json` está parado em `"current": "0.11.0"` desde 30/10, apesar das atualizações até a 0.11.6.

Nenhuma das duas atualizações gravou o resultado nem limpou o `prepared.json`. Isso indica que o processo do apply **morreu no meio**, nas duas vezes:
- no 1º clique, antes do `os.replace`;
- no 2º clique, entre o `point_current` e o `_write_state`, provavelmente durante o `_smoke`.

**Causa provável:**
- `src/agentos/launcher/processes.py` coloca cada filho num job com `KILL_ON_JOB_CLOSE` (`LimitFlags = 0x2000`), e o docstring diz: "a process spawned by a child inherits the job".
- O Electron é filho do launcher. O apply é filho do Electron (`main.cjs:226`, que no Windows usa `detached: false`). Então o apply está no job.
- `stop_before_switching()` encerra o launcher, o kernel fecha o job e mata o apply.
- O ponto exato da morte varia com a corrida contra o fechamento do job, o que explica por que às vezes funciona no 2º clique.
- O `--restart` também não reabre nada, porque o processo que reabriria já morreu.

Os dados da 0.12.1 estão íntegros, então não é preciso agir no PC do Carlos. Só sobra o `prepared.json` velho, que já é ignorado (`prepared_release()` exige `0.12.1.ready/`).

## Global Constraints

- O launcher continua sem deixar órfãos. `BREAKAWAY_OK` só permite sair do job com `CREATE_BREAKAWAY_FROM_JOB` explícito, e shims e gateways não usam esse flag.
- Toda mensagem de banner fica em pt-BR, curta, sem caminho de arquivo nem stack trace.
- O rollback automático (`_activate`/`_restore`) não pode piorar.
- Linux e macOS não mudam de comportamento. O `detached: true` deles já tira o processo do grupo.

## Review Focus

1. **Falha no meio da troca:** se o processo do apply morrer entre `point_current` e `_write_state`, o próximo Orin precisa reconhecer a versão ativa e não oferecer de novo a mesma atualização.
2. **Ambiente que não permite sair do job:** num job externo, como o de um terminal ou de uma sessão SSH, `CREATE_BREAKAWAY_FROM_JOB` dá `ERROR_ACCESS_DENIED`. O apply cai para o spawn normal e registra isso no log.
3. **Erro que não é `UpdateError`:** um `PermissionError` no `os.replace` (antivírus segurando o `.exe` recém-validado) precisa virar tentativa `failed`, gravada com motivo, e o Orin precisa ser reaberto.
4. **Banner depois de uma falha:** mostra o motivo e "Tentar de novo", nunca o mesmo "Reiniciar" sem explicação.
5. **Dois cliques rápidos:** o 2º clique não pode disparar um 2º apply concorrente. A trava `updating` do Electron morre com a janela, então a trava precisa ficar no disco.

---

### Task 1: Provar a causa com um teste de integração no Windows

**Files:**
- Create: `tests/integration/launcher/test_apply_survives_job_close.py`

**Interfaces:**
- Consumes: o job de `src/agentos/launcher/processes.py` (a classe que cria o job e expõe `adopt(pid)`; use o nome real da classe).

- [ ] **Step 1: Escreva o teste.** `test_child_of_adopted_process_dies_when_job_closes` (`skipif(os.name != "nt")`):
  1. cria o job como o launcher cria;
  2. adota um processo A (`python -c` que dorme);
  3. A gera um neto B, que escreve um arquivo-sentinela a cada 100 ms;
  4. fecha o handle do job;
  5. confirma que B parou de escrever.

  Isso reproduz o cenário Electron → apply.
- [ ] **Step 2: Rode e confirme que passa.** O teste documenta o comportamento atual. Se B sobreviver, a hipótese está errada: pare, volte ao diagnóstico e investigue antes de seguir.
- [ ] **Step 3: Adicione o teste que vai falhar.** `test_breakaway_grandchild_survives_job_close`: B é criado com `CREATE_BREAKAWAY_FROM_JOB` e precisa continuar escrevendo depois do fechamento. Esperado agora: FAIL, porque sem `BREAKAWAY_OK` a criação dá `ERROR_ACCESS_DENIED`.
- [ ] **Step 4: Commit** do teste vermelho marcado com `xfail(strict=True)`, para registrar a causa: `test(launcher): reproduce update apply dying with the launcher job`.

### Task 2: Job que permite saída explícita e apply fora do job

**Files:**
- Modify: `src/agentos/launcher/processes.py`, onde `LimitFlags = 0x2000` (por volta da linha 79)
- Modify: `src/agentos/launcher/cli.py`, no parser `update` (perto da linha 92) e em `command_update` (linhas 300 a 360)
- Modify: `desktop/electron/main.cjs:240-251` (`updateCommand`)
- Test: `tests/integration/launcher/test_apply_survives_job_close.py`
- Test: `tests/unit/launcher/test_update_detach.py`

**Interfaces:**
- Produces: a flag de CLI `orin update --apply --restart --detach`. No Windows, ela gera de novo o mesmo comando sem `--detach`, com `creationflags = CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, e sai com código 0. Se o breakaway der `OSError`, ela gera sem esse flag e registra `breakaway=denied` no log da Task 3. Fora do Windows, `--detach` não faz nada.
- Produces: `_spawn_detached_apply(argv: list[str], *, popen=subprocess.Popen) -> bool` em `cli.py`, que segue o padrão de `_spawn_runtime` em `src/agentos/cli_code/runtime.py:138`.

- [ ] **Step 1: Teste unitário.** `test_detach_respawns_with_breakaway_and_exits`: com um `popen` falso, `--detach` chama `popen` uma vez, com `CREATE_BREAKAWAY_FROM_JOB` nos `creationflags` e sem `--detach` no argv, e devolve 0. Escreva também `test_detach_falls_back_when_breakaway_denied`: o `popen` levanta `OSError` na 1ª chamada, e o 2º spawn acontece sem o flag.
- [ ] **Step 2: Rode e veja falhar.** `uv run pytest tests/unit/launcher/test_update_detach.py -q`.
- [ ] **Step 3: Implemente.** No job, use `LimitFlags = 0x2000 | 0x800` (`KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK`) e atualize o docstring para dizer que só a saída explícita escapa. Adicione `--detach` e `_spawn_detached_apply`. Em `updateCommand()` do `main.cjs`, acrescente `'--detach'`.
- [ ] **Step 4: Rode a unidade e a integração.** Tire o `xfail` da Task 1. Esperado: os dois testes da Task 1 e os dois da Task 2 passam no Windows.
- [ ] **Step 5: Commit.** `fix(update): run the apply outside the launcher job so stopping Orin cannot kill it`

### Task 3: O apply grava log e resultado em qualquer falha

**Files:**
- Modify: `src/agentos/installation/updater.py`: `apply_prepared` (linha 248), `_activate` (linha 425) e `_write_state` (linha 539)
- Modify: `src/agentos/launcher/cli.py`, em `command_update`, no `except UpdateError` (linha 340)
- Test: `tests/unit/installation/test_updater_apply_failures.py`

**Interfaces:**
- Produces: `Updater.record_failure(version: str, message: str) -> None`, que grava `last_attempt = {"status": "failed", "version", "message", "at"}` em `update-state.json`.
- Produces: o log do apply em `<logs>/update.log`, com logger `orin.update` e uma linha por etapa (`stop`, `validate`, `activate`, `smoke`, `state`, `relaunch`), com resultado e duração. O arquivo usa o mesmo diretório de `launcher.log` (`OrinPaths.logs`).

- [ ] **Step 1: Testes que falham.**
  - `test_os_error_during_replace_is_recorded_and_retryable`: `os.replace` levanta `PermissionError`. Esperado: `UpdateError` (não `PermissionError`), `last_attempt.status == "failed"`, a mensagem contém "não foi possível trocar a pasta", e `prepared_release()` continua devolvendo a 0.12.1.
  - `test_write_state_failure_is_logged_not_swallowed`: com `_write_state` falhando em `os.replace`, o logger `orin.update` registra um `WARNING`.
  - `test_restart_relaunches_even_on_unexpected_exception`: em `command_update`, quando `apply_prepared` levanta `RuntimeError`, `_relaunch_desktop` é chamado e o código de saída é 1.
- [ ] **Step 2: Rode e veja falhar.**
- [ ] **Step 3: Implemente.**
  - Em `_activate`, envolva o `os.replace` num retry de até 5 tentativas, com espera de 0,2 s × tentativa, para `PermissionError` no Windows, e converta o erro final em `UpdateError(step="activate")`.
  - Em `_write_state`, troque o `except OSError: pass` por log `WARNING`.
  - Em `command_update`, amplie o `except` para `Exception`, chame `record_failure` e mantenha o relaunch.
  - Configure o handler de arquivo `update.log` no começo de `command_update` quando `--apply` estiver presente.
- [ ] **Step 4: Rode.** `uv run pytest tests/unit/installation -q`. Esperado: passa.
- [ ] **Step 5: Commit.** `fix(update): record and log every apply failure and always reopen Orin`

### Task 4: Estado coerente depois de uma troca interrompida

**Files:**
- Modify: `src/agentos/installation/updater.py`: `prepared_release` (linha 235) e um método novo
- Modify: `src/agentos/installation/update_job.py:46-67` (`status`)
- Test: `tests/unit/installation/test_updater_reconcile.py`

**Interfaces:**
- Produces: `Updater.reconcile() -> None`. Ela é idempotente e roda no `status()`.
  - Se `active_version()` for maior ou igual à versão de `prepared.json`, apaga `prepared.json`.
  - Se `update-state.json["current"]` for diferente de `active_version()`, grava `previous` (o `current` antigo), `current` (a versão ativa) e `last_attempt = {"status": "updated", "version": <ativa>}`.

- [ ] **Step 1: Teste que falha.** `test_reconcile_after_switch_without_state_write` monta o estado real do PC do Carlos: `current → 0.12.1`, `prepared.json` com a 0.12.1, sem `0.12.1.ready/`, e `update-state.json` com `current: 0.11.0`. Esperado depois de `reconcile()`: `prepared.json` some, e o state fica com `current: "0.12.1"` e `previous: "0.11.0"`. Escreva também `test_reconcile_keeps_newer_prepared`: com `prepared` 0.12.2 e ativa 0.12.1, nada muda.
- [ ] **Step 2: Rode e veja falhar.**
- [ ] **Step 3: Implemente `reconcile()`** e chame-a em `UpdateJob.status()` antes de `prepared_release()`.
- [ ] **Step 4: Rode.** `uv run pytest tests/unit/installation -q`. Esperado: passa.
- [ ] **Step 5: Commit.** `fix(update): reconcile state after an interrupted switch`

### Task 5: Banner que explica a falha e trava de clique duplo

**Files:**
- Modify: `frontend/src/components/UpdateBanner.tsx`: `attemptNotice` (linha 22) e o bloco `state === 'ready'` (linha 66)
- Modify: `frontend/src/api/updateJob.ts`: o tipo `UpdateAttempt` passa a aceitar `status: 'failed'`
- Modify: `src/agentos/launcher/cli.py`: trava em disco
- Test: `frontend/tests/unit/UpdateBanner.test.tsx`
- Test: `tests/unit/launcher/test_update_detach.py`

**Interfaces:**
- Consumes: `last_attempt.status === 'failed'` e `last_attempt.message`, gravados pela Task 3.
- Produces: o arquivo de trava `<run>/update-apply.lock`, criado com `O_EXCL`. Ele guarda o PID do apply e é apagado no `finally`. Se já existir com um PID vivo, um 2º `--apply` sai com 0 sem fazer nada. Se o PID estiver morto, o arquivo é tratado como velho e é substituído.

- [ ] **Step 1: Testes que falham (Vitest).** Crie `mostra o motivo quando a última tentativa falhou e a mesma versão ainda está pronta`. Com `state: 'ready'`, `version: '0.12.1'` e `last_attempt: {status: 'failed', version: '0.12.1', message: 'X'}`, o banner tem eyebrow "Atualização não concluída", título com "0.12.1", hint contendo "X" e botão "Tentar de novo", que chama `applyUpdate`. Crie também `não mostra 'pronta' para a versão que já está rodando`: com `state: 'ready'`, `version` igual a `current_version` e nenhum `last_attempt`, o componente devolve `null`.
- [ ] **Step 2: Teste que falha (pytest).** `test_second_apply_exits_when_lock_held_by_live_pid`.
- [ ] **Step 3: Rode os dois e veja falhar.**
- [ ] **Step 4: Implemente.** No banner, mantenha o mesmo componente `Banner` e o texto atual dos outros estados.
- [ ] **Step 5: Rode.** Em `frontend/`, rode `npx vitest run tests/unit/UpdateBanner.test.tsx`, `npm run lint` e `npm run build`. Rode também `uv run pytest tests/unit/launcher -q`. Esperado: tudo passa.
- [ ] **Step 6: Commit.** `fix(update-ui): explain a failed apply and ignore double clicks`

### Task 6: Teste real de ponta a ponta (manual, com o Carlos)

- [ ] **Step 1:** gere duas builds locais, `X` e `X+1`, com `npm run build` e o empacotamento usual, instale a `X` e sirva a `X+1` num `release.json` local (`ORIN_UPDATE_BASE_URL` ou o equivalente em `default_base_url()`).
- [ ] **Step 2:** abra pelo atalho, baixe e clique uma vez. Esperado:
  - o Orin reabre sozinho na `X+1`;
  - `update-state.json` tem `last_attempt.status == "updated"`;
  - `prepared.json` some;
  - `update.log` tem todas as etapas.
- [ ] **Step 3:** repita com o antivírus segurando o `.exe`. Para simular, deixe o `orin.exe` da `X+1` aberto num `Get-Content -Wait`. Esperado: banner "Atualização não concluída" com o motivo, Orin reaberto na `X`, e "Tentar de novo" funcionando depois que o arquivo é liberado.
