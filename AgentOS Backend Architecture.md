# AgentOS Backend Architecture
**Versão:** 0.1 (Draft)  
**Status:** Arquitetura Base  
**Objetivo:** Definir a arquitetura do backend do AgentOS.

---

# 1. Visão Geral

O AgentOS **não é um chatbot**.

O AgentOS é um **Sistema Operacional para Agentes de Inteligência Artificial**, permitindo que agentes persistentes trabalhem de forma colaborativa, utilizem recursos computacionais, compartilhem contexto e executem tarefas complexas durante longos períodos.

O chat é apenas uma interface.

O verdadeiro núcleo do sistema é um **Runtime de Execução de Agentes**.

---

# 2. Objetivos

O backend deve permitir:

- agentes persistentes;
- criação dinâmica de agentes;
- comunicação entre agentes;
- contexto compartilhado;
- memória permanente;
- ferramentas (Tools);
- capacidades (Capabilities);
- workers assíncronos;
- execução de longa duração;
- observabilidade completa;
- arquitetura extensível.

---

# 3. Princípios Arquiteturais

## Everything is an Execution

Toda ação do sistema é representada como uma Execution.

Exemplos:

- mensagem enviada pelo usuário;
- execução de Skill;
- navegação;
- pesquisa;
- geração de código;
- criação de agente;
- análise de arquivos.

Nunca existe "executar um chat".

Sempre existe uma Execution.

---

## Event Driven

Todo evento importante gera um Event.

Exemplos:

- AgentCreated
- ExecutionStarted
- ToolStarted
- ToolFinished
- BrowserOpened
- MemorySaved
- ExecutionFinished

Eventos são utilizados para:

- frontend
- auditoria
- logs
- memória
- métricas
- sincronização

---

## Runtime First

Toda regra de negócio pertence ao Runtime.

FastAPI apenas expõe a API.

React apenas consome eventos.

O Runtime não conhece:

- HTTP
- React
- SSE
- Banco
- Playwright

Ele conhece apenas interfaces.

---

## Plugins First

Providers.

Tools.

Capabilities.

Skills.

Tudo deve ser registrável dinamicamente.

Nada deve depender de switch/case espalhados pelo projeto.

---

# 4. Arquitetura Geral

```
Frontend
      │
 REST + SSE
      │
FastAPI Gateway
      │
Execution Manager
      │
Execution Queue
      │
ARQ Workers
      │
Agent Runtime
      │
Tool Runtime
      │
Resources
```

---

# 5. Stack

## API

- FastAPI

## Banco

- PostgreSQL

## Cache/Filas

- Redis

## Workers

- ARQ

## Runtime

- asyncio

## Browser

- Playwright

## ORM

- SQLAlchemy 2

## Migrações

- Alembic

## Validação

- Pydantic v2

---

# 6. Módulos

```
backend/

api/
core/
runtime/
providers/
agents/
memory/
context/
tools/
capabilities/
resources/
browser/
workers/
scheduler/
events/
storage/
security/
settings/
database/
telemetry/
```

Cada módulo possui apenas uma responsabilidade.

---

# 7. Núcleo do Sistema

O núcleo é composto por:

- Runtime
- Execution Manager
- Context Manager
- Tool Runtime
- Provider Runtime
- Event Bus

Nada mais conhece todas essas peças.

---

# 8. Runtime

O Runtime é responsável por executar agentes.

Fluxo:

```
Recebe Execution

↓

Monta Contexto

↓

Seleciona Modelo

↓

Executa LLM

↓

Recebe Tool Calls

↓

Executa Tools

↓

Atualiza Contexto

↓

Repete

↓

Finaliza
```

---

# 9. Execution

Execution é a unidade principal do sistema.

Ela possui:

- id
- workspace
- usuário
- agente responsável
- tarefa
- contexto
- estado
- custo
- eventos
- mensagens
- logs

Estados:

- QUEUED
- STARTING
- RUNNING
- WAITING_TOOL
- WAITING_USER
- PAUSED
- COMPLETED
- FAILED
- CANCELLED

---

# 10. Agentes

Agentes são entidades persistentes.

Cada agente possui:

- id
- nome
- avatar
- cor
- modelo
- prompt
- capabilities
- tools
- skills
- memória
- configuração

Agentes nunca morrem após uma conversa.

Eles permanecem disponíveis.

---

# 11. Multiagentes

Um agente pode:

- criar agentes;
- conversar com agentes;
- compartilhar contexto;
- delegar tarefas;
- aguardar respostas;
- cancelar tarefas.

O Kernel coordena essas operações.

---

# 12. Kernel de Orquestração

Responsabilidades:

- criar agentes
- destruir agentes
- iniciar execuções
- controlar dependências
- sincronizar tarefas
- distribuir contexto
- controlar timeouts
- cancelar execuções

