"""Registered setting keys for Package D memory lifecycle, quarantine, conflicts, and agent scope (WS-D2)."""

from __future__ import annotations

from .registry import Scope, SettingKey, register

MEMORY_KEYS: tuple[SettingKey, ...] = (
    SettingKey(
        path="memory.quarantine.enabled",
        type="boolean",
        default=True,
        scope=Scope.USER_ONLY,
        group="memory",
        label="Quarentena de memórias vindas de ferramentas e web",
        description="Mantém em quarentena memórias originadas de ferramentas externas, web ou arquivos até aprovação do usuário.",
    ),
    SettingKey(
        path="memory.conflicts.llmAdjudication",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="memory",
        label="Usar modelo para decidir conflitos ambíguos",
        description="Consulta um modelo auxiliar para classificar contradições ambíguas entre memórias antes de encaminhá-las para revisão.",
    ),
    SettingKey(
        path="memory.agent.enabled",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="memory",
        label="Memória própria de cada agente",
        description="Ativa o escopo de memória dedicado para agentes personalizados e para o verificador.",
    ),
    SettingKey(
        path="memory.agent.maxPromptChars",
        type="number",
        default=4000,
        scope=Scope.USER_ONLY,
        group="memory",
        label="Tamanho do resumo por agente",
        description="Limite máximo de caracteres para o resumo congelado de memórias injetado no prompt do subagente.",
        minimum=200,
        maximum=20000,
    ),
    SettingKey(
        path="memory.agent.learnFromVerifiedOnly",
        type="boolean",
        default=True,
        scope=Scope.USER_ONLY,
        group="memory",
        label="Só aprender de trabalho verificado",
        description="Promove automaticamente propostas de memória de subagentes apenas após verificação aprovada.",
    ),
    SettingKey(
        path="memory.instructionLint.enabled",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="memory",
        label="Conferir memórias contra CLAUDE.md/ORIN.md/AGENTS.md",
        description="Habilita a verificação automática de contradições entre memórias ativas e arquivos de instrução do workspace.",
    ),
    SettingKey(
        path="memory.tombstoneRetentionDays",
        type="number",
        default=90,
        scope=Scope.USER_ONLY,
        group="memory",
        label="Retenção de memórias esquecidas (dias)",
        description="Número de dias que o marcador (tombstone) de uma memória esquecida é mantido para evitar recriação automática antes da purga física.",
        minimum=1,
        maximum=3650,
    ),
)

for _key in MEMORY_KEYS:
    register(_key)

__all__ = ["MEMORY_KEYS"]
