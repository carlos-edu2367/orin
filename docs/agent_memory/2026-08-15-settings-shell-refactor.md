# Settings shell refactor

- O shell de Settings agora usa `frontend/src/features/settings/sections.ts` como fonte declarativa das dez seções, incluindo navegação lateral, badges degradáveis e rotas `/settings/*`.
- `/providers`, `/skills` e `/schedules` são aliases compatíveis que redirecionam para o shell; a superfície legada de componentes continua disponível apenas onde testes e compatibilidade exigem.
- Providers usam cards com marcas locais e detalhe em região não modal. O cliente continua recebendo apenas estado público; chaves, tokens, secrets e hints sensíveis são filtrados antes da renderização e nunca aparecem em logs ou SSE.
- O detalhe de provider centraliza configure, revoke, catálogo, favoritos, Ollama, OmniRoute, erros sanitizados e `retry_after`, mantendo idempotência e CSRF do cliente existente.
- Validação desta implementação: frontend unitário 60 arquivos/323 testes, backend unit/integration 1463 passed/68 skipped, typecheck, lint e build passaram; E2E de providers 4/4 e acessibilidade do shell 10/10 passaram. O E2E que depende do backend real ainda deve ser repetido com a API local em execução; sem ela o Vite registra `ECONNREFUSED 127.0.0.1:8000` e os badges entram no estado degradado esperado.
