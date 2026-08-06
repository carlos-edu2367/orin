# ADR 004 — Playwright em Browser Workers dedicados

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

O AgentOS precisa automatizar navegação, interação, uploads, downloads, screenshots, DOM, perfis e cookies sem converter a API, o Runtime ou um Worker genérico em processos que detenham um browser privilegiado. Esses recursos acumulam estado, executam conteúdo não confiável e podem consumir CPU, memória, rede e espaço temporário de forma imprevisível. Um crash, um vazamento de perfil ou uma navegação para destino proibido não pode atravessar a fronteira de uma `Execution`, de um Workspace ou do host.

O Browser também produz conteúdo potencialmente volumoso e sensível. Cookies, storage state, tokens, URLs com segredo, DOM e downloads não podem ser publicados em Events, logs ou por caminhos físicos arbitrários. A operação deve permanecer recuperável mesmo quando um processo de browser morre, uma fila redelivera o job ou um efeito externo fica incerto.

## Decisão

Usar **Playwright exclusivamente em Browser Workers dedicados**, atrás de uma porta de jobs pública. API, Runtime, domínio, Workers de agente e outros pools enviam jobs autorizados, com contexto, lease, limites e grants mínimos; eles nunca controlam objetos Playwright diretamente.

O Browser Worker não recebe acesso a banco, ORM, Redis, estado do Runtime nem credenciais desses componentes. Ele recebe referências e grants já autorizados, volta a validar seu escopo e usa portas limitadas para input, Artifact, eventos e segredo efêmero. Cada sessão usa contexto isolado, com perfil, cache, storage state e diretório temporário separados; páginas pertencem a uma sessão sob lease. O pool `BROWSER` é separado dos pools `AGENT`, `MAINTENANCE` e `SCHEDULER`.

Toda operação aplica limites de tempo, páginas, redirects, DOM, upload, download e rede. Navegação e redirect revalidam destino, DNS e política; loopback, redes privadas, metadata services e schemes não permitidos permanecem negados por padrão. Conteúdo web é dado não confiável. DOM, screenshots e downloads tornam-se Artifacts somente após a confirmação de seus fluxos autorizados; cookies e storage state ficam sob referências secretas.

## Consequências

### Benefícios

- Contém Playwright e suas dependências em um pool escalável, observável e isolado da borda HTTP e do Kernel.
- Preserva a regra de portas: o Runtime coordena uma `Execution`, mas não conhece handles, SDK ou processo de browser.
- Reduz o alcance de uma falha, de conteúdo hostil ou de consumo excessivo a uma sessão e a um Worker compatível.
- Mantém a substituibilidade futura de engine e topologia sem expor semântica concreta ao domínio.

### Custos e falhas aceitas

- Browser Workers aumentam custo de imagens, processos, sandbox, capacidade de rede, aquecimento e monitoramento específico por pool.
- Navegações podem expirar, falhar por DNS, política, quota, crash, download inválido ou encerramento de Worker; sessão viva não é transferida para outro processo.
- Fila pode atrasar ou redeliver jobs e cancelamento cooperativo pode chegar tarde; operações com submit, clique, upload ou download podem ter efeito externo incerto e não recebem retry cego.
- Isolamento de processos não elimina a necessidade de policy de rede, grants mínimos, limpeza de temporários, quotas e revalidação de ownership.

### O que esta decisão não resolve

Esta decisão não escolhe engine alternativa, imagem de container, topologia de fila, crawler, semântica de extração, gravação de vídeo nem interface visual. Também não torna conteúdo web confiável, não fornece persistência de perfil por si só e não autoriza acesso a rede, filesystem do host, banco ou segredos fora de grants explícitos.

## Alternativas consideradas

- **Executar Playwright na API ou no Runtime:** rejeitada porque mistura borda, Kernel e processo não confiável, além de bloquear isolamento e recuperação adequados.
- **Permitir Playwright em Workers genéricos:** rejeitada porque contamina pools de agente com requisitos de sandbox, perfil e rede incompatíveis.
- **Browser Worker com acesso direto ao banco:** rejeitada porque amplia credenciais e permite que um processo de automação decida ou consulte estado de domínio.
- **Serviço remoto de browser desde o início:** adiado; pode implementar a mesma porta quando escala ou operação justificarem o custo, preservando jobs, grants e isolamento.

## Relações com RFCs

- [RFC 405 — Browser](../architecture/400-tools-resources/405-browser.md) define a porta de jobs, isolamento, políticas e proibições do Browser Worker.
- [RFC 402 — Resource Manager](../architecture/400-tools-resources/402-resource-manager.md) define leases e ciclo de vida de recursos.
- [RFC 403 — Filesystem](../architecture/400-tools-resources/403-filesystem.md) limita uploads e temporários ao Workspace autorizado.
- [RFC 602 — Artifact Storage](../architecture/600-platform-data/602-artifact-storage.md) define referências, staging e integridade de downloads, DOM e screenshots.
- [RFC 702 — Segurança](../architecture/700-api-security/702-security.md) define autorização, secrets, tenancy e revogação.
- [RFC 801 — Workers e filas](../architecture/800-operations/801-workers.md) define isolamento dos pools, retry, fencing e recuperação.
