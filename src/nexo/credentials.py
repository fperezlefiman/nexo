"""Per-profile credentials: one key at a time, never the whole file.

The engine runs a single shared process, so a profile cannot get its own environment. This module
resolves one variable from NEXO_HOME/<profile>/.env on demand: that is what lets two profiles use
different Gmail or GitHub accounts without duplicating services or leaking the rest of the file.
"""

from pathlib import Path

from .paths import home


def env_file(profile: str) -> Path:
    return home() / profile / ".env"


def _pairs(profile: str):
    path = env_file(profile)
    if not path.is_file():
        raise FileNotFoundError(f"no .env for profile '{profile}' ({path})")
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        yield name.strip(), value.strip().strip('"').strip("'")


def get(profile: str, service: str, key: str | None = None) -> str:
    """Value of one variable. `get(p, "gmail", "app_password")` reads GMAIL_APP_PASSWORD."""
    wanted = f"{service}_{key}".upper() if key else service.upper()
    for name, value in _pairs(profile):
        if name == wanted:
            if not value:
                raise ValueError(f"{wanted} is empty in {env_file(profile)}")
            return value
    raise KeyError(f"{wanted} not set for profile '{profile}'")


def keys(profile: str) -> list[str]:
    """Variable names only: values never leave the file."""
    return sorted({name for name, _ in _pairs(profile)})


def services(profile: str) -> dict[str, list[str]]:
    """Group variable names by the prefix before the first underscore, still without values."""
    out: dict[str, list[str]] = {}
    for name in keys(profile):
        head = name.split("_", 1)[0].lower()
        out.setdefault(head, []).append(name)
    return out
