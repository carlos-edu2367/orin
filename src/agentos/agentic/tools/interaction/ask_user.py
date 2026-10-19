"""Ask user tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping, Literal

from pydantic import Field

from agentos.agentic.models import MAX_USER_QUESTION_ITEMS
from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _QUESTION_ID, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec, ToolInput


MAX_OPTION_PREVIEW_CHARS = 480


class AskUserTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def ask_user(self, questions: list[Mapping[str, Any]]) -> ToolOutcome:
        """Validate a bounded, display-safe batch before exposing it to the UI.

        The model only proposes this payload.  The frontend receives the
        normalized version from the durable activity stream and returns answers
        as an ordinary, authenticated conversation message.
        """
        if not isinstance(questions, list) or not 1 <= len(questions) <= 8:
            raise AgentToolError("questions must contain between 1 and 8 items.")
        normalized: list[dict[str, object]] = []
        question_ids: set[str] = set()
        for index, raw in enumerate(questions):
            if not isinstance(raw, Mapping):
                raise AgentToolError(f"questions[{index}] must be an object.")
            question_id = str(raw.get("id") or "").strip()
            prompt = str(raw.get("question") or "").strip()
            mode = str(raw.get("mode") or "").strip()
            if not _QUESTION_ID.fullmatch(question_id) or question_id in question_ids:
                raise AgentToolError(f"questions[{index}].id must be unique and use letters, numbers, '_' or '-'.")
            if not prompt or len(prompt) > 512:
                raise AgentToolError(f"questions[{index}].question must contain at most 512 characters.")
            if mode not in {"checkbox", "single_choice", "text"}:
                raise AgentToolError(f"questions[{index}].mode is invalid.")
            raw_options = raw.get("options") or []
            if not isinstance(raw_options, list) or len(raw_options) > 12:
                raise AgentToolError(f"questions[{index}].options must contain at most 12 items.")
            options: list[dict[str, str]] = []
            option_ids: set[str] = set()
            for option_index, raw_option in enumerate(raw_options):
                if not isinstance(raw_option, Mapping):
                    raise AgentToolError(f"questions[{index}].options[{option_index}] must be an object.")
                option_id = str(raw_option.get("id") or "").strip()
                label = str(raw_option.get("label") or "").strip()
                if not _QUESTION_ID.fullmatch(option_id) or option_id in option_ids or not label or len(label) > 240:
                    raise AgentToolError(f"questions[{index}].options[{option_index}] is invalid.")
                option_ids.add(option_id)
                option = {"id": option_id, "label": label}
                description = str(raw_option.get("description") or "").strip()
                if description:
                    option["description"] = description[:240]
                preview = str(raw_option.get("preview") or "")
                if preview.strip():
                    option["preview"] = preview if len(preview) <= MAX_OPTION_PREVIEW_CHARS else preview[: MAX_OPTION_PREVIEW_CHARS - 1] + "…"
                options.append(option)
            if mode == "text" and options:
                raise AgentToolError(f"questions[{index}] in text mode cannot have options.")
            if mode == "checkbox" and not options:
                raise AgentToolError(f"questions[{index}] in checkbox mode needs at least one option.")
            if mode == "single_choice" and len(options) < 2:
                raise AgentToolError(f"questions[{index}] in single_choice mode needs at least two options.")
            placeholder = str(raw.get("placeholder") or "").strip()
            if len(placeholder) > 240:
                raise AgentToolError(f"questions[{index}].placeholder must contain at most 240 characters.")
            question = {
                "id": question_id, "question": prompt, "mode": mode,
                "options": options, "placeholder": placeholder,
            }
            header = str(raw.get("header") or "").strip()[:12]
            if header:
                question["header"] = header
            normalized.append(question)
            question_ids.add(question_id)
        # The public activity event gives this validated form its own bounded
        # budget. Account for every question/option entry so a valid tool call
        # can never vanish from the UI later. The budget counts only the optional
        # fields actually present, so the documented maximum batch of 8 x 12 plain options still fits.
        entries = sum(6 + ("header" in item) for item in normalized) + sum(
            3 + ("description" in option) + ("preview" in option) for item in normalized for option in item["options"]
        )
        if entries > MAX_USER_QUESTION_ITEMS:
            raise AgentToolError("This question batch is too large to display safely; split it into smaller batches.")
        names = ", ".join(item["id"] for item in normalized)
        return ToolOutcome(
            "succeeded",
            f"Aguardando resposta para {len(normalized)} pergunta{'s' if len(normalized) != 1 else ''}",
            "The structured questions are now visible to the user. Stop here and wait for their next message; do not guess an answer or continue the task yet.",
            {"questions": normalized, "wait_for_user": True, "tool_kind": "user_input", "label": names[:200]},
        )


class QuestionOption(ToolInput):
    label: str = Field(description="Option text (1-5 words).")
    description: str = Field(description="What choosing it means.")


class Question(ToolInput):
    question: str = Field(description="The full question.")
    header: str = Field(max_length=12, description="Very short label for the question.")
    options: list[QuestionOption] = Field(min_length=2, max_length=4)
    multiSelect: bool = Field(description="Allow several answers.")


class AskUserQuestionInput(ToolInput):
    questions: list[Question] = Field(min_length=1, max_length=4)


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="ask_user",
        description="Ask the person one or more structured questions, then wait for their next message. Each question uses mode 'checkbox' (zero or more options plus an optional note), 'single_choice' (one option or an optional note), or 'text' (free text). Put independent questions together in the questions array. Do not use this when you can safely proceed without clarification.",
        legacy_schema=_schema({
            "questions": {
                "type": "array", "minItems": 1, "maxItems": 8,
                "items": _schema({
                    "id": {**_TEXT, "description": "Stable short identifier, e.g. deployment_target."},
                    "question": {**_TEXT, "description": "The question shown to the person."},
                    "mode": {"type": "string", "enum": ["checkbox", "single_choice", "text"]},
                    "options": {
                        "type": "array", "maxItems": 12,
                        "items": _schema({"id": _TEXT, "label": _TEXT}, ("id", "label")),
                    },
                    "placeholder": _TEXT,
                }, ("id", "question", "mode")),
            },
        }, ("questions",)),
        handler="ask_user",
        kind="user_input",
        input_model=AskUserQuestionInput,
        public_description='Ask the user 1-4 multiple-choice questions when you are blocked on a decision only they can make, then stop and wait for the answer.',
        policy_tags=("user_input",),
        order=200,
    ),
)
