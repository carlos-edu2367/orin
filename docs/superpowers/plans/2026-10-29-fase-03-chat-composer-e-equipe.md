# Plano de Implementação — Fase 3: Conversa de Chat (`ref-03.png`), Composer Compacto & Equipe Multi-Agente (`ref-10.png`)

Espelho do artefato `plano-fase-03-chat-composer-e-equipe.md`:
1. **Tarefa 1 (Paralela):** `Composer.tsx`, `QueuePanel.tsx`, `MemoryLearnedCard.tsx`, `WorkspaceFileCard.tsx` + `Phase3ChatWidgets.test.tsx`.
2. **Tarefa 2 (Paralela):** `TeamActivityView.tsx`, `teamActivity.css` + `TeamActivityView.test.tsx` (`ref-10.png`).
3. **Tarefa 3 (Sequencial):** `ChatPage.tsx`, `agentos.css` + `ChatPageRef03AndTeam.test.tsx` (`ref-03.png` + integração `TeamActivityView`).
4. **Tarefa 4:** Verificação visual no Playwright + Auditoria por Subagente Revisor Independente (sem contexto de decisões) + Commit & Push em `refactor/interface`.
