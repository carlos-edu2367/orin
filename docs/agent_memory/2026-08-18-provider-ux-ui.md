# UX/UI dos providers

- A tela de detalhe dos providers usa `SettingsDrawer`; os fluxos de Ollama, provider genérico, chaves de API e catálogo compartilham os estilos de `frontend/src/styles/index.css`.
- O formulário do Ollama foi reorganizado em overview, endpoint/acesso, modo Local/Cloud, campos agrupados, toggle de disponibilidade e ações responsivas. Os contratos de API e o comportamento de credenciais write-only foram preservados.
- A lista de chaves agora tem cards, controles de ordenação, renomeação, adição, cooldown e um aviso visual: adicionar mais de uma chave ativa o fallback automático.
- O teste E2E de provider usa `exact: true` no botão `Salvar` para não confundir a ação principal com `Salvar tempo de cooldown`.
- A release correspondente é `0.2.7`, alinhada em `pyproject.toml`, `src/agentos/version.py`, `desktop/package.json` e `desktop/package-lock.json`.
- Validação desta alteração: frontend unitário 389/389, build de produção, E2E de providers 4/4 e visual focado de providers 2/2. A suíte visual completa ainda depende de API local em alguns cenários de chat/execution.
