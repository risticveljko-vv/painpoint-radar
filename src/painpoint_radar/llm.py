"""llm: one schema-checked completion call with a disk cache, for the extractor,
clusterer and sentiment rubric. Standard library only; the model runs behind a CLI.

Backends
  claude   `claude -p --json-schema`   (default outside a Claude Code session; uses the Claude subscription)
  codex    `codex exec --output-schema` (default inside a Claude Code session, where `claude -p` refuses to nest)

Environment
  PAINPOINT_RADAR_LLM        claude | codex | auto (default auto)
  PAINPOINT_RADAR_LLM_MODEL  model name for the chosen backend (claude: haiku, codex: gpt-6-astra)
  PAINPOINT_RADAR_CACHE      cache root (default ~/.cache/painpoint-radar; LLM outputs under llm/)

CLI
  painpoint-radar llm --selftest            one tiny call, prints backend, model, seconds, parsed JSON
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_MODEL = {"claude": "haiku", "codex": "gpt-6-astra"}
CACHE_DIR = Path(os.environ.get("PAINPOINT_RADAR_CACHE", Path.home() / ".cache" / "painpoint-radar")) / "llm"


class LLMError(RuntimeError):
    pass


def pick_backend(name: str | None = None) -> str:
    name = (name or os.environ.get("PAINPOINT_RADAR_LLM") or "auto").lower()
    if name == "auto":
        name = "codex" if os.environ.get("CLAUDECODE") else "claude"
    if name not in DEFAULT_MODEL:
        raise LLMError(f"unknown backend {name!r}; use claude or codex")
    if shutil.which(name) is None:
        raise LLMError(f"backend {name!r} chosen but the `{name}` binary is not on PATH")
    return name


def _key(backend: str, model: str, schema: dict, prompt: str) -> str:
    h = hashlib.sha256()
    for part in (backend, model, json.dumps(schema, sort_keys=True), prompt):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _run(cmd: list[str], stdin: str, timeout: int, cwd: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)


def _claude(prompt: str, schema: dict, model: str, timeout: int) -> dict:
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--strict-mcp-config", "--mcp-config", "{\"mcpServers\": {}}", "--disable-slash-commands",
           "--setting-sources", "user", "--json-schema", json.dumps(schema)]
    p = _run(cmd, prompt, timeout)
    if p.returncode != 0:
        raise LLMError(f"claude exit {p.returncode}: {p.stderr.strip()[:400] or p.stdout.strip()[:400]}")
    try:
        envelope = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"claude returned non-JSON: {p.stdout[:200]!r}") from e
    out = envelope.get("structured_output")
    if out is None and isinstance(envelope.get("result"), str):
        try:
            out = json.loads(envelope["result"])
        except json.JSONDecodeError:
            out = None
    if not isinstance(out, dict):
        raise LLMError(f"claude gave no structured output (is_error={envelope.get('is_error')}, subtype={envelope.get('subtype')})")
    return out


def _codex(prompt: str, schema: dict, model: str, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="painpoint-radar-llm-") as d:
        schema_path = Path(d) / "schema.json"
        out_path = Path(d) / "last.json"
        schema_path.write_text(json.dumps(schema))
        cmd = ["codex", "exec", "-m", model, "-s", "read-only", "--skip-git-repo-check", "--ephemeral",
               "--output-schema", str(schema_path), "-o", str(out_path), "-C", d, "-"]
        p = _run(cmd, prompt, timeout, cwd=d)
        if p.returncode != 0:
            raise LLMError(f"codex exit {p.returncode}: {p.stderr.strip()[-400:] or p.stdout.strip()[-400:]}")
        if not out_path.exists():
            raise LLMError("codex wrote no last message")
        text = out_path.read_text().strip()
    try:
        out = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"codex last message is not JSON: {text[:200]!r}") from e
    if not isinstance(out, dict):
        raise LLMError("codex output is not a JSON object")
    return out


def complete(prompt: str, schema: dict, backend: str | None = None, model: str | None = None,
             use_cache: bool = True, timeout: int = 900, retries: int = 1) -> dict:
    """Return the model's JSON object for `prompt`, constrained to `schema`. Cached on disk by
    (backend, model, schema, prompt). Raises LLMError after `retries` extra attempts."""
    backend = pick_backend(backend)
    model = model or os.environ.get("PAINPOINT_RADAR_LLM_MODEL") or DEFAULT_MODEL[backend]
    key = _key(backend, model, schema, prompt)
    cache_file = CACHE_DIR / f"{key}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())
    call = _claude if backend == "claude" else _codex
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            out = call(prompt, schema, model, timeout)
            break
        except (LLMError, subprocess.TimeoutExpired) as e:  # noqa: PERF203
            last = e
            if attempt < retries:
                time.sleep(2)
    else:
        raise LLMError(f"{backend}/{model} failed after {retries + 1} attempts: {last}")
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar llm", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    a = ap.parse_args(argv)
    if not a.selftest:
        ap.print_help()
        return 0
    schema = {"type": "object", "additionalProperties": False, "required": ["answer", "confidence"],
              "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}}}
    backend = pick_backend(a.backend)
    model = a.model or os.environ.get("PAINPOINT_RADAR_LLM_MODEL") or DEFAULT_MODEL[backend]
    t0 = time.time()
    out = complete("What protocol do mail clients use to fetch mail from a server? One word.", schema,
                   backend=backend, model=model, use_cache=False)
    print(json.dumps({"backend": backend, "model": model, "seconds": round(time.time() - t0, 1), "output": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
