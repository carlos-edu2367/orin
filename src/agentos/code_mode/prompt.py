"""Modular prompt policy for Orin Code mode, inspired by Claude Code's engineering discipline."""
from __future__ import annotations

from .models import (
    CodeAutonomy,
    CodePermissionMode,
    CodeWorkKind,
    normalize_code_autonomy,
    normalize_permission_mode,
)


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
        "- Prefira sempre as ferramentas nativas estruturadas em vez de comandos shell equivalentes em `run_command` (`Bash`):\n"
        "  - Para ler arquivos (`Read`): use `read_file` (ou `view_file` para PDFs/imagens) — nunca use `cat`, `head` ou `tail` via `run_command`.\n"
        "  - Para editar arquivos existentes (`Edit`): leia primeiro com `read_file` (invariante read-before-edit) e aplique mudanças cirúrgicas com `edit_file` — nunca use `sed`, `awk` ou redirecionamento shell.\n"
        "  - Para criar ou sobrescrever arquivos (`Write`): use `write_file` (lendo antes com `read_file` se o arquivo já existir) — nunca use `echo >` ou heredoc (`cat <<EOF`).\n"
        "  - Para encontrar arquivos (`Glob`) ou buscar conteúdo (`Grep`): use `glob_files`, `list_files`, `search_files` e `search_code` — nunca use `find` ou `grep` via shell.\n"
        "- Ao chamar `run_command` (`Bash`), forneça sempre uma `description` curta e clara (5-10 palavras) descrevendo o comando.\n"
        "- Reserve `run_command` para compilação, execução de testes, gerenciadores de pacotes, git e servidores de desenvolvimento."
    )


def parallel_execution_section() -> str:
    return (
        "### Execução Paralela de Ferramentas e Subagentes\n"
        "- Sempre que precisar ler múltiplos arquivos, buscar em vários padrões ou inspecionar partes independentes do projeto, emita todas as chamadas de ferramenta independentes na mesma resposta.\n"
        "- Quando houver dependência estrita (ex.: ler um arquivo com `read_file` antes de modificá-lo com `edit_file`), execute sequencialmente.\n"
        "- Para delegar exploração, auditoria ou tarefas paralelas a subagentes no Modo Code, prefira `run_subagent(subagent_type=\"explore\"|\"plan\"|\"general\"|\"fork\", task=\"...\", description=\"...\", model_id=\"...\")`. "
        "Ao informar `model_id`, copie o identificador exatamente como listado nos modelos favoritos (incluindo o prefixo `~` quando existir) ou omita `model_id` para usar o modelo atual do turno."
    )


def act_dont_rederive_section() -> str:
    return (
        "### Entrega Completa e Citações de Código\n"
        "- Seja direto, conciso e técnico; evite preâmbulos desnecessários.\n"
        "- Nunca reduza silenciosamente o escopo solicitado, não substitua implementação real por comentários `TODO`/`FIXME` ou stubs vazios, e não re-derive um plano já aprovado.\n"
        "- Ao referenciar arquivos no texto, use links inline concisos `[nome](workspace://caminho/arquivo.ext)` sem quebrar listas ou parágrafos; ao citar trechos ou símbolos específicos, use `caminho/arquivo.ext:linha`."
    )


def permission_mode_section(
    *,
    permission_mode: CodePermissionMode,
    level: CodeAutonomy,
    plan_path: str | None = None,
) -> str:
    modes_overview = (
        f"### Modo de Permissão Ativo: `{permission_mode.value}`\n"
        "- Modos de permissão suportados na sessão Orin Code (`Shift+Tab`):\n"
        "  - `accept_edits`: auto-aprova edições de arquivos no workspace (`write_file`, `edit_file`) e comandos locais seguros; execução direta sem burocracia.\n"
        "  - `plan_mode`: modo somente leitura e arquitetura; explora o repositório, escreve o plano em `.orin/plans/` e chama `exit_plan_mode` para aprovação.\n"
        "  - `ask_permissions`: solicita confirmação antes de edições de arquivos e comandos shell.\n"
        "  - `full_autonomy`: autonomia completa dentro do workspace (mantendo confirmação para deploy em produção)."
    )
    if permission_mode is CodePermissionMode.PLAN_MODE or level is CodeAutonomy.PLAN_MODE:
        target_plan = plan_path or ".orin/plans/<slug>.md"
        behavior = (
            "Você está em `plan_mode` (somente leitura e planejamento). "
            "Explore o repositório apenas com ferramentas de leitura (`read_file`, `glob_files`, `search_files`, `list_files`, `search_code`), "
            f"escreva o plano estruturado em `.orin/plans/` (`{target_plan}`) e chame `exit_plan_mode` para aprovação do usuário. "
            "Não edite arquivos de código de produção nem execute `run_command` mutável enquanto estiver em `plan_mode`."
        )
    elif permission_mode in {CodePermissionMode.ACCEPT_EDITS, CodePermissionMode.FULL_AUTONOMY} or level is not CodeAutonomy.APPROVAL_REQUIRED:
        behavior = (
            "Você já tem autonomia de código (`accept_edits`) para este turno. Aja diretamente como no Claude Code "
            "(`Read` / `read_file`, `Grep` / `search_files`, `Glob` / `glob_files`, `Edit` / `edit_file`, `Write` / `write_file`, `Bash` / `run_command`, `TodoWrite` / `todo_write`) "
            "sem cerimônia desnecessária e sem criar arquivos de plano não solicitados. "
            "Para tarefas de múltiplos passos, use `todo_write`, `task_create` e `task_update` (mantendo no máximo uma tarefa `in_progress` por vez). "
            "Use `enter_plan_mode` e `exit_plan_mode` apenas quando estiver em `plan_mode` ou quando precisar planejar mudanças arquiteturais complexas."
        )
    else:
        behavior = (
            "Você está em `ask_permissions`. Antes de qualquer escrita em código, detalhe o plano e use `ask_user` "
            "(ou `enter_plan_mode` / `exit_plan_mode`) para aprovar o plano ou aguarde confirmação de permissão. "
            "Para tarefas de múltiplos passos, use `todo_write`, `task_create` e `task_update`."
        )
    return f"{modes_overview}\n- {behavior}"


