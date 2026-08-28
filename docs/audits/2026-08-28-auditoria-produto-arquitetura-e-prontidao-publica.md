# Auditoria do Orin: produto, arquitetura e prontidao para publico leigo

Data: 2026-08-28

Base auditada: `main`, commit `876bd01`, tag mais recente `v0.2.11`

Escopo: runtime de agentes, persistencia e recuperacao, API, frontend, Electron, instalacao, atualizacao, inicializacao, CI/release, seguranca, acessibilidade e experiencia do usuario.

## Resumo executivo

O Orin ja possui um nucleo de agente local-first substancial: ciclo de execucao duravel, ferramentas, memoria, skills, subagentes, navegador, agendamentos, integracoes MCP/plugins e recuperacao conservadora de efeitos incertos. A arquitetura esta mais proxima de um produto agentico serio do que a interface de distribuicao sugere.

O principal risco da `0.3.0` nao e falta de recursos. E a diferenca entre essa capacidade interna e a confianca oferecida a uma pessoa nao tecnica. Hoje a instalacao recomendada ainda exige PowerShell, a inicializacao exibe componentes internos, a atualizacao nao comprova recuperacao completa, nao existe onboarding guiado e a release publica pode ser publicada mesmo quando a validacao do mesmo commit falha.

Conclusao: a `0.3.0` deve ser uma release de **confianca, orientacao e assertividade**, nao uma colecao ampla de novas integracoes. A promessa deve ser:

> Instalar em poucos cliques, entender o que o Orin esta fazendo, recuperar-se sem terminal e considerar uma tarefa concluida somente com evidencia.

## Como a auditoria foi conduzida

Foram inspecionados os fluxos e contratos existentes no repositorio, incluindo:

- empacotamento Windows, supervisor, Electron e scripts de instalacao;
- atualizacao, versionamento, migrations e armazenamento local;
- runtime canonico de chat, ferramentas, prompts, controle e recuperacao;
- navegacao, configuracoes, chat, atividade de agentes e textos da interface;
- workflows de validacao e publicacao;
- testes Python, testes frontend, lint, build e Playwright E2E;
- release publica `v0.2.11` e seus artefatos.

Esta auditoria separa fatos observados de propostas. Nomes de novos contratos e telas apresentados adiante sao propostas para a `0.3.0`, nao componentes existentes.

## O que ja existe e deve ser preservado

### Nucleo local-first e distribuicao standalone

- O pacote Windows inclui runtime Python, frontend e Chromium do Playwright.
- O perfil instalado usa SQLite local e nao exige Docker, Python ou Node do usuario final.
- O supervisor e a autoridade de inicializacao e publica estado para a splash do Electron.
- A instalacao atual usa versoes lado a lado e um apontador `current`, uma boa base para atualizacao atomica.

### Runtime agentico

- `RuntimeService` e a autoridade do ciclo de vida de chat em producao.
- `AgenticTurnRuntime` funciona como adaptador de compatibilidade, sem substituir essa autoridade.
- O sistema possui tools de arquivos, terminal, web, navegador, memoria, skills, MCP, plugins e subagentes.
- Ha controle de execucao, eventos em tempo real e retomada apos desconexao.
- Efeitos externos com resultado `UNKNOWN` pausam para reconciliacao em vez de serem repetidos automaticamente. Esta garantia precisa permanecer invariavel.

### Experiencia durante a execucao

- O chat ja apresenta atividade, agrupamento de eventos, pedidos de entrada ao usuario e representacao de subagentes.
- Ha suporte a movimento reduzido e testes de acessibilidade em areas importantes.
- Segredos de providers possuem contratos publicos que evitam devolver o valor salvo ao frontend.

## Resultado das verificacoes

| Verificacao | Resultado | Leitura correta |
|---|---:|---|
| Python | 1826 aprovados, 69 ignorados, 19 avisos | Base ampla aprovada; skips nao equivalem a E2E operacional |
| Frontend unitario | 390 aprovados em 73 arquivos | Contratos unitarios em bom estado |
| Frontend lint | Falhou: 9 erros e 1 aviso | Bloqueador de release |
| Frontend build | Aprovado com avisos de chunks grandes | Funcional, mas com divida de performance |
| Playwright E2E | 27 aprovados, 1 falhou | Bloqueador de release; falha no estado vazio de Plugins |
| Auditoria estatica premium | Sem achados | Resultado inconclusivo: faltam manifesto e contratos de design para cobertura util |

