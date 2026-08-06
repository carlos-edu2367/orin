# Task 5 — Documentar Tools, Capabilities e recursos

Crie seis RFCs Markdown, sem código de produção, após ler fundações e Kernel:

- `docs/architecture/400-tools-resources/401-tool-runtime.md`
- `docs/architecture/400-tools-resources/402-resource-manager.md`
- `docs/architecture/400-tools-resources/403-filesystem.md`
- `docs/architecture/400-tools-resources/404-terminal.md`
- `docs/architecture/400-tools-resources/405-browser.md`
- `docs/architecture/400-tools-resources/406-capabilities.md`

Requisitos: Tool Registry/contrato uniforme com validação, autorização, execução, streaming, cancelamento e eventos; Tools atômicas e nunca chamam Tools. Capabilities orquestram múltiplas Tools, criam/operam Executions e respeitam permissões. Resource Manager aloca, isola, faz leasing/limpeza e audita Filesystem, Terminal e Browser. Filesystem só opera sob raiz canonicalizada de Workspace e bloqueia path traversal/symlink escape. Terminal é persistente (`id`, cwd, pid, status, owner, workspace, buffer), controlado/cancelável e isolado. Browser usa Playwright somente em Browser Workers; suporta perfis/sessões/páginas/cookies/DOM/screenshots/uploads/downloads e nunca acessa banco. Toda RFC traz objetivo, fora de escopo, responsabilidades, contratos tipados, dados, eventos, fluxos normal/falha/cancelamento, segurança, observabilidade, invariantes, extensibilidade e futuro. Use user/workspace/agent/execution/correlation/purpose explicitamente nas operações sensíveis. Links relativos válidos. 

Ao fim, criar `task-5-report.md` no diretório SDD com status, arquivos e verificações. Responder apenas o resumo.
