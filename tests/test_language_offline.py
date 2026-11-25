"""Voseo guard: the check that decides whether an answer needs one corrective turn."""

from nexo.dispatch import language_violation

CLEAN = """Hoy toca trote Z2 de 20 minutos. Sube a 8 kg si la primera serie sale limpia.
Cuida la búlgara y recorta el deslizante si el tiempo se estira."""

DIRTY = """Hoy toca trote Z2. Bajá una serie y sumá 10 minutos de prehab.
Acordate de anotar el peso."""


def test_clean_spanish_passes():
    assert language_violation(CLEAN) == []


def test_voseo_imperatives_are_caught():
    assert language_violation(DIRTY) == ["acordate", "bajá", "sumá"]


def test_pronouns_and_present_forms_are_caught():
    assert language_violation("Vos sos el que tenés que decidir.") == ["sos", "tenés", "vos"]


def test_accented_and_unaccented_forms_both_flagged():
    assert language_violation("tenes y podes") == ["podes", "tenes"]


def test_emojis_and_punctuation_do_not_confuse_the_scanner():
    assert language_violation("💪 ¡Sube 8 kg! 🟢 (RIR 2)") == []
