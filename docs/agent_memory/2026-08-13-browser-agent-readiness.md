# Browser para agentes: estado de prontidão

- O domínio `src/agentos/browser` tem contratos, grants, limites, política e testes. Em 2026-08-13, Playwright Chromium passou a ser dependência do projeto e `scripts/install-browser.ps1` provisiona a engine.
- O runtime local usa `IsolatedConversationBrowser`: cada turno cria um subprocesso Chromium que recebe apenas comandos via pipe. O worker de chat não possui um objeto Playwright. As ferramentas disponíveis são browse/observe/click não-submissivo/fill não-password/press seguro/select/check/screenshot.
- Toda observação grava uma PNG limitada em `browser-captures/` dentro do workspace da conversa. O SSE recebe apenas o path sanitizado; `BrowserActivityCard` mostra a miniatura pela rota autenticada e abre o preview existente. Nunca enviar bytes, cookies ou segredos por SSE.
- Submit, senha, JavaScript arbitrário, cookies, upload/download, clipboard, câmera e geolocalização seguem bloqueados até existir approval/profile/broker durável. A análise e o plano completo estão em `docs/audits/2026-08-13-browser-agent-readiness.md` e `docs/plans/2026-08-13-browser-agents-production-plan.md`.
