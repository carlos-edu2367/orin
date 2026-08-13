# Auto-learning through Skills in the conversational runtime

- `TurnSession` now receives both the read-only `SkillRegistry` and the persistent library service. The publishing tool binds `user_id` to the trusted turn record; the model never supplies user, scope, or authorization.
- `create_skill` requires a description, triggers, exclusions, and Workflow/Validation sections. `edit_skill` publishes an immutable new version and is restricted to `USER` scope, protecting system Skills and other users.
- Skill tool declarations are checked against the tools exposed to the current turn. The system prompt tells the agent to search for duplicates, obtain explicit confirmation before persisting, and offer a reusable Skill after the user confirms resolution.
- Discovery metadata (`capabilities`, `when_to_use`, `when_not_to_use`, dependencies, and required tools) is preserved by both the in-memory and PostgreSQL services and accepted by the API.
