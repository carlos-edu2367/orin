"""Which production-impacting actions a shell command performs, for Code mode's confirmation guard.

The guard used to search the command text for the words deploy/release/publish. That blocked read-only
commands that merely mention them (``grep deploy deploy.yml``, a commit message saying "deploy") and let
riskier ones through (SQL sent to a production database over the Supabase Management API). This module
classifies by effect instead: it reads each simple command the shell would run, finds the program (behind
``npx``, ``sudo``, ``env`` and similar wrappers) and its subcommand, and reports what that does.

Commands hidden one level down are classified too: the string a nested shell runs (``bash -c "npm
publish"``, ``pwsh -Command``, ``eval``) and command substitutions (``$(...)``, backticks). Here-document
bodies are data (a commit message), never commands. Only a command too large to analyse falls back to a few
multi-word patterns. A single word is never enough.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from agentos.permissions.powershell import analyze_for
from agentos.permissions.shell import default_shell

KIND_LABELS = {
    "deploy": "Deploy em produção",
    "publish": "Publicação de pacote/imagem",
    "release": "Release ou workflow remoto",
    "infra": "Mudança de infraestrutura",
    "db_write": "Escrita no banco de produção",
    "credentials": "Leitura de credenciais de produção",
}


@dataclass(frozen=True, slots=True)
class ProductionAction:
    kind: str
    detail: str
    segment: str

    @property
    def label(self) -> str:
        return f"{KIND_LABELS.get(self.kind, self.kind)} ({self.detail})"


_WRAPPERS = frozenset({
    "sudo", "time", "nohup", "env", "command", "exec", "call", "&", "npx", "bunx", "pnpx", "uvx", "doas",
    "xargs", "timeout", "nice", "watch", "start-process",
})
_NESTED = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "cmd", "powershell", "pwsh", "eval", "invoke-expression", "iex"})
_NESTED_FLAGS = frozenset({"-c", "/c", "/k", "-command", "-c", "-encodedcommand"})
_MAX_DEPTH = 3
_WRAPPER_PAIRS = {
    ("pnpm", "dlx"), ("pnpm", "exec"), ("yarn", "dlx"), ("yarn", "exec"), ("npm", "exec"), ("bun", "x"),
    ("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"), ("rye", "run"),
    ("dotenv", "--"),
}
_SCRIPT_RUNNERS = frozenset({"node", "python", "python3", "py", "bash", "sh", "zsh", "pwsh", "powershell", "deno", "bun", "tsx", "ts-node", "ruby", "php"})
_TASK_RUNNERS = frozenset({"make", "just", "task", "mage", "invoke", "rake", "grunt", "gulp", "nx", "turbo"})
_SENSITIVE_NAME = re.compile(r"(?:^|[:_\-./])(deploy|release|publish)(?:$|[:_\-.])", re.IGNORECASE)
_PROD_NAME = re.compile(r"(?:^|[:_\-])(prod|production)$", re.IGNORECASE)
_SQL_CLIENTS = frozenset({"psql", "pgcli", "mysql", "mariadb", "mycli", "sqlcmd", "mongosh", "mongo", "cockroach", "usql"})
_HTTP_CLIENTS = frozenset({"curl", "wget", "http", "https", "xh", "invoke-webrequest", "invoke-restmethod", "iwr", "irm"})
_LOCAL_HOSTS = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|::1|\[::1\]|0\.0\.0\.0|host\.docker\.internal|db|postgres|mysql)$", re.IGNORECASE)
_READ_ONLY_SQL_START = frozenset({"select", "with", "explain", "show", "describe", "desc", "values", "table", "\\d", "\\dt", "\\l"})
_SQL_WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|upsert|into)\b", re.IGNORECASE)

# Multi-word patterns scanned over a command the analysis cannot fully read (nested shells, substitutions).
_FALLBACK_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), kind, detail)
    for pattern, kind, detail in (
        (r"\bsupabase\s+(?:\S+\s+)*?functions\s+(deploy|delete)\b", "deploy", "supabase functions"),
        (r"\bsupabase\s+(?:\S+\s+)*?db\s+push\b", "db_write", "supabase db push"),
        (r"\bvercel\b[^\n;|&]*\s--prod\b", "deploy", "vercel --prod"),
        (r"\b(?:vercel|netlify|firebase|flyctl|fly|wrangler|railway|cdk|serverless|sls|sam|eb|copilot|kamal|sst)\s+(?:\S+\s+)*?deploy\b", "deploy", "deploy"),
        (r"\b(?:npm|pnpm|yarn|bun|cargo|uv|poetry|twine|flit|hatch|vsce|ovsx)\s+(?:\S+\s+)*?(publish|upload)\b", "publish", "publish"),
        (r"\b(?:docker|podman)\s+push\b", "publish", "docker push"),
        (r"\b(?:terraform|tofu|terragrunt)\s+(?:\S+\s+)*?(apply|destroy)\b", "infra", "terraform"),
        (r"\bpulumi\s+(?:\S+\s+)*?(up|destroy)\b", "infra", "pulumi"),
        (r"\bkubectl\s+(?:\S+\s+)*?(apply|delete|replace|patch|rollout|scale)\b", "infra", "kubectl"),
        (r"\bhelm\s+(?:\S+\s+)*?(install|upgrade|uninstall|rollback)\b", "infra", "helm"),
        (r"\bgh\s+release\s+(create|delete|upload|edit)\b", "release", "gh release"),
        (r"api\.supabase\.com/v1/projects/[^/\s\"']+/database/(query|migrations)", "db_write", "Supabase Management API"),
    )
)


def _program(word: str) -> str:
    name = re.split(r"[\\/]", word.strip("\"'"))[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        name = name.removesuffix(suffix)
    return name


def _words(segment: str, dialect: str) -> list[str]:
    try:
        words = shlex.split(segment, posix=dialect == "posix")
    except ValueError:
        words = segment.split()
    return [word.strip("\"'") if dialect != "posix" else word for word in words]


def _unwrap(words: list[str]) -> list[str]:
    """Drop env assignments and wrappers (sudo, npx, pnpm dlx, uv run...) in front of the real program."""
    index = 0
    while index < len(words):
        word = words[index]
        program = _program(word)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            index += 1
            continue
        if program in _WRAPPERS:
            index += 1
            while index < len(words) and words[index].startswith("-"):
                index += 1
            continue
        if index + 1 < len(words) and (program, words[index + 1].lower()) in _WRAPPER_PAIRS:
            index += 2
            while index < len(words) and words[index].startswith("-") and words[index] != "--":
                index += 1
            if index < len(words) and words[index] == "--":
                index += 1
            continue
        break
    return words[index:]


def _positionals(args: list[str]) -> list[str]:
    return [arg for arg in args if not arg.startswith("-")]


def _flag_value(args: list[str], *names: str) -> list[str]:
    values: list[str] = []
    for index, arg in enumerate(args):
        for name in names:
            if arg == name and index + 1 < len(args):
                values.append(args[index + 1])
            elif name.startswith("--") and arg.startswith(name + "="):
                values.append(arg[len(name) + 1:])
            elif not name.startswith("--") and len(name) == 2 and arg.startswith(name) and len(arg) > 2 and not arg.startswith("--"):
                values.append(arg[2:])
    return values


def sql_is_read_only(sql: str) -> bool:
    """True when every statement only reads (SELECT/WITH/EXPLAIN/SHOW...). Unknown text counts as a write."""
    text = re.sub(r"--[^\n]*", " ", sql)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"\$\$.*?\$\$", " $$ ", text, flags=re.DOTALL)
    text = re.sub(r"'(?:[^']|'')*'", "''", text)
    statements = [part.strip() for part in text.split(";") if part.strip()]
    if not statements:
        return False
    for statement in statements:
        first = statement.split(None, 1)[0].lower().strip("(")
        if first not in _READ_ONLY_SQL_START:
            return False
        if first in {"select", "with", "values", "table"} and _SQL_WRITE_WORDS.search(statement):
            return False
    return True


def _read_body(value: str, cwd: Path | None) -> str | None:
    """The request body or SQL a flag points at; ``@file`` is read from ``cwd``. None when it cannot be read."""
    if not value.startswith("@"):
        return value
    if cwd is None:
        return None
    try:
        path = Path(value[1:])
        path = path if path.is_absolute() else cwd / path
        return path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return None


def _sql_from_body(body: str) -> str:
    match = re.search(r'"query"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
    if match:
        return bytes(match.group(1), "utf-8").decode("unicode_escape", errors="replace")
    return body


def _http_actions(args: list[str], segment: str, cwd: Path | None) -> list[tuple[str, str]]:
    text = " ".join(args)
    found: list[tuple[str, str]] = []
    method = next(iter(_flag_value(args, "-X", "--request", "-Method")), "").upper()
    bodies = _flag_value(args, "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json", "-Body", "--body-data", "--post-data")
    if not method:
        method = "POST" if bodies else "GET"
    if re.search(r"api\.supabase\.com/v1/projects/[^/\s]+/database/(query|migrations)", text):
        sqls = [_read_body(body, cwd) for body in bodies]
        if not sqls or any(sql is None or not sql_is_read_only(_sql_from_body(sql)) for sql in sqls):
            found.append(("db_write", "SQL via Supabase Management API"))
    if re.search(r"api\.supabase\.com/v1/projects/[^/\s]+/(api-keys|secrets)", text):
        found.append(("credentials" if method == "GET" else "deploy", "chaves/segredos via Supabase Management API"))
    if re.search(r"\.supabase\.co/rest/v1/", text) and method in {"POST", "PUT", "PATCH", "DELETE"}:
        found.append(("db_write", f"{method} na REST API do Supabase"))
    return found


def _host_is_local(args: list[str]) -> bool:
    hosts = _flag_value(args, "-h", "--host", "-S", "--server")
    for arg in args:
        match = re.search(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://(?:[^@/\s]*@)?([^:/?\s]+)", arg)
        if match:
            hosts.append(match.group(1))
        match = re.search(r"\bhost=([^\s]+)", arg)
        if match:
            hosts.append(match.group(1))
    return all(_LOCAL_HOSTS.match(host.strip("[]") if host != "[::1]" else host) for host in hosts)


def _sql_client_actions(program: str, args: list[str], cwd: Path | None) -> list[tuple[str, str]]:
    if _host_is_local(args):
        return []
    commands = _flag_value(args, "-c", "--command", "-e", "--execute", "-Q", "--eval")
    files = _flag_value(args, "-f", "--file", "-i", "--input-file")
    sqls: list[str | None] = [*commands, *(_read_body("@" + name, cwd) for name in files)]
    if not sqls or any(sql is None or not sql_is_read_only(sql) for sql in sqls):
        return [("db_write", f"{program} em host remoto")]
    return []


def _classify(words: list[str], segment: str, cwd: Path | None) -> list[tuple[str, str]]:
    words = _unwrap(words)
    if not words:
        return []
    program = _program(words[0])
    args = words[1:]
    pos = [p.lower() for p in _positionals(args)]
    first = pos[0] if pos else ""
    second = pos[1] if len(pos) > 1 else ""
    flags = {arg.lower().split("=", 1)[0] for arg in args if arg.startswith("-")}
    linked = bool(flags & {"--linked", "--db-url"})
    found: list[tuple[str, str]] = []

    if program == "supabase":
        pair = (first, second)
        if pair in {("functions", "deploy"), ("functions", "delete")}:
            found.append(("deploy", f"supabase functions {second}"))
        elif pair == ("db", "push") or (pair in {("db", "reset"), ("migration", "up"), ("migration", "repair")} and linked):
            found.append(("db_write", f"supabase {first} {second}"))
        elif pair in {("secrets", "set"), ("secrets", "unset")}:
            found.append(("deploy", f"supabase secrets {second}"))
        elif pair == ("projects", "api-keys"):
            found.append(("credentials", "supabase projects api-keys"))
    elif program in {"vercel", "vc"}:
        if "--prod" in flags or first in {"deploy", "promote", "rollback", "redeploy", "remove", "rm"} or (
            first in {"env"} and second in {"add", "rm", "remove"}
        ) or (not first and not flags & {"--version", "-v", "--help", "-h"}) or first.startswith((".", "/")):
            found.append(("deploy", f"vercel {first or ''}".strip()))
    elif program in {"netlify", "ntl"}:
        if first in {"deploy", "env:set", "env:unset", "sites:delete"}:
            found.append(("deploy", f"netlify {first}"))
    elif program == "firebase":
        if any(p == "deploy" or p.endswith(":deploy") for p in pos):
            found.append(("deploy", "firebase deploy"))
        elif first in {"firestore:delete", "database:set", "database:update", "database:push", "database:remove", "functions:delete"}:
            found.append(("db_write" if first.startswith(("firestore", "database")) else "deploy", f"firebase {first}"))
    elif program in {"fly", "flyctl"}:
        if first in {"deploy", "scale"} or (first == "secrets" and second in {"set", "unset"}) or (first in {"apps", "machine", "machines"} and second in {"destroy", "remove"}):
            found.append(("deploy", f"fly {first}"))
    elif program == "wrangler":
        if first in {"deploy", "publish"} or (first == "pages" and second in {"deploy", "publish"}) or (first == "versions" and second == "deploy") or (first == "secret" and second in {"put", "delete"}):
            found.append(("deploy", f"wrangler {first}"))
        elif first == "d1" and second in {"execute", "migrations"} and "--remote" in flags:
            sqls = _flag_value(args, "--command")
            if second == "migrations" or not sqls or not all(sql_is_read_only(sql) for sql in sqls):
                found.append(("db_write", f"wrangler d1 {second} --remote"))
    elif program == "railway":
        if first in {"up", "redeploy", "down"} or (first in {"variables", "variable"} and "--set" in flags):
            found.append(("deploy", f"railway {first}"))
    elif program == "heroku":
        if first in {"releases:rollback", "config:set", "config:unset", "ps:scale", "pg:reset", "apps:destroy", "container:release", "container:push"}:
            found.append(("deploy", f"heroku {first}"))
    elif program == "gcloud":
        if "deploy" in pos or (first == "builds" and second == "submit"):
            found.append(("deploy", "gcloud " + " ".join(pos[:3])))
        elif "delete" in pos[:4]:
            found.append(("infra", "gcloud " + " ".join(pos[:3])))
        elif pos[:3] == ["secrets", "versions", "access"]:
            found.append(("credentials", "gcloud secrets versions access"))
    elif program == "az":
        if "deploy" in pos or "up" in pos[:2] or (first == "deployment" and "create" in pos):
            found.append(("deploy", "az " + " ".join(pos[:3])))
        elif pos[:3] == ["keyvault", "secret", "show"]:
            found.append(("credentials", "az keyvault secret show"))
    elif program == "aws":
        service, operation = first, second
        writes = {
            "cloudformation": {"deploy", "create-stack", "update-stack", "delete-stack"},
            "lambda": {"update-function-code", "update-function-configuration", "publish-version", "delete-function"},
            "ecs": {"update-service", "delete-service"},
            "amplify": {"start-job", "start-deployment"},
            "elasticbeanstalk": {"update-environment", "terminate-environment"},
            "cloudfront": {"create-invalidation"},
        }
        if operation in writes.get(service, set()):
            found.append(("deploy", f"aws {service} {operation}"))
        elif service == "s3" and operation in {"sync", "cp", "mv"} and pos[-1:] and pos[-1].startswith("s3://"):
            found.append(("deploy", f"aws s3 {operation} para bucket"))
        elif service == "s3" and operation in {"rm", "rb"}:
            found.append(("deploy", f"aws s3 {operation}"))
        elif (service, operation) in {("secretsmanager", "get-secret-value")} or (service == "ssm" and operation.startswith("get-parameter") and "--with-decryption" in flags):
            found.append(("credentials", f"aws {service} {operation}"))
    elif program in {"terraform", "tofu", "terragrunt"}:
        if first in {"apply", "destroy", "import", "run-all"} or (first == "state" and second in {"rm", "mv", "push"}):
            if first != "run-all" or second in {"apply", "destroy"}:
                found.append(("infra", f"{program} {first}"))
    elif program == "pulumi":
        if first in {"up", "update", "destroy", "import"} or (first == "stack" and second == "rm"):
            found.append(("infra", f"pulumi {first}"))
    elif program in {"cdk", "serverless", "sls", "sam", "eb", "copilot", "kamal", "sst", "amplify"}:
        if any(p in {"deploy", "destroy", "remove", "delete", "terminate", "redeploy", "rollback", "publish", "push"} for p in pos[:2]):
            found.append(("deploy", f"{program} {' '.join(pos[:2])}"))
    elif program == "cap" and "deploy" in pos:
        found.append(("deploy", "capistrano deploy"))
    elif program in {"eas", "expo"}:
        if first in {"submit", "update", "publish"}:
            found.append(("deploy", f"{program} {first}"))
    elif program in {"kubectl", "oc"}:
        if "--dry-run" not in flags and (
            first in {"apply", "create", "delete", "replace", "patch", "scale", "set", "edit"} or (first == "rollout" and second in {"restart", "undo"})
        ):
            found.append(("infra", f"{program} {first}"))
    elif program == "helm":
        if first in {"install", "upgrade", "uninstall", "delete", "rollback"} and "--dry-run" not in flags:
            found.append(("infra", f"helm {first}"))
    elif program in {"docker", "podman"}:
        if first == "push" or (first == "buildx" and "--push" in flags) or (first == "stack" and second == "deploy") or (first == "compose" and "push" in pos):
            found.append(("publish", f"{program} {first}"))
    elif program in {"npm", "pnpm", "yarn", "bun"}:
        if first in {"publish", "unpublish", "deprecate"} or (program == "yarn" and first == "npm" and second == "publish"):
            found.append(("publish", f"{program} {first}"))
        else:
            script = second if first in {"run", "run-script"} else first
            builtin = {"install", "i", "add", "remove", "test", "build", "dev", "start", "lint", "exec", "x", "ci", "audit", "outdated", "ls", "list", "why", "init", "create", "link", "update", "upgrade"}
            if script and script not in builtin and (_SENSITIVE_NAME.search(script) or _PROD_NAME.search(script)):
                found.append(("deploy", f"script {script}"))
    elif program in {"cargo"}:
        if first in {"publish", "yank", "owner"}:
            found.append(("publish", f"cargo {first}"))
    elif program == "twine" and first == "upload":
        found.append(("publish", "twine upload"))
    elif program in {"uv", "poetry", "flit", "hatch", "vsce", "ovsx"} and first == "publish":
        found.append(("publish", f"{program} publish"))
    elif program == "gem" and first in {"push", "yank"}:
        found.append(("publish", f"gem {first}"))
    elif program == "dotnet" and first == "nuget" and second == "push":
        found.append(("publish", "dotnet nuget push"))
    elif program in {"mvn", "mvnw"} and any(p in {"deploy", "release:perform"} for p in pos):
        found.append(("publish", "mvn deploy"))
    elif program in {"gradle", "gradlew"} and any(p.startswith("publish") and "local" not in p for p in pos):
        found.append(("publish", "gradle publish"))
    elif program == "gh":
        if first == "release" and second in {"create", "delete", "upload", "edit"}:
            found.append(("release", f"gh release {second}"))
        elif first == "workflow" and second == "run":
            found.append(("release", "gh workflow run"))
        elif first == "secret" and second in {"set", "delete"}:
            found.append(("deploy", f"gh secret {second}"))
    elif program in _TASK_RUNNERS:
        target = next((p for p in pos if _SENSITIVE_NAME.search(p) or _PROD_NAME.search(p)), None)
        if target:
            found.append(("deploy", f"{program} {target}"))
    elif program in _SQL_CLIENTS:
        found.extend(_sql_client_actions(program, args, cwd))
    elif program == "turso" and first == "db" and second == "shell":
        statements = pos[3:]
        if not statements or not all(sql_is_read_only(sql) for sql in statements):
            found.append(("db_write", "turso db shell"))
    elif program in _HTTP_CLIENTS:
        found.extend(_http_actions(args, segment, cwd))
    if not found:
        script_word = None
        if program in _SCRIPT_RUNNERS and pos:
            script_word = next((p for p in _positionals(args) if re.search(r"\.(sh|ps1|js|mjs|cjs|ts|py|rb|php)$", p, re.IGNORECASE)), None)
        elif re.search(r"[\\/]", words[0]) or re.search(r"\.(sh|ps1)$", words[0], re.IGNORECASE):
            script_word = words[0]
        if script_word:
            stem = re.split(r"[\\/]", script_word)[-1].rsplit(".", 1)[0]
            if _SENSITIVE_NAME.search(stem):
                found.append(("deploy", f"script {re.split(r'[\\/]', script_word)[-1]}"))
    return found


_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n")


def strip_heredocs(command: str) -> str:
    """``command`` without here-document bodies: they are stdin data, not commands the shell runs."""
    out: list[str] = []
    position = 0
    while True:
        match = _HEREDOC.search(command, position)
        if match is None:
            out.append(command[position:])
            return "".join(out)
        out.append(command[position:match.end()])
        terminator = re.compile(rf"^[ \t]*{re.escape(match.group(2))}[ \t]*$", re.MULTILINE)
        end = terminator.search(command, match.end())
        if end is None:
            return "".join(out)
        position = end.end()


def _inner_commands(words: list[str]) -> list[str]:
    """The command string a nested shell or ``eval`` runs, if this simple command is one."""
    words = _unwrap(words)
    if not words:
        return []
    program = _program(words[0])
    if program not in _NESTED:
        return []
    if program in {"eval", "invoke-expression", "iex"}:
        return [" ".join(words[1:])] if len(words) > 1 else []
    for index, word in enumerate(words[1:], start=1):
        if word.lower() in _NESTED_FLAGS and index + 1 < len(words):
            return [" ".join(words[index + 1:])]
    return []


def _substitutions(command: str) -> list[str]:
    inner = re.findall(r"\$\(((?:[^()]|\([^()]*\))*)\)", command)
    inner += re.findall(r"`([^`]*)`", command)
    return [text for text in inner if text.strip()]


def production_actions(command: str, tool: str, *, cwd: Path | None = None, _depth: int = 0) -> list[ProductionAction]:
    """Every production-impacting action in ``command``, each with the simple command that performs it."""
    if not isinstance(command, str) or not command.strip():
        return []
    dialect = "powershell" if tool == "powershell" else default_shell()
    word_dialect = "posix" if dialect == "posix" else "other"
    command = strip_heredocs(command)
    analysis = analyze_for(command, dialect)
    actions: list[ProductionAction] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, detail: str, segment: str) -> None:
        if (kind, detail) not in seen:
            seen.add((kind, detail))
            actions.append(ProductionAction(kind, detail, segment[:300]))

    nested: list[str] = []
    for segment in analysis.subcommands:
        words = _words(segment, word_dialect)
        for kind, detail in _classify(words, segment, cwd):
            add(kind, detail, segment)
        nested.extend(_inner_commands(words))
    nested.extend(_substitutions(command))
    if _depth < _MAX_DEPTH:
        for inner in nested:
            for action in production_actions(inner, tool, cwd=cwd, _depth=_depth + 1):
                add(action.kind, action.detail, action.segment)
    if not analysis.subcommands:
        # Too long or unreadable for the analysis: fall back to multi-word patterns (fail closed).
        plain = re.sub(r"[\"'`\\]", " ", command)
        for pattern, kind, detail in _FALLBACK_PATTERNS:
            match = pattern.search(plain)
            if match:
                add(kind, detail, match.group(0))
    return actions


__all__ = ["KIND_LABELS", "ProductionAction", "production_actions", "sql_is_read_only", "strip_heredocs"]
