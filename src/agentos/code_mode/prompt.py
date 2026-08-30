"""Prompt policy for Code mode, intentionally short enough for weak models."""
from __future__ import annotations

from .models import CodeAutonomy, CodeWorkKind


def code_mode_instructions(*, work_kind: str, autonomy: str, plan_path: str | None = None) -> str:
    kind = CodeWorkKind(work_kind)
    level = CodeAutonomy(autonomy)
    approval = "Você já tem autonomia de código para este turno." if level is not CodeAutonomy.APPROVAL_REQUIRED else (
        "Antes de qualquer escrita, crie um contrato claro e use `ask_user` para aprovar o plano. "
        "Não escreva arquivos, rode comandos mutáveis nem faça commit antes da resposta."
    )
    external = (
        "Push, PR e deploy não precisam de nova confirmação neste turno, exceto deploy em produção, que sempre exige `ask_user`."
        if level is CodeAutonomy.FULL_AUTONOMY
        else "Push, PR e qualquer deploy exigem `ask_user`; deploy em produção sempre exige `ask_user`."
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
        investigation,
        frontend,
        "Se um teste pré-existente e fora do escopo falhar, pare e peça uma decisão com evidência; nunca oculte a falha.",
        external,
        "No fim, relate mudanças, testes executados, validação visual, ressalvas e próximos passos com base em evidências reais.",
    ))
