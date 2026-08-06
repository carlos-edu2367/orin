# Task 6 — Relatório

## Status

Concluído após correções P1/P2/P3 da revisão. As duas RFCs normativas de Providers e Model Catalog foram criadas exclusivamente em Markdown. Nenhum backend, código de produção, endpoint, schema ORM, configuração executável ou adapter foi implementado.

## Arquivos

- `docs/architecture/500-providers-models/501-provider-api.md`
- `docs/architecture/500-providers-models/502-model-catalog.md`

## Resumo

- A RFC 501 define a porta uniforme de geração e streaming, conteúdo de visão, Tool calls não confiáveis, cancelamento idempotente, limites, uso, custo, erros normalizados, telemetria e adapters iniciais OpenAI, Anthropic e OpenRouter.
- A fronteira de Provider impede que SDKs, payloads, exceções, objetos de stream, credenciais, IDs externos e semânticas proprietárias vazem para Runtime, Agent, Context ou Tool Runtime.
- A RFC 502 define descriptors versionados com Provider, nome, contexto, custo, visão, Tools, streaming, status e metadata pública tipada.
- Os perfis `CODING`, `REASONING`, `ORCHESTRATOR`, `VISION`, `CHEAP` e `BALANCED` são resolvidos por hard constraints e preferências versionadas.
- Seleções registram versões, preço, disponibilidade, explicação, validade, primário e fallbacks explicitamente permitidos; nenhuma troca de modelo ou Provider ocorre silenciosamente.
- Depreciação, desabilitação e retirada preservam histórico, exigem Events e nunca transformam sucessor sugerido em substituição automática.
- Todas as operações sensíveis carregam `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`.

## Correções da revisão

- P1: `ModelSelection` agora referencia `ApprovedModelRequirementsSnapshot` imutável com ownership, classificação, região, formato, capabilities, cancelamento, limites, Providers/modelos, fallback e versões de catálogo/política. `ProviderRequest` recebe referência e snapshot, e `ProviderPort` revalida integridade, validade e todos os constraints antes da transmissão.
- P2: `InvocationCancellationCapability` foi separada de `StreamingCapability`, com modos `COOPERATIVE_REMOTE`, `LOCAL_ONLY` e `UNSUPPORTED` e requisito resolvível `ANY`, `LOCAL_OR_REMOTE` ou `REMOTE_REQUIRED`. `cancel` retorna `terminal_ref`; `read_stream` preserva `StreamCancelled`; e `await_terminal` garante observação do terminal e contabilização final confirmada, estimada ou indisponível.
- P3: a RFC 501 explicita que visão é `ImagePart` de entrada e Tool call é outcome/delta da mesma invocação, razão pela qual `generate`, `open_stream` e `read_stream` unificam essas modalidades. `ProviderStatus = RETIRED` agora mapeia para adapter/registro `RETIRED`, sem binding nem nova invocação e com inspeção histórica preservada.

## Verificações

- 2 de 2 RFCs esperadas presentes; nenhum arquivo inesperado no diretório `500-providers-models`.
- 14 seções obrigatórias presentes em cada RFC: objetivo, fora de escopo, responsabilidades, entidades/dados, contratos tipados, eventos, fluxos normal/falha/cancelamento, segurança, observabilidade, invariantes, extensibilidade e futuro.
- 6 campos sensíveis presentes nos contextos das duas RFCs: `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`.
- 25 links relativos verificados; nenhum destino ausente.
- Nenhum placeholder literal `TBD`, `TODO` ou `FIXME` encontrado.
- Contratos específicos da RFC 501 verificados: geração, streaming, visão, Tool calls, cancelamento, erros, limites, custo, OpenAI, Anthropic, OpenRouter e proibição de vazamento de SDK.
- Contratos específicos da RFC 502 verificados: todos os metadados requeridos, seis perfis, resolução, compatibilidade, fallback explícito e descontinuação.
- Correção P1: 11 termos contratuais verificados para snapshot imutável e revalidação pré-transmissão.
- Correção P2: 12 termos contratuais verificados para capability, modos, requisito e observação terminal/contábil.
- Correção P3: 7 termos contratuais verificados para unificação de modalidades e lifecycle `RETIRED`.
- Resultado da verificação automatizada: 0 falhas.
