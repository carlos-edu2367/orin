# Motion System

## Regras

Motion representa transições observáveis, é interrompível e respeita `prefers-reduced-motion`. Estado backend determina *o que* mudou; Motion determina *como* a mudança é percebida. Nunca bloquear uma ação em função de animação.

| Domínio | Gatilho confirmado | Motion |
| --- | --- | --- |
| Execution | mudança de estado/version | crossfade de label, halo de atividade e layout spring. |
| Tool | `ToolStarted/Progressed/Finished` projetado | bloco contrai/expande; progresso por ticks, não por percentagem inventada. |
| Delegação | `DelegationCreated` | nó filho surge conectado; pulso A→B uma vez. |
| Retorno | `DelegationResultReturned` | pulso B→A e redução de atividade do filho. |
| Espera | `AgentWaitRegistered/Satisfied` | parent desacelera/retoma; sem simular processamento. |
| Reconnect | snapshot/replay | atualização discreta sem repetir animações históricas. |

## Motion / Framer Motion

- Usar `layout`/`layoutId` para morph home → execution rail e rail → graph.
- Springs curtas para estrutura; 160–240 ms para feedback e 300–480 ms para transições de contexto.
- Animar somente `transform` e `opacity` em listas; usar `AnimatePresence` com saída curta e estável.
- Distinguir `initial` de evento histórico: replay não deve refazer uma coreografia completa.

## Acessibilidade e fallback

Com `prefers-reduced-motion`, remover partículas, pulsos em deslocamento, parallax e layout morph; preservar mudança de cor, texto e foco. Com GPU fraca/aba inativa, pausar R3F, usar rail 2D e reduzir atualização para snapshots/event batches. Toda informação transmitida por movimento deve possuir texto e estado semântico equivalente.