Os nove erros de lint estao concentrados em atualizacoes de estado dentro de effects em `ChatPage`, dialogs de plugins/MCP, biblioteca de plugins e detalhes/chaves de provider. O E2E falha ao procurar o titulo acessivel `Nenhum plugin instalado` em `/settings/plugins`.

Os avisos de build mostram um logo raster de aproximadamente 1,4 MB e chunks relevantes acima do limite recomendado, incluindo o bundle principal, Three.js e a cena de orquestracao.

## Problemas encontrados

### P0-01 — A publicacao nao depende da validacao do mesmo commit

**Encontrado:** o workflow de release por tag chama o build Windows com `-SkipTests`, valida apenas parte do versionamento e nao executa a suite frontend completa. Para o commit da `v0.2.11`, o workflow de validacao falhou no lint e o workflow de publicacao concluiu com sucesso.

**Impacto:** uma release publicamente marcada como pronta pode conter falhas ja detectadas pelo proprio repositorio.

**Correcao imediata:** a publicacao deve depender de um unico gate verde e imutavel para o SHA da tag: Python, lint, unitarios, build, E2E, smoke do pacote e verificacao de versao/assinatura.

### P0-02 — O caminho recomendado de instalacao ainda e tecnico

**Encontrado:** o README recomenda abrir PowerShell e executar `irm ... | iex`. A release publica oferece ZIP, `install.ps1` e manifesto, mas nao um instalador grafico `.exe` assinado. O script e em ingles e termina orientando o usuario a executar `orin`.

**Impacto:** cria friccao, alerta de seguranca e dependencia de termos que o publico pretendido nao deveria conhecer.

**Correcao imediata:** instalador grafico por usuario, em portugues, com atalhos, desinstalacao e recuperacao. PowerShell deve permanecer somente como canal avancado/automatizado.

### P0-03 — O executavel desktop nao se auto-inicializa

**Encontrado:** `desktop/electron/main.cjs` depende de `--status-file` para acompanhar o supervisor. Sem esse argumento, a splash nao recebe estado. O atalho criado pelo script atual contorna isso apontando para `orin.exe --desktop`; um atalho NSIS padrao para `Orin Desktop.exe` ficaria preso na splash.

**Impacto:** simplesmente habilitar o alvo NSIS existente nao entrega um aplicativo funcional.

**Correcao imediata:** transformar o Electron em entrada dupla segura: sem estado do supervisor, iniciar o runtime oculto e entregar a janela a ele; com estado, atuar como a interface supervisionada. Deve haver protecao contra loops e multiplas instancias.

### P0-04 — Atualizacao sem transacao completa nem rollback comprovado

**Encontrado:** o Electron dispara `orin update` de forma destacada; a interface web informa sucesso e pede reinicio manual. O backend considera a instalacao concluida quando o script retorna. Nao foi encontrado backup automatico do SQLite antes de migrations, restauracao do banco ou validacao do candidato antes do commit definitivo.

**Impacto:** a versao anterior do binario pode permanecer disponivel, mas deixar de ser compativel com um banco ja migrado. O usuario pode ficar sem caminho simples de recuperacao.

**Correcao imediata:** coordenador de atualizacao persistente, backup consistente, bloqueio de escrita, verificacao do candidato, commit apenas depois de health/readiness e rollback conjunto de binario e dados.

### P0-05 — Inicializacao fala a linguagem da implementacao

**Encontrado:** a splash mostra `AMBIENTE LOCAL`, launcher, banco de dados, migrations, API, worker e scheduler. Em erro, `Abrir logs` aparece como acao primaria. O progresso deriva da contagem de servicos, sem representar necessariamente o tempo real.

**Impacto:** o primeiro contato transmite fragilidade e exige que o usuario interprete infraestrutura.

**Correcao imediata:** tres estagios honestos em linguagem humana — `Preparando seus dados`, `Iniciando o Orin`, `Tudo pronto` — com detalhes tecnicos recolhidos e um centro de reparo orientado a acoes.

