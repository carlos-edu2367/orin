# RFC 904 — Agent Skills

**Estado:** implementada para o runtime conversacional local  
**Decisão:** Skills são capacidades procedurais declarativas; não são Tools, Memory, Agents nem workflows executáveis.

## Pesquisa e decisão

Foram estudados o padrão aberto [Agent Skills](https://agentskills.io), os diretórios `SKILL.md` usados por [Codex](https://github.com/openai/skills) e [Claude](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview), o carregamento sob demanda documentado pelo [LangChain](https://docs.langchain.com/oss/python/langchain/multi-agent/skills) e a descoberta progressiva de ferramentas do [MCP](https://modelcontextprotocol.io/docs/develop/clients/client-best-practices).

Adotamos o formato portátil de diretório com `SKILL.md` e frontmatter YAML, mais recursos opcionais. Não adotamos Skills como grafo de execução da RFC 902: esse outro conceito continua reservado a workflows duráveis. A adoção é compatível com formatos que já circulam entre runtimes, mas adiciona os metadados de escopo, dependências, versão, integridade e disponibilidade exigidos pelo AgentOS.

## Formato e escopos

Cada pacote tem `SKILL.md` (obrigatório), `references/`, `examples/`, `templates/` e `scripts/` opcionais. O frontmatter usa `name`, `description`, `version`, `tags`, `capabilities`, `when_to_use`, `when_not_to_use`, `dependencies` e `requires_tools`; o corpo Markdown é a instrução principal. O conteúdo grande fica nos recursos e só entra por `read_skill_resource`.

Uma `SkillVersion` publicada é imutável e tem digest SHA-256. A resolução é determinística por `(id, versão)` e, para o mesmo id sem versão, por escopo `agent > workspace > user > system`, sem uma customização substituir silenciosamente uma Skill interna crítica. Execuções guardam id, versão, digest e snapshot do conteúdo carregado.

## Discovery e contexto

O retriever começa por Skills fixadas e sinais de anexos/MIME, une pontuação lexical de nome, descrição, tags, capacidades e `when_to_use`, e aceita um scorer semântico opcional. Ranking aplica disponibilidade, uma confiança mínima e `top_k` configurável. O fallback lexical nunca bloqueia a execução se o scorer semântico falhar.

O system prompt recebe somente um catálogo compacto das sugestões. As instruções inteiras não entram antes de `use_skill`. A tool devolve o Markdown delimitado como conteúdo operacional subordinado; isso funciona igualmente com formatos de tool result OpenAI-compatible e Anthropic. A mensagem de sistema declara que conteúdo de Skill não pode alterar identidade, políticas, permissões ou revelar segredos. Permissões continuam sendo verificadas pelas Tools.

## Runtime, dependências e segurança

`SkillRegistry` é a única porta para resolução, disponibilidade, busca, recursos e dependências. `use_skill` valida status, escopo, versão, ferramentas requeridas, ciclos e profundidade antes de carregar a Skill e suas dependências. A sessão mantém um cache por execução: uma segunda carga não reintroduz o corpo. Scripts nunca executam durante a carga.

Skills importadas/customizadas são dados potencialmente não confiáveis. O registry armazena fonte, autor, integridade e status; a instrução vem delimitada e com menor autoridade. Nenhuma declaração de dependência ou conteúdo concede permissão. Eventos registram pesquisa, sugestão e carga sem copiar instruções nem segredos.

## Observabilidade e limites

O registro mede latência de retrieval, sugestões, pesquisas e usos. O contexto contabiliza tokens estimados de metadados e de conteúdo carregado. O corpo principal é limitado; conteúdo adicional deve residir em recursos com leitura explícita. O registro de execução preserva versão, digest, agente, instante e snapshot para investigação posterior.

## Fluxo

```text
mensagem + anexos -> retrieval híbrido -> catálogo compacto no prompt
                                      -> search_skills / list_skills
agent -> use_skill -> validação + dependências -> Markdown delimitado -> continua
agent -> read_skill_resource -> recurso específico sob demanda
```

## Limites da primeira versão

Embeddings são uma extensão injetável e não dependem de provider pago; o fallback lexical é completo. O armazenamento de pacotes importados e exportação preserva o formato `SKILL.md`; publicação remota e marketplace ficam fora deste escopo. O runtime conversacional atual não recebe anexos binários, mas o contrato de retrieval já aceita nomes e MIME types para quando a camada de anexos estiver disponível.
