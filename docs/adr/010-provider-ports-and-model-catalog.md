# ADR 010 — Portas de Provider e catálogo de modelos

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS precisa usar modelos com geração, streaming, visão e pedidos de Tool, mas fornecedores divergem em SDKs, payloads, nomes, limites, preços, erros, telemetria, políticas de cancelamento e disponibilidade. Permitir que esses tipos atravessem o Runtime, API, Context ou Tool Runtime criaria condicionais por fornecedor, impossibilitaria testes consistentes e permitiria que detalhes de SDK decidissem seleção, fallback ou autorização.

Escolher um modelo também é uma decisão de policy. Capabilities, classificação, região, contexto, budget, custo, streaming, Tool calls, status e fallback não podem ser inferidos no adapter nem alterados silenciosamente após uma falha. É necessário preservar uma explicação e as revisões usadas em cada seleção para auditoria, custo e reprodução da decisão.

## Decisão

Adotar uma **`ProviderPort` uniforme**, implementada por adapters de fornecedor, e um **Model Catalog** versionado que resolve modelos antes de qualquer invocação. OpenAI, Anthropic e OpenRouter são adapters iniciais; seus SDKs, objetos, exceções, headers, bindings, IDs externos e payloads proprietários permanecem encapsulados no adapter.

O Runtime envia à porta apenas uma seleção opaca e um snapshot imutável de requisitos aprovado pelo catálogo. A porta normaliza geração, streaming, visão, Tool calls, cancelamento, uso, custo e erros; pedidos de Tool retornados pelo modelo são dados não confiáveis e só podem ser validados e executados pelo Runtime e Tool Runtime. O adapter não escolhe modelo, fallback ou policy e não acessa Memory, Artifact Storage, filesystem ou segredos por caminho lateral.

O catálogo separa Provider de Model, publica descriptors, preço, perfis, status e policy em revisões imutáveis, aplica hard constraints antes de preferências e materializa primário, fallbacks permitidos, explicação, versões e validade. Fallback só ocorre em ordem ou policy explícita, revalida ownership, finalidade, capacidade, classificação, budget, cancelamento e disponibilidade, e contabiliza todas as tentativas. Modelo ou Provider desabilitado/retirado nunca inicia nova invocação.

## Consequências

### Benefícios

- Mantém o Kernel e as demais camadas independentes de SDKs e semânticas proprietárias.
- Oferece um vocabulário único para erros, streaming, custo, uso e cancelamento, simplificando observabilidade e recuperação.
- Faz seleção, preço, capacidades e fallback explicáveis e reprodutíveis a partir de snapshots versionados.
- Permite adicionar, desativar ou retirar Providers e modelos sem `switch` espalhado pelo Runtime.

### Custos e falhas aceitas

- Adapters exigem manutenção contínua conforme cada fornecedor muda APIs, modelos, limites ou formatos de stream.
- Normalização não elimina diferenças reais: campos de uso ou custo podem ser estimados, indisponíveis ou finais somente após término.
- Falhas de rede, rate limit, timeout, cancelamento tardio, resposta inválida, duplicidade e custo parcial são possíveis; retry pertence ao Runtime e só é permitido sob policy e idempotência apropriadas.
- Catálogo, snapshots de disponibilidade e revisões de preço acrescentam governança, persistência e operação; uma seleção expirada ou metadata contraditória falha explicitamente.

### O que esta decisão não resolve

Esta decisão não escolhe o melhor modelo universal, não garante resposta equivalente entre fornecedores, não executa Tools, não monta Context, não armazena prompts ou respostas completos e não substitui autorização, limites da `Execution`, segredos ou observabilidade de custo.

## Alternativas consideradas

- **Chamar SDKs diretamente a partir do Runtime:** rejeitada porque acopla o Kernel a fornecedores e vaza objetos proprietários para decisões de domínio.
- **Deixar cada adapter escolher modelo e fallback:** rejeitada porque torna policy, classificação, budget e custo opacos e não auditáveis.
- **Usar um único Provider sem abstração:** rejeitada porque impede substituição, degradação controlada e comparação explícita de capabilities.
- **Fallback automático para qualquer modelo disponível:** rejeitada porque pode violar região, classificação, formato, capacidade, budget ou finalidade.

## Relações com RFCs

- [RFC 501 — Provider API](../architecture/500-providers-models/501-provider-api.md) define a porta uniforme, adapters, streaming, cancelamento, custo e erros normalizados.
- [RFC 502 — Model Catalog](../architecture/500-providers-models/502-model-catalog.md) define descriptors, seleção, snapshots, status, preço e fallback explícito.
- [RFC 101 — Runtime](../architecture/100-kernel/101-runtime.md) consome portas sem conhecer SDKs de Provider.
- [RFC 104 — Pipeline de contexto](../architecture/100-kernel/104-context-pipeline.md) prepara contexto e orçamento antes da invocação.
- [RFC 401 — Tool Runtime](../architecture/400-tools-resources/401-tool-runtime.md) valida Tool calls retornadas como dados não confiáveis.
- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define autorização, classificação, segredos e isolamento aplicados à invocação.