### P0-06 — Falta protecao de dados antes de operacoes de risco

**Encontrado:** nao foi localizada copia de seguranca automatica antes de migration/upgrade. A desinstalacao pode apagar dados locais; exportacao e preservacao nao formam um fluxo primario para o usuario leigo.

**Impacto:** perda ou incompatibilidade de dados tem impacto desproporcional em um produto local-first.

**Correcao imediata:** backup pre-atualizacao, politica de retencao, restauracao testada e escolha clara entre manter, exportar ou remover dados na desinstalacao.

### P0-07 — Confianca do artefato Windows e insuficiente para publico amplo

**Encontrado:** existe verificacao SHA-256, mas nao foi encontrada assinatura Authenticode implementada. Hash e pacote distribuidos pelo mesmo canal nao substituem assinatura de codigo. A configuracao Electron declara NSIS, mas o build publicado usa diretorio/ZIP.

**Impacto:** SmartScreen, proveniencia e percepcao de seguranca ficam aquem da proposta para leigos.

**Correcao imediata:** assinar runtime, executavel Electron e instalador; verificar assinatura no CI. A disponibilidade do certificado e uma dependencia externa ainda nao confirmada.

### P0-08 — Efeito incerto vira uma espera sem caminho de resolucao

**Relato recorrente do usuario:** a atividade termina com `Aguardando reconciliação de efeito externo`, codigo `EFFECT_RECONCILIATION_REQUIRED`, enquanto o cartao tambem mostra `Falhou`. A captura de 2026-08-28 mostra o problema depois de operacoes de arquivo e terminal.

**Encontrado no codigo:** o journal duravel classifica efeitos externos como `APPLIED`, `NOT_APPLIED` ou `UNKNOWN` e pausa corretamente quando nao pode provar o resultado. Porem:

- nao foi encontrado endpoint/acao de usuario que inspecione o efeito, registre uma decisao autorizada e retome o checkpoint;
- `ToolOutcome` nao transporta `effect_state`, retryability, recibo nem estrategia de reconciliacao;
- uma tool nao read-only que retorna `failed` e classificada genericamente como `UNKNOWN`, inclusive em falhas que podem ter ocorrido antes de qualquer efeito;
- uma interrupcao do stream do provider depois do primeiro evento registra `UNKNOWN`, mas o loop ainda pode fazer retry dentro do budget, mantendo a execucao marcada para reconciliacao;
- `recover_stale` reconhece que falta um adaptador futuro para consumir resultados confirmados e hoje pausa checkpoints que nao podem ser retomados pelo fluxo antigo;
- a UI projeta o estado `PAUSED` como atividade `TURN_FAILED`, criando a contradicao visual entre `aguardando` e `falhou`;
- ha teste unitario do journal, mas nao foi localizada cobertura ponta a ponta de detectar, reconciliar e retomar sem repetir o efeito.

**Impacto:** uma protecao arquitetural correta se transforma em beco sem saida para o usuario. A frequencia pode ser inflada por classificacao conservadora demais, e a unica saida pratica tende a ser iniciar outra mensagem/tarefa sem saber se algo ja ocorreu.

**Correcao imediata:** implementar contratos de efeito por adapter, reconciliadores automaticos, API autorizada, cartao de resolucao em linguagem simples e retomada por checkpoint. Falhas comprovadamente anteriores ao efeito viram `NOT_APPLIED`; efeitos confirmados viram `APPLIED`; somente incerteza real permanece `UNKNOWN`. Em nenhum caso a correcao pode virar retry cego.

### P0-09 — Cancelamento pode deixar Execution e conversa divergentes

**Relato recorrente do usuario:** depois de pressionar parar, a atividade mostra `Cancelado`, mas o Orin continua exibindo `Pensando`, o botao de parada permanece e o compositor nao permite enviar outra mensagem. A captura de 2026-08-28 confirma a contradicao na mesma conversa.

**Encontrado no codigo:**

