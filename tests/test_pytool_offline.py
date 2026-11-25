"""The `python` tool: process isolation, error capture and the truncation that keeps output usable."""

import pytest

from nexo.mcp import handle
from nexo.pytool import render, run


def test_prints_stdout():
    result = run("print(6 * 7)")
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "42"


def test_syntax_error_is_returned_not_raised():
    result = run("def broken(:\n    pass")
    assert result["returncode"] != 0
    assert "SyntaxError" in result["stderr"]


def test_exception_keeps_traceback():
    result = run("raise ValueError('boom')")
    assert result["returncode"] != 0
    assert "ValueError: boom" in result["stderr"]


def test_state_does_not_persist_between_calls():
    run("persisted = 1")
    result = run("print(persisted)")
    assert result["returncode"] != 0
    assert "NameError" in result["stderr"]


def test_timeout_kills_the_process():
    result = run("import time; time.sleep(5)", timeout=1)
    assert result["returncode"] is None
    assert "timeout" in result["stderr"]


def test_cwd_is_respected(tmp_path):
    result = run("import os; print(os.getcwd())", cwd=str(tmp_path))
    assert result["stdout"].strip() == str(tmp_path)


def test_missing_cwd_raises():
    with pytest.raises(FileNotFoundError):
        run("print(1)", cwd="/no/existe")


def test_empty_code_raises():
    with pytest.raises(ValueError):
        run("   ")


def test_output_is_truncated_with_a_marker():
    result = run("print('x' * 60000)")
    assert result["truncated"] is True
    assert "chars omitted" in result["stdout"]


def test_render_shows_exit_code_and_never_empty():
    assert "(sin salida)" in render({"stdout": "", "stderr": "", "returncode": 0, "duration_ms": 3})
    assert "exit 1" in render({"stdout": "", "stderr": "e", "returncode": 1, "duration_ms": 3})


def test_mcp_tools_call_runs_python():
    response = handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                       "params": {"name": "python", "arguments": {"code": "print(sum(range(10)))"}}})
    assert "45" in response["result"]["content"][0]["text"]
    assert response["result"]["isError"] is False


def test_mcp_python_reports_tool_errors_as_is_error():
    response = handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                       "params": {"name": "python", "arguments": {"code": ""}}})
    assert response["result"]["isError"] is True
