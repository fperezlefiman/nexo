"""MCP server: the agent's write-side tools, exposed over stdio (F6).

The engine owns the loop; nexo only adds tools, through the protocol it already speaks.
Nothing here is imported by the turn path: the engine spawns `nexo mcp` as a child process.
"""

import json
import os
import re
import subprocess
import sys

from .db import connect
from . import credentials as creds
from .jobs import add_job, list_jobs, set_enabled, next_run_local
from .registry import DEFAULT_ROOT, deploy_to_engine, parse_frontmatter, sync

PROTOCOL = "2024-11-05"
SERVER = {"name": "nexo", "version": "1.0"}

TOOLS = [
    {
        "name": "memory_write",
        "description": "Save a durable fact about the user or the project. Use it when told to remember something.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "Profile name, e.g. shopping."},
                "content": {"type": "string", "description": "The fact, one or two sentences."},
                "kind": {"type": "string", "description": "fact | preference | rule", "default": "fact"},
            },
            "required": ["profile", "content"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search saved facts for a profile by words they contain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["profile", "query"],
        },
    },
    {
        "name": "credentials",
        "description": "Read one credential of the active profile from its .env (never the whole file). action=list returns variable names only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "Profile name, e.g. finance."},
                "action": {"type": "string", "description": "get | list", "default": "get"},
                "service": {"type": "string", "description": "Prefix, e.g. gmail or github."},
                "key": {"type": "string", "description": "Suffix, e.g. app_password. Omit to use service as the full name."},
            },
            "required": ["profile"],
        },
    },
    {
        "name": "session_search",
        "description": "Search past conversation messages across every profile.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
            "required": ["query"],
        },
    },
    {
        "name": "job_create",
        "description": "Create or update a scheduled job. Either a prompt (the agent runs a turn) or a shell command.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string"},
                "name": {"type": "string", "description": "Stable slug, e.g. recordatorio-turno-medico."},
                "schedule": {"type": "string", "description": "5-field cron in local time, e.g. '0 8 * * 1-6'."},
                "prompt": {"type": "string", "description": "Agent prompt; omit if you pass command."},
                "command": {"type": "string", "description": "Shell command; omit if you pass prompt."},
            },
            "required": ["profile", "name", "schedule"],
        },
    },
    {
        "name": "job_list",
        "description": "List scheduled jobs with their next run.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "job_cancel",
        "description": "Disable a scheduled job by name (kept for audit, never deleted).",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "python",
        "description": (
            "Run a Python snippet and get stdout, stderr and the exit code. Use it instead of "
            "escaping multi-line code into bash. NOT persistent: variables do not survive between "
            "calls, every call is a fresh process."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute."},
                "timeout": {"type": "number", "description": "Seconds, default 60, max 300."},
                "cwd": {"type": "string", "description": "Working directory, default the home directory."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "vision_analyze",
        "description": "Read a local image or an http(s) URL with a multimodal model and answer a question about it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Absolute path or http(s) URL of the image."},
                "question": {"type": "string", "description": "What to answer about the image."},
                "model": {"type": "string", "description": "Optional model override for the vision call."},
            },
            "required": ["source"],
        },
    },
    {
        "name": "skill_write",
        "description": "Create or update a skill in the skills repo and commit it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "category": {"type": "string", "description": "Profile name it belongs to, or 'general'."},
                "content": {"type": "string", "description": "Full SKILL.md including frontmatter."},
            },
            "required": ["slug", "category", "content"],
        },
    },
]


