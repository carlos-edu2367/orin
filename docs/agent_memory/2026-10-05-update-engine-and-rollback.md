# Motor de atualização em Python, com progresso e rollback automático

- `agentos.installation.updater.Updater` é o único caminho de instalar uma release (`orin update`, `POST /v1/installation/update` via `versions.start_update`). `install.ps1/.sh` continuam só para a primeira instalação e desinstalação (fases 3 e 4 do plano).
- Etapas (`STEPS`): check, download, verify, extract, validate, activate. O motor só emite `UpdateEvent`; `launcher/update_ui.py` renderiza (barra redesenhada em TTY, linhas simples em log) e `JsonRenderer` emite NDJSON para o app.
- Segurança: URL do pacote precisa começar com `<base>/download/`, SHA-256 validado, extração recusa links e caminhos que escapam (tar usa `filter="data"`), checagem de espaço (4x o pacote).
- Rollback automático: depois de apontar `current` para a nova versão, roda `current/.../orin --version`; se falhar, volta o ponteiro, apaga a versão nova e `UpdateError.rolled_back=True`. `before_activate` (parar o Orin em execução) roda só depois do teste da versão staged, então falha antes disso não derruba nada.
- `orin update --rollback` usa `update-state.json` (`previous`). Flag da versão exata é `--to` (não `--version`, que colide com o `--version` global).
- Windows: `current` é junction (`_winapi.CreateJunction`), POSIX: symlink trocado atomicamente. O caminho Windows não foi exercitado em teste automatizado (os runtimes falsos são scripts shell); validar num build real.
