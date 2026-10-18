from __future__ import annotations

from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.search import McpToolSearchDocument, McpToolSearchEngine


def _sample_documents() -> list[McpToolSearchDocument]:
    return [
        McpToolSearchDocument.from_descriptor(
            server_slug="auryly",
            server_name="Auryly",
            descriptor=McpToolDescriptor(
                name="get_funnel_metrics",
                description="Retrieve conversion rates, drop-off percentages, and step views for a quiz funnel.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "funnel_id": {"type": "string", "description": "Unique funnel identifier"},
                        "date_from": {"type": "string", "description": "Start ISO date"},
                        "date_to": {"type": "string", "description": "End ISO date"},
                    },
                    "required": ["funnel_id"],
                },
            ),
        ),
        McpToolSearchDocument.from_descriptor(
            server_slug="auryly",
            server_name="Auryly",
            descriptor=McpToolDescriptor(
                name="save_funnel_draft",
                description="Save or update the steps and transitions of a funnel draft.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "funnel_id": {"type": "string"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step_id": {"type": "string", "description": "Step slug"},
                                    "step_type": {
                                        "type": "string",
                                        "enum": ["quiz_single_choice", "quiz_multi_choice", "checkout_redirect"],
                                    },
                                    "redirect_url": {"type": "string", "description": "Destination URL after completion"},
                                },
                            },
                        },
                    },
                    "required": ["funnel_id", "steps"],
                },
            ),
        ),
        McpToolSearchDocument.from_descriptor(
            server_slug="notion",
            server_name="Notion",
            descriptor=McpToolDescriptor(
                name="createPage",
                description="Create a new workspace page under a parent database or page.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "parent_id": {"type": "string", "description": "Database or parent page ID"},
                        "title": {"type": "string", "description": "Page title"},
                    },
                    "required": ["parent_id", "title"],
                },
            ),
        ),
    ]


def test_search_matches_exact_and_camel_or_snake_case_tool_names():
    engine = McpToolSearchEngine(_sample_documents())
    hits = engine.search("get_funnel_metrics")
    assert hits
    assert hits[0].qualified_name == "mcp__auryly__get_funnel_metrics"

    camel_hits = engine.search("create page")
    assert camel_hits
    assert camel_hits[0].qualified_name == "mcp__notion__createPage"


def test_search_matches_nested_json_schema_properties_and_enums():
    engine = McpToolSearchEngine(_sample_documents())
    hits_by_prop = engine.search("redirect_url step_id")
    assert hits_by_prop
    assert hits_by_prop[0].qualified_name == "mcp__auryly__save_funnel_draft"

    hits_by_enum = engine.search("quiz_single_choice checkout_redirect")
    assert hits_by_enum
    assert hits_by_enum[0].qualified_name == "mcp__auryly__save_funnel_draft"


def test_search_supports_bilingual_pt_en_and_accent_insensitive_queries():
    engine = McpToolSearchEngine(_sample_documents())
    hits = engine.search("métricas de conversão do funil")
    assert hits
    assert hits[0].qualified_name == "mcp__auryly__get_funnel_metrics"

    page_hits = engine.search("criar página no notion")
    assert page_hits
    assert page_hits[0].qualified_name == "mcp__notion__createPage"


def test_search_filters_by_server_slug_or_display_name():
    engine = McpToolSearchEngine(_sample_documents())
    notion_only = engine.search("", server="Notion")
    assert [item.qualified_name for item in notion_only] == ["mcp__notion__createPage"]

    auryly_only = engine.search("page", server="auryly")
    assert all(item.server_slug == "auryly" for item in auryly_only)


def test_search_incorporates_optional_semantic_scorer_and_survives_scorer_errors():
    docs = _sample_documents()
    engine_boosted = McpToolSearchEngine(
        docs,
        semantic_scorer=lambda q, doc: 0.99 if doc.tool_name == "createPage" else 0.0,
    )
    boosted = engine_boosted.search("workspace")
    assert boosted[0].qualified_name == "mcp__notion__createPage"

    def broken_scorer(q: str, doc: McpToolSearchDocument) -> float:
        raise RuntimeError("embedding service offline")

    engine_resilient = McpToolSearchEngine(docs, semantic_scorer=broken_scorer)
    hits = engine_resilient.search("funnel metrics")
    assert hits
    assert hits[0].qualified_name == "mcp__auryly__get_funnel_metrics"


def test_search_handles_deeply_nested_or_adversarial_schema_without_recursion_error():
    nested: dict = {"type": "object", "properties": {}}
    cursor = nested["properties"]
    for idx in range(50):
        cursor[f"level_{idx}"] = {"type": "object", "properties": {}}
        cursor = cursor[f"level_{idx}"]["properties"]
    doc = McpToolSearchDocument.from_descriptor(
        server_slug="adv",
        server_name="Adversarial",
        descriptor=McpToolDescriptor(name="deep_tool", description="Deep", input_schema=nested),
    )
    engine = McpToolSearchEngine([doc])
    hits = engine.search("level_0")
    assert len(hits) == 1
    assert hits[0].qualified_name == "mcp__adv__deep_tool"
