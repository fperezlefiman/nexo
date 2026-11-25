"""Import a profile from the old system: SOUL, memories, transcript.

Everything here is idempotent: run it twice and the second run adds nothing.
"""

import shutil
import sqlite3
from pathlib import Path

from . import paths
from .db import connect
from .store import new_id

LEGACY_ROOT = Path.home() / ".hermes"


def snapshot(legacy: Path | None = None) -> dict:
    """Copy SOUL and memories out of the old system into NEXO_HOME/cache/import-sources.

    state.db stays behind on purpose: the transcript already lives in Postgres and those files
    reach 1.1 GB. Run it whenever the old system's memory changes; `require` only reads the copy.
    """
    legacy = (legacy or LEGACY_ROOT).expanduser()
    # `profiles/default` existe pero está vacío: el perfil default del sistema viejo es la raíz.
    roots = {"default": legacy}
    profiles = legacy / "profiles"
    if profiles.is_dir():
        roots.update({p.name: p for p in sorted(profiles.iterdir()) if p.is_dir() and p.name != "default"})
    # Folder names are English by rule; the old system's profile names are not.
    copied: dict[str, int] = {}
    for name, src in roots.items():
        soul, memories = src / "SOUL.md", src / "memories"
        if not soul.exists() and not memories.is_dir():
            continue
        dest = paths.import_sources() / name
        (dest / "memories").mkdir(parents=True, exist_ok=True)
        files = 0
        if soul.exists():
            shutil.copy2(soul, dest / "SOUL.md")
            files += 1
        for path in sorted(memories.glob("*.md")):
            shutil.copy2(path, dest / "memories" / path.name)
            files += 1
        copied[name] = files
    return {"target": str(paths.import_sources()), "profiles": copied}


def import_profile(name: str, chat_id: str, model: str, source: Path | None = None) -> dict:
    src = source or paths.import_sources() / name
    # Un nombre que no existe en el sistema viejo importaba CERO y decía "listo": creó un perfil
    # vacío y un canal sin decirlo. Falla fuerte en vez de fingir éxito.
    if not src.is_dir():
        raise FileNotFoundError(f"no existe el perfil de origen: {src} (corre `nexo snapshot`)")
    if not (src / "SOUL.md").exists() and not (src / "memories").is_dir():
        raise ValueError(f"{src} no parece un perfil: falta SOUL.md y memories/")
    soul = (src / "SOUL.md").read_text() if (src / "SOUL.md").exists() else ""
    profile_id = new_id("prf")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM profiles WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            profile_id = row["id"]
            cur.execute("UPDATE profiles SET system_prompt = %s, default_model = %s WHERE id = %s", (soul, model, profile_id))
        else:
            cur.execute(
                "INSERT INTO profiles (id, name, system_prompt, default_model) VALUES (%s, %s, %s, %s)",
                (profile_id, name, soul, model),
            )
        cur.execute(
            """INSERT INTO channels (id, kind, external_id, profile_id) VALUES (%s, 'telegram', %s, %s)
               ON CONFLICT (kind, external_id) DO UPDATE SET profile_id = EXCLUDED.profile_id""",
            (new_id("chn"), chat_id, profile_id),
        )
        conn.commit()

    memories = _import_memories(src, profile_id)
    transcript = _import_transcript(src, profile_id, name)
    return {"profile_id": profile_id, "soul_chars": len(soul), "memories": memories, "transcript": transcript}


def _import_memories(src: Path, profile_id: str) -> int:
    added = 0
    with connect() as conn, conn.cursor() as cur:
        for path in sorted((src / "memories").glob("*.md")):
            # MEMORY.md y USER.md son las convenciones del sistema viejo; cualquier otro .md de esa
            # carpeta es contexto que el perfil usa (en career son 12 KB de estado de la búsqueda).
            # Ignorarlos migraba el perfil sin la mitad de lo que sabe.
            kind = {"MEMORY.md": "fact", "USER.md": "preference"}.get(path.name, "context")
            filename = path.name
            for chunk in _chunks(path.read_text()):
                cur.execute(
                    "SELECT 1 FROM memory_items WHERE profile_id = %s AND content = %s",
                    (profile_id, chunk),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    "INSERT INTO memory_items (profile_id, kind, content, source) VALUES (%s, %s, %s, %s)",
                    (profile_id, kind, chunk, f"import:{filename}"),
                )
                added += 1
        conn.commit()
    return added


def _chunks(text: str, min_len: int = 40) -> list[str]:
    """Split a memory file into entries.

    The old system separates entries with a lone `§` line; blank lines are the fallback.
    """
    raw = text.replace("\n§\n", "\n\n")
    parts = [p.strip() for p in raw.split("\n\n")]
    return [p for p in parts if len(p) >= min_len]


def _import_transcript(src: Path, profile_id: str, profile_name: str) -> int:
    """Copy the old chat history as an imported conversation (read-only reference)."""
    db = src / "state.db"
    if not db.exists():
        return 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM conversations WHERE profile_id = %s AND title = %s", (profile_id, "imported history"))
        row = cur.fetchone()
        if row:
            return 0
        conv = new_id("cnv")
        cur.execute(
            "INSERT INTO conversations (id, profile_id, title) VALUES (%s, %s, %s)",
            (conv, profile_id, "imported history"),
        )
        conn.commit()

    old = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    # La base raíz del sistema viejo es multiplex (todos los perfiles en un archivo). Sin filtro,
    # importar `default` desde ahí se llevaba los 88k mensajes de los seis perfiles.
    multiplex = any(row[1] == "profile_name" for row in old.execute("PRAGMA table_info(sessions)"))
    if multiplex:
        rows = old.execute(
            "SELECT m.role, m.content, m.timestamp FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE s.profile_name = ? AND m.role IN ('user','assistant') AND m.content IS NOT NULL "
            "ORDER BY m.timestamp",
            (profile_name,),
        ).fetchall()
    else:
        rows = old.execute(
            "SELECT role, content, timestamp FROM messages WHERE role IN ('user','assistant') AND content IS NOT NULL "
            "ORDER BY timestamp"
        ).fetchall()
    old.close()

    with connect() as conn, conn.cursor() as cur:
        for role, content, ts in rows:
            cur.execute(
                """INSERT INTO messages (conversation_id, role, content, created_at)
                   VALUES (%s, %s, %s, to_timestamp(%s))""",
                (conv, role, content, ts),
            )
        conn.commit()
    return len(rows)


def find_message(profile_name: str, needle: str) -> dict | None:
    """Used by the F1 gate: can the imported history answer about an old message?"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.role, m.content, m.created_at FROM messages m
                 JOIN conversations c ON c.id = m.conversation_id
                 JOIN profiles p ON p.id = c.profile_id
                WHERE p.name = %s AND m.content ILIKE %s
                ORDER BY m.created_at DESC LIMIT 1""",
            (profile_name, f"%{needle}%"),
        )
        return cur.fetchone()
