"""Registry tests: frontmatter parsing, hashing and the invalid-skill path."""

from pathlib import Path

from nexo.dispatch import build_prompt
from nexo.registry import deploy_to_engine, parse_frontmatter, skill_hash

VALID = """---
name: demo
description: "A demo skill. Uses: none."
tags: [demo, test]
---

# Demo

body line
"""


def test_parses_name_description_and_list():
    meta, body = parse_frontmatter(VALID)
    assert meta["name"] == "demo"
    assert meta["description"].startswith("A demo skill")
    assert meta["tags"] == ["demo", "test"]
    assert body.startswith("# Demo")


def test_joined_description_lines():
    meta, _ = parse_frontmatter('---\nname: x\ndescription: "one\n  two"\n---\nbody\n')
    assert meta["description"] == "one two"


def test_missing_block_is_invalid():
    try:
        parse_frontmatter("# no frontmatter\n")
    except ValueError as exc:
        assert "frontmatter" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_unterminated_block_is_invalid():
    try:
        parse_frontmatter("---\nname: x\n")
    except ValueError as exc:
        assert "unterminated" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_hash_changes_when_a_reference_file_changes(tmp_path: Path):
    (tmp_path / "SKILL.md").write_text(VALID)
    (tmp_path / "references").mkdir()
    reference = tmp_path / "references" / "note.md"
    reference.write_text("v1")
    first = skill_hash(tmp_path)
    assert skill_hash(tmp_path) == first
    reference.write_text("v2")
    assert skill_hash(tmp_path) != first


def test_deploy_copies_and_prunes(tmp_path: Path):
    root = tmp_path / "repo"
    skill = root / "trainer" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(VALID)
    (skill / "references").mkdir()
    (skill / "references" / "note.md").write_text("v1")
    engine = tmp_path / "engine"
    (engine / "impeccable").mkdir(parents=True)
    (engine / "impeccable" / "SKILL.md").write_text("---\nname: impeccable\ndescription: x\n---\n")

    first = deploy_to_engine(root, engine)
    assert first["files"] == 2
    assert (engine / "demo" / "SKILL.md").exists()
    assert (engine / "impeccable" / "SKILL.md").exists()  # never touches foreign skills

    (skill / "references" / "note.md").unlink()
    second = deploy_to_engine(root, engine)
    assert second["files"] == 1 and second["removed"] == 1
    assert not (engine / "demo" / "references" / "note.md").exists()


def test_prompt_does_not_inject_skill_bodies():
    prompt = build_prompt("SYS", ["m1"], [], "hola")
    assert "## Skills" not in prompt


def test_deploy_sums_the_public_and_private_roots(tmp_path: Path, monkeypatch):
    """El repo trae las genéricas; las de perfil viven en su propio directorio (2026-11-25)."""
    from nexo.registry import deploy_to_engine

    public = tmp_path / "public" / "shared" / "demo"
    private = tmp_path / "private" / "trainer" / "propia"
    for directory, name in ((public, "demo"), (private, "propia")):
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: demo\n---\nbody\n")
    monkeypatch.setenv("NEXO_SKILLS_ROOT", str(tmp_path / "public"))
    monkeypatch.setenv("NEXO_SKILLS_PRIVATE", str(tmp_path / "private"))
    engine = tmp_path / "engine"

    report = deploy_to_engine(target=engine)
    assert report["files"] == 2
    assert (engine / "demo" / "SKILL.md").exists()
    assert (engine / "propia" / "SKILL.md").exists()
