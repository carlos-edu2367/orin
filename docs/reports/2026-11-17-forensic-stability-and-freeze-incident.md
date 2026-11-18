# Relatório Forense de Estabilidade: Congelamento do Orin em 17/11/2026

## 1. Resumo Executivo

Ao chegar em casa por volta das **20:38–20:39 BRT** de **17/11/2026**, o Orin apresentou um congelamento severo (~60 a 74 segundos de irresponsividade na interface e no envio de mensagem), seguido de recuperação espontânea por volta de **20:40:05 BRT**.

A auditoria cruzada entre o **Windows Event Log (`System` / `Application`)**, a **árvore de processos Win32**, os logs de runtime (`%LOCALAPPDATA%\Orin\logs\*.log`) e a telemetria em microssegundos do banco **`%LOCALAPPDATA%\Orin\data\orin.db` (704 MB)** comprovou que o congelamento foi resultado da **convergência de três fatores reais**:

1. **Gatilho físico/SO exato no minuto do incidente (`stornvme` Event ID 129 no drive `C:`):**
   - O drive `C:` (`SSD UP2000 M.2 512GB`, barramento NVMe `\Device\RaidPort2`) sofreu **dois timeouts/resets consecutivos de controlador NVMe** exatamente às **`20:38:43` BRT** e **`20:39:53` BRT** (além de um às `17:18:00` BRT e **36 ocorrências nos últimos 7 dias**). Cada Evento `129` bloqueia todo I/O síncrono em `C:` no kernel do Windows pelo timeout do StorPort (~30 segundos).
   - Embora a tela aberta parecesse ociosa (`Orin Friend`), **havia uma sessão pesada rodando em segundo plano**: `True Crime #002: Chris Watts` (`chat_9b505406`, `turn_8c016b22`, iniciada às `18:53:19` BRT), que às **`20:38:03.5` BRT** acabara de disparar o subagente `antigravity-11` (`PID 10100`, `agy.EXE` + `rea.mjs mcp` 252 MB + `@playwright/mcp` 64 MB), logo após o término do cron `freelas e vagas` (`19:00:00`–`20:23:07` BRT).
2. **Amplificadores e bugs de I/O / concorrência SQLite dentro do Orin:**
   - **Tempestade de escritas de heartbeat sem throttle:** `publisher.poll_once` (`0,35 s`) e `scheduler.main` (`1,0 s`) ignoravam o throttle de `10 s` de `ChatWorker._refresh_heartbeat_if_due()`, executando **3,86 transações de escrita SQLite WAL por segundo (~333.000 commits/dia)** mesmo com o Orin ocioso.
   - **Upgrade de snapshot `BEGIN DEFERRED` (`SELECT` antes de `INSERT`/`UPDATE`):** `PostgresAgenticActivityStore.record_event()` e `PostgresChatStore.heartbeat()` abriam leitura antes da escrita na mesma transação, causando contenção/falha de upgrade no WAL sobre o `orin.db` de **704 MB** (`199.253` linhas em `conversation_activity_events` = 318 MB c/ índices; `67.043` linhas em `conversation_turn_steps` = 291 MB). Quando a mensagem `"como ta o progresso?"` (`turn_2717eab1`) foi enviada às **`20:39:08.556` BRT**, o `with self._engine.begin()` de `send()` ficou bloqueado por **32,367 segundos** até **`20:39:40.923` BRT**, sofrendo em seguida o segundo stall NVMe (`20:39:45.520` -> `20:40:04.617` BRT, **+19,1 s**).
   - **Varredura `rglob("*")` sem poda de diretórios no `ProjectIndexer`:** cada scan abria handles Win32 (`GetFinalPathNameByHandleW` + `stat()`) em dezenas de milhares de arquivos dentro de `node_modules/`, `.venv/` e `.git/` antes de consultar o `IndexFilter`.
   - **Desserialização integral de payloads em `overview()`:** lia os blobs completos de `payload` de todos os `tool.finished` da conversa em Python.
3. **Vazamentos de memória, threads, arquivos `%TEMP%` e subprocessos:**
   - **`atexit.register(self.close)` em `ClaudeCliProvider.__init__`:** prendia permanentemente na RAM do `worker` (`574,5 MB` Private, `PID 21948`) todas as instâncias de `ClaudeCliProvider` criadas por turno/subagente e vazou **44 arquivos `orin-claude-sys-*.prompt.txt` em `%TEMP%`**.
   - **Recriação de `OllamaEmbedder` a cada turno/busca:** descartava o cooldown de 300 s de HTTP 404 e vazava instâncias de `httpx.Client`, gerando **106 requisições `POST http://localhost:11434/api/embed` (404)** no `worker.log` hoje.
   - **`BackgroundAgentRegistry._executors`:** criava um `ThreadPoolExecutor(max_workers=32)` por conversa sem nunca encerrá-lo ao fim dos turnos (`worker` acumulou **51 threads**).
   - **Subprocessos filhos de CLIs no Windows:** cada instância de `agy.EXE` carrega MCPs globais de `~/.gemini/antigravity/mcp_config.json` (`rea.mjs mcp` = ~254 MB + `@playwright/mcp` = ~65 MB, somando ~650 MB por slot CLI; às `14:52:28` o Windows WER registrou `RADAR_PRE_LEAK_64` para `agy.EXE`), e saídas com `exit=1` podiam escapar sem encerrar processos netos (encontrados 2 pares `cmd.exe -> Antigravity.exe` órfãos desde `16/11/2026`: PIDs `5520/172` e `6848/13352`).

