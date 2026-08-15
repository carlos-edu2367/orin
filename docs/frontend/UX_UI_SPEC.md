# UX/UI Specification

## Princípio

**Conversa, atividade, resultado.** Em repouso, a home e a execution parecem simples. Complexidade só aparece por intenção: expansão de atividade, inspector ou vista de orquestração. Motion comunica transição de estado, nunca inventa causalidade não observada.

## Jornadas

### Home → execution

A home tem marca AgentOS, uma pergunta curta e composer. A pessoa escreve a mensagem e escolhe provider e modelo entre as opções autorizadas do catálogo por usuário; o browser não envia `agent_id` nem `task_ref`. O envio mantém a home no estado de preparação enquanto a mutação está pendente e a transição para `/execution/{execution_id}` acontece somente após o recibo `201` confirmado. Erro preserva a mensagem, anuncia a falha e devolve o foco ao composer.

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

## Settings: índice, sala e gaveta

Settings usa uma única composição: o índice de grupos permanece à esquerda, a sala de conteúdo mantém o mesmo cabeçalho e a gaveta de detalhe preserva a grade atrás dela. Uma seção nunca monta `app-shell` ou `topbar` próprios.

Três decisões sustentam a superfície:

- O status em mono na barra lateral é estado real e degradável; valor desconhecido não vira zero.
- O ponto violeta de pendência tem nome acessível e significa que uma aprovação aguarda a pessoa.
- Cards entram em sequência uma única vez, com atraso de 40ms, e respeitam `prefers-reduced-motion`.

Violeta continua racionado: marca o agente, a ação primária e a pendência. Marcas de providers são SVGs locais, inlined no build, e o status do provider permanece em texto para não depender de cor.
# Composer de conversa e catálogo (2026-08-10)

Na Home, o primeiro campo é a mensagem. Provider e modelo têm rótulos explícitos, o catálogo é pesquisável e favoritos aparecem como atalhos. O botão permanece indisponível até existir um modelo autorizado; catálogo vazio direciona a pessoa para a configuração do provider. A interface nunca mostra identificadores de agente, referências de tarefa ou valores de segredo.

Em Configurações, cada painel separa claramente credencial (campo de senha, somente escrita) de catálogo (atualização, lista normalizada e favorito). Escolher/favoritar um modelo não muda a chave nem define um modelo global de execução.
