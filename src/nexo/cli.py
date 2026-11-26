"""CLI: migrate, import, channel, ask, health, history."""

import argparse
import json
import sys
from pathlib import Path

from .adapters.opencode import OpenCodeRuntime
from .db import connect, migrate


def _job_command(args) -> int:
    from .jobs import LOCAL_TZ, add_job, list_jobs, run_once, set_enabled

    if args.action == "list":
        for row in list_jobs():
            # El scheduler programa en hora local; la base devuelve UTC. Mostrarlo crudo hacía leer
            # "12:00" para un cron de las 9 de la mañana.
            when = row["next_run_at"].astimezone(LOCAL_TZ)
            print(f"{row['name']:32} {row['kind']:7} {row['schedule']:12} owner={row['owner']:6} "
                  f"enabled={row['enabled']!s:5} next={when:%Y-%m-%d %H:%M} local")
        return 0

    if not args.name:
        sys.exit("job name is required")

    if args.action in ("add-script", "add-prompt"):
        is_prompt = args.action == "add-prompt"
        if is_prompt and not args.prompt:
            sys.exit("--prompt is required for add-prompt")
        if not is_prompt and not args.command:
            sys.exit("--command is required for add-script")
        if not args.once and not args.schedule:
            sys.exit("--schedule (or --once for a one-shot) is required")
        run_at = None
        if args.once:
            from datetime import datetime
            try:
                run_at = datetime.fromisoformat(args.once).replace(tzinfo=LOCAL_TZ)
            except ValueError:
                sys.exit(f"--once espera 'YYYY-MM-DD HH:MM' (hora local), recibí: {args.once}")
        if not args.profile:
            sys.exit("--profile is required")
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT p.id AS profile_id, c.id AS channel_id FROM profiles p
                     JOIN channels c ON c.profile_id = p.id WHERE p.name = %s LIMIT 1""",
                (args.profile,),
            )
            row = cur.fetchone()
        if not row:
            sys.exit(f"el perfil {args.profile} no tiene canal en nexo")
        add_job(args.name, "prompt" if is_prompt else "script", args.schedule,
                profile_id=row["profile_id"], channel_id=row["channel_id"],
                command=None if is_prompt else args.command,
                payload=args.prompt, run_at=run_at)
        print(f"job {args.name} added" + (" (one-shot)" if run_at else ""))
        return 0

    if args.action in ("enable", "disable"):
        set_enabled(args.name, args.action == "enable")
        print(f"{args.name}: {args.action}d")
        return 0

    if args.action == "run":
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE name = %s", (args.name,))
            job = cur.fetchone()
        result = run_once(job)
        print(f"{result['status']} | {len(result['output'])} chars")
        print(result["output"][:800])
        return 0
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nexo")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="apply the database schema")

    imp = sub.add_parser("import", help="import a profile from the old system")
    imp.add_argument("name")
    imp.add_argument("--chat-id", required=True)
    imp.add_argument("--model", required=True)
    # El perfil default vive en la raíz (~/.hermes), no en ~/.hermes/profiles/<nombre>.
    imp.add_argument("--source", default=None, help="directorio a importar (default: NEXO_HOME/cache/import-sources/<nombre>)")

    snap = sub.add_parser("snapshot", help="copy SOUL and memories out of the old system into NEXO_HOME")
    snap.add_argument("--from", dest="legacy", default=None, help="raíz del sistema viejo (default: ~/.hermes)")

    chan = sub.add_parser("channel", help="run a channel")
    chan.add_argument("kind", choices=["telegram"])

    ask = sub.add_parser("ask", help="one prompt against the engine, no database")
    ask.add_argument("text")
    ask.add_argument("--model", required=True)
    ask.add_argument("--timeout", type=float, default=180.0)

    sub.add_parser("health", help="check the engine is reachable")

    mod = sub.add_parser("model", help="show or set a profile's model, as provider/modelo")
    mod.add_argument("profile")
    mod.add_argument("entry", nargs="?", help="provider/modelo; empty shows the current one")

    hist = sub.add_parser("history", help="search the imported transcript")
    hist.add_argument("profile")
    hist.add_argument("needle")

    sub.add_parser("scheduler", help="run the job scheduler")
    sub.add_parser("worker", help="run the turn worker")
    sub.add_parser("mcp", help="run the MCP tool server (spoken to by the engine)")
    sub.add_parser("metrics", help="print per-job runtime and failures")
    notify = sub.add_parser("notify", help="send one message to a channel (scripts outside nexo)")
    notify.add_argument("profile", help="profile name, chat id, or 'telegram' for the operator's chat")
    notify.add_argument("text")
    notify.add_argument("--md", action="store_true",
                        help="parse the text as Markdown (``` blocks become tappable copy blocks)")
    ev = sub.add_parser("evals", help="measure memory retrieval against the gold set")
    ev.add_argument(
        "--retriever",
        default="all",
        choices=["substring", "keywords", "lexical", "vector", "hybrid", "all"],
    )
    ev.add_argument("--k", type=int, default=5)
    ev.add_argument(
        "--split",
        default="all",
        choices=["dev", "test", "all"],
        help="dev para iterar, test solo al cambiar de arquitectura (evals.md §1)",
    )

    vec = sub.add_parser("vectors", help="build or inspect the memory vector index")
    vec.add_argument("action", choices=["index", "stats"])
    vec.add_argument("--profile", default=None)
    vec.add_argument("--force", action="store_true")

    sk = sub.add_parser("skills", help="manage the skills registry")
    sk.add_argument("action", choices=["sync", "list", "deploy", "export"])

    cred = sub.add_parser("credentials", help="read one credential of a profile from its .env")
    cred.add_argument("action", choices=["list", "get"])
    cred.add_argument("name", nargs="?", help="service, or SERVICE_KEY for get")
    cred.add_argument("--profile", required=True)

    br = sub.add_parser("browse", help="fetch a JS-rendered page with a headless browser")
    br.add_argument("url")
    br.add_argument("--wait", help="CSS selector to wait for")
    br.add_argument("--text", action="store_true", help="print only the page text")

    job = sub.add_parser("job", help="manage jobs")
    job.add_argument("action", choices=["list", "add-script", "add-prompt", "enable", "disable", "run"])
    job.add_argument("name", nargs="?")
    job.add_argument("--schedule", help="5-field cron")
    job.add_argument("--command", help="shell command for a script job")
    job.add_argument("--prompt", help="prompt for a prompt job (the agent runs a turn)")
    job.add_argument("--once", metavar="'YYYY-MM-DD HH:MM'",
                     help="one-shot at that local time instead of a cron schedule")
    job.add_argument("--profile", help="profile name to attach the job to")

    args = parser.parse_args(argv)

    if args.cmd == "job":
        return _job_command(args)

    if args.cmd == "scheduler":
        from .jobs import serve

        serve()
        return 0

    if args.cmd == "worker":
        from .worker import serve as worker_serve

        worker_serve()
        return 0

    if args.cmd == "mcp":
        from .mcp import main as mcp_main

        return mcp_main()

    if args.cmd == "vectors":
        from . import vectors
        from .db import connect as _connect

        if args.action == "stats":
            print(vectors.stats())
        else:
            # --profile llega como NOMBRE; index() filtra por profile_id.
            profile_id = args.profile
            if profile_id:
                with _connect() as conn, conn.cursor() as cur:
                    cur.execute("SELECT id FROM profiles WHERE name = %s", (profile_id,))
                    row = cur.fetchone()
                if not row:
                    sys.exit(f"no existe el perfil {profile_id}")
                profile_id = row["id"]
            print(vectors.index(profile_id=profile_id, force=args.force))
        return 0

    if args.cmd == "evals":
        from .evals import RETRIEVERS, run

        names = list(RETRIEVERS) if args.retriever == "all" else [args.retriever]
        for name in names:
            try:
                r = run(name, k=args.k, split=args.split)
            except Exception as exc:
                print(f"{name:10} no se pudo medir: {type(exc).__name__}: {exc}")
                continue
            print(
                f"{name:10} [{args.split}] recall@{args.k}={r['recall']:.3f} "
                f"({r['hits']}/{r['n']})  MRR={r['mrr']:.3f}"
            )
            for miss in r["misses"][:6]:
                print(f"           falla: {miss}")
        return 0

    if args.cmd == "notify":
        from .jobs import notify as notify_channel

        try:
            chat = notify_channel(args.profile, args.text,
                                  parse_mode="Markdown" if args.md else None)
        except RuntimeError as exc:
            sys.exit(f"no pude enviar: {exc}")
        print(f"sent to {chat}")
        return 0

    if args.cmd == "metrics":
        from .jobs import job_metrics

        print(f"{'job':34} {'runs':>5} {'avg s':>7} {'max s':>7} {'fail':>5}  last run")
        for row in job_metrics():
            last = row["last_run"].strftime("%d-%b %H:%M") if row["last_run"] else "—"
            print(f"{row['name']:34} {row['runs']:5} {row['avg_s']:>7} {row['max_s']:>7} "
                  f"{row['failures']:5}  {last}")
        return 0

    if args.cmd == "skills":
        from .registry import deploy_to_engine, export_skills, list_skills, sync

        if args.action == "sync":
            print(json.dumps(sync(), indent=1))
        elif args.action == "list":
            for row in list_skills():
                print(f"{row['slug']:34} {row['category']:16} profiles={list(row['profiles']) or 'all'}")
        elif args.action == "deploy":
            print(json.dumps(deploy_to_engine(), indent=1))
        else:
            print(json.dumps(export_skills(), indent=1))
        return 0

    if args.cmd == "browse":
        from .browser import main as browse_main

        return browse_main([args.url] + (["--wait", args.wait] if args.wait else []) + (["--text"] if args.text else []))

    if args.cmd == "migrate":
        migrate()
        return 0

    if args.cmd == "snapshot":
        from .importers import snapshot

        print(json.dumps(snapshot(Path(args.legacy) if args.legacy else None), indent=1))
        return 0

    if args.cmd == "import":
        from .importers import import_profile
        from pathlib import Path

        source = Path(args.source).expanduser() if args.source else None
        print(import_profile(args.name, args.chat_id, args.model, source))
        return 0

    if args.cmd == "credentials":
        from . import credentials as creds

        if args.action == "list":
            print(json.dumps(creds.services(args.profile), indent=1, ensure_ascii=False))
        else:
            # `credentials get GMAIL_APP_PASSWORD`: the first underscore splits prefix from suffix.
            service, _, key = (args.name or "").partition("_")
            print(creds.get(args.profile, service, key or None))
        return 0

    if args.cmd == "channel":
        from .channels.telegram import TelegramChannel

        # Tokens are resolved per channel from the database (channels.settings.token_env).
        TelegramChannel(OpenCodeRuntime()).run()
        return 0

    if args.cmd == "model":
        from .db import connect as _connect

        with _connect() as conn, conn.cursor() as cur:
            if args.entry:
                cur.execute("UPDATE profiles SET default_model = %s WHERE name = %s",
                            (args.entry, args.profile))
                if cur.rowcount == 0:
                    sys.exit(f"no existe el perfil {args.profile}")
                conn.commit()
            cur.execute("SELECT default_model FROM profiles WHERE name = %s", (args.profile,))
            row = cur.fetchone()
        print((row or {}).get("default_model") or "")
        return 0

    if args.cmd == "history":
        from .importers import find_message

        print(find_message(args.profile, args.needle))
        return 0

    runtime = OpenCodeRuntime()
    if args.cmd == "health":
        ok = runtime.health()
        print("ok" if ok else "unavailable")
        return 0 if ok else 1

    handle = runtime.open_session(args.model, title="cli")
    runtime.prompt(handle.id, args.text)
    result = runtime.wait_turn(handle.id, timeout=args.timeout)
    print(result.text)
    print(
        f"[{result.model_id} tokens={result.tokens} cost={result.cost} {result.latency_ms}ms]",
        file=sys.stderr,
    )
    return 0
