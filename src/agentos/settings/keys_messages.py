"""Registered setting keys for agent messages and Friend live-session delivery (WS-B0a)."""

from __future__ import annotations

from .registry import Scope, SettingKey, register

FRIEND_SESSION_DELIVERY_CHOICES: tuple[str, ...] = ("now", "queue")

MESSAGES_KEYS: tuple[SettingKey, ...] = (
    SettingKey(
        path="orchestration.agentMessages.parentTool",
        type="boolean",
        default=True,
        scope=Scope.PROJECT_OFF_ONLY,
        group="messages",
        label="Pai pode mandar mensagem a subagente",
        description="Permite que o agente pai envie orientações adicionais a subagentes da própria subárvore durante a execução.",
    ),
    SettingKey(
        path="orchestration.agentMessages.continueOnUnread",
        type="boolean",
        default=True,
        scope=Scope.USER_ONLY,
        group="messages",
        label="Continuar o subagente quando chega mensagem no fim do turno",
        description="Quando um subagente encerra com mensagens ainda não lidas na fila, inicia uma rodada adicional para processá-las em vez de repassá-las ao pai.",
    ),
    SettingKey(
        path="friend.sessionDelivery",
        type="choice",
        default="now",
        scope=Scope.USER_ONLY,
        group="friend",
        label="Friend: entrega em sessões em andamento",
        description="Define se mensagens enviadas pelo Orin Friend para uma sessão ativa tentam entrega no turno em andamento (now) ou ficam na fila para depois do turno (queue).",
        choices=FRIEND_SESSION_DELIVERY_CHOICES,
    ),
)

for _key in MESSAGES_KEYS:
    register(_key)

__all__ = ["FRIEND_SESSION_DELIVERY_CHOICES", "MESSAGES_KEYS"]
