# Visao futura do Orin

Data: 2026-08-28

## Direcao de produto

O futuro mais coerente para o Orin e ser um **ambiente pessoal de trabalho agentico, local-first e verificavel**. Chat permanece como a porta de entrada, mas o produto evolui de uma conversa que dispara tools para um sistema que entende um objetivo, mantem contexto, coordena capacidades, comprova o resultado e preserva a autonomia do usuario.

Essa direcao aproveita o que ja existe: `RuntimeService` como autoridade do ciclo de vida, execucoes persistentes, tools, memoria, skills, subagentes, navegador, agendamentos, plugins/MCP e recuperacao conservadora. O futuro nao exige trocar esse nucleo; exige torna-lo compreensivel, avaliavel e extensivel.

## Principios permanentes

- **Local-first de verdade:** dados, historico, configuracao e controle permanecem locais por padrao. Quando um provider externo for usado, a interface explica o que sera enviado.
- **Conclusao com evidencia:** o Orin nao declara sucesso apenas porque gerou uma resposta; ele relaciona criterios de aceite a verificacoes.
- **Autonomia proporcional ao risco:** tarefas simples fluem diretamente; mudancas destrutivas, efeitos externos e incerteza pedem confirmacao ou reconciliacao.
- **Pessoa no controle, sem microgerenciamento:** o usuario ve objetivo, progresso, decisoes e bloqueios, mas nao precisa acompanhar logs nem raciocinio interno.
- **Capacidades substituiveis:** modelos, tools, skills e agentes entram por contratos versionados, autorizados e observaveis.
- **Modelos menores continuam uteis:** estrutura, contexto e verificadores deterministicos compensam parte da diferenca de raciocinio.
- **Confianca antes de expansao:** atualizacao, rollback, privacidade e recuperacao sao recursos de produto, nao detalhes de infraestrutura.
- **Incerteza com saida:** quando uma acao perde a confirmacao, o Orin verifica, explica e oferece uma decisao segura; nunca entrega apenas um codigo interno e uma tarefa parada.

## Evolucao esperada

### 0.3.x — Orin confiavel para pessoas nao tecnicas

- instalador grafico Windows, assinatura e atualizacao recuperavel;
- onboarding, linguagem clara e modo seguro;
- contrato de tarefa, progresso semantico e evidencia final;
- roteamento automatico baseado em capacidades do modelo;
- avaliacao continua com modelos fortes e fracos;
- design e estados assincornos consistentes entre Electron e web.

### 0.4.x — Trabalho duravel e coordenado

- workflows persistentes compostos por capacidades versionadas;
- handoffs entre agentes com contexto minimo e proveniencia;
- quadro compartilhado de objetivo, decisoes, artefatos e evidencias;
- retomada apos dias ou reinicio sem reconstruir a conversa inteira;
- politicas de custo, latencia e risco por tipo de tarefa.

Esta etapa deve ampliar os servicos de orquestracao ja presentes sem criar uma segunda autoridade concorrente ao `RuntimeService`.

### 0.5.x — Ecossistema local confiavel

- catalogo de skills/plugins com permissoes, assinatura e reputacao local;
- testes de compatibilidade e qualidade antes da ativacao;
- pacotes de capacidade por profissao ou fluxo, sem transformar a configuracao em um painel tecnico;
- suporte multiplataforma quando o ciclo Windows estiver estabilizado;
- compartilhamento/exportacao de artefatos e workflows com segredos removidos.

### Horizonte posterior

- sincronizacao privada e opcional entre dispositivos, sem exigir conta SaaS para o uso local;
- assistencia proativa controlada por politicas explicitas do usuario;
- modelos locais e remotos combinados por custo, privacidade e capacidade;
- mercado de capacidades auditaveis, mantendo autorizacao e dados como responsabilidade do runtime local.

## O que "muito mais inteligente" deve significar

Nao deve significar apenas usar um modelo maior. Para o Orin, inteligencia de produto deve ser medida por:

- entender corretamente objetivo, restricoes e entregaveis;
- escolher o fluxo e as tools adequadas;
- preservar decisoes relevantes entre turnos;
- detectar quando falta informacao realmente bloqueante;
- validar o proprio trabalho;
- recuperar-se de interrupcoes sem repetir efeitos externos;
- explicar resultado e incerteza em linguagem proporcional ao usuario.

## Norte mensuravel

O Orin estara avancando na direcao correta quando:

- mais tarefas forem concluidas na primeira tentativa com criterios verificados;
- modelos mais fracos se aproximarem do baseline dos modelos fortes em tarefas estruturadas;
- cair a necessidade de intervencao do usuario por erro evitavel;
- instalar, atualizar e reparar dispensarem terminal;
- uma pessoa conseguir retomar uma tarefa entendendo o estado em poucos segundos;
- nenhum ganho de autonomia enfraquecer autorizacao, isolamento ou reconciliacao.

## Limites estrategicos

- Nao criar uma segunda maquina de estados para chat ao lado do `RuntimeService`.
- Nao expor chain-of-thought como recurso de transparencia; mostrar plano, acoes, evidencias e incertezas.
- Nao tratar prompts ou frontend como fonte de verdade para permissao.
- Nao esconder falhas com progresso ficticio ou mensagens vagas.
- Nao transformar a `0.3.0` em expansao indiscriminada de providers e integracoes antes de estabilizar o ciclo de vida do produto.
