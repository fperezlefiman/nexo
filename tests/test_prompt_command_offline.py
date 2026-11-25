"""Prompt jobs that carry a command: the script runs first and its output lands in the prompt."""

import nexo.jobs as jobs


def _job(payload="Filtrá esto.", command="echo hola"):
    return {"payload": payload, "command": command}


def test_command_output_is_appended_to_the_prompt():
    merged = jobs._merge_command_output(_job())
    assert merged.startswith("Filtrá esto.")
    assert "=== SALIDA DEL COMANDO (echo hola) ===" in merged
    assert merged.rstrip().endswith("hola")


def test_stderr_is_kept_so_the_agent_can_report_the_failure():
    merged = jobs._merge_command_output(_job(command="echo boom >&2; exit 3"))
    assert "[stderr]" in merged
    assert "boom" in merged


def test_a_broken_command_does_not_raise():
    merged = jobs._merge_command_output(_job(command="exit 1"))
    assert "=== SALIDA DEL COMANDO" in merged


def test_timeout_is_reported_inside_the_prompt(monkeypatch):
    monkeypatch.setattr(jobs, "SCRIPT_TIMEOUT", 1)
    merged = jobs._merge_command_output(_job(command="sleep 3"))
    assert "no terminó" in merged


def test_long_output_is_truncated_with_a_marker():
    merged = jobs._merge_command_output(_job(command="seq 1 5000"), limit=200)
    assert "salida truncada" in merged
    assert len(merged) < 1200