def build_code_mode_system_prompt(
    permission_mode: str | CodePermissionMode | CodeAutonomy | None = None,
    *,
    work_kind: str | CodeWorkKind = "implementation",
    autonomy: str | CodeAutonomy | CodePermissionMode | None = None,
    plan_path: str | None = None,
    permits_push: bool = False,
    workspace_root: str | None = None,
    **_extra: object,
) -> str:
    raw_mode = permission_mode if permission_mode is not None else (autonomy if autonomy is not None else "accept_edits")
    resolved_perm = normalize_permission_mode(raw_mode)
    level = normalize_code_autonomy(autonomy if autonomy is not None else resolved_perm)
    try:
        kind = CodeWorkKind(str(work_kind))
    except ValueError:
        kind = CodeWorkKind.IMPLEMENTATION

    investigation = (
        "Esta é uma investigação: reproduza e mostre evidências; não altere código ou faça commit sem uma aprovação posterior para a correção."
        if kind is CodeWorkKind.INVESTIGATION else
        "Crie ou atualize testes automatizados para a alteração. Execute os checks relevantes e corrija falhas que pertençam ao escopo antes de concluir."
    )
    frontend = (
        "Se a mudança afetar frontend, valide em browser real: fluxo afetado, viewport estreito e largo, acessibilidade, screenshot e console. "
        "Corrija divergências do escopo e valide de novo."
    )
    plan_notice = (
        f" O plano desta execução deve ser salvo em `{plan_path}`."
        if plan_path and (resolved_perm is CodePermissionMode.PLAN_MODE or level is CodeAutonomy.PLAN_MODE)
        else ""
    )
    workspace_notice = f"\n- Workspace vinculado: `{workspace_root}`." if workspace_root else ""
    return "\n".join((
        "## Modo Code",
        "Você está executando uma tarefa de engenharia com entrega verificável." + plan_notice + workspace_notice,
        permission_mode_section(permission_mode=resolved_perm, level=level, plan_path=plan_path),
        "Ao iniciar um projeto do zero, use primeiro o gerador oficial da stack via `verify_project` com `scaffold`; criar package.json ou a estrutura-base manualmente é último recurso.",
        investigation,
        frontend,
        "Se um teste pré-existente e fora do escopo falhar, pare e peça uma decisão com evidência; nunca oculte a falha.",
        reversibility_and_blast_radius_section(level=level, permits_push=permits_push),
        dedicated_tools_over_shell_section(),
        parallel_execution_section(),
        act_dont_rederive_section(),
        "No fim, relate mudanças, testes executados, validação visual, ressalvas e próximos passos com base em evidências reais.",
    ))


def code_mode_instructions(
    *,
    work_kind: str = "implementation",
    autonomy: str = "accept_edits",
    plan_path: str | None = None,
    permits_push: bool = False,
    permission_mode: str | None = None,
) -> str:
    return build_code_mode_system_prompt(
        permission_mode=permission_mode or autonomy,
        work_kind=work_kind,
        autonomy=autonomy,
        plan_path=plan_path,
        permits_push=permits_push,
    )


__all__ = [
    "act_dont_rederive_section",
    "build_code_mode_system_prompt",
    "code_mode_instructions",
    "dedicated_tools_over_shell_section",
    "parallel_execution_section",
    "permission_mode_section",
    "reversibility_and_blast_radius_section",
]

