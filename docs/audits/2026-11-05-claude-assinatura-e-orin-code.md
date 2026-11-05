# Auditoria: Claude pela assinatura (`claude -p`) e Orin Code — 2026-11-05

Fontes: banco do Orin instalado (v0.11.6, `%LOCALAPPDATA%\Orin\data\orin.db`), sessões dos projetos Educly e Orin,
duas auditorias de código e conferência manual dos pontos principais.

## Sessões analisadas

| Sessão | Sintoma | Causa (itens abaixo) |
|---|---|---|
| Orin, 05/11 12:33 (`chat_64b2…`) | 4 min só `\n\n`, depois 31 tool calls num bloco (read + edit do mesmo arquivo + verify_project); todos os edits recusados; pytest rodou à toa | 1, 6, 12 |
| Educly, 05/11 12:32 (`chat_7e63…`) | 3 min de silêncio, 16 calls com paths inventados e reads duplicados; reabriu as imagens | 1, 8 |
| Ashy, 03/11 19:38 (`chat_2186…`) | modelo escreveu `[Tool Result id=call_…]` falsos no próprio texto | 1 |
| todas | contexto 200k para Opus 5.5 | 3 |

## Críticos — bridge do Claude

1. **Tool calls simuladas em texto.** `--tools "" --max-turns 1`, processo novo por iteração, histórico achatado numa
   mensagem (`claude_code.py:304-326`, `opencode.py:546-609`). Prompt incentiva várias chamadas por resposta
   (`opencode.py:431`); nada corta no primeiro `</orin_tool_call>`; chamadas após resultado inventado são executadas
   (`claude_code.py:972-994`). Lote cego, alucinação de resultados, custo quadrático, ~2s de spawn por iteração.
   O bridge depende de helpers privados do `opencode.py`.
2. **Opus 5.5 escolhido, Opus 4.7 executado.** `_LEGACY_CLAUDE_MODEL_MAP` (`claude_code.py:106`); aliases de subagente
   (`agent_definitions.py:31`, `fable` → `sonnet-4-6`). Uso gravado com o id errado; effort padrão muda.
3. **Teto de 200k fixo** em `workers/chat.py:940`. Catálogo tem 1M. Subir só junto com cache incremental (item 1).
4. **Silêncio**: texto das calls escondido, calls só emitidas no fim do processo (`claude_code.py:1394`), thinking do 5.5
   vem vazio. Hipótese: idle timeout de 180s pode matar thinking longo.
5. **Erros**: `api_error_status` ignorado (`claude_code.py:1345`), 404 vira 500 e é retentado; `max_output_tokens`
   nunca repassado.

## Críticos — Orin Code

6. `edit_file` falha em CRLF com `old_text` multi-linha (`edit.py:71`).
7. Ferramenta "Bash" roda cmd.exe (`bash.py:186`); prompt sem seção de ambiente.
8. Aging a cada iteração no bridge (`runtime.py:49`, `:1556`): só 8 resultados inteiros, imagens viram "[omitted]";
   reabrir imagem pelo cache volta sem pixels (`runtime.py:2242`); reler arquivo devolve stub "inalterado"
   (`read.py:346-397`).
9. `read_file` corta em 12.000 chars mas registra linhas 1-400 como lidas.

## Altos

10. Estado "arquivo lido" zera a cada mensagem do usuário (`session.py:1125`); fork compartilha o estado com o pai.
11. Anexos em `<repo>/uploads/` (`uploads/promotion.py:48`); `.orin/` também dentro do repo, sem exclude no git.
12. Gate de conclusão força `verify_project` do repo inteiro (`pip install -e .` no Python global, sem cancelamento);
    falha pré-existente muda o stage para "fixing".
13. Chamadas idênticas no mesmo lote rodam duas vezes; nada recusa edit no mesmo lote do read.

## Médios / baixos

~75 ferramentas (9,4k tokens) reenviadas por iteração; todos auto-concluídos no fim do turno; Grep case-insensitive e
`content` por padrão; lint após cada edit; semáforo global de 3 CLIs; `~/.claude/settings.json` vaza para o bridge;
resultado de ferramenta sem escape no transcript; nenhum log das chamadas `claude -p`; prompt colado vira anexo e a
mensagem fica vazia.

## Plano

- **Orin Code** (independente de provedor): itens 6-13.
- **Claude**: adapter próprio do `claude -p`, sem dependência do OpenCode, usando o loop nativo de ferramentas com as
  ferramentas do Orin expostas por MCP local; correções específicas (2, 3, 5, logs). Protocolo de texto só como fallback.
- Depois: Antigravity (`agy -p`) e Codex (`codex -p`) sobre a mesma base.

## Não verificado

`cache_read` real da sessão de 216k; keepalive da CLI em thinking longo; efeito do `--safe-mode` sobre `--mcp-config`;
texto do erro de limite de 5h.
