"""Image understanding as a tool: the engine can read image files with a multimodal model.

Verified on 2026-11-24 against the real engine: a session opened with `gemini-4-neon` and asked to read
a 64x64 PNG answered the colour correctly, so this needs no separate provider integration and no API
key of its own — the engine already has both (`docs/migration.md` §4.5).

The tool exists because the profile that needs it most (`freelance`, 145 calls) is also the one that
cannot be migrated without it: a job post screenshot is not text.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

from .adapters.opencode import OpenCodeRuntime
from .errors import ModelRateLimited, ModelUnavailable, Unknown

# Free multimodal model first, paid one as fallback. The free window is shared with everything else on
# the machine, so it runs out; when it does, falling back beats failing a tool call mid-turn.
DEFAULT_MODELS = ("nemotron-3.5-super-vl-free", "gemini-4-neon")
MAX_BYTES = 12 * 1024 * 1024
SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _models() -> tuple[str, ...]:
    override = os.environ.get("NEXO_VISION_MODEL", "").strip()
    return (override,) if override else DEFAULT_MODELS


def resolve(source: str) -> Path:
    """Accept a local path or an http(s) URL. URLs are downloaded to a temp file with a real suffix."""
    if source.startswith(("http://", "https://")):
        suffix = Path(source.split("?")[0]).suffix.lower() or ".png"
        target = Path(tempfile.mkdtemp(prefix="nexo-vision-")) / f"image{suffix}"
        with urllib.request.urlopen(source, timeout=30) as resp:
            data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError(f"imagen demasiado grande (> {MAX_BYTES // 1024 // 1024} MB)")
        target.write_bytes(data)
        return target

    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no existe la imagen: {path}")
    if path.suffix.lower() not in SUFFIXES:
        raise ValueError(f"extensión no soportada: {path.suffix} (esperaba {', '.join(sorted(SUFFIXES))})")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"imagen demasiado grande (> {MAX_BYTES // 1024 // 1024} MB)")
    return path


def analyze(source: str, question: str, model: str | None = None, timeout: float = 120.0) -> dict:
    """Ask a multimodal model about an image. Returns the text, the model used and the resolved path."""
    path = resolve(source)
    try:
        return _analyze(path, question, model, timeout)
    finally:
        # A downloaded image lives in its own temp dir; the engine only needs it during the turn.
        if path.parent.name.startswith("nexo-vision-"):
            shutil.rmtree(path.parent, ignore_errors=True)


def _analyze(path: Path, question: str, model: str | None, timeout: float) -> dict:
    ask = question.strip() or "Describe la imagen y transcribe el texto que se vea."
    prompt = (
        f"Lee el archivo de imagen {path} con tus herramientas y responde en español: {ask}. "
        "No inventes lo que no se ve."
    )

    runtime = OpenCodeRuntime()
    errors = []
    for name in (model,) if model else _models():
        try:
            handle = runtime.open_session(name, title="vision")
            runtime.prompt(handle.id, prompt)
            result = runtime.wait_turn(handle.id, timeout=timeout)
            if not result.text.strip():
                raise Unknown("el modelo devolvió una respuesta vacía")
            return {"text": result.text.strip(), "model": result.model_id, "path": str(path)}
        except (ModelRateLimited, ModelUnavailable) as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            continue
    raise ModelUnavailable(f"ningún modelo de visión respondió ({'; '.join(errors) or 'sin modelos'})")
