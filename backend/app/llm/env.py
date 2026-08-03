"""Zero-dependency .env loader.

The sim's core stays import-light, so this is a ~30-line parser rather than a
python-dotenv call. Semantics that matter:

  * **fill, don't override** -- a key already present in the real environment
    wins over the file (so `AI_TOWN_LIVE=1 uvicorn ...` beats a stale `.env`).
  * **idempotent** -- safe to call from the factory, the server and the script;
    only the first call touches disk.

Recognized keys (all optional): AI_TOWN_LIVE, GROQ_API_KEY, GEMINI_API_KEY,
OPENAI_API_KEY, AI_TOWN_LANG, AI_TOWN_BUDGET_USD, AI_TOWN_DB_URL.
"""

from __future__ import annotations

import os
from pathlib import Path

# repo root = backend/app/llm/env.py -> parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_loaded = False


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # strip a single layer of matching quotes; leave inner content intact
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_env(path: str | os.PathLike | None = None, *, override: bool = False) -> bool:
    """Load ``.env`` into ``os.environ``, filling only missing keys unless
    ``override``. Returns True if a file was read. Idempotent for the default
    path (later calls are no-ops)."""
    global _loaded
    if path is None:
        if _loaded:
            return False
        env_path = _REPO_ROOT / ".env"
    else:
        env_path = Path(path)

    if not env_path.is_file():
        if path is None:
            _loaded = True
        return False

    for key, value in _parse(env_path.read_text(encoding="utf-8")).items():
        if override or key not in os.environ:
            os.environ[key] = value

    if path is None:
        _loaded = True
    return True
