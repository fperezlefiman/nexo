"""Offline tests for the channel: no network, no database."""

from nexo.channels.telegram import split_message
from nexo.dispatch import build_prompt, idempotency_key


def test_split_short_message_is_untouched():
    assert split_message("hola") == ["hola"]


def test_split_respects_limit_and_keeps_content():
    text = "\n\n".join("bloque " + str(i) * 300 for i in range(40))
    parts = split_message(text, limit=4096)
    assert len(parts) > 1
    assert all(len(p) <= 4096 for p in parts)
    assert "bloque " in "".join(parts)


def test_split_hard_breaks_a_huge_block():
    parts = split_message("x" * 9000, limit=4096)
    assert [len(p) for p in parts] == [4096, 4096, 808]


def test_idempotency_key_is_stable_and_chat_scoped():
    a = idempotency_key("telegram", "-100", "42")
    assert a == idempotency_key("telegram", "-100", "42")
    assert a != idempotency_key("telegram", "-101", "42")
    assert a != idempotency_key("telegram", "-100", "43")


def test_prompt_puts_system_and_memory_before_history():
    prompt = build_prompt(
        "SISTEMA",
        ["hecho 1", "hecho 2"],
        [{"role": "user", "content": "viejo"}, {"role": "assistant", "content": "respuesta"}],
        "nuevo",
    )
    assert prompt.index("SISTEMA") < prompt.index("## Memory") < prompt.index("## Recent conversation")
    assert prompt.index("## Recent conversation") < prompt.index("## Message")
    assert "user: viejo" in prompt and "you: respuesta" in prompt


def test_prompt_without_optional_blocks():
    assert build_prompt("S", [], [], "hola").strip() == "S\n\n## Message\nhola"
