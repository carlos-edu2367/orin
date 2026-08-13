# Runtime standalone Windows: SQLite e fila local

## Implementação

O runtime local do Orin agora usa um arquivo SQLite em `OrinPaths.data`, com
WAL, foreign keys, `busy_timeout` e conexões SQLAlchemy configuradas em
`agentos.persistence.sqlite`. O launcher sempre gera/força a URL SQLite no
perfil local, aplica as migrations existentes e não inicia Docker Compose.

O processo `worker` faz polling da tabela durável `conversation_dispatches`,
reivindica turnos por transição atômica já existente e preserva heartbeats,
watchdog, cancelamento e recuperação. Redis e ARQ saíram das dependências de
runtime; API, worker e scheduler permanecem processos separados.

## Empacotamento

`packaging/orin.spec` e `scripts/build-windows.ps1` preparam o launcher
PyInstaller, frontend e Chromium Playwright para `resources/runtime`. O
`desktop/package.json` inclui esse runtime como `extraResources`; o host
Electron continua carregando somente a API loopback.

O instalador de release é `install.ps1`: valida manifest/hash, faz staging e
rollback por versão, cria o shim `orin` e oferece o atalho `orin.exe --desktop`.
GitHub Actions/publicação ainda são uma etapa separada.

## OmniRoute

Por decisão do usuário, OmniRoute continua opcional e externo, instalado por
npm. Ele não é empacotado nem necessário para instalar/iniciar Orin.
