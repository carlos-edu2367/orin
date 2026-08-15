"""A curated set of known MCP servers.

This exists so the agent can explain a connection instead of guessing one: each
entry says what the server does, how it is launched, and exactly which secret
the user has to fetch and where from.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import McpTransport


@dataclass(frozen=True, slots=True)
class McpSecretRequirement:
    name: str
    label: str
    how_to_obtain: str


@dataclass(frozen=True, slots=True)
class McpCatalogEntry:
    catalog_id: str
    display_name: str
    summary: str
    transport: McpTransport
    setup_instructions: str
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    secrets: tuple[McpSecretRequirement, ...] = ()
    keywords: tuple[str, ...] = ()
    # Placeholder tokens inside args that the user fills in (e.g. a folder path).
    arguments: tuple[str, ...] = ()


CATALOG: tuple[McpCatalogEntry, ...] = (
    McpCatalogEntry(
        catalog_id="filesystem",
        display_name="Filesystem",
        summary="Lê e escreve arquivos em uma pasta que você autoriza.",
        transport=McpTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "{root}"),
        arguments=("root",),
        setup_instructions="Escolha uma pasta. O servidor só enxerga o que estiver dentro dela.",
        keywords=("arquivos", "files", "pasta", "diretorio"),
    ),
    McpCatalogEntry(
        # @modelcontextprotocol/server-github (the old npx package) is deprecated
        # upstream. GitHub's hosted remote server accepts a Personal Access Token via
        # `Authorization: Bearer <PAT>` (docs.github.com "Setting up the GitHub MCP
        # Server"), so this needs no OAuth — the `token` secret name is not
        # arbitrary: toolset.py's HTTP connector special-cases it into that header.
        catalog_id="github",
        display_name="GitHub",
        summary="Issues, pull requests e código dos seus repositórios.",
        transport=McpTransport.HTTP,
        url="https://api.githubcopilot.com/mcp/",
        secrets=(McpSecretRequirement(
            name="token",
            label="Personal access token",
            how_to_obtain="github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens. Marque só os repositórios que o agente pode ver.",
        ),),
        setup_instructions="Crie um token de acesso pessoal com escopo de leitura nos repositórios desejados.",
        keywords=("github", "git", "repositorio", "pull request"),
    ),
    McpCatalogEntry(
        catalog_id="postgres",
        display_name="PostgreSQL",
        summary="Consulta somente-leitura em um banco PostgreSQL.",
        transport=McpTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-postgres", "{connection_url}"),
        arguments=("connection_url",),
        setup_instructions="Use uma connection string de um usuário com permissão apenas de SELECT.",
        keywords=("postgres", "sql", "banco", "database"),
    ),
    McpCatalogEntry(
        catalog_id="notion",
        display_name="Notion",
        summary="Páginas e bancos de dados do seu workspace Notion.",
        transport=McpTransport.HTTP,
        url="https://mcp.notion.com/mcp",
        setup_instructions="O servidor pede autorização na primeira conexão. Nenhuma chave é digitada aqui.",
        keywords=("notion", "notas", "wiki"),
    ),
    McpCatalogEntry(
        catalog_id="sentry",
        display_name="Sentry",
        summary="Erros e releases dos seus projetos no Sentry.",
        transport=McpTransport.HTTP,
        url="https://mcp.sentry.dev/mcp",
        setup_instructions="Requer uma conta Sentry com acesso à organização.",
        keywords=("sentry", "erros", "observabilidade"),
    ),
)


def find_catalog_entry(catalog_id: str) -> McpCatalogEntry | None:
    return next((entry for entry in CATALOG if entry.catalog_id == catalog_id), None)


def search_catalog(text: str) -> tuple[McpCatalogEntry, ...]:
    needle = text.strip().lower()
    if not needle:
        return CATALOG
    return tuple(
        entry for entry in CATALOG
        if needle in entry.display_name.lower()
        or needle in entry.summary.lower()
        or any(needle in keyword for keyword in entry.keywords)
    )


__all__ = ["CATALOG", "McpCatalogEntry", "McpSecretRequirement", "find_catalog_entry", "search_catalog"]