- `ChatApplication.cancel()` transiciona primeiro a `Execution` canonica diretamente para `CANCELLED` e, depois, muda o turno da conversa para `cancelling`;
- `ExecutionControlService.request_cancel()` e terminal, enquanto `current_signal()` sempre retorna `CONTINUE`, embora `RuntimeService` consulte `CANCEL_REQUESTED` para coordenar uma parada cooperativa;
- o worker ainda pode estar dentro do provider/tool quando a `Execution` ja foi encerrada. Ao observar o cancelamento no `PostgresChatStore`, ele tenta confirmar `CANCELLED` novamente usando a versao/estado anterior e pode receber conflito;
- a projecao do chat pode permanecer `cancelling` se a confirmacao final ou a transicao posterior falhar;
- `ChatPage` calcula `running` pelo estado projetado da conversa. `Composer` bloqueia submit enquanto `running` e substitui o botao de enviar pelo botao parar;
- o teste unitario atual exige que a `Execution` fique `CANCELLED` antes da projecao do chat, consolidando a ordem que cria a corrida. O teste do frontend prova apenas que o POST foi enviado, nao que o estado terminal convergiu e o compositor foi liberado.

**Impacto:** o usuario perde o controle da conversa e precisa abandonar/reabrir outra tarefa. O estado visual deixa de representar a autoridade canonica e um comando idempotente de cancelamento termina como deadlock de UX.

**Correcao imediata:** separar `cancelamento solicitado` de `cancelamento confirmado`. O pedido duravel deve gerar um sinal observavel, bloquear novos efeitos, propagar cancelamento a provider/tools/subagentes e chegar a `CANCELLED` somente quando o worker confirmar a parada ou a recuperacao determinar um terminal seguro. Projecoes de conversa devem convergir a partir do evento canonico, com watchdog para comandos aceitos sem acknowledgement. A UI permanece utilizavel enquanto mostra `Parando…` e libera automaticamente o novo envio quando o terminal for confirmado.

### P1-01 — Nao existe primeiro uso guiado

**Encontrado:** nao foi encontrada rota de boas-vindas/onboarding. A Home assume OpenRouter como fallback mesmo sem configuracao e remete a `Settings > Providers`. Erros mencionam backend, worker e porta 8000.

**Impacto:** a primeira conversa pode comecar em estado invalido e falhar com uma explicacao tecnica.

**Correcao imediata:** onboarding com escolha de conexao, teste real, modelo `Automatico (recomendado)`, explicacao de privacidade e uma primeira tarefa orientada.

### P1-02 — Idioma e arquitetura de configuracoes sao inconsistentes

**Encontrado:** navegacao e textos misturam portugues e ingles (`General`, `Providers`, `Memory`, `Running`, `External`, `Runtime`, `gateway`). Informacoes de versao/instalacao estao duplicadas entre `AboutSection` e `RuntimeSettingsPage`.

**Impacto:** aumenta a carga cognitiva e torna areas criticas inconsistentes.

**Correcao imediata:** glossario pt-BR, conteudo centralizado, uma unica area `Aplicativo e atualizacoes` e separacao de funcoes avancadas.

### P1-03 — Interacoes criticas dependem de confirmacao nativa do navegador

**Encontrado:** ha usos de `window.confirm` para remover versoes, skills e configuracoes de provider.

**Impacto:** estilo, acessibilidade, explicacao de consequencias e recuperacao variam por navegador/sistema.

**Correcao imediata:** dialog de confirmacao pertencente ao aplicativo, com foco gerenciado, consequencia explicita, acao destrutiva nomeada e alternativa segura.

### P1-04 — O agente executa bem, mas nao firma um contrato de conclusao

**Encontrado:** o prompt de sessao orienta leitura, edicao, uso de tools e verificacao. Porem, nao ha contrato estruturado obrigatorio ligando objetivo, entregaveis, restricoes e criterios de aceite a evidencias finais. Tambem nao foram encontradas tools de primeira classe para inspecionar diff/status, executar checks declarados e registrar evidencia de conclusao.

**Impacto:** modelos fortes compensam por raciocinio; modelos fracos tendem a esquecer restricoes, declarar sucesso cedo ou validar superficialmente.

**Correcao imediata:** contrato de tarefa persistente, contexto compacto, tools estruturadas, fase verificadora e bloqueio de `concluido` sem evidencia ou declaracao explicita do que nao foi verificavel.

### P1-05 — A conexao agente–usuario mostra atividade, mas nao progresso semantico

