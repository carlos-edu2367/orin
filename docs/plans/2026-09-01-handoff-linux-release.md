# Handoff — execução inline do release Linux

**De:** sessão que investigou, desenhou e planejou o release Linux (spike empírico em Ubuntu
real + design + plano de implementação).
**Para:** a sessão que vai executar o plano, começando pela Task 1.
**Leitura obrigatória antes de tocar em código, nesta ordem:**
1. [2026-09-01-linux-release-design.md](../superpowers/specs/2026-09-01-linux-release-design.md) — o design, com a evidência do spike.
2. [2026-09-01-linux-release.md](../superpowers/plans/2026-09-01-linux-release.md) — o plano, 9 tasks, cada uma com TDD onde faz sentido e verificação real em WSL onde não faz.

Este documento não repete o conteúdo dos dois acima — só o estado do ambiente e por onde retomar.

## Estado do repositório

`main` está limpo, sem branch de feature aberta para este trabalho. Últimos commits:

```
df4d35e docs(plans): Linux release implementation plan
67d31c0 docs(specs): design for a Linux release
c61c29f feat(frontend): show what the agent learned and let it be corrected   <- Fase 1 de memória, já mergeada
```

**Antes da Task 1:** crie uma branch de feature a partir de `main` (`git checkout -b
feature/linux-release-fase-1` ou nome equivalente), do mesmo jeito que a Fase 1 de memória
fez. Não implemente direto em `main`.

## Estado do ambiente WSL (Ubuntu 24.04, `wsl.exe -d Ubuntu`)

Confirmado ainda de pé nesta máquina no momento deste handoff:

- `uv` instalado em `~/.local/bin` — precisa de `source $HOME/.local/bin/env` no início de
  cada comando (não persiste em `PATH` entre invocações de `wsl.exe -e bash -lc "..."`).
- Python 3.13.15 já instalado via `uv python install 3.13`.
- **Node NÃO está instalado** — necessário só na Task 7 (`build-linux.sh`). O plano já
  descreve dois jeitos de instalar (nodesource + apt, ou nvm sem precisar de root).
- O diretório de scratch usado nas verificações do spike (`~/orin-spike`) já foi limpo.
  Cada task que precisa de um ambiente Python em WSL deve rodar `uv sync --frozen
  --all-groups` de novo antes de qualquer `uv run`.

## Cuidado operacional que já causou um incidente nesta investigação

**O WSL monta o repositório Windows como a mesma pasta física** — `/mnt/c/Users/User/
Documents/GitHub/Carlos/pessoal/orin` e `C:\Users\User\Documents\GitHub\Carlos\pessoal\
orin` são o mesmo diretório em disco. Rodar `uv sync` (ou qualquer comando que crie/
sobrescreva `.venv`) de dentro do WSL contra esse caminho **substitui o `.venv` do
Windows por um venv Linux**, quebrando o ambiente de desenvolvimento Windows até que
`uv sync --frozen --all-groups` seja rodado de novo no lado Windows para restaurá-lo.

Isso já aconteceu uma vez durante o spike e foi corrigido. Duas formas de evitar que
aconteça de novo:

1. **Preferível para qualquer verificação rápida/pontual:** rode os comandos do plano
   exatamente como escritos — eles já usam `cd /mnt/c/.../orin` porque as tasks 1-6
   precisam ler/escrever no repositório de verdade (testes, `install.sh`, etc.) — mas
   **nunca rode `uv sync` a partir do WSL sem, em seguida, rodar `uv sync --frozen
   --all-groups` também no lado Windows antes de voltar a usar `.venv/Scripts/python.exe`.**
2. **Para a Task 7** (que precisa de um ambiente de build completo, Node incluso, e vai
   gerar bastante lixo de build), considere copiar o repo para o filesystem nativo do
   WSL primeiro (`rsync -a --exclude='.venv' --exclude='node_modules' --exclude='.git'
   /mnt/c/.../orin/ ~/orin-build/`), exatamente como o spike fez — isola os dois
   ambientes de vez e evita qualquer risco de contaminação cruzada.

Depois de qualquer sessão de trabalho no WSL contra `/mnt/c/...`, rode
`.venv/Scripts/python.exe -m pytest tests/unit/agentic/test_learning_ledger.py -v` do
lado Windows como sanity check rápido de que o `.venv` ainda é o do Windows (o teste roda
em < 1s; se `.venv/Scripts/python.exe` não existir mais, o venv foi substituído — rode
`uv sync --frozen --all-groups` na raiz do repo, no lado Windows, para restaurar).

## Por onde retomar

O plano tem 9 tasks em ordem de dependência. Comece pela **Task 1** (correção do bug do
processo zumbi) — é o único pré-requisito duro, sem ele nada do resto do plano se sustenta
(`read_process_output`/`stop_process` continuam quebrados em Linux).

Use a skill `superpowers:executing-plans` (ou `subagent-driven-development`, se a sessão
tiver acesso a subagentes e preferir esse caminho) apontando para
`docs/superpowers/plans/2026-09-01-linux-release.md`.

**Duas ações que o plano deixa explicitamente fora da execução automática** — ambas exigem
autorização explícita do usuário quando chegar a hora, não são para fazer sozinho: `git
push` (Tasks 6 e 9 fazem mudanças em CI/release que só são provadas de verdade depois de
enviadas) e o disparo real de uma tag `v*` (Task 9, publica uma release pública).
