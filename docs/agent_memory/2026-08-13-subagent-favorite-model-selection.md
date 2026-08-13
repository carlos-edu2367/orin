# Subagent model selection

The conversational runtime now treats `create_agent.model_id` as optional.
When omitted, the child uses the active turn's model. When supplied, the
worker authorizes it against the authenticated user's favorites in the active
turn's provider only; models from other providers, other users, or the
unfavorited catalog are rejected.

Authorization is repeated when dispatching a stored child agent, and the
child's transport and usage records receive its selected model ID. The prompt
only lists favorite IDs as guidance; PostgreSQL-backed validation remains the
source of truth.

If initializing an explicitly chosen favorite model fails before the child
starts running, Orin falls back once to the current turn model and persists
that effective model on the child record. It deliberately does not retry after
the child runtime starts, because the first run might already have invoked a
side-effecting tool.
