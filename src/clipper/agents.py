"""Run a coding-agent CLI headless and get schema-validated JSON back.

Supported: claude (Claude Code), codex (OpenAI Codex CLI), agy (Google Antigravity CLI).
Everyone already has one of these logged in, so there is no API key to share.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 15 * 60
ARGV_SAFE = 28_000  # Windows command lines max out around 32k characters


class AgentError(RuntimeError):
    pass


def _exe(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise AgentError(f"`{name}` CLI not found on PATH. Set `agent:` in config.yaml to one you have.")
    return exe


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise AgentError(f"agent returned no JSON: {text[:300]}")
    return json.loads(m.group(1))


def run_agent(agent: str, prompt: str, schema: dict, model: str = "", workdir: Path | None = None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="clipper-agent-"))
    try:
        if agent == "claude":
            cmd = [_exe("claude"), "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
                   "--tools", "", "--no-session-persistence"]
            if model:
                cmd += ["--model", model]
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               cwd=tmp, timeout=TIMEOUT)
            if p.returncode != 0:
                raise AgentError(f"claude failed: {p.stderr[-800:] or p.stdout[-800:]}")
            out = json.loads(p.stdout)
            if out.get("is_error"):
                raise AgentError(f"claude error: {out.get('result')}")
            return out.get("structured_output") or _extract_json(out.get("result", ""))

        if agent == "codex":
            schema_file, out_file = tmp / "schema.json", tmp / "out.json"
            schema_file.write_text(json.dumps(schema), encoding="utf-8")
            cmd = [_exe("codex"), "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                   "--output-schema", str(schema_file), "-o", str(out_file)]
            if model:
                cmd += ["-m", model]
            cmd.append("-")
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               cwd=tmp, timeout=TIMEOUT)
            if p.returncode != 0 or not out_file.exists():
                raise AgentError(f"codex failed: {p.stderr[-800:] or p.stdout[-800:]}")
            return _extract_json(out_file.read_text(encoding="utf-8"))

        if agent == "agy":
            if len(prompt) > ARGV_SAFE:
                pf = tmp / "pick_prompt.md"
                pf.write_text(prompt, encoding="utf-8")
                arg = f"Read the file {pf} and do exactly what it says. Reply only with the JSON it asks for."
            else:
                arg = prompt
            cmd = [_exe("agy"), "--output-format", "json", "--json-schema", json.dumps(schema)]
            if model:
                cmd += ["--model", model]
            cmd += ["--add-dir", str(tmp), f"--print={arg}"]
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=tmp, timeout=TIMEOUT)
            if p.returncode != 0:
                raise AgentError(f"agy failed: {p.stderr[-800:] or p.stdout[-800:]}")
            out = json.loads(p.stdout)
            if out.get("status") not in (None, "SUCCESS"):
                raise AgentError(f"agy status {out.get('status')}: {out.get('response', '')[:300]}")
            return out.get("structured_output") or _extract_json(out.get("response", ""))

        raise AgentError(f"unknown agent '{agent}' (use claude, codex or agy)")
    except subprocess.TimeoutExpired as e:
        raise AgentError(f"{agent} timed out after {TIMEOUT}s") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
