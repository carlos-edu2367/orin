# Navegador travando sempre no app instalado + linha duplicada do ask_user

- `packaging/frozen_entry.py` nunca chamava `multiprocessing.freeze_support()`. No
  `orin.exe` empacotado (Windows, PyInstaller), qualquer `multiprocessing.Process`
  com contexto `spawn` — inclusive `IsolatedConversationBrowser`, o host Playwright
  isolado por turno — reexecuta o próprio executável com argv tipo
  `--multiprocessing-fork parent_pid=... pipe_handle=...`. Sem `freeze_support()`
  logo no início, esse argv caía direto no `argparse` da CLI (`orin: error:
  argument command: invalid choice: 'parent_pid=...'`, visível em `worker.log`), o
  processo filho nunca chegava a existir, e o pai estourava em ~35s com "browser
  operation timed out" — sempre, independente do site. Corrigido chamando
  `multiprocessing.freeze_support()` como a primeira coisa em `frozen_entry.py`,
  guardado por `if __name__ == "__main__":`. É um no-op fora de um build congelado
  (`sys.frozen` não existe em dev), então não muda nada localmente. Regressão
  coberta em `tests/unit/launcher/test_frozen_entry.py`, simulando o argv de fork
  do PyInstaller com `sys.frozen=True` via `runpy.run_path`.
- Separadamente: toda chamada de `ask_user` sempre gera dois grupos de atividade
  na mesma turn — o `tool.started`/`tool.finished` (toolName `ask_user`, vira o
  `UserQuestionCard` interativo) e um evento de lifecycle `turn.waiting_user`
  (`runtime.py` emite os dois em sequência). Como são grupos diferentes
  (`groupingKey` distingue `tool:` de `lifecycle:`), os dois renderizavam: o card
  interativo, e logo abaixo/depois dele uma linha genérica "Aguardando sua
  resposta" que nunca se atualiza — mesmo depois de respondida a pergunta, fica
  parada dizendo "Aguardando você". Isso é o que o usuário via como "o card ficou
  no final, depois da resposta do agente". Corrigido excluindo
  `turn.waiting_user` de `isRenderable` em `activitySummary.ts` (mesmo padrão já
  usado para `turn.started`), já que o card do `ask_user` sozinho já comunica
  esse estado e, ao contrário da linha de lifecycle, atualiza corretamente quando
  respondido. Teste em `frontend/tests/unit/activitySummary.test.ts`.
