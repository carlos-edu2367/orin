"""Contextual, schema-aware, and optional semantic search engine for MCP tools.

Indexes tool names (camelCase / snake_case / kebab-case split), descriptions,
server metadata, and nested JSON ``input_schema`` fields (property names,
descriptions, titles, enum values, and required fields) with PT/EN bilingual
synonym expansion and safe recursion bounds.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import McpToolDescriptor, qualified_tool_name

MAX_SCHEMA_WALK_DEPTH = 16
MAX_SCHEMA_TEXT_CHARS = 16_000

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_WORD_RE = re.compile(r"[a-z0-9]{2,}")

# Common PT <-> EN bilingual equivalents in agentic/MCP workflows so queries in
# Portuguese ("métricas de conversão do funil", "criar página") match English
# MCP tool names/schemas and vice-versa.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("metrica", "metricas", "metric", "metrics", "analytics", "estatistica", "estatisticas", "stats"),
    ("conversao", "conversion", "conversions", "taxa", "rate", "rates", "dropoff"),
    ("funil", "funis", "funnel", "funnels"),
    ("etapa", "etapas", "passo", "passos", "step", "steps", "stage", "stages"),
    ("criar", "cria", "create", "new", "novo", "nova", "adicionar", "add", "insert"),
    ("listar", "lista", "list", "all", "todos", "todas", "index"),
    ("obter", "pegar", "buscar", "busca", "procurar", "ler", "get", "fetch", "read", "retrieve", "search", "find", "query"),
    ("atualizar", "editar", "alterar", "modificar", "salvar", "update", "edit", "modify", "save", "patch", "put", "set"),
    ("excluir", "deletar", "apagar", "remover", "delete", "remove", "destroy", "drop"),
    ("pagina", "paginas", "page", "pages", "document", "documento", "doc", "docs"),
    ("rascunho", "draft", "drafts"),
    ("usuario", "usuarios", "user", "users", "member", "members", "account", "conta"),
    ("configuracao", "configuracoes", "config", "configuration", "settings", "setting", "setup"),
    ("arquivo", "arquivos", "file", "files", "upload", "download"),
    ("imagem", "imagens", "foto", "image", "images", "picture", "photo"),
    ("audio", "voz", "som", "voice", "sound", "speech", "tts", "track", "tracks"),
    ("video", "videos", "clip", "clips", "media"),
    ("relatorio", "relatorios", "report", "reports", "summary", "resumo"),
    ("lead", "leads", "contato", "contatos", "contact", "contacts", "prospect"),
    ("dominio", "dominios", "domain", "domains", "url", "link", "redirect", "redirecionamento"),
    ("banco", "database", "db", "tabela", "table", "tables", "collection"),
    ("mensagem", "mensagens", "message", "messages", "chat", "send", "enviar"),
    ("projeto", "projetos", "project", "projects", "workspace"),
    ("aula", "aulas", "licao", "lesson", "lessons", "course", "curso", "cursos", "module", "modulo"),
)

_SYNONYM_MAP: dict[str, frozenset[str]] = {}
for _group in _SYNONYM_GROUPS:
    _group_set = frozenset(_group)
    for _term in _group:
        _SYNONYM_MAP[_term] = _SYNONYM_MAP.get(_term, frozenset()) | _group_set


def _fold_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _split_identifiers(text: str) -> str:
    spaced = _ACRONYM_BOUNDARY.sub(r"\1 \2", text or "")
    spaced = _CAMEL_BOUNDARY.sub(r"\1 \2", spaced)
    return spaced.replace("_", " ").replace("-", " ").replace(".", " ").replace("/", " ")


def _tokenize(text: str) -> tuple[str, ...]:
    folded = _fold_accents(_split_identifiers(text))
    seen: dict[str, None] = {}
    for token in _WORD_RE.findall(folded):
        seen[token] = None
    return tuple(seen)


def _expand_tokens(tokens: Iterable[str]) -> frozenset[str]:
    expanded: set[str] = set()
    for token in tokens:
        expanded.add(token)
        if token in _SYNONYM_MAP:
            expanded.update(_SYNONYM_MAP[token])
        # Simple plural / singular normalization
        if len(token) > 4 and token.endswith("oes"):
            singular = token[:-3] + "ao"
            expanded.add(singular)
            if singular in _SYNONYM_MAP:
                expanded.update(_SYNONYM_MAP[singular])
        elif len(token) > 3 and token.endswith("s"):
            singular = token[:-1]
            expanded.add(singular)
            if singular in _SYNONYM_MAP:
                expanded.update(_SYNONYM_MAP[singular])
    return frozenset(expanded)


def _extract_schema_fields(
    schema: Any,
    *,
    depth: int = 0,
    param_names: list[str] | None = None,
    schema_texts: list[str] | None = None,
) -> tuple[tuple[str, ...], str]:
    if param_names is None:
        param_names = []
    if schema_texts is None:
        schema_texts = []
    if depth > MAX_SCHEMA_WALK_DEPTH or not isinstance(schema, Mapping):
        return tuple(dict.fromkeys(param_names)), " ".join(schema_texts)[:MAX_SCHEMA_TEXT_CHARS]

    for key in ("title", "description"):
        val = schema.get(key)
        if isinstance(val, str) and val.strip():
            schema_texts.append(val.strip())

    enum_vals = schema.get("enum")
    if isinstance(enum_vals, Sequence) and not isinstance(enum_vals, (str, bytes)):
        for item in enum_vals[:50]:
            if isinstance(item, (str, int, float)):
                schema_texts.append(str(item))

    required = schema.get("required")
    if isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
        for req in required[:100]:
            if isinstance(req, str) and req.strip():
                param_names.append(req.strip())

    props = schema.get("properties")
    if isinstance(props, Mapping):
        for prop_name, prop_schema in list(props.items())[:200]:
            if isinstance(prop_name, str) and prop_name.strip():
                param_names.append(prop_name.strip())
            if isinstance(prop_schema, Mapping):
                _extract_schema_fields(
                    prop_schema,
                    depth=depth + 1,
                    param_names=param_names,
                    schema_texts=schema_texts,
                )

    for branch_key in ("items", "additionalProperties"):
        branch = schema.get(branch_key)
        if isinstance(branch, Mapping):
            _extract_schema_fields(
                branch,
                depth=depth + 1,
                param_names=param_names,
                schema_texts=schema_texts,
            )

    for combo_key in ("anyOf", "oneOf", "allOf"):
        combo = schema.get(combo_key)
        if isinstance(combo, Sequence) and not isinstance(combo, (str, bytes)):
            for option in combo[:20]:
                if isinstance(option, Mapping):
                    _extract_schema_fields(
                        option,
                        depth=depth + 1,
                        param_names=param_names,
                        schema_texts=schema_texts,
                    )

    return tuple(dict.fromkeys(param_names)), " ".join(schema_texts)[:MAX_SCHEMA_TEXT_CHARS]


@dataclass(frozen=True, slots=True)
class McpToolSearchDocument:
    server_slug: str
    server_name: str
    tool_name: str
    qualified_name: str
    description: str
    input_schema: Mapping[str, Any]
    parameter_names: tuple[str, ...]
    schema_text: str
    name_tokens: frozenset[str]
    param_tokens: frozenset[str]
    description_tokens: frozenset[str]
    schema_tokens: frozenset[str]
    server_tokens: frozenset[str]

    @classmethod
    def from_descriptor(
        cls,
        *,
        server_slug: str,
        server_name: str,
        descriptor: McpToolDescriptor,
    ) -> "McpToolSearchDocument":
        param_names, schema_text = _extract_schema_fields(descriptor.input_schema)
        q_name = qualified_tool_name(server_slug, descriptor.name)
        name_tokens = _expand_tokens(_tokenize(f"{descriptor.name} {q_name}"))
        param_tokens = _expand_tokens(_tokenize(" ".join(param_names)))
        desc_tokens = _expand_tokens(_tokenize(descriptor.description))
        schema_tokens = _expand_tokens(_tokenize(schema_text))
        server_tokens = _expand_tokens(_tokenize(f"{server_slug} {server_name}"))
        return cls(
            server_slug=server_slug,
            server_name=server_name,
            tool_name=descriptor.name,
            qualified_name=q_name,
            description=descriptor.description,
            input_schema=dict(descriptor.input_schema),
            parameter_names=param_names,
            schema_text=schema_text,
            name_tokens=name_tokens,
            param_tokens=param_tokens,
            description_tokens=desc_tokens,
            schema_tokens=schema_tokens,
            server_tokens=server_tokens,
        )


@dataclass(frozen=True, slots=True)
class McpToolSearchResult:
    server_slug: str
    server_name: str
    tool_name: str
    qualified_name: str
    description: str
    input_schema: Mapping[str, Any]
    parameter_names: tuple[str, ...]
    score: float

    def to_dict(self, *, include_schema: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "qualified_name": self.qualified_name,
            "server": self.server_slug,
            "server_name": self.server_name,
            "tool": self.tool_name,
            "description": self.description,
            "parameters": list(self.parameter_names),
            "score": round(self.score, 2),
        }
        if include_schema:
            payload["input_schema"] = dict(self.input_schema)
        return payload


SemanticScorer = Callable[[str, McpToolSearchDocument], float]

_STOPWORDS = frozenset({
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "para", "por", "com", "um", "uma",
    "the", "of", "in", "on", "to", "for", "with", "an", "and", "or", "is", "are",
})


def _matches_token(query_token: str, target_tokens: frozenset[str]) -> float:
    if query_token in target_tokens:
        return 1.0
    query_variants = _expand_tokens((query_token,))
    if query_variants & target_tokens:
        return 0.9
    if len(query_token) >= 4:
        stem = query_token[:5] if len(query_token) >= 6 else query_token[:4]
        for candidate in target_tokens:
            if len(candidate) >= 4 and (candidate.startswith(stem) or query_token.startswith(candidate[:4])):
                return 0.65
    return 0.0


class McpToolSearchEngine:
    """Indexes and ranks MCP tools across names, descriptions, and JSON schemas."""

    def __init__(
        self,
        documents: Iterable[McpToolSearchDocument],
        *,
        semantic_scorer: SemanticScorer | None = None,
    ) -> None:
        self._documents = tuple(documents)
        self._semantic_scorer = semantic_scorer

    @property
    def documents(self) -> tuple[McpToolSearchDocument, ...]:
        return self._documents

    def _filter_by_server(self, server: str | None) -> tuple[McpToolSearchDocument, ...]:
        if not server or not server.strip():
            return self._documents
        target = _fold_accents(server.strip())
        return tuple(
            doc
            for doc in self._documents
            if _fold_accents(doc.server_slug) == target
            or _fold_accents(doc.server_name) == target
            or target in _fold_accents(doc.server_slug)
            or target in _fold_accents(doc.server_name)
        )

    def search(
        self,
        query: str = "",
        *,
        server: str | None = None,
        limit: int = 10,
    ) -> list[McpToolSearchResult]:
        bounded_limit = max(1, min(int(limit or 10), 200))
        candidates = self._filter_by_server(server)
        if not candidates:
            return []

        clean_query = (query or "").strip()
        if not clean_query:
            return [
                McpToolSearchResult(
                    server_slug=doc.server_slug,
                    server_name=doc.server_name,
                    tool_name=doc.tool_name,
                    qualified_name=doc.qualified_name,
                    description=doc.description,
                    input_schema=doc.input_schema,
                    parameter_names=doc.parameter_names,
                    score=1.0,
                )
                for doc in sorted(candidates, key=lambda d: (d.server_slug, d.tool_name))[:bounded_limit]
            ]

        folded_query = _fold_accents(clean_query)
        raw_tokens = _tokenize(clean_query)
        query_tokens = tuple(tok for tok in raw_tokens if tok not in _STOPWORDS) or raw_tokens

        scored: list[McpToolSearchResult] = []
        for doc in candidates:
            score = 0.0
            folded_tool = _fold_accents(doc.tool_name)
            folded_qname = _fold_accents(doc.qualified_name)

            if folded_query in {folded_tool, folded_qname}:
                score += 40.0
            elif folded_query in folded_tool or folded_tool in folded_query:
                score += 18.0

            # Exact parameter name substring check (e.g. "step_id", "redirect_url")
            for param in doc.parameter_names:
                folded_param = _fold_accents(param)
                if folded_param and folded_param in folded_query:
                    score += 14.0

            # Exact enum/schema substring check (e.g. "quiz_single_choice")
            folded_schema_text = _fold_accents(doc.schema_text)
            for raw_piece in clean_query.split():
                folded_piece = _fold_accents(raw_piece.strip(",;\"'"))
                if len(folded_piece) >= 4 and ("_" in folded_piece or "-" in folded_piece):
                    if folded_piece in folded_schema_text:
                        score += 12.0

            matched_token_count = 0
            for token in query_tokens:
                name_match = _matches_token(token, doc.name_tokens)
                param_match = _matches_token(token, doc.param_tokens)
                desc_match = _matches_token(token, doc.description_tokens)
                schema_match = _matches_token(token, doc.schema_tokens)
                server_match = _matches_token(token, doc.server_tokens)

                token_score = (
                    name_match * 10.0
                    + param_match * 7.0
                    + desc_match * 4.0
                    + schema_match * 3.0
                    + server_match * 3.0
                )
                if token_score > 0:
                    matched_token_count += 1
                    score += token_score

            if len(query_tokens) > 1 and matched_token_count > 1:
                coverage = matched_token_count / len(query_tokens)
                score += coverage * 8.0

            if self._semantic_scorer is not None:
                try:
                    sem = float(self._semantic_scorer(clean_query, doc))
                except Exception:
                    sem = 0.0
                if sem > 0.0:
                    score += max(0.0, min(1.0, sem)) * 12.0

            if score > 0.0:
                scored.append(
                    McpToolSearchResult(
                        server_slug=doc.server_slug,
                        server_name=doc.server_name,
                        tool_name=doc.tool_name,
                        qualified_name=doc.qualified_name,
                        description=doc.description,
                        input_schema=doc.input_schema,
                        parameter_names=doc.parameter_names,
                        score=score,
                    )
                )

        scored.sort(key=lambda item: (-item.score, item.server_slug, item.tool_name))
        return scored[:bounded_limit]


__all__ = [
    "McpToolSearchDocument",
    "McpToolSearchEngine",
    "McpToolSearchResult",
    "SemanticScorer",
]