---

# 13. Context Manager

Responsável por construir o contexto enviado ao modelo.

Combina:

- mensagens
- resumo
- memórias
- arquivos
- decisões
- eventos
- resultados de tools

Respeita orçamento de tokens.

---

# 14. Memory Manager

Responsável por memória permanente.

Tipos:

## Private Memory

Pertence ao agente.

## Workspace Memory

Compartilhada.

## User Memory

Preferências.

## Semantic Memory

Conhecimento.

## Blackboard

Estado compartilhado.

---

# 15. Blackboard

Representa conhecimento compartilhado.

Exemplos:

- decisões
- descobertas
- bugs
- tarefas
- contratos
- arquitetura

---

# 16. Tool Runtime

Toda Tool implementa a mesma interface.

```
execute()

validate()

cancel()
```

O Runtime nunca conhece implementações específicas.

---

# 17. Capabilities

Capabilities representam workflows.

Exemplo:

Implementar Feature

↓

Filesystem

↓

Terminal

↓

Git

↓

Testes

↓

Resumo

O modelo chama a Capability.

A Capability utiliza várias Tools.

---

# 18. Resource Manager

Recursos persistentes do sistema.

Tipos iniciais:

- Filesystem
- Terminal
- Browser

Futuros:

- SSH
- Docker
- Kubernetes
- Redis
- Banco
- GitHub

---

# 19. Browser Runtime

Baseado em Playwright.

Suporta:

- múltiplos browsers
- múltiplas páginas
- screenshots
- uploads
- downloads
- cookies
- perfis
- visão computacional

---

# 20. Providers

Todos seguem a mesma interface.

```
generate()

stream()

vision()

tool_call()

cancel()
```

Providers iniciais:

- OpenAI
- Anthropic
- OpenRouter

---

# 21. Model Catalog

Modelos não ficam fixos.

Cada modelo possui:

- provider
- nome
- contexto
- capabilities
- custo
- visão
- tool calling
- streaming

Perfis:

- coding
- reasoning
- orchestrator
- vision
- cheap
- balanced

---

# 22. Segurança

Single-user inicialmente.

Arquitetura preparada para multiusuário.

Autenticação:

- sessão server-side
- Redis
- cookies HttpOnly
- CSRF

API externa:

- Personal Access Tokens

Secrets:

- AES-256-GCM
- APP_MASTER_KEY

---

# 23. Workspaces

Cada projeto possui um Workspace.

```
workspace/

├── arquivos
├── downloads
├── screenshots
├── logs
├── cache
└── artefatos
```

Nenhuma Tool pode escapar da raiz do Workspace.

---

# 24. Persistência

## PostgreSQL

- agentes
- chats
- execuções
- mensagens
- eventos
- memórias
- configurações

## Redis

- filas
- pub/sub
- locks
- sessões
- cancelamentos

## Filesystem

- screenshots
- downloads
- uploads
- logs
- artefatos

---

# 25. Workers

Workers independentes.

Tipos:

- Agent Worker
- Browser Worker
- Scheduler Worker
- Maintenance Worker

---

# 26. Scheduler

Responsável por:

- execuções futuras
- Skills agendadas
- recorrências
- watchdogs
- limpeza

---

# 27. Observabilidade

Tudo gera eventos.

Tudo gera métricas.

Tudo gera logs.

O sistema deve permitir reconstruir qualquer execução posteriormente.

---

# 28. Extensibilidade

Todo componente deve ser substituível.

- Providers
- Tools
- Capabilities
- Skills
- Storage
- Browser
- Memory

Sem alterar o Runtime.

---

# 29. Filosofia

O AgentOS deve ser tratado como um Sistema Operacional para Agentes.

O Runtime representa o Kernel.

Os Agentes representam processos inteligentes.

As Tools representam chamadas de sistema.

Os Resources representam dispositivos.

As Capabilities representam programas compostos.

O Frontend representa apenas uma interface gráfica para controlar esse sistema.

---

# 30. Roadmap de Especificações

Após este documento, a arquitetura será detalhada em documentos independentes:

1. Runtime de Execução
2. Sistema de Agentes
3. Sistema de Contexto
4. Sistema de Memória
5. Tool Runtime
6. Capabilities
7. Resource Manager
8. Browser Runtime
9. Providers e Model Catalog
10. Persistência
11. API REST + SSE
12. Workers e Scheduler
13. Segurança
14. Plugin SDK
15. Skills
16. MCP (futuro)

---

# Missão do Projeto

> Construir uma plataforma open source para execução de agentes persistentes, colaborativos e orientados a eventos, capaz de evoluir de um assistente pessoal para um verdadeiro Sistema Operacional de Agentes, mantendo modularidade, observabilidade, extensibilidade e independência entre todos os seus componentes.