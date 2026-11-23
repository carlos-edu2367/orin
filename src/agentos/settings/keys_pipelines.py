"""Registered setting keys for Package C: pipelines, subagent budget ceilings and the /goal token cap (WS-C4)."""

from __future__ import annotations

from .registry import Scope, SettingKey, register

DEFAULT_GOAL_MAX_TOKENS = 300_000

PIPELINE_KEYS: tuple[SettingKey, ...] = (
    SettingKey(
        path="pipelines.enabled",
        type="boolean",
        default=True,
        scope=Scope.PROJECT_OFF_ONLY,
        group="pipelines",
        label="Pipelines",
        description="Permite executar pipelines declarativos. Nada roda sem um comando do usuário; o projeto só pode desligar.",
    ),
    SettingKey(
        path="pipelines.agentMayStart",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="pipelines",
        label="Agente pode iniciar pipeline",
        description="Publica a ferramenta RunPipeline para o agente, que só inicia pipelines já aprovados.",
    ),
    SettingKey(
        path="orchestration.budget.subagent.maxTokens",
        type="number",
        default=None,
        scope=Scope.PROJECT_LOWER_ONLY,
        group="budget",
        label="Teto de tokens por subagente",
        description="Limite de tokens de cada subagente, também sobre o orçamento pedido pelo agente. Vazio não limita; o projeto só reduz.",
        minimum=1,
    ),
    SettingKey(
        path="orchestration.budget.subagent.maxCostUsd",
        type="number",
        default=None,
        scope=Scope.PROJECT_LOWER_ONLY,
        group="budget",
        label="Teto de custo por subagente (US$)",
        description="Limite de custo em dólares de cada subagente (só conta custo conhecido; assinaturas sem preço contam tokens). Vazio não limita; o projeto só reduz.",
        minimum=0.01,
    ),
    SettingKey(
        path="goal.defaultMaxTokens",
        type="number",
        default=DEFAULT_GOAL_MAX_TOKENS,
        scope=Scope.USER_ONLY,
        group="goal",
        label="Teto de tokens do /goal",
        description="Total de tokens que um /goal pode gastar antes de parar, quando o comando não traz --max-tokens.",
        minimum=1,
    ),
)

for _key in PIPELINE_KEYS:
    register(_key)

__all__ = ["DEFAULT_GOAL_MAX_TOKENS", "PIPELINE_KEYS"]
