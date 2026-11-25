"""Run Python on behalf of the agent, like `execute_code` did before the migration.

`bash` already runs `python3 -c`, so this is not a new capability — it is the *ergonomic* one. In
`freelance`, `execute_code` was 2.290 of 7.001 tool calls (a third): multi-line analysis with quotes,
heredocs and escaping fails often enough that the model re-tries, and each retry is a whole turn.

What this is NOT: a persistent kernel. Every call is a fresh process, so variables do not survive
between calls. That is stated in the tool description, because a model that assumes persistence writes
one long script on the second call and gets a NameError it cannot explain.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 300.0
MAX_OUTPUT = 20_000


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT:
        return text, False
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n… [{len(text) - MAX_OUTPUT} chars omitted] …\n{text[-half:]}", True


def run(code: str, timeout: float | None = None, cwd: str | None = None) -> dict:
    """Execute `code` with the interpreter of this process (the project venv) and capture everything."""
    if not code or not code.strip():
        raise ValueError("code is empty")

    limit = DEFAULT_TIMEOUT if timeout is None else max(1.0, min(float(timeout), MAX_TIMEOUT))
    workdir = Path(cwd).expanduser() if cwd else Path.home()
    if not workdir.is_dir():
        raise FileNotFoundError(f"cwd no existe: {workdir}")

    with tempfile.TemporaryDirectory(prefix="nexo-py-") as tmp:
        script = Path(tmp) / "snippet.py"
        script.write_text(code, encoding="utf-8")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "NEXO_PY_TOOL": "1"}
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(workdir), env=env, capture_output=True, text=True, timeout=limit,
            )
            stdout, out_cut = _truncate(proc.stdout or "")
            stderr, err_cut = _truncate(proc.stderr or "")
            return {
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": out_cut or err_cut,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        except subprocess.TimeoutExpired:
            return {
                "returncode": None,
                "stdout": "",
                "stderr": f"timeout después de {limit:.0f}s: el proceso se mató",
                "truncated": False,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }


def render(result: dict) -> str:
    """A compact text form: the model reads this, so it must not bury the output in JSON noise."""
    body = result["stdout"] or ""
    if result["stderr"]:
        body += ("\n" if body else "") + "[stderr]\n" + result["stderr"]
    if not body.strip():
        body = "(sin salida)"
    return f"{body}\n--- exit {result['returncode']} · {result['duration_ms']} ms"
