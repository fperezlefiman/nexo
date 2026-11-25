"""Global rules: they must reach every profile, always, and survive a SOUL that says otherwise."""

from nexo.dispatch import system_prompt_for
from nexo.rules import GLOBAL_RULES


def _channel(**overrides) -> dict:
    base = {"profile_name": "shopping", "system_prompt": "SOUL VIEJO DEL PERFIL."}
    return {**base, **overrides}


def test_rules_are_injected_after_the_soul():
    prompt = system_prompt_for(_channel())
    assert prompt.index("SOUL VIEJO") < prompt.index("Reglas globales") < prompt.index("## Entorno")


def test_every_requested_rule_is_present():
    for fragment in (
        "Español neutro con tuteo",
        "Nunca voseo",
        "Economía de salida",
        "8 líneas o menos",
        "Nada de tablas en Telegram",
        "skill `ponytail`",
        "skill `impeccable`",
        "Nunca inventes",
        "pedí confirmación explícita",
        "America/Santiago",
    ):
        assert fragment in GLOBAL_RULES, fragment


def test_profile_name_and_language_land_in_the_environment_block():
    prompt = system_prompt_for(_channel(profile_name="personal-trainer"))
    assert 'profile="personal-trainer"' in prompt
    assert "español neutro" in prompt


def test_rules_do_not_depend_on_the_profile():
    first = system_prompt_for(_channel(profile_name="finance"))
    second = system_prompt_for(_channel(profile_name="shopping"))
    assert GLOBAL_RULES in first and GLOBAL_RULES in second
