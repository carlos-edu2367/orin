# Release desktop e CLI

O release final precisa ser um pacote único por versão, contendo `orin.exe` e
`Orin Desktop.exe` no mesmo diretório de instalação. O instalador atual em
`scripts/install-orin.ps1` prepara um checkout de desenvolvimento e não deve
ser reaproveitado como instalador de GitHub Release.

A GitHub Action de release deve bloquear divergência entre a tag, a versão de
`pyproject.toml` e `desktop/package.json`; o instalador de terminal deve baixar
um manifesto, validar SHA-256 e promover uma pasta versionada de modo atômico.
Isso impede que a CLI e o host Electron sejam atualizados separadamente.
