# Frontend do AgentOS

## Estado desta documentação

Esta pasta descreve o frontend a partir do código do backend inspecionado em 2026-08-07. O backend é a fonte de verdade técnica; o briefing de produto é a fonte de direção de experiência. Nenhuma API, evento ou estado foi presumido apenas porque seria útil à interface.

O achado mais importante é de integração: os domínios e os adaptadores de teste existem, mas `agentos.bootstrap.production.create_production_app()` instala serviços indisponíveis quando a composição real não é injetada. Logo, não há hoje uma superfície de frontend operacional para autenticar, consultar recursos ou receber eventos em produção. O plano propõe uma UX pronta para os contratos, mas prioriza essa composição como pré-requisito.

## Leitura recomendada

1. [BACKEND_DISCOVERY.md](BACKEND_DISCOVERY.md) — fatos observados no código e nos testes.
2. [BACKEND_CAPABILITY_MATRIX.md](BACKEND_CAPABILITY_MATRIX.md) — o que pode ou não ser observado/controlado.
3. [UX_UI_SPEC.md](UX_UI_SPEC.md) e [SCREEN_MAP.md](SCREEN_MAP.md) — experiência e telas.
4. [REALTIME_ARCHITECTURE.md](REALTIME_ARCHITECTURE.md) e [BACKEND_UI_MAPPING.md](BACKEND_UI_MAPPING.md) — ponte rigorosa entre contrato e UI.
5. [FRONTEND_ARCHITECTURE.md](FRONTEND_ARCHITECTURE.md), [MOTION_SYSTEM.md](MOTION_SYSTEM.md), [AGENT_VISUAL_LANGUAGE.md](AGENT_VISUAL_LANGUAGE.md) e [COMPONENT_SYSTEM.md](COMPONENT_SYSTEM.md) — implementação futura.
6. [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) — ordem incremental e gates; [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — tarefas, contratos e verificações por fase.

## Convenções

- **Confirmado**: caminho de código e/ou teste verificável.
- **Derivado**: projeção determinística de fatos confirmados; nunca substitui estado backend.
- **Proposto**: decisão de frontend que depende de validação de produto ou de um gap ser fechado.
- Campos terminados em `*_ref` são opacos. Não representam conteúdo seguro para renderizar.

## Evidência executada

`python -m pytest -q tests/unit/api/test_api_asgi.py tests/unit/execution tests/unit/multi_agent tests/unit/tool_runtime tests/unit/providers/test_provider_api.py` → **100 passed**.
