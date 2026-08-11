# OmniRoute no AgentOS

## Papel e arquitetura

OmniRoute é um provider opcional. O AgentOS continua sendo responsável por agentes, subagentes, contexto, memória, ferramentas, permissões, workspaces, execuções, artefatos e auditoria. Quando selecionado, somente a chamada de modelo segue para o gateway:

```text
AgentOS → OmniRoute (/v1) → provider/modelo ou Combo configurado no OmniRoute
```

OpenAI, Anthropic e OpenRouter continuam configuráveis e selecionáveis de modo direto. Desabilitar, remover ou deixar o OmniRoute indisponível não altera esses providers.

## API validada

A implementação foi baseada na documentação atual do projeto OmniRoute:

- `GET /v1/models` para testar a conexão e descobrir o catálogo;
- `POST /v1/chat/completions` para streaming OpenAI-compatible e ferramentas;
- modelos `auto/*` como rotas públicas documentadas (por exemplo, `auto/coding`);
- Combos persistidos são expostos pelo catálogo como IDs selecionáveis. A API OpenAI-compatible não garante um marcador estável para distinguir cada Combo de um modelo comum, portanto o AgentOS só classifica explicitamente as rotas `auto/*`; não infere os outros tipos.

Fontes: [README](https://github.com/diegosouzapw/OmniRoute/blob/main/README.md), [referência da API](https://github.com/diegosouzapw/OmniRoute/blob/main/docs/API_REFERENCE.md) e [guia de uso](https://github.com/diegosouzapw/OmniRoute/blob/main/docs/guides/USER_GUIDE.md).

## Instalação local e lifecycle

O AgentOS inclui uma ação explícita **Instalar OmniRoute**, que executa somente o comando oficial `npm install -g omniroute` (com limite de 180 segundos e sem expor logs do processo). O endpoint padrão oficial é `http://localhost:20128/v1`.

Em **Settings → OmniRoute**, a opção **Start OmniRoute when AgentOS launches** é persistida localmente. Quando habilitada, o AgentOS primeiro consulta o endpoint `/v1/models`: se uma instância saudável já existe, ela aparece como **External** e não é duplicada nem encerrada. Caso esteja ausente, o AgentOS inicia o executável detectado (`AGENTOS_OMNIROUTE_COMMAND`, quando configurado; caso contrário `omniroute`) e aguarda a saúde por no máximo 12 segundos. A falha não impede o AgentOS de iniciar e é apresentada sem stack trace.

Somente um processo iniciado pelo próprio AgentOS recebe ações **Stop** e **Restart**. O AgentOS não encerra a instância quando fecha; auto-start não implica auto-stop.

No AgentOS, abra **Settings → Providers → OmniRoute → Configurar**, informe a Base URL, teste a conexão e salve. A chave é de escrita única e é armazenada cifrada pela mesma infraestrutura de secrets usada pelos demais providers. Um gateway local sem autenticação também pode ser salvo/testado com a chave vazia.

Depois use **Atualizar catálogo** e selecione o modelo ou rota no seletor de modelo. A configuração persiste por usuário.

## Streaming, tools, uso e observabilidade

O OmniRoute reutiliza o transporte OpenAI-compatible existente do AgentOS: streaming SSE, tool/function calls, cancelamento cooperativo do turno pelo AgentOS, tokens de entrada/saída/total quando fornecidos pelo gateway, além de erros e rate limits normalizados.

O AgentOS registra `model.routing_started` como “Selecionando rota” e mostra a rota solicitada. Não afirma qual provider/modelo ou fallback foi escolhido, pois essa informação não é garantida por `/v1/chat/completions` nem por `/v1/models`. O fallback entre recursos configurados no OmniRoute é dele; o AgentOS não reenvia a mesma chamada ao OmniRoute para evitar loops.

Custos sem dado confirmado permanecem desconhecidos; não há contabilização duplicada nem preço inventado.

## Opções gratuitas legítimas

O painel **Configurar gratuitos** é uma orientação baseada no guia oficial, com fontes e data de verificação centralizadas no frontend. Ele lista somente fluxos legítimos: provider sem autenticação habilitado no próprio OmniRoute ou OAuth da conta do usuário. O AgentOS nunca solicita senha; credenciais de providers que o OmniRoute administra são conectadas no dashboard do OmniRoute.

OmniRoute não fornece magicamente tokens ilimitados. Ele permite agregar e rotear acesso a providers, incluindo serviços que oferecem quotas gratuitas, OAuth, modelos gratuitos ou execução local. Disponibilidade e limites dependem de cada provider.

Não são exibidas estimativas de quota ou contagens de modelos/providers, pois essas informações variam. Atualize o catálogo e consulte a fonte oficial antes de tomar decisões de custo.

## Desenvolvimento e troubleshooting

- Falha de conexão: confirme que a Base URL termina em `/v1`, que o processo OmniRoute está acessível e teste novamente.
- Autenticação: use uma chave emitida pelo OmniRoute, quando sua instalação a exigir. Ela não é retornada por APIs, eventos, logs ou a interface.
- Sem modelos: atualize o catálogo após conectar providers/contas no dashboard do OmniRoute.
- Modelo indisponível: escolha outro catálogo/Combo. O AgentOS não tenta deduzir o fallback interno do gateway.
- Provider direto falha: revise a configuração do provider direto; ela é independente do OmniRoute.

Os testes unitários cobrem URL segura, descoberta, conexão autenticada e sem autenticação, persistência de endpoint, rotas API sem vazamento de segredo, stream/tool compatibility por transporte reutilizado e o evento de rota solicitada.
