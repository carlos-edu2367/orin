"""A curated set of known MCP servers.

This exists so the agent can explain a connection instead of guessing one: each
entry says what the server does, how it is launched, and exactly which secret
the user has to fetch and where from — or, for a server that requires OAuth,
which environment variable has to carry a client_id before it can be connected
at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .models import McpTransport


@dataclass(frozen=True, slots=True)
class McpSecretRequirement:
    name: str
    label: str
    how_to_obtain: str


@dataclass(frozen=True, slots=True)
class McpOAuthRequirement:
    """What an OAuth-gated catalog entry needs before it can be connected.

    ``client_id_env_var`` names the environment variable this deployment must
    set; nothing here ever carries a client_id value itself — see
    agentos.oauth for the generic authorization-code+PKCE mechanism that
    consumes this once a client_id exists.

    ``authorize_url``/``token_url`` are None when the server follows the MCP
    Authorization spec's own discovery convention (a `/.well-known/oauth-
    protected-resource` document pointing at an authorization server's own
    `/.well-known/oauth-authorization-server` metadata) instead of publishing
    fixed, citable endpoint URLs — see agentos.oauth.discovery. Hardcoding a
    guess here would be citing a URL nobody has published.
    """
    client_id_env_var: str
    scopes: tuple[str, ...]
    authorize_url: str | None = None
    token_url: str | None = None


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
    oauth: McpOAuthRequirement | None = None


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
    McpCatalogEntry(
        catalog_id="google-drive",
        display_name="Google Drive",
        summary="Busca e lê arquivos do seu Google Drive.",
        transport=McpTransport.HTTP,
        url="https://drivemcp.googleapis.com/mcp/v1",
        setup_instructions=(
            "Requer um client_id OAuth registrado no Google Cloud Console (Google Auth Platform → "
            "Clients → Create Client, tipo 'Desktop app' para aceitar o redirect loopback) e "
            "configurado na variável de ambiente AGENTOS_OAUTH_GOOGLE_CLIENT_ID. Sem isso, esta "
            "conexão não pode ser autorizada — ninguém digita uma chave aqui."
        ),
        keywords=("google", "drive", "arquivos", "gmail", "workspace"),
        # Fixed endpoints, not discovered: Google's Drive MCP docs say a client_id
        # is registered manually in Cloud Console against Google's standard, long-
        # stable OAuth 2.0 endpoints (developers.google.com/identity/protocols/oauth2)
        # — Drive does not publish MCP-style discovery metadata.
        oauth=McpOAuthRequirement(
            client_id_env_var="AGENTOS_OAUTH_GOOGLE_CLIENT_ID",
            scopes=("https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file"),
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
        ),
    ),
    McpCatalogEntry(
        catalog_id="vercel",
        display_name="Vercel",
        summary="Projetos, deployments e analytics na Vercel.",
        transport=McpTransport.HTTP,
        url="https://mcp.vercel.com",
        setup_instructions=(
            "A Vercel MCP implementa a especificação de autorização do MCP: os endpoints de "
            "autorização não são fixos, são descobertos em tempo real a partir de "
            "https://mcp.vercel.com/.well-known/oauth-protected-resource. Além disso, a Vercel só "
            "aceita clientes que ela mesma revisou e aprovou — registrar seu próprio client_id não "
            "é suficiente aqui, diferente do Google. Configurar AGENTOS_OAUTH_VERCEL_CLIENT_ID só "
            "habilita a conexão se a Vercel já tiver aprovado o Orin como cliente."
        ),
        keywords=("vercel", "deploy", "deployments", "projetos"),
        # No authorize_url/token_url: discovered per-connection from Vercel's own
        # metadata (see McpOAuthRequirement docstring) rather than guessed here.
        oauth=McpOAuthRequirement(client_id_env_var="AGENTOS_OAUTH_VERCEL_CLIENT_ID", scopes=()),
    ),
)


def oauth_configured(entry: McpCatalogEntry) -> bool:
    """True when an entry needs no OAuth client_id, or already has one set."""
    if entry.oauth is None:
        return True
    return bool(os.getenv(entry.oauth.client_id_env_var))


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


__all__ = [
    "CATALOG", "McpCatalogEntry", "McpOAuthRequirement", "McpSecretRequirement",
    "find_catalog_entry", "oauth_configured", "search_catalog",
]