**Encontrado:** a interface comunica tools e subagentes, mas nao oferece de modo uniforme objetivo atual, proximos passos, decisoes, alteracoes e bloqueios. Alguns rotulos, como `Main`, permanecem tecnicos/em ingles.

**Impacto:** muita atividade pode parecer ruido; ao retornar mais tarde, o usuario precisa reconstruir o estado da tarefa.

**Correcao imediata:** painel compacto da tarefa com objetivo, 3–7 etapas em linguagem comum, mudancas concluidas, ponto atual, pedidos ao usuario e evidencia final — sem expor raciocinio interno.

### P1-06 — Recursos avancados aparecem sem uma camada de seguranca para iniciantes

**Encontrado:** plugins, MCP, skills, runtime externo e OmniRoute ocupam a navegacao principal. Plugins e MCP podem adicionar codigo/capacidades de terceiros.

**Impacto:** usuarios iniciantes podem habilitar superficies poderosas sem compreender permissoes, proveniencia e impacto de dados.

**Correcao imediata:** modo padrao seguro, area `Ferramentas avancadas`, resumo de permissoes em linguagem simples e autorizacao validada no backend. A interface nunca deve ser a fonte de verdade de permissao.

### P1-07 — Formularios e segredos precisam de comportamento uniforme

**Encontrado:** campos de segredo nao possuem um padrao acessivel de revelar/ocultar; formularios nao adotam consistentemente validacao controlada pelo app.

**Impacto:** erros e acessibilidade variam, especialmente no onboarding.

**Correcao imediata:** componentes padronizados de segredo, erro, ajuda e validacao; nunca renderizar credenciais devolvidas pelo servidor.

### P1-08 — Nao e possivel orientar o agente durante uma execucao

**Encontrado:** `ChatPage.submit()` e `Composer` recusam envio quando `running`; o botao enviar e substituido pelo botao parar. O backend, por outro lado, aceita um follow-up criando imediatamente outro turno e nao possui contrato para anexar uma orientacao ao turno ativo. Apenas habilitar o botao atual poderia criar dois turnos concorrentes, disputar o mesmo workspace e sobrescrever a projecao de estado da conversa.

**Impacto:** o usuario nao consegue corrigir rumo, acrescentar contexto ou pedir uma mudanca enquanto o Orin trabalha. A alternativa vira cancelar — fluxo que hoje tambem pode ficar preso — ou esperar uma tarefa longa terminar incorretamente.

**Melhoria imediata:** criar uma caixa de entrada duravel da `Execution`. Durante o trabalho, a mensagem deve ser aceita, ordenada e aplicada no proximo limite seguro, antes de uma nova decisao do modelo e nunca no meio de um efeito nao idempotente. O usuario tambem pode escolher `Enviar depois`, criando o proximo turno somente quando o atual terminar. Corridas com conclusao/cancelamento devem ser resolvidas atomicamente pelo backend, nao pelo frontend.

### P2-01 — Sistema visual fragmentado entre web e splash

**Encontrado:** nao ha `DESIGN.md` nem `UX-CONTRACT.md`. Tokens do frontend sao pequenos e a splash Electron repete cores/medidas. Estilos principais estao divididos entre `theme.css`, `index.css`, `agentos.css` e `splash.css`.

**Impacto:** instalacao, splash e produto podem divergir; estados assincornos ficam inconsistentes.

**Melhoria:** formalizar a identidade atual — grafite escuro, violeta como sinal, tipografia legivel, movimento funcional — e compartilhar tokens/contratos entre superficies.

### P2-02 — Auditoria visual automatizada nao possui cobertura configurada

**Encontrado:** o script premium em modo estrito terminou sem achados, mas nao existem manifesto `premium-ui.json`, `DESIGN.md` ou `UX-CONTRACT.md` para definir ownership e regras.

**Impacto:** um resultado vazio pode ser interpretado incorretamente como conformidade.

**Melhoria:** adicionar contratos e manifesto antes de usar o audit como gate.

### P2-03 — Peso inicial e pacote sao altos

**Encontrado:** logo com cerca de 1,4 MB, bundle principal perto de 793 KB, modulo Three.js perto de 696 KB e ZIP publicado perto de 570 MB.

