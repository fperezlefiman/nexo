"""Skills: one git directory is the source of truth, the database is an index (F5).

Sync is idempotent by content hash: a second run with no changes reindexes nothing.
An invalid skill is reported, never silently dropped.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

from . import paths
from .db import connect

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "skills"
SKILL_FILE = "SKILL.md"
REQUIRED = ("name", "description")
MAX_DESCRIPTION = 1024


def profile_names() -> set[str]:
    """Profile names from the database, the only source for "this skill is private".

    A constant here once went stale after a profile rename and a private skill was silently
    indexed as shared (2026-11-25).
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM profiles")
        return {row["name"] for row in cur.fetchall()}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML frontmatter reader: `key: value`, quoted values, and lists in brackets."""
    if not text.startswith("---"):
        raise ValueError("missing frontmatter block")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("unterminated frontmatter block")
    raw, body = text[3:end], text[end + 4 :].lstrip("\n")
    meta: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and key:  # continuation of a folded value
            meta[key] = f"{meta[key]} {line.strip()}".strip()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
        else:
            meta[key] = value
    for name, value in list(meta.items()):
        if isinstance(value, str) and len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            meta[name] = value[1:-1]
    return meta, body


def skill_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(directory)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def iter_skills(root: Path):
    for path in sorted(root.rglob(SKILL_FILE)):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue  # .template y otros directorios ocultos no son skills
        yield path.parent




def private_root() -> Path | None:
    """Where a profile's own skills live, when they are kept out of this repo.

    A public checkout ships generic tooling; `career`, `finance` and the rest are personal and
    live in their own directory (NEXO_SKILLS_PRIVATE, or NEXO_HOME/skills-private).
    """
    raw = os.environ.get("NEXO_SKILLS_PRIVATE", "").strip()
    path = Path(raw).expanduser() if raw else paths.home() / "skills-private"
    return path if path.is_dir() else None


def roots(root: Path | None = None) -> list[Path]:
    if root:
        return [root]
    found = [default_root()]
    extra = private_root()
    if extra and extra not in found:
        found.append(extra)
    return found


def sync(root: Path | None = None) -> dict:
    known = profile_names()
    report = {"added": 0, "updated": 0, "unchanged": 0, "invalid": []}
    for directory in [d for r in roots(root) for d in iter_skills(r)]:
        slug = directory.name
        try:
            meta, body = parse_frontmatter((directory / SKILL_FILE).read_text())
            missing = [field for field in REQUIRED if not meta.get(field)]
            if missing:
                raise ValueError(f"missing {', '.join(missing)}")
            if meta["name"] != slug:
                raise ValueError(f"name '{meta['name']}' does not match directory '{slug}'")
            if len(meta["description"]) > MAX_DESCRIPTION:
                raise ValueError("description too long")
        except ValueError as exc:
            report["invalid"].append({"path": str(directory), "reason": str(exc)})
            continue

        digest = skill_hash(directory)
        category = directory.parent.name
        # A category that names a profile is that profile's private skill (precision first):
        # the trainer's programming skill has no business being injected into the shopping bot.
        allowlist = [category] if category in known else []
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT content_hash FROM skills WHERE slug = %s", (slug,))
            row = cur.fetchone()
            cur.execute(
                """INSERT INTO skills (slug, category, path, description, frontmatter, content_hash, body, profiles, synced_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (slug) DO UPDATE
                     SET category = EXCLUDED.category, path = EXCLUDED.path,
                         description = EXCLUDED.description, frontmatter = EXCLUDED.frontmatter,
                         content_hash = EXCLUDED.content_hash, body = EXCLUDED.body,
                         profiles = EXCLUDED.profiles, synced_at = now()""",
                (slug, category, str(directory), meta["description"], json.dumps(meta), digest, body, allowlist),
            )
            conn.commit()
        if not row:
            report["added"] += 1
        elif row["content_hash"] != digest:
            report["updated"] += 1
        else:
            report["unchanged"] += 1
    return report


def list_skills() -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug, category, profiles, enabled, synced_at FROM skills ORDER BY category, slug")
        return cur.fetchall()


def deploy_to_engine(root: Path | None = None, target: Path | None = None) -> dict:
    """Copy the repo into the engine's skills directory, manifest-managed.

    The engine loads skills on demand (progressive disclosure), so nothing is injected into
    the prompt: 77 k of bodies every turn would be paid for nothing. Only files this command
    wrote are ever removed; the engine's own skills stay untouched.
    """
    target = target or Path(os.environ.get("NEXO_ENGINE_SKILLS", "~/.config/opencode/skills")).expanduser()
    manifest_path = target / ".nexo-manifest.json"
    previous = set()
    if manifest_path.exists():
        previous = set(json.loads(manifest_path.read_text()).get("files", []))
    current: list[str] = []
    for directory in [d for r in roots(root) for d in iter_skills(r)]:
        for source in directory.rglob("*"):
            if not source.is_file():
                continue
            destination = target / directory.name / source.relative_to(directory)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            current.append(str(destination.relative_to(target)))
    removed = 0
    for stale in previous - set(current):
        stale_path = target / stale
        if stale_path.exists() and ".." not in stale:
            stale_path.unlink()
            removed += 1
    target.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"generated_by": "nexo skills deploy", "files": sorted(current)}, indent=1))
    return {"target": str(target), "files": len(current), "removed": removed}


def export_skills(root: Path | None = None, target: Path | None = None) -> dict:
    """Mirror the repo into Nexo's own export tree, one directory per profile.

    Before, this wrote into each old-system profile under `skills/shared/`; now the target is
    NEXO_HOME/cache/skills-export, so nothing of Nexo lands inside ~/.hermes. A private skill
    (category = profile name) goes only to that profile; collisions with an existing copy are
    reported, not overwritten.
    """
    target = target or paths.skills_export()
    written: dict = {}
    known = profile_names()
    for name in sorted(known):
        profile_dir = target / name
        manifest_path = profile_dir / ".nexo-manifest.json"
        previous = set()
        if manifest_path.exists():
            previous = set(json.loads(manifest_path.read_text()).get("files", []))
        current: list[str] = []
        exports, collisions = 0, []
        for base in roots(root):
            for directory in iter_skills(base):
                category = directory.parent.name
                if category in known and category != name:
                    continue  # private skill of another profile
                relative_dir = directory.relative_to(base)
                destination_dir = profile_dir / relative_dir
                # Un directorio con el mismo slug fuera de la ruta que exportamos es una segunda copia.
                if any(p.is_dir() and p != destination_dir for p in profile_dir.rglob(directory.name)):
                    collisions.append(directory.name)
                for source in directory.rglob("*"):
                    if not source.is_file():
                        continue
                    destination = destination_dir / source.relative_to(directory)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    current.append(str(destination.relative_to(profile_dir)))
                    exports += 1
        for stale in previous - set(current):  # remove only what we previously wrote
            stale_path = profile_dir / stale
            if stale_path.exists() and ".." not in stale:
                stale_path.unlink()
        profile_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"generated_by": "nexo skills export", "files": sorted(current)}, indent=1)
        )
        written[name] = {"files": exports, "collisions": collisions}
    return written


def default_root() -> Path:
    return Path(os.environ.get("NEXO_SKILLS_ROOT", DEFAULT_ROOT))
