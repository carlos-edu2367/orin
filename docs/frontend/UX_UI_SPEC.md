# UX/UI Specification

## Princípio

**Conversa, atividade, resultado.** Em repouso, a home e a execution parecem simples. Complexidade só aparece por intenção: expansão de atividade, inspector ou vista de orquestração. Motion comunica transição de estado, nunca inventa causalidade não observada.

## Jornadas

### Home → execution

A home tem marca AgentOS, uma pergunta curta e composer. O envio cria uma intenção de execution; a interface muda para “Preparando tarefa” somente após o recibo 202. A entidade abstrata do hero sofre morph para o rail de agentes da execution. Sem um contrato de prompt/output, o composer deve ser planejado como **futuro**: inicialmente pode selecionar `agent_id` e enviar `task_ref` conhecido, nunca fingir chat livre.

### Execution principal

1. Header discreto: identidade do agent, estado humano derivado e controles válidos.
2. Transcript: pedido/referência, bloco de atividade semântico e resultado quando houver conteúdo autorizado.
3. Rail compacto de colaboração: somente se houver delegação/mensagem observável.
4. Inspector fechado: dados técnicos, erros, versões e referências autorizadas.

O estado visual `working` deriva de `QUEUED`, `STARTING`, `RUNNING` ou Tool em execução. `waiting_for_user`, `waiting_for_tool`, `paused`, terminal e falha são explicitamente distintos. “Thinking”, “communicating” e “using tool” são rótulos visuais derivados, não estados persistidos.

### Progressive disclosure

| Nível | Representação | Só aparece quando |
| --- | --- | --- |
| 0 | “Trabalhando”, “Aguardando você”, “Concluído” | Sempre, com semântica de execution. |
| 1 | “N ações observadas” por grupo | Há eventos Tool ou Resource projetados. |
| 2 | Tipo, contagem, resultado sanitizado, falha/cancelamento | Usuário expande o grupo. |
| 3 | IDs, versão, timestamps, refs, policy, correlação | Inspector e permissão permitem. |

Não renderizar cada Event. Normalizar por `event_id`, ordenar por execution+sequence e projetar em Activities: lifecycle, tool invocation, delegation, resource effect e system notice. Agrupar por `invocation_id` quando existir; na ausência dele, não inferir uma Tool Call de transições de Execution.

### Erros e estados incertos

`202` = aceito, não concluído. Exibir confirmação suave e acompanhar a projeção. `CONFLICT` pede refresh, preservando a intenção do usuário; `INDETERMINATE` pede reconciliação; `RATE_LIMITED` mostra prazo; auth/revogação pede login. Falha nunca mostra stack/payload bruto; oferece correlation ID em “Copiar para suporte”.

## Decisões visuais

- Paleta: fundo quase opaco, superfícies seletivamente glass, texto alto contraste e um accent por agent.
- Não usar tabelas, cards de métricas ou logs na área principal.
- Glass apenas em navegação, command palette, menus e inspector.
- Conexões de agents descrevem delegação/mensagem confirmada; pulso de ida ocorre em `DelegationCreated` ou `AgentMessageCreated`, retorno em `DelegationResultReturned`. Não animar “mensagem” se só houver child execution.
