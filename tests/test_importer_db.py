"""Importer against a real Postgres: every memory file comes across, and running twice adds nothing.

Run with ./scripts/test.sh (sets NEXO_TEST_DATABASE_URL).
"""

import os

import pytest

TEST_URL = os.environ.get("NEXO_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="set NEXO_TEST_DATABASE_URL to run queue tests")


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    monkeypatch.setenv("NEXO_DATABASE_URL", TEST_URL)
    from nexo.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE turns, messages, conversations, channels, profiles, memory_items "
                    "RESTART IDENTITY CASCADE")
        conn.commit()


def _fake_profile(tmp_path):
    prof = tmp_path / "demo-profile"
    (prof / "memories").mkdir(parents=True)
    (prof / "SOUL.md").write_text("Sos un perfil de prueba.", encoding="utf-8")
    (prof / "memories" / "MEMORY.md").write_text(
        "Primer hecho durable del perfil, con suficiente texto.\n§\nSegundo hecho durable del perfil, también largo.\n",
        encoding="utf-8")
    (prof / "memories" / "USER.md").write_text(
        "Preferencia del usuario sobre cómo quiere las respuestas.\n", encoding="utf-8")
    # Contexto que NO es MEMORY ni USER: en career son 12 KB de estado de búsqueda.
    (prof / "memories" / "CONTEXTO-X.md").write_text(
        "Párrafo de contexto uno, con el estado actual del trabajo.\n\n"
        "Párrafo de contexto dos, con el detalle que importa.\n", encoding="utf-8")
    return prof


def test_imports_every_memory_file_not_just_the_two_conventions(tmp_path):
    from nexo.importers import import_profile

    result = import_profile("demo", "-100", "model", source=_fake_profile(tmp_path))
    assert result["soul_chars"] == len("Sos un perfil de prueba.")
    assert result["memories"] == 5      # 2 + 1 + 2

    from nexo.db import connect
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT kind, count(*) c FROM memory_items GROUP BY 1 ORDER BY 1")
        kinds = {r["kind"]: r["c"] for r in cur.fetchall()}
    assert kinds == {"context": 2, "fact": 2, "preference": 1}


def test_second_import_adds_nothing(tmp_path):
    from nexo.importers import import_profile

    source = _fake_profile(tmp_path)
    import_profile("demo", "-100", "model", source=source)
    again = import_profile("demo", "-100", "model", source=source)
    assert again["memories"] == 0


def test_channel_and_profile_are_reused_on_reimport(tmp_path):
    from nexo.importers import import_profile
    from nexo.db import connect

    source = _fake_profile(tmp_path)
    first = import_profile("demo", "-100", "model", source=source)
    second = import_profile("demo", "-100", "model", source=source)
    assert first["profile_id"] == second["profile_id"]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) c FROM channels")
        assert cur.fetchone()["c"] == 1