---

## 2. Linha do Tempo Forense Comprovada (17/11/2026 — Horário Local BRT `-03:00`)

| Horário (BRT) | Fonte | Evento Comprovado |
|---|---|---|
| `08:07:43–52` | `launcher.log` / Win32 | Inicialização do Orin `0.12.30`: `launcher` (`12008`), `Orin Desktop` (`14376`), `backend` (`9952`), `worker` (`21948`), `scheduler` (`21080`). |
| `08:52:10` | Event Log `System` | `powercfg.exe` ativa o plano `Desempenho Máximo` (`641ef8fb-...`), onde `Primary NVMe Idle Timeout = 200 ms` e `Primary NVMe Power State Transition Latency Tolerance = 0 ms`. |
| `14:52:28` | Event Log `Application` | Windows Error Reporting (`Event 1001`): `RADAR_PRE_LEAK_64` disparado para `P1: agy.EXE` durante turnos paralelos com múltiplos MCPs Node. |
| `17:18:00` | Event Log `System` | `stornvme` **Event ID 129**: `Redefinir para dispositivo, \Device\RaidPort2, emitido.` — gap comprovado de `20,0 s` + `12,8 s` nos eventos de `orin.db` (`17:17:45` -> `17:18:18`). |
| `18:53:19` | `orin.db` / Win32 | Início do turno `turn_8c016b22` (`True Crime #002: Chris Watts`, `PID 10104` `agy.EXE` em `run-21948-0`), que permaneceu rodando continuamente em background. |
| `19:00:00–20:23:07` | `orin.db` / `worker.log` | Execução agendada `freelas e vagas` (`turn_a8a2f2a8`) em paralelo com `True Crime #002`, disparando 6 subagentes. |
| `20:35:12` | `worker.log` | Subagente `antigravity-9` de `True Crime #002` conclui após `5773,7 s` (96 min). |
| `20:38:03.5` | `orin.db` / Win32 | `True Crime #002` dispara novo subagente em background `antigravity-11` (`PID 10100` `agy.EXE` + `PID 21596` `rea.mjs` 251,5 MB + `PID 12924` `@playwright/mcp` 63,7 MB). |
| **`20:38:34–20:38:54`** | Event Log + `orin.db` | **Congelamento #1:** `stornvme` **Event ID 129** em `\Device\RaidPort2` (`C:`) às **`20:38:43`**. Gap de `20,1 s` sem gravações em `orin.db`. |
| **`20:39:08.556`** | `orin.db` | Carlos envia `"como ta o progresso?"` no `Orin Friend` (`turn_2717eab1`). Em condições normais o commit leva `0,15–0,35 s`. |
| **`20:39:08.5 -> 20:39:40.9`** | `orin.db` | **Bloqueio de 32,367 s no commit SQLite de `send()`:** `Turn queued` só consegue gravar às `20:39:40.923` BRT (mesmo décimo de segundo em que `check_subagent` destrava às `20:39:41.029`). |
| **`20:39:45.5 -> 20:40:04.6`** | Event Log + `orin.db` | **Congelamento #2:** segundo `stornvme` **Event ID 129** em `\Device\RaidPort2` (`C:`) às **`20:39:53`**. Gap de `19,1 s` em todos os processos. |
| `20:40:04.6–20:40:22.4` | `orin.db` | Controlador NVMe recupera; UI destrava e `turn_2717eab1` conclui a resposta às `20:40:22.417` BRT. |

---

## 3. Patches de Estabilidade Implementados no Repositório

1. **Tuning SQLite/WAL para bancos grandes (`src/agentos/persistence/sqlite.py`):**
   - Adicionados `PRAGMA wal_autocheckpoint=4000` (16 MB), `PRAGMA journal_size_limit=67108864` (64 MB), `PRAGMA temp_store=MEMORY` e `PRAGMA cache_size=-32768` (32 MB de page cache por conexão).
2. **Eliminação de snapshot-upgrade `SELECT -> INSERT/UPDATE` nos caminhos quentes (`src/agentos/persistence/postgres/agentic_activity.py`, `src/agentos/conversations/chat.py`):**
   - `record_event()` agora executa `INSERT` otimista primeiro (adquirindo lock de escrita imediatamente no SQLite) e só consulta `_existing_by_event_id()` em caso de `IntegrityError`.
   - `heartbeat()` agora executa `UPDATE` direto primeiro e só faz `INSERT` quando `rowcount == 0`.
