# Visão geral: modelo e uso de tokens por agente

## Objetivo

Permitir que uma pessoa identifique o provider, o modelo e o consumo de tokens de cada agente de uma conversa, sem poluir o mapa principal da visão geral.

## Experiência

- O mapa e a legenda mantêm a apresentação atual, com nome e estado do agente.
- Selecionar um agente abre um painel de detalhes com nome, papel, estado, provider, modelo e o seu consumo de tokens.
- A visão geral mostra um único total de tokens gastos na conversa, que é a soma dos usos confirmados do agente principal e de todos os subagentes.
- O painel de detalhes mostra tokens de entrada, saída e total. A interface usa o total como métrica prioritária.
- Quando o provedor não reportar uso, a interface mostra `indisponível`; nunca interpreta ausência de telemetria como zero.
- Dados criados antes desta mudança, que não tenham telemetria persistida, também aparecem como `indisponível`.

## Dados e contratos

Cada subagente persiste um retrato do provider e do modelo com que foi criado. Atualmente o subagente herda ambos da conversa, mas o retrato evita que o histórico mude quando a seleção da conversa ou a estratégia de delegação evoluir.

Uma tabela de uso por agente armazena totais acumulados por conversa e agente:

- `input_tokens`, `output_tokens` e `total_tokens`;
- `usage_reported`, para diferenciar zero confirmado de uso que o provider não informou;
- provider e model snapshot para permitir auditoria de uso, inclusive para o agente principal implícito.

O runtime informa cada evento de uso ao armazenamento do agente que executou a chamada. O armazenamento acumula apenas valores positivos e mantém a operação segura para repetição do fluxo de persistência. A API de visão geral retorna:

- provider e modelo em cada item de `agents`;
- `token_usage` no nível da conversa, com o total e a disponibilidade;
- `token_usage` em cada agente, com entrada, saída, total e disponibilidade.

## Implementação

1. Criar migração Alembic para os campos de provider/modelo de `conversation_agents` e para uma tabela `conversation_agent_usage` indexada por conversa.
2. Estender `ConversationAgentStore` para gravar e expor o snapshot do modelo e acumular uso por agente, incluindo o agente principal implícito.
3. Estender o protocolo de armazenamento do runtime e seus adaptadores principal/subagente para registrar telemetria de cada evento `USAGE`.
4. Agregar os novos dados em `PostgresChatStore.overview` e validá-los no cliente web.
5. Adicionar o painel de detalhes do agente selecionado e o indicador de tokens totais à `OverviewPanel`.

## Erros e compatibilidade

- A leitura de conversas existentes deve continuar funcionando sem backfill: snapshots e usos ausentes são normalizados como indisponíveis.
- Eventos de uso sem campos de entrada ou saída ainda contribuem com `total_tokens`, se disponível.
- Eventos sem qualquer número de uso não tornam a soma conhecida; a disponibilidade permanece falsa até que haja uma medição confirmada.
- O escopo é somente a visão geral da conversa; não inclui estimativa de custo monetário, exportação nem detalhamento por chamada.

## Testes

- Testes unitários de persistência cobrem criação do agente com modelo, acumulação de tokens para agente principal e subagente, e ausência de telemetria.
- Testes da API/serviço cobrem o contrato da visão geral e a soma correta do total.
- Testes unitários do frontend cobrem parsing retrocompatível, seleção de subagente e a renderização de modelo/tokens.
- A suíte de frontend e os testes de backend relacionados devem ser executados após a migração.
