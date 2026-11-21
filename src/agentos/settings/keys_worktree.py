"""Registered setting keys for worktrees, shared task board, background resume and stall detection (WS-B0)."""

from __future__ import annotations

from .registry import Scope, SettingKey, register

DEFAULT_WORKTREE_LINK_DIRS: tuple[str, ...] = ("node_modules", ".venv")

WORKTREE_AND_ORCHESTRATION_KEYS: tuple[SettingKey, ...] = (
    SettingKey(
        path="orchestration.worktree.enabled",
        type="boolean",
        default=False,
        scope=Scope.PROJECT_OFF_ONLY,
        group="worktree",
        label="Worktree por subagente",
        description="Permite que subagentes com isolation: worktree executem em uma worktree Git isolada e integrem as alterações pela fila serializada.",
    ),
    SettingKey(
        path="orchestration.worktree.linkDirs",
        type="string_list",
        default=list(DEFAULT_WORKTREE_LINK_DIRS),
        scope=Scope.ANY,
        group="worktree",
        label="Pastas ligadas na worktree",
        description="Diretórios relativos de dependências (como node_modules e .venv) ligados por junção/link simbólico na worktree sem duplicar arquivos.",
        item_type="relative_dir",
    ),
    SettingKey(
        path="orchestration.worktree.mergeCheckCommand",
        type="string",
        default=None,
        scope=Scope.ANY,
        group="worktree",
        label="Comando de checagem da integração",
        description="Comando opcional executado após aplicar as alterações da worktree na árvore principal; se falhar, a integração é desfeita (ignorado em repositórios não confiáveis).",
    ),
    SettingKey(
        path="orchestration.taskBoard.enabled",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="task_board",
        label="Quadro de tarefas compartilhado",
        description="Habilita o quadro de tarefas por conversa com reivindicação atômica e controle de dependências entre subagentes.",
    ),
    SettingKey(
        path="orchestration.background.autoResume",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="background",
        label="Retomar agentes após reinício",
        description="Retoma automaticamente subagentes interrompidos quando o Orin reinicia, respeitando as políticas de cota configuradas.",
    ),
    SettingKey(
        path="orchestration.stall.minutes",
        type="number",
        default=10,
        scope=Scope.ANY,
        group="background",
        label="Considerar agente travado após (minutos)",
        description="Tempo sem atividade de ferramentas ou resposta em minutos antes de marcar um subagente em execução como travado (stalled).",
        minimum=1,
        maximum=1440,
    ),
    SettingKey(
        path="orchestration.stall.autoCancel",
        type="boolean",
        default=False,
        scope=Scope.USER_ONLY,
        group="background",
        label="Cancelar agente travado automaticamente",
        description="Quando ligado, cancela automaticamente subagentes que atingirem o limite configurado de inatividade.",
    ),
)

for _key in WORKTREE_AND_ORCHESTRATION_KEYS:
    register(_key)

__all__ = [
    "DEFAULT_WORKTREE_LINK_DIRS",
    "WORKTREE_AND_ORCHESTRATION_KEYS",
]
