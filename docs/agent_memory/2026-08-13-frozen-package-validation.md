# ValidaÃ§Ã£o do pacote congelado Windows

O PyInstaller `onedir` coloca os assets em `runtime\\_internal`. O
`RuntimeProfile` conserva `runtime` como raiz lÃ³gica (para encontrar o host
Electron duas pastas acima) e pesquisa web, Chromium e instalador tambÃ©m em
`_internal`.

`packaging/frozen_entry.py` importa o CLI como pacote; usar
`agentos/launcher/__main__.py` diretamente quebra imports relativos. O spec
declara como imports ocultos os entrypoints dinÃ¢micos do backend, worker e
scheduler. A validaÃ§Ã£o local confirmou `dist\\runtime\\orin.exe --version` e
o ciclo completo SQLite, API, workers e frontend em `http://127.0.0.1:8000`.

Depois de gerar o runtime Ã© necessÃ¡rio executar o Electron Builder antes de
`scripts\\package-release.ps1`, pois o pacote Electron copia
`dist\\runtime` para `resources\\runtime`. O ZIP e `release.json` resultantes
foram validados por SHA-256 e pela presenÃ§a de `Orin Desktop.exe` e
`resources\\runtime\\orin.exe`.
