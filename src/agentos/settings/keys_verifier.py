"""Registered setting keys for independent verifier policy and model tiers (WS-A2)."""

from __future__ import annotations

from agentos.agentic.model_tiers import default_model_tiers_copy

from .registry import EFFORT_LEVELS, Scope, SettingKey, register

VERIFIER_MODEL_POLICY_CHOICES: tuple[str, ...] = ("cross_family", "same_model", "explicit")

VERIFIER_KEYS: tuple[SettingKey, ...] = (
    SettingKey(
        path="orchestration.verifier.enabled",
        type="boolean",
        default=False,
        scope=Scope.PROJECT_OFF_ONLY,
        group="verifier",
        label="Verificador independente",
        description="Executa um agente revisor independente após turnos com alterações acima do limiar configurado.",
    ),
    SettingKey(
        path="orchestration.verifier.modelPolicy",
        type="choice",
        default="cross_family",
        scope=Scope.USER_ONLY,
        group="verifier",
        label="Política de modelo do verificador",
        description="Define como escolher o modelo do verificador: outra família com nível equivalente ou superior (cross_family), mesmo modelo em contexto novo (same_model) ou modelo fixo (explicit).",
        choices=VERIFIER_MODEL_POLICY_CHOICES,
    ),
    SettingKey(
        path="orchestration.verifier.explicit",
        type="object",
        default=None,
        scope=Scope.USER_ONLY,
        group="verifier",
        label="Modelo fixo do verificador",
        description="Objeto {provider, model, effort?} usado quando a política do verificador estiver configurada como explicit.",
        item_type="verifier_explicit",
    ),
    SettingKey(
        path="orchestration.verifier.effort",
        type="choice",
        default="high",
        scope=Scope.USER_ONLY,
        group="verifier",
        label="Esforço do verificador",
        description="Nível de esforço de raciocínio aplicado ao verificador independente, ajustado ao máximo aceito pelo modelo escolhido.",
        choices=EFFORT_LEVELS,
    ),
    SettingKey(
        path="orchestration.verifier.maxRounds",
        type="number",
        default=2,
        scope=Scope.PROJECT_LOWER_ONLY,
        group="verifier",
        label="Rodadas de correção",
        description="Número máximo de rodadas de correção e nova verificação por turno (1 a 5); o projeto só pode reduzir este limite.",
        minimum=1,
        maximum=5,
    ),
    SettingKey(
        path="orchestration.verifier.includeSubagents",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="verifier",
        label="Verificar também subagentes",
        description="Quando ligado, subagentes de execução que ultrapassarem o limiar também passam pelo verificador independente.",
    ),
    SettingKey(
        path="orchestration.modelTiers",
        type="map",
        default=default_model_tiers_copy(),
        scope=Scope.USER_ONLY,
        group="verifier",
        label="Famílias e níveis de modelos",
        description="Tabela editável de famílias e níveis de capacidade por modelo usada para escolher o verificador independente.",
        item_type="model_tier",
        value_minimum=0,
        value_maximum=10,
    ),
)

for _key in VERIFIER_KEYS:
    register(_key)

__all__ = ["VERIFIER_KEYS", "VERIFIER_MODEL_POLICY_CHOICES"]
