# Chromium opcional, instalado sob demanda

- O Chromium saiu do pacote (PyInstaller, `build-*.sh/ps1`). O release leva só o **driver** do Playwright (`playwright/driver`, incluso no `orin.spec`); os scripts de build agora falham se o driver faltar ou se um Chromium aparecer no pacote.
- `agentos.browser.engine` detecta (`INSTALLATION_COMPLETE` em `chromium-*`/`chromium_headless_shell-*`), instala em thread (driver `install chromium`, progresso lido do stdout) e expõe estado: `ready | missing | installing | failed | unsupported`.
- Destino: `PLAYWRIGHT_BROWSERS_PATH`, por padrão `<cache>/playwright` (`launcher.environment.browser_directory`); um pacote antigo com Chromium embutido continua valendo.
- Superfícies: `GET /v1/runtime/browser`, `POST /v1/runtime/browser/install` (capability `ui_updater`), Configurações > Navegador, e `orin browser install|status`.
- Agente: com o pacote `playwright` mas sem binário, `browse_page` é publicado como ferramenta-guia (`AgentToolset`) e o prompt manda explicar a instalação ao usuário; sem o pacote, nenhuma ferramenta de browser (como antes). A fábrica `conversation_browser_for` só cria browser com Chromium instalado.
- Testes que abrem Chromium de verdade seguem exigindo `playwright install chromium` (CI já faz).
