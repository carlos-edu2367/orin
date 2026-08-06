# ADR 014 — Pydantic v2 para validação nas bordas

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS recebe dados não confiáveis de HTTP, configuração, plugins, Tools e Providers. A implementação precisa validar e serializar representações externas de modo consistente, sem tornar classes de biblioteca o contrato arquitetural nem reutilizar DTOs como entidades mutáveis de domínio.

## Decisão

Adotar **Pydantic v2** para DTOs e validação nas bordas da implementação Python. Models Pydantic representam transporte, configuração e adaptação; são convertidos para tipos públicos antes de entrar no domínio. Schemas publicados preservam versionamento e limites definidos nas RFCs. Objetos Pydantic, validadores e erros da biblioteca não atravessam portas do Kernel, não são persistidos como entidades e não substituem autorização ou invariantes de domínio.

## Consequências

- Borda e adapters ganham validação declarativa e geração controlada de schema.
- É necessária tradução explícita de erros para categorias públicas sanitizadas.
- Atualizações major da biblioteca exigem testes de compatibilidade de serialização.
- Validação estrutural não concede permissão, não resolve referências e não torna conteúdo confiável semanticamente.

## Alternativas consideradas

- **Dicionários sem schema:** rejeitados por produzir validação dispersa e contratos implícitos.
- **Classes Pydantic como domínio e ORM:** rejeitadas por acoplar camadas e ciclos de vida distintos.
- **Schemas específicos por adapter sem vocabulário comum:** rejeitados por favorecer divergência de tipos públicos.

## Relações com RFCs

- [RFC 060 — Glossário e convenções](../architecture/060-glossary-and-conventions.md) define pseudocódigo independente de linguagem.
- [RFC 701 — API e SSE](../architecture/700-api-security/701-api-sse.md) define validação de transporte.
- [RFC 604 — Configuração](../architecture/600-platform-data/604-configuration.md) define schemas e snapshots de configuração.
- [RFC 901 — Plugin SDK](../architecture/900-extensibility/901-plugin-sdk.md) trata manifesto e saída de plugin como dados não confiáveis.
