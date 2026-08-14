# Memória técnica — perguntas, versões de Skills e desktop

## Decisões

- Eventos públicos de `ask_user` mantêm o limite genérico para atividades, mas o campo estruturado `questions` usa orçamento próprio e continua limitado a 8 perguntas e 12 opções por pergunta. Isso evita rejeitar um lote válido ao contar o envelope inteiro como se fosse uma atividade arbitrária.
- Um turno `waiting_user` precisa atualizar o estado da conversa no snapshot e no SSE; enquanto houver perguntas abertas, o composer permanece enviável mesmo que o estado da conversa esteja momentaneamente atrasado.
- A remoção de versões é permitida apenas para versões antigas de Skills do escopo do usuário. A versão SemVer mais alta não pode ser removida, versões fixadas a agentes são protegidas e os snapshots históricos de execução permanecem intactos.
- O desktop expõe `DELETE /v1/skills/{skill_id}/versions/{version}` com autenticação, rate limit, autorização e isolamento por usuário. A UI mostra versões instaladas, marca a atual, protege versões não removíveis e pede confirmação antes da remoção.
- O autostart opcional do OmniRoute não pode bloquear o startup do FastAPI: o endpoint `/healthz` deve existir enquanto o gateway ainda inicializa. O supervisor continua observando o gateway depois que a API local está pronta.

## Diagnóstico do release anterior

Os logs instalados em `0.1.4` mostraram `ValueError: refreshed_at must be timezone-aware` ao listar o catálogo de provedores. A correção de normalização UTC já existente no código-fonte foi incorporada ao runtime `0.1.5`. O mesmo log mostrou o backend aguardando o autostart do OmniRoute por dezenas de segundos; o novo caminho não espera essa operação dentro do evento de startup.

## Validação

- Suíte Python unitária: 1237 aprovados, 3 ignorados.
- Testes direcionados frontend de conversa e Skills: 30 aprovados.
- Build frontend e empacotamento PyInstaller/Electron concluídos.
- Artefato Windows: `dist/Orin-0.1.5-windows-x64.zip`; SHA-256 publicado no `dist/release.json`.