3. **Throttle real de heartbeats no Worker e no Scheduler (`src/agentos/workers/publisher.py`, `src/agentos/workers/chat.py`, `src/agentos/workers/scheduler.py`):**
   - `poll_once` (`0,35 s`) passou a respeitar `ChatWorker._refresh_heartbeat_if_due()` (`10 s`, com primeiro pulso imediato mesmo em boot recente), e `scheduler.main` (`1,0 s`) ganhou o mesmo intervalo de `10 s` — reduzindo as escritas ociosas no SQLite em **~95%** (~316.000 commits/dia a menos).
4. **Otimização de `overview()` via `json_extract` no SQLite (`src/agentos/conversations/chat.py`):**
   - Mantém o uso do índice composto `ix_conversation_activity_conv_type_id` em query única, extraindo apenas os campos escalares leves em C no SQLite sem transferir/desserializar dezenas de megabytes de `payload` JSON em Python (`Marketfy`: `291,6 ms -> 195,8 ms`; `freelas e vagas`: `175,2 ms -> 132,0 ms`).
5. **Fim do vazamento `atexit` / `%TEMP%` e Job Object Win32 para árvores CLI (`src/agentos/providers/claude_cli/provider.py`, `src/agentos/providers/claude_cli/native_loop.py`):**
   - Substituído `atexit.register(self.close)` por `weakref.finalize`, liberando instâncias de `ClaudeCliProvider` ao fim de cada turno/subagente e removendo automaticamente o `orin-claude-sys-*.prompt.txt`.
   - Anexado `_WindowsJob(kill_on_close=True)` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) aos subprocessos CLI reais no Windows para extinguir qualquer processo filho/neto remanescente ao encerrar o turno.
6. **Cache compartilhado de `OllamaEmbedder` e poda de diretórios no `ProjectIndexer` (`src/agentos/retrieval/factory.py`, `src/agentos/retrieval/indexer.py`):**
   - `build_embedder()` reutiliza instância thread-safe por `(base_url, model)`, preservando o cooldown de 300 s em HTTP 404 e evitando leak de `httpx.Client`.
   - `ProjectIndexer._candidates()` usa `os.walk(..., topdown=True)` podando `DENIED_SEGMENTS` (`node_modules`, `.venv`, `.git`, `.orin`, etc.) e diretórios `.gitignore` antes de descer neles.
7. **Reciclagem de `ThreadPoolExecutor` ociosos de subagentes (`src/agentos/agentic/background_agents.py`, `src/agentos/conversations/chat.py`):**
   - Pools por conversa são encerrados automaticamente quando não restam turnos nem subagentes em background ativos na conversa (e em `_forget_runtime_state`).

---

## 4. Recomendações Operacionais para a Máquina Windows (Fora do Escopo de Código)

1. **Desativar transição agressiva de estado de energia NVMe (APST) no plano `Desempenho Máximo` para eliminar os resets `stornvme` Event ID 129 no `SSD UP2000 M.2 512GB` (`C:`):**
   - Os 36 eventos `stornvme` ID 129 em 7 dias no `SSD UP2000 M.2 512GB` são típicos de SSDs NVMe DRAM-less falhando ao acordar de estados profundos de APST (com `Primary NVMe Idle Timeout = 200 ms` e `Transition Latency Tolerance = 0 ms` — o que também causa travadinhas severas de 0.1%/1% low em jogos instalados/cacheados no `C:`).
   - Comando recomendado em PowerShell (Administrador) para manter o NVMe primário ativo em AC:
     ```powershell
     powercfg /setacvalueindex SCHEME_CURRENT SUB_DISK d639518a-e56d-4345-8af2-b9f32fb26109 0
     powercfg /setacvalueindex SCHEME_CURRENT SUB_DISK fc95af4d-40e7-4b6d-835a-56d131dbc80e 0
     powercfg /setactive SCHEME_CURRENT
     ```
   - Verificar também ventilação/temperatura M.2 e eventual atualização de firmware do `SSD UP2000 M.2 512GB`.
2. **Revisar MCPs globais em `C:\Users\carlos\.gemini\antigravity\mcp_config.json` e `C:\Users\carlos\.gemini\settings.json`:**
   - Como o `agy.EXE` inicializa os MCPs globais do usuário (`rea.mjs mcp` consumindo ~254 MB e `@playwright/mcp` ~65 MB **por processo `agy.EXE`**), cada subagente paralelo adiciona ~320 MB extras de processos Node duplicados mesmo sem usar `rea`. Remover ou marcar `disabled: true` para `rea`/`diffusion` no `mcp_config.json` global quando não estiverem em uso ativo economiza ~250–320 MB de RAM por subagente.
