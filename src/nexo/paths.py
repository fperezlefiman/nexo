"""Nexo's own directories.

Nothing here points at the old system: what Nexo reads or writes lives under NEXO_HOME
(default ~/.local/share/nexo). `nexo snapshot` is the only door into Hermes, and it copies.
"""

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".local" / "share" / "nexo"))).expanduser()


def import_sources() -> Path:
    """SOUL and memories per profile, copied out of the old system by `nexo snapshot`."""
    return home() / "cache" / "import-sources"


def skills_export() -> Path:
    """Per-profile copy of the repo's skills. Nothing of Nexo is written inside ~/.hermes."""
    return home() / "cache" / "skills-export"