**Impacto:** download, instalacao e primeira abertura sofrem, especialmente em maquinas e conexoes modestas.

**Melhoria:** otimizar ativos, lazy-load de rotas e da experiencia 3D, revisar Chromium/arquivos duplicados e definir orcamentos mensuraveis.

### P2-04 — Rotas e titulos nao possuem acabamento de produto

**Encontrado:** rota desconhecida cai silenciosamente na Home e nao foi encontrado gerenciamento consistente de titulo por tela.

**Impacto:** navegacao, historico e diagnostico perdem contexto.

**Melhoria:** tela 404 pertencente ao app, titulos por rota e retorno seguro.

### P2-05 — Divida de warnings e documentacao arquitetural

**Encontrado:** a suite Python emite avisos de APIs/dependencias depreciadas. Documentacao antiga descreve PostgreSQL como sistema de registro enquanto o perfil instalado força SQLite. Superficies de versao nao sao verificadas por uma unica fonte.

**Impacto:** drift entre arquitetura declarada, runtime instalado e processo de release.

**Melhoria:** zerar warnings controlaveis, registrar ADR do perfil local, declarar uma fonte de versao e validar todas as superficies relevantes.

### P2-06 — Falta E2E operacional de ciclo de vida

**Encontrado:** 69 testes Python estao ignorados em funcao de integracoes/ambiente. Nao foi encontrada uma matriz automatizada em Windows limpo cobrindo instalar, primeiro uso, conversar, atualizar, falhar, restaurar e desinstalar.

**Impacto:** testes de unidade e UI nao comprovam o percurso que define a confianca do publico leigo.

**Melhoria:** matriz de maquina virtual limpa com falhas de rede, disco, migration, processo interrompido e banco preexistente.

## Hipoteses e incertezas que nao devem ser tratadas como fatos

- Nao esta confirmado que o projeto ja possua certificado Authenticode ou verba/processo para obte-lo.
- Nao foi feita medicao em hardware de baixa especificacao; o impacto de performance e indicado pelos artefatos, nao por benchmark de usuario real.
- A causa exata da falha E2E de Plugins ainda precisa de diagnostico durante a implementacao; o fato confirmado e a regressao observavel.
- A melhor tecnologia do instalador pode continuar sendo NSIS, pois ja esta configurada, mas a decisao final depende dos requisitos de assinatura, manutencao e auto-update.
- Nomes como `TaskContract`, `CompletionEvidence` e `Repair Center` sao propostas de contrato; devem ser ajustados aos padroes do dominio ao implementar.

## Priorizacao para a 0.3.0

### Bloqueadores de release

- CI/publicacao ligada ao mesmo SHA e todas as suites verdes;
- instalador grafico assinado e entrada desktop auto-inicializavel;
- atualizacao transacional com backup e rollback testado;
- reconciliacao de efeito incerto com inspecao, decisao e retomada sem replay;
- splash e recuperacao em linguagem nao tecnica;
- onboarding e provider funcional antes da primeira conversa;
- contrato de tarefa, verificacao e evidencia para elevar assertividade;
- E2E limpo de instalacao ao rollback.

### Necessarios na mesma release

- unificacao de configuracoes e idioma;
- dialogs, formularios e segredos acessiveis;
- modo seguro e separacao de recursos avancados;
- contexto de progresso entre agente e usuario;
- contratos de design compartilhados;
- reducao de peso e orcamentos de performance;
- atualizacao de ADRs, versao e baseline de warnings.

## Criterio de prontidao publica

A `0.3.0` nao deve ser chamada de pronta para publico leigo se qualquer uma destas condicoes permanecer:

- release publicavel com gate vermelho;
- instalacao primaria dependente de terminal;
- artefatos Windows sem assinatura, salvo decisao explicita de distribuir como beta com o risco comunicado;
- update sem backup/restauracao verificados;
- execucao pausada por efeito incerto sem acao de inspecao/retomada compreensivel;
- primeira conversa disponivel sem provider/modelo valido;
- conclusao agentica sem evidencia para tarefas modificadoras;
- falha em E2E do fluxo principal ou em maquina Windows limpa.

O plano detalhado e a matriz que cobre cada achado estao em `docs/plans/2026-08-28-release-0.3.0-publico-leigo.md`.
