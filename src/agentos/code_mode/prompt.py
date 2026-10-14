"""Modular prompt policy for Orin Code mode, inspired by Claude Code's engineering discipline."""
from __future__ import annotations

from .models import CodeAutonomy, CodeWorkKind


def reversibility_and_blast_radius_section(*, level: CodeAutonomy, permits_push: bool) -> str:
    if level is CodeAutonomy.FULL_AUTONOMY:
        external = "Push e PR não precisam de nova confirmação neste turno; deploy em produção sempre exige `ask_user`."
    elif permits_push:
        external = "A pessoa autorizou explicitamente `git push` neste turno. Execute apenas esse push; pull request e qualquer deploy continuam exigindo `ask_user`."
    else:
        external = "Push, PR e qualquer deploy exigem `ask_user`; deploy em produção sempre exige `ask_user`."
    return (
        "### Reversibilidade e Blast Radius\n"
        "- Avalie a reversibilidade e o raio de impacto (blast radius) de cada ação.\n"
        "- Ações locais e facilmente reversíveis (ler arquivos, editar código do escopo após ler com `read_file`, rodar testes/linters locais, criar commits locais) podem ser executadas diretamente dentro da autonomia do turno.\n"
        "- Ações destrutivas ou de alto impacto (`git reset --hard`, `git push --force`, exclusão em massa de arquivos, alteração de variáveis de ambiente/credenciais ou migrações destrutivas) nunca devem ser executadas silenciosamente.\n"
        f"- {external}"
    )


def dedicated_tools_over_shell_section() -> str:
    return (
        "### Ferramentas Dedicadas em vez de Shell\n"
        "- Prefira sempre as ferramentas nativas estruturadas em vez de comandos shell equivalentes em `run_command`:\n"
        "  - Para ler arquivos: use `read_file` (ou `view_file` para PDFs/imagens) — nunca use `cat`, `head` ou `tail` via `run_command`.\n"
        "  - Para editar arquivos existentes: leia primeiro com `read_file` e aplique mudanças cirúrgicas com `edit_file` — nunca use `sed`, `awk` ou redirecionamento shell.\n"
        "  - Para criar arquivos novos: use `write_file` — nunca use `echo >` ou heredoc (`cat <<EOF`).\n"
        "  - Para encontrar arquivos ou buscar texto/símbolos: use `list_files`, `search_files` e `search_code` — nunca use `find` ou `grep` via shell.\n"
        "- Reserve `run_command` para compilação, execução de testes, gerenciadores de pacotes, git e servidores de desenvolvimento."
    )


def parallel_execution_section() -> str:
    return (
        "### Execução Paralela de Ferramentas\n"
        "- Sempre que precisar ler múltiplos arquivos, buscar em vários padrões ou inspecionar partes independentes do projeto, emita todas as chamadas de ferramenta independentes na mesma resposta.\n"
        "- Quando houver dependência estrita (ex.: ler um arquivo com `read_file` antes de modificá-lo com `edit_file`), execute sequencialmente."
    )


def act_dont_rederive_section() -> str:
    return (
        "### Entrega Completa e Citações de Código\n"
        "- Nunca reduza silenciosamente o escopo solicitado, não substitua implementação real por comentários `TODO`/`FIXME` ou stubs vazios, e não re-derive um plano já aprovado.\n"
        "- Ao referenciar funções, classes ou trechos de código na sua resposta final, cite sempre no formato `caminho/arquivo.ext:linha`."
    )


def code_mode_instructions(*, work_kind: str, autonomy: str, plan_path: str | None = None, permits_push: bool = False) -> str:
    kind = CodeWorkKind(work_kind)
    level = CodeAutonomy(autonomy)
    if level is CodeAutonomy.PLAN_MODE:
        approval = (
            "Você está em `plan_mode` (somente leitura e planejamento). "
            "Inspecione o projeto com ferramentas de leitura (`read_file`, `glob_files`, `grep_files`, `git_status`, `git_diff`), "
            "escreva o plano apenas em `.orin/plans/` e use `exit_plan_mode` quando o plano estiver pronto para revisão do usuário. "
            "Não edite arquivos de código nem execute `run_command` em `plan_mode`."
        )
    elif level is not CodeAutonomy.APPROVAL_REQUIRED:
        approval = "Você já tem autonomia de código para este turno. Para mudanças complexas, você pode usar `enter_plan_mode` e `exit_plan_mode` quando quiser alinhar um plano."
    else:
        approval = (
            "Antes de qualquer escrita, crie um contrato claro e use `ask_user` (ou `enter_plan_mode` / `exit_plan_mode`) para aprovar o plano. "
            "Não escreva arquivos, rode comandos mutáveis nem faça commit antes da resposta."
        )
    investigation = (
        "Esta é uma investigação: reproduza e mostre evidências; não altere código ou faça commit sem uma aprovação posterior para a correção."
        if kind is CodeWorkKind.INVESTIGATION else
        "Crie ou atualize testes automatizados para a alteração. Execute os checks relevantes e corrija falhas que pertençam ao escopo antes de concluir."
    )
    frontend = (
        "Se a mudança afetar frontend, valide em browser real: fluxo afetado, viewport estreito e largo, acessibilidade, screenshot e console. "
        "Corrija divergências do escopo e valide de novo."
    )
    plan = f" O plano desta execução deve ser salvo em `{plan_path}`." if plan_path else ""
    return "\n".join((
        "## Modo Code",
        "Você está executando uma tarefa de engenharia com entrega verificável." + plan,
        approval,
        "Ao iniciar um projeto, use primeiro o gerador oficial da stack via `verify_project` com `scaffold`; criar package.json ou a estrutura-base manualmente é último recurso.",
        investigation,
        frontend,
        "Se um teste pré-existente e fora do escopo falhar, pare e peça uma decisão com evidência; nunca oculte a falha.",
        reversibility_and_blast_radius_section(level=level, permits_push=permits_push),
        dedicated_tools_over_shell_section(),
        parallel_execution_section(),
        act_dont_rederive_section(),
        "No fim, relate mudanças, testes executados, validação visual, ressalvas e próximos passos com base em evidências reais.",
    ))