def _text(value) -> dict:
    body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": body}], "isError": False}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _profile_id(name: str) -> str:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM profiles WHERE name = %s", (name,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown profile '{name}'")
    return row["id"]


def _channel_for_profile(profile_id: str) -> str | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.id FROM conversations v JOIN channels c ON c.id = v.channel_id
                WHERE v.profile_id = %s ORDER BY v.created_at DESC LIMIT 1""",
            (profile_id,),
        )
        row = cur.fetchone()
    return row["id"] if row else None


def credentials_tool(args: dict) -> dict:
    """One value, or the list of variable names. The file itself is never returned."""
    profile = args["profile"]
    _profile_id(profile)  # validates the name; an unknown profile must not reach the filesystem
    if args.get("action", "get") == "list":
        return _text(creds.services(profile))
    return _text(creds.get(profile, args["service"], args.get("key")))


def memory_write(args: dict) -> dict:
    profile_id = _profile_id(args["profile"])
    content = args["content"].strip()
    if not content:
        raise ValueError("empty content")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM memory_items
                WHERE profile_id = %s AND content = %s AND deleted_at IS NULL""",
            (profile_id, content),
        )
        if cur.fetchone():
            return _text("already stored, nothing to do")
        cur.execute(
            """INSERT INTO memory_items (profile_id, kind, content, source)
               VALUES (%s, %s, %s, 'agent') RETURNING id""",
            (profile_id, args.get("kind", "fact"), content),
        )
        new_id = cur.fetchone()["id"]
        conn.commit()
    # Embed inline so a fresh memory is searchable at once. Best effort: a missing model must not
    # make the write fail, it only costs recall until the next `nexo vectors index`.
    try:
        from .vectors import index_one

        index_one(new_id, content)
    except Exception:
        pass
    return _text({"stored": True, "id": new_id})


def memory_search(args: dict) -> dict:
    """Semantic search over the profile's memories, measured against a gold set (docs/evals).

    Vectors only: on 52 memories a hybrid RRF fusion scored *worse* than cosine alone (0.886 vs
    0.971 recall@5) because the lexical side kept promoting documents the vectors had ranked low.
    Fusion assumes both retrievers are similarly good; here they are not, so it was measured away.

    The lexical branch survives only as coverage: a memory that was just written and not yet
    embedded would be invisible to a pure vector query.
    """
    profile_id = _profile_id(args["profile"])
    limit = int(args.get("limit", 10))
    rows = _vector_search(profile_id, args["query"], limit)
    if not rows:  # no model, no index, or this profile not embedded yet: better a keyword answer
        rows = _lexical_search(profile_id, args["query"], limit)
    if not rows:
        return _text("no matches")
    return _text(
        [
            {"content": r["content"], "kind": r["kind"], "at": r["created_at"].isoformat()}
            for r in rows
        ]
    )


def _vector_search(profile_id: str, query: str, limit: int) -> list[dict] | None:
    from .vectors import embed_query, stats

    # The global count is only a cheap pre-check: a profile with no vectors yet returns no rows
    # and must still fall back to words.
    if stats()["vectors"] == 0:
        return None
    vector = str(embed_query(query))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.content, m.kind, m.created_at
                 FROM memory_vectors v JOIN memory_items m ON m.id = v.memory_id
                WHERE m.profile_id = %s AND m.deleted_at IS NULL
                ORDER BY v.embedding <=> %s::vector LIMIT %s""",
            (profile_id, vector, limit),
        )
        return cur.fetchall()


def _lexical_search(profile_id: str, query: str, limit: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT content, kind, created_at FROM memory_items
                WHERE profile_id = %s AND deleted_at IS NULL AND content ILIKE %s
                ORDER BY created_at DESC LIMIT %s""",
            (profile_id, f"%{query}%", limit),
        )
        return cur.fetchall()


