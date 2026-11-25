"""Vision tool: path handling is tested offline; the model call is a live check (not in CI)."""


import pytest

from nexo.vision import analyze, resolve


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        resolve("/tmp/no-existe-nexo-xyz.png")


def test_unsupported_extension_raises(tmp_path):
    target = tmp_path / "nota.txt"
    target.write_text("hola")
    with pytest.raises(ValueError):
        resolve(str(target))


def test_local_png_resolves(tmp_path):
    target = tmp_path / "px.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    assert resolve(str(target)) == target


def test_empty_question_still_asks_for_a_description():
    """The prompt must never reach the model empty: an empty instruction returns an empty answer."""
    with pytest.raises(FileNotFoundError):
        analyze("/tmp/no-existe-nexo-xyz.png", "")
