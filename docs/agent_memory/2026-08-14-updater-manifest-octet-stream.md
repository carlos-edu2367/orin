# Memória técnica — updater e manifests de release

- GitHub Release assets podem responder `release.json` com `Content-Type: application/octet-stream`. Em diferentes versões do PowerShell, `Invoke-RestMethod`/`Invoke-WebRequest` pode devolver texto ou `byte[]`, em vez de desserializar JSON automaticamente.
- O instalador deve baixar o corpo com `Invoke-WebRequest`, decodificar `byte[]` como UTF-8, remover BOM e chamar `ConvertFrom-Json` explicitamente. A validação posterior continua exigindo versão SemVer, URL oficial e SHA-256 de 64 caracteres.
- O empacotador agora grava `release.json` em UTF-8 sem BOM. Isso melhora compatibilidade, mas não substitui o parse explícito porque o CDN de assets continua usando `application/octet-stream`.
- Uma instalação antiga que já contém o instalador defeituoso não consegue se autoatualizar pelo comando `orin update`; é necessário executar uma vez o `install.ps1` da release mais recente diretamente. Depois disso, os updates normais usam o parser corrigido.