def session_search(args: dict) -> dict:
    # shortcut: ILIKE over the whole transcript. Instant at a few thousand messages; the day it
    # hurts, this is the call that F7 (vector/FTS) replaces, not the schema.
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT p.name AS profile, m.role, m.created_at,
                      left(m.content, 400) AS snippet
                 FROM messages m
                 JOIN conversations v ON v.id = m.conversation_id
                 JOIN profiles p ON p.id = v.profile_id
                WHERE m.content ILIKE %s
                ORDER BY m.created_at DESC LIMIT %s""",
            (f"%{args['query']}%", int(args.get("limit", 10))),
        )
        rows = cur.fetchall()
    if not rows:
        return _text("no matches")
    return _text(
        [
            {"profile": r["profile"], "role": r["role"], "at": r["created_at"].isoformat(),
             "snippet": r["snippet"]}
            for r in rows
        ]
    )


def job_create(args: dict) -> dict:
    profile_id = _profile_id(args["profile"])
    prompt, command = args.get("prompt"), args.get("command")
    if bool(prompt) == bool(command):
        raise ValueError("pass exactly one of prompt or command")
    next_run_local(args["schedule"])  # validate the cron expression before writing
    job_id = add_job(
        args["name"],
        "prompt" if prompt else "script",
        args["schedule"],
        profile_id=profile_id,
        channel_id=_channel_for_profile(profile_id),
        command=command,
        payload=prompt,
    )
    return _text({"job": args["name"], "id": job_id, "schedule": args["schedule"]})


def job_list(_: dict) -> dict:
    rows = list_jobs()
    if not rows:
        return _text("no jobs")
    return _text(
        [
            {"name": r["name"], "kind": r["kind"], "schedule": r["schedule"], "enabled": r["enabled"],
             "next_run_at": r["next_run_at"].isoformat() if r["next_run_at"] else None}
            for r in rows
        ]
    )


def job_cancel(args: dict) -> dict:
    set_enabled(args["name"], False)
    return _text({"disabled": args["name"]})


SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def python(args: dict) -> dict:
    """Programmatic execution with real quoting: the reason `execute_code` was used 2.290 times."""
    from .pytool import render, run

    result = run(
        args["code"],
        timeout=args.get("timeout"),
        cwd=args.get("cwd") or None,
    )
    return _text(render(result))


def vision_analyze(args: dict) -> dict:
    """Multimodal read of an image file or URL. Slow by nature (one model turn), so not for batches."""
    from .vision import analyze

    result = analyze(
        args["source"],
        args.get("question") or "",
        model=args.get("model") or None,
    )
    return _text(result)


def skill_write(args: dict) -> dict:
    slug, category, content = args["slug"], args["category"], args["content"]
    # The model supplies both parts of a filesystem path, so they are validated before use:
    # `category="../../.."` would write outside the repo.
    if not SLUG.match(slug or "") or not SLUG.match(category or ""):
        raise ValueError("slug and category must be lowercase letters, digits or hyphens (max 41)")
    try:
        meta, _ = parse_frontmatter(content)
    except ValueError as exc:
        raise ValueError(f"invalid frontmatter: {exc}")
    if meta.get("name") != slug:
        raise ValueError(f"frontmatter name '{meta.get('name')}' must equal slug '{slug}'")
    if not meta.get("description"):
        raise ValueError("frontmatter needs a description")

    from .registry import private_root, profile_names

    base = private_root() if category in profile_names() and private_root() else DEFAULT_ROOT
    target = base / category / slug
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(content)
    repo = base.parent
    identity = os.environ.get("NEXO_GIT_IDENTITY", "nexo <nexo@localhost>")
    name, _, email = identity.partition("<")
    git = ["git", "-C", str(repo), "-c", f"user.name={name.strip()}",
           "-c", f"user.email={email.rstrip('>')}"]
    subprocess.run([*git, "add", str(target)], check=True, capture_output=True)
    commit = subprocess.run([*git, "commit", "-q", "-m", f"skill({category}): {slug}"], capture_output=True, text=True)
    report = sync()
    deployed = deploy_to_engine()
    return _text(
        {
            "path": str(target / "SKILL.md"),
            "committed": commit.returncode == 0,
            "commit_out": (commit.stdout or commit.stderr).strip()[:200],
            "sync": report,
            "deployed_files": deployed["files"],
        }
    )


HANDLERS = {
    "memory_write": memory_write,
    "memory_search": memory_search,
    "credentials": credentials_tool,
    "session_search": session_search,
    "job_create": job_create,
    "job_list": job_list,
    "job_cancel": job_cancel,
    "python": python,
    "vision_analyze": vision_analyze,
    "skill_write": skill_write,
}


def handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:  # notification: run nothing, answer nothing
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": SERVER},
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/call":
        params = request.get("params") or {}
        handler = HANDLERS.get(str(params.get("name") or ""))
        if not handler:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "unknown tool"}}
        try:
            result = handler(params.get("arguments") or {})
        except ValueError as exc:
            result = _error(str(exc))
        except Exception as exc:  # a tool failure must not kill the server
            print(f"tool {params.get('name')} failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            result = _error(f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print(f"unparseable line: {line[:120]}", file=sys.stderr, flush=True)
            continue
        response = handle(request)
        if response:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
