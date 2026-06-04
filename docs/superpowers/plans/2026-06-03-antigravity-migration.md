# Antigravity CLI Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fully replace the Gemini CLI reviewer backend with the Antigravity CLI (`agy`) backend, because Google sunsets Gemini CLI for our auth tier on 2026-06-18.

**Architecture:** The `gemini` backend is renamed to `antigravity` across config, reviewers, processor, CLI, preflight, and web UI. A new `antigravity_cli.py` module invokes `agy -p <prompt> -o json` (prompt mode only; the gemini extension flow is removed). The gemini quota-probe machinery in `backend_usage.py` is deleted. Historical review records keep rendering via legacy `gemini` stage names in `history_server.py` and the web UI.

**Tech Stack:** Python 3.12, pydantic, pytest, uv; React/TypeScript web UI; Docker.

**Spec:** `docs/superpowers/specs/2026-06-03-antigravity-migration-design.md`

**Repo:** `/Users/inkvi/dev/code-reviewer` (all paths relative to repo root)

**Important ordering note:** The backend rename is atomic by nature (config field names are shared between modules). Tasks 1 and 8-11 are independently green; Tasks 2-5 form one rename sweep that is only green at the end of Task 5, where it is committed as one commit. Do not run the full suite between Tasks 2 and 4 and expect green.

---

### Task 1: New antigravity reviewer module (additive, TDD)

**Files:**
- Create: `tests/test_antigravity_cli.py`
- Create: `src/code_reviewer/reviewers/antigravity_cli.py`
- Modify: `src/code_reviewer/reviewers/__init__.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_antigravity_cli.py`:

```python
import asyncio
from pathlib import Path

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.antigravity_cli import (
    _build_antigravity_prompt_command,
    _extract_antigravity_review_text,
    run_antigravity_review,
)


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
    )


def test_build_antigravity_prompt_command_without_model() -> None:
    args = _build_antigravity_prompt_command("Summarize findings", model=None)

    assert args[0] == "agy"
    assert "-p" in args
    prompt_idx = args.index("-p")
    assert args[prompt_idx + 1] == "Summarize findings"
    assert "-o" in args
    output_idx = args.index("-o")
    assert args[output_idx + 1] == "json"
    assert "-m" not in args


def test_build_antigravity_prompt_command_with_model() -> None:
    args = _build_antigravity_prompt_command("Summarize findings", model="agy-default")

    assert args[0] == "agy"
    assert "-m" in args
    model_idx = args.index("-m")
    assert args[model_idx + 1] == "agy-default"


def test_extract_antigravity_review_text_from_stdout() -> None:
    stdout = "### Findings\n- [P2] file.rs:10 - issue"
    stderr = "logs"

    result = _extract_antigravity_review_text(stdout, stderr)
    assert result == stdout


def test_extract_antigravity_review_text_from_json() -> None:
    stdout = '{"response": "### Findings\\n- No material findings."}'
    stderr = ""

    result = _extract_antigravity_review_text(stdout, stderr)
    assert "No material findings" in result


def test_extract_antigravity_review_text_from_multiline_json() -> None:
    stdout = (
        "Loaded cached credentials.\n"
        '{\n  "session_id": "abc",\n'
        '  "response": "### Findings\\n- No material findings."\n}\n'
    )
    stderr = ""

    result = _extract_antigravity_review_text(stdout, stderr)
    assert "No material findings" in result


def test_extract_antigravity_review_text_from_json_with_parts() -> None:
    stdout = '{"parts": [{"text": "agy review content"}]}'
    stderr = ""

    result = _extract_antigravity_review_text(stdout, stderr)
    assert result == "agy review content"


def test_extract_antigravity_review_text_joins_json_parts() -> None:
    stdout = '{"parts": [{"text": "part one"}, {"text": "part two"}]}'
    stderr = ""

    result = _extract_antigravity_review_text(stdout, stderr)
    assert result == "part one\npart two"


def test_extract_antigravity_review_text_empty() -> None:
    result = _extract_antigravity_review_text("", "")
    assert result == ""


def test_run_antigravity_review_errors_without_prompt_path(tmp_path: Path) -> None:
    pr = _sample_pr()

    result = asyncio.run(run_antigravity_review(pr, tmp_path, 45, prompt_path=None))

    assert result.status == "error"
    assert "full_review_prompt_path" in (result.error or "")


def test_run_antigravity_review_uses_prompt_execution(monkeypatch, tmp_path: Path) -> None:
    pr = _sample_pr()
    captured: dict[str, object] = {}
    prompt_path = tmp_path / "full.toml"
    prompt_path.write_text('prompt = "Review {url}"\n', encoding="utf-8")

    async def fake_run_antigravity_prompt(prompt, workspace, timeout_seconds, *, model=None):  # noqa: ANN001
        captured["prompt"] = prompt
        captured["workspace"] = workspace
        captured["timeout_seconds"] = timeout_seconds
        captured["model"] = model
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    monkeypatch.setattr(
        "code_reviewer.reviewers.antigravity_cli.run_antigravity_prompt",
        fake_run_antigravity_prompt,
    )

    result = asyncio.run(
        run_antigravity_review(
            pr,
            tmp_path,
            45,
            model="agy-default",
            prompt_path=str(prompt_path),
        )
    )

    assert result.status == "ok"
    assert result.reviewer == "antigravity"
    assert captured["workspace"] == tmp_path
    assert captured["timeout_seconds"] == 45
    assert captured["model"] == "agy-default"
    assert "Review https://github.com/polymerdao/obul/pull/64" in str(captured["prompt"])
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `cd /Users/inkvi/dev/code-reviewer && uv run pytest tests/test_antigravity_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'code_reviewer.reviewers.antigravity_cli'`

- [ ] **Step 1.3: Create the module**

Create `src/code_reviewer/reviewers/antigravity_cli.py`. This is `gemini_cli.py` adapted: binary `agy`, `-o json` instead of `--approval-mode yolo --output-format json`, no extension mode (prompt_path required), names renamed:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import build_full_review_bundle
from code_reviewer.shell import run_command_async


def _build_antigravity_prompt_command(prompt: str, *, model: str | None) -> list[str]:
    args = [
        "agy",
        "-p",
        prompt,
        "-o",
        "json",
    ]
    if model:
        args.extend(["-m", model])
    return args


def _extract_markdown_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in ("response", "text", "output", "result", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    parts = payload.get("parts")
    if isinstance(parts, list):
        text_parts: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
        if text_parts:
            return "\n".join(text_parts)

    return ""


def _iter_json_payloads(text: str) -> list[object]:
    payloads: list[object] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start == -1:
            break
        try:
            payload, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        payloads.append(payload)
        index = end
    return payloads


def _extract_antigravity_markdown_from_json(stdout: str) -> str:
    """Try to extract review markdown from JSON output."""
    payloads = _iter_json_payloads(stdout)
    for payload in reversed(payloads):
        markdown = _extract_markdown_from_payload(payload)
        if markdown:
            return markdown

    return ""


def _summarize_antigravity_error(stderr: str) -> str:
    """Extract a concise error summary from agy's stderr.

    Pull out a recognizable "Error: message" line and drop the noise.
    The full stderr is still available in ReviewerOutput.stderr.
    """
    lines = stderr.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        if "Error:" in stripped and not stripped.startswith("at "):
            return stripped
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return stderr.strip()[:200]


def _extract_antigravity_review_text(stdout: str, stderr: str) -> str:
    """Extract review text from agy output, trying JSON then plain text."""
    markdown = _extract_antigravity_markdown_from_json(stdout)
    if markdown:
        return markdown

    stdout_text = stdout.strip()
    if stdout_text:
        return stdout_text

    return ""


async def run_antigravity_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
    prompt_path: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)
    prompt_text = ""
    system_prompt_text: str | None = None

    try:
        if prompt_path is None:
            raise RuntimeError(
                "antigravity reviewer requires full_review_prompt_path "
                "(prompt mode is the only supported mode)"
            )
        bundle = build_full_review_bundle(pr, workspace, prompt_path)
        prompt_text = bundle.prompt
        system_prompt_text = bundle.system_prompt
        markdown = await run_antigravity_prompt(
            bundle.prompt,
            workspace,
            timeout_seconds,
            model=model,
        )
        stdout = markdown
        stderr = ""
        status = "ok"
        error = None
    except TimeoutError:
        stdout = ""
        stderr = f"agy review timed out after {timeout_seconds}s"
        status = "error"
        error = stderr
        markdown = ""
    except Exception as exc:  # noqa: BLE001
        stdout = ""
        stderr = str(exc)
        status = "error"
        error = str(exc)
        markdown = ""

    ended = datetime.now(UTC)
    return ReviewerOutput(
        reviewer="antigravity",
        status=status,
        markdown=markdown,
        stdout=stdout,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
        prompt=prompt_text,
        system_prompt=system_prompt_text,
    )


async def run_antigravity_prompt(
    prompt: str,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
) -> str:
    try:
        code, raw_stdout, stderr = await run_command_async(
            _build_antigravity_prompt_command(prompt, model=model),
            cwd=workspace,
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise RuntimeError(f"agy prompt timed out after {timeout_seconds}s") from exc

    if code != 0:
        detail = _summarize_antigravity_error(stderr)
        if not detail:
            detail = raw_stdout.strip()[:500] or "(no output)"
        raise RuntimeError(f"agy exited with status {code}: {detail}")

    markdown = _extract_antigravity_review_text(raw_stdout, stderr)
    if not markdown:
        raise RuntimeError("Antigravity returned an empty response")
    return markdown
```

Note vs gemini_cli.py: the stderr-marker fallback in `_extract_gemini_review_text` (scanning for a bare `gemini` line) is gemini-CLI-specific output behavior and is intentionally dropped.

- [ ] **Step 1.4: Export from the reviewers package**

In `src/code_reviewer/reviewers/__init__.py`, next to the existing gemini import/export, add:

```python
from code_reviewer.reviewers.antigravity_cli import run_antigravity_review
```

and add `"run_antigravity_review",` to `__all__`. Do NOT remove the gemini export yet (consumers still import it until Task 3).

- [ ] **Step 1.5: Run tests to verify they pass**

Run: `cd /Users/inkvi/dev/code-reviewer && uv run pytest tests/test_antigravity_cli.py -v`
Expected: PASS (all)

- [ ] **Step 1.6: Commit**

```bash
cd /Users/inkvi/dev/code-reviewer
git add tests/test_antigravity_cli.py src/code_reviewer/reviewers/antigravity_cli.py src/code_reviewer/reviewers/__init__.py
git commit -m "feat: add antigravity (agy) reviewer backend module"
```

---

### Task 2: Config rename

**Files:**
- Modify: `src/code_reviewer/config.py`

- [ ] **Step 2.1: Rename backend name and fields**

In `src/code_reviewer/config.py`:

1. Line 10: `_ALLOWED_BACKENDS = {"claude", "codex", "antigravity", "opencode"}`
2. Line 31 (error message in `_normalize_backend_list`): `f"{field_name} entries must be one of: claude, codex, antigravity, opencode"`
3. Lines 62-64, replace:
   ```python
   antigravity_model: str | None = None
   antigravity_fallback_model: str | None = None
   antigravity_timeout_seconds: int = Field(default=900, ge=30)
   ```
4. Line 86: `triage_backend: list[str] = Field(default_factory=lambda: ["antigravity"])`
5. Line 87: `triage_model: str | None = None` (drop the `gemini-3-flash-preview` default; agy model identifiers differ)
6. Line 91: `lightweight_review_backend: list[str] = Field(default_factory=lambda: ["antigravity"])`
7. Line 92: `lightweight_review_model: str | None = None`
8. Line 157: `allowed = {"claude", "codex", "antigravity", "opencode"}`
9. Line 164 error message: `"enabled_reviewers entries must be one of: claude, codex, antigravity, opencode"`
10. Rename validators `validate_gemini_model` -> `validate_antigravity_model` (decorator `@field_validator("antigravity_model")`, message `"antigravity_model cannot be empty"`) and `validate_gemini_fallback_model` -> `validate_antigravity_fallback_model` (decorator `@field_validator("antigravity_fallback_model")`, message `"antigravity_fallback_model cannot be empty"`).

- [ ] **Step 2.2: Verify no gemini remnants in config.py**

Run: `grep -n -i gemini src/code_reviewer/config.py`
Expected: no output. Do NOT run the test suite yet (consumers are renamed in Tasks 3-4).

---

### Task 3: Rename in triage, lightweight, reconcile

**Files:**
- Modify: `src/code_reviewer/reviewers/triage.py`
- Modify: `src/code_reviewer/reviewers/lightweight.py`
- Modify: `src/code_reviewer/reviewers/reconcile.py`
- Modify: `src/code_reviewer/reviewers/__init__.py`

All three files have the identical gemini pattern: an import, a circuit-breaker model-resolution preamble, a `if b == "gemini":` branch with fallback-model retry, and a `models_map` comprehension. The transformation is the same in each file.

- [ ] **Step 3.1: triage.py**

1. Replace import:
   ```python
   from code_reviewer.reviewers.antigravity_cli import run_antigravity_prompt
   ```
   (removes the `gemini_cli` import)
2. Signature of `run_triage`: `backend: list[str] | str = "antigravity"` and rename kwarg `gemini_fallback_model: str | None = None` -> `antigravity_fallback_model: str | None = None`.
3. Replace the model-resolution preamble (currently lines 108-114):
   ```python
   # Resolve effective antigravity model: if primary's circuit is open, use fallback
   _base_antigravity_model = model if backends[0] == "antigravity" else None
   _effective_antigravity_model = _base_antigravity_model
   if antigravity_fallback_model and antigravity_fallback_model != _base_antigravity_model:
       opened, _ = _cb_is_open("antigravity", _base_antigravity_model)
       if opened:
           _effective_antigravity_model = antigravity_fallback_model
   ```
4. Replace the `if b == "gemini":` branch inside `_try`:
   ```python
   if b == "antigravity":
       use_model = _effective_antigravity_model
       try:
           return await run_antigravity_prompt(
               prompt,
               workspace,
               timeout_seconds,
               model=use_model,
           )
       except RuntimeError as exc:
           if (
               antigravity_fallback_model
               and use_model != antigravity_fallback_model
               and "reset after" in str(exc)
           ):
               _cb_record_failure("antigravity", use_model, exc)
               fb_opened, _ = _cb_is_open("antigravity", antigravity_fallback_model)
               if not fb_opened:
                   log.info(
                       "retrying antigravity triage with fallback model %s %s",
                       antigravity_fallback_model,
                       pr.url,
                   )
                   return await run_antigravity_prompt(
                       prompt,
                       workspace,
                       timeout_seconds,
                       model=antigravity_fallback_model,
                   )
           raise
   ```
5. Replace the `models_map` comprehension:
   ```python
   models_map[b] = (
       _effective_antigravity_model
       if b == "antigravity"
       else (model if b == backends[0] else None)
   )
   ```

- [ ] **Step 3.2: lightweight.py**

Same five transformations as Step 3.1 applied to `run_lightweight_review` (the branch returns `text, None` tuples; log message says `"retrying antigravity lightweight review with fallback model %s %s"`). The kwarg renames from `gemini_fallback_model` to `antigravity_fallback_model`.

- [ ] **Step 3.3: reconcile.py**

Same five transformations applied to `reconcile_reviews` (uses `reconciler_model` instead of `model` in the preamble, `t = _timeout_for(b)` for the timeout, log message `"retrying antigravity reconcile with fallback model %s %s"`). Kwarg renames to `antigravity_fallback_model`.

- [ ] **Step 3.4: Drop gemini export from reviewers/__init__.py**

Remove the `from code_reviewer.reviewers.gemini_cli import run_gemini_review` line and `"run_gemini_review"` from `__all__` (added back in Task 1 context: `run_antigravity_review` stays).

- [ ] **Step 3.5: Verify**

Run: `grep -rn -i gemini src/code_reviewer/reviewers/triage.py src/code_reviewer/reviewers/lightweight.py src/code_reviewer/reviewers/reconcile.py src/code_reviewer/reviewers/__init__.py`
Expected: no output.

---

### Task 4: Rename in processor.py, cli.py, preflight.py

**Files:**
- Modify: `src/code_reviewer/processor.py`
- Modify: `src/code_reviewer/cli.py`
- Modify: `src/code_reviewer/preflight.py`

- [ ] **Step 4.1: processor.py**

1. Import: `run_gemini_review` -> `run_antigravity_review` (from `code_reviewer.reviewers`).
2. `_usage_snapshot_for_model` (line ~231): the gemini special-case becomes dead after backend_usage cleanup; simplify to always return `snapshot`:
   ```python
   def _usage_snapshot_for_model(
       snapshot: BackendUsageSnapshot,
       model: str | None,
   ) -> BackendUsageSnapshot | None:
       return snapshot
   ```
   (Keep the function so call sites stay unchanged.)
3. Rename `_resolve_gemini_review_model` -> `_resolve_antigravity_review_model`; inside, replace `config.gemini_model`/`config.gemini_fallback_model` with the antigravity fields, `"gemini"` string literals with `"antigravity"`, and log words `Gemini` -> `Antigravity`.
4. `_resolve_reconciler_settings`: `elif primary == "gemini":` -> `elif primary == "antigravity":` with `model = config.reconciler_model or config.antigravity_model`; timeout map `elif b == "gemini": backend_timeouts[b] = config.gemini_timeout_seconds` -> `elif b == "antigravity": backend_timeouts[b] = config.antigravity_timeout_seconds`.
5. `_build_review_meta` (line ~459): `elif name == "gemini":` -> `elif name == "antigravity":` using `config.antigravity_model` / `config.antigravity_fallback_model`.
6. Both full-review start blocks (lines ~698-717 and ~959-975): rename the `"gemini"` keys/strings to `"antigravity"`, `gemini_available, gemini_model` locals to `antigravity_available, antigravity_model`, call `run_antigravity_review(...)` with `config.antigravity_timeout_seconds`, log messages `Gemini` -> `Antigravity`.
7. Mid-review fallback restart block (lines ~811-832): same renames (`reviewer_name == "antigravity"`, `config.antigravity_fallback_model`, `_circuit_is_open("antigravity", ...)`, `run_antigravity_review`, `config.antigravity_timeout_seconds`).
8. Anywhere `run_triage`/`run_lightweight_review`/`reconcile_reviews` are called with `gemini_fallback_model=config.gemini_fallback_model`, rename the kwarg and field to `antigravity_fallback_model=config.antigravity_fallback_model`.

9. Verify: `grep -n -i gemini src/code_reviewer/processor.py` -> no output.

- [ ] **Step 4.2: cli.py**

Mechanical rename throughout:
- `--gemini-model` -> `--antigravity-model`, `--gemini-fallback-model` -> `--antigravity-fallback-model` (option names and help strings)
- function params / locals `gemini_model` -> `antigravity_model`, `gemini_fallback_model` -> `antigravity_fallback_model`
- type aliases `GeminiModelOption`/`GeminiFallbackModelOption` -> `AntigravityModelOption`/`AntigravityFallbackModelOption` (check their definitions near the imports)
- `_apply_field_override(config, "gemini_model", ...)` -> `"antigravity_model"` etc.
- help text `Allowed: claude, codex, gemini, opencode` -> `Allowed: claude, codex, antigravity, opencode`
- line ~329: `model = config.reconciler_model or config.gemini_model` -> `config.antigravity_model`
- line ~611: `reconciler_primary != "gemini"` -> `!= "antigravity"`
- line ~619 summary table: `table.add_row("Antigravity model", cfg.antigravity_model or "default")`

Verify: `grep -n -i gemini src/code_reviewer/cli.py` -> no output.

- [ ] **Step 4.3: preflight.py**

1. Delete `_GEMINI_CODE_REVIEW_EXTENSION = "code-review"` (line 11).
2. Replace `uses_gemini_cli` block (lines 35-40) with:
   ```python
   uses_antigravity_cli = (
       "antigravity" in enabled
       or "antigravity" in reconciler_backends
       or "antigravity" in triage_backends
       or "antigravity" in lightweight_backends
   )
   ```
3. Delete line 47 (`uses_gemini_extension_review = ...`) and replace with the prompt-path guard:
   ```python
   if "antigravity" in enabled and config.full_review_prompt_path is None:
       raise RuntimeError(
           "Antigravity reviewer requires full_review_prompt_path to be set "
           "(prompt mode is the only supported mode)."
       )
   ```
4. `required.append("gemini")` -> `required.append("agy")` (guarded by `uses_antigravity_cli`).
5. Replace the version check and DELETE the whole extension-check block (lines 136-159):
   ```python
   if uses_antigravity_cli:
       run_command(["agy", "--version"])
   ```

Verify: `grep -n -i gemini src/code_reviewer/preflight.py` -> no output.

- [ ] **Step 4.4: Sweep check on src (excluding allowed remnants)**

Run: `grep -rn -i gemini src/code_reviewer/ | grep -v history_server.py | grep -v backend_usage.py`
Expected: no output (history_server and backend_usage are handled in Tasks 6-7).

---

### Task 5: Update tests for the rename and get the suite green

**Files:**
- Delete: `src/code_reviewer/reviewers/gemini_cli.py`, `tests/test_gemini_cli.py`
- Modify: `tests/test_config.py`, `tests/test_triage.py`, `tests/test_lightweight.py`, `tests/test_reconcile.py`, `tests/test_processor.py`, `tests/test_cli.py`, `tests/test_preflight.py`, `tests/test_fallback.py`, `tests/test_circuit_breaker.py`, `tests/test_local_review.py` (any file the grep below lists)

- [ ] **Step 5.1: Delete the gemini module and its tests**

```bash
cd /Users/inkvi/dev/code-reviewer
git rm src/code_reviewer/reviewers/gemini_cli.py tests/test_gemini_cli.py
```

- [ ] **Step 5.2: Update each test file**

List affected files: `grep -rln -i gemini tests/`

For each (except `test_backend_usage.py` and `test_history_server.py`, handled in Tasks 6-7), apply the same renames as the source:
- backend name strings `"gemini"` -> `"antigravity"`
- config fields/kwargs `gemini_model`/`gemini_fallback_model`/`gemini_timeout_seconds` -> `antigravity_*`
- monkeypatch targets `code_reviewer.reviewers.gemini_cli.*` / `...run_gemini_prompt` -> `code_reviewer.reviewers.antigravity_cli.*` / `...run_antigravity_prompt` (note: triage/lightweight/reconcile import `run_antigravity_prompt` into their own namespace, so patches like `code_reviewer.reviewers.triage.run_gemini_prompt` become `code_reviewer.reviewers.triage.run_antigravity_prompt`)
- any test asserting the old default `triage_model == "gemini-3-flash-preview"` now asserts `is None`
- any test asserting allowed-backend error messages updates to `claude, codex, antigravity, opencode`

- [ ] **Step 5.3: Run the full suite**

Run: `cd /Users/inkvi/dev/code-reviewer && uv run pytest -x -q`
Expected: PASS except possibly `test_backend_usage.py` / `test_history_server.py` gemini cases (handled next; if they fail, proceed to Tasks 6-7 before committing, then re-run).

- [ ] **Step 5.4: Commit the rename sweep**

```bash
cd /Users/inkvi/dev/code-reviewer
git add -u src/ tests/
git commit -m "feat!: replace gemini backend with antigravity (agy) across config, reviewers, processor, cli, preflight"
```

---

### Task 6: Remove gemini quota machinery from backend_usage.py

**Files:**
- Modify: `src/code_reviewer/backend_usage.py`
- Modify: `tests/test_backend_usage.py`

- [ ] **Step 6.1: Strip gemini support**

In `src/code_reviewer/backend_usage.py`:
1. `_SUPPORTED_BACKENDS = {"claude", "codex", "opencode"}`
2. Delete `_GEMINI_AUTH_TYPE_MAP` and `_GEMINI_QUOTA_PROBE_SCRIPT` (lines 17-65).
3. Delete every function whose name contains `gemini` (`_default_gemini_home`, `_load_gemini_settings`, `_find_gemini_core_module`, `_load_gemini_quota_payload`, and any `gemini` branch in `load_backend_usage_snapshot` / snapshot dispatch — find them with `grep -n -i gemini src/code_reviewer/backend_usage.py` and remove each block plus its call sites).
4. After editing: `grep -n -i gemini src/code_reviewer/backend_usage.py` -> no output.

With `"antigravity"` not in `_SUPPORTED_BACKENDS`, `load_backend_usage_snapshot("antigravity")` raises `ValueError`, which `_backend_has_available_usage` in processor.py already catches as "usage check unavailable -> proceed". No antigravity-specific code needed.

- [ ] **Step 6.2: Update tests**

In `tests/test_backend_usage.py`: delete gemini-specific test cases; if a test asserts the supported-backend set or unsupported-backend error, update it (e.g. asserting `load_backend_usage_snapshot("gemini")` now raises `ValueError`; keep/adjust per existing test style).

- [ ] **Step 6.3: Run tests and commit**

Run: `uv run pytest tests/test_backend_usage.py tests/test_processor.py -q`
Expected: PASS

```bash
git add -u src/code_reviewer/backend_usage.py tests/test_backend_usage.py
git commit -m "refactor: drop gemini quota probe machinery from backend_usage"
```

---

### Task 7: history_server stage names (keep legacy)

**Files:**
- Modify: `src/code_reviewer/history_server.py`
- Modify: `tests/test_history_server.py`

- [ ] **Step 7.1: Add antigravity stages, keep gemini for old records**

In `src/code_reviewer/history_server.py`:
1. Line 29 area (stage list containing `"gemini"`): add `"antigravity",` alongside it. Keep `"gemini"`.
2. Line 36 area (`"gemini.prompt"`): add `"antigravity.prompt",`. Keep `"gemini.prompt"`.
3. Line 101: `if any(s in stages for s in ("claude", "codex", "gemini", "antigravity", "opencode")):`

- [ ] **Step 7.2: Add a regression test**

In `tests/test_history_server.py`, follow the existing stage-listing test pattern and add a case asserting a record with an `antigravity` stage is categorized the same way a `gemini` one is (copy the nearest existing gemini stage test, duplicate for antigravity; keep the gemini test as the legacy-records guarantee).

- [ ] **Step 7.3: Run tests and commit**

Run: `uv run pytest tests/test_history_server.py -q`
Expected: PASS

```bash
git add -u src/code_reviewer/history_server.py tests/test_history_server.py
git commit -m "feat: recognize antigravity stages in history server (keep gemini for old records)"
```

---

### Task 8: Web UI badge and reviewer display

**Files:**
- Modify: `web/src/components/Badge.tsx:64`
- Modify: `web/src/pages/PRDetail.tsx:102-108`

- [ ] **Step 8.1: Badge color**

In `web/src/components/Badge.tsx` line 64, alongside `gemini: "green",` add:

```ts
    antigravity: "green",
```

(Keep `gemini` so historical records render.)

- [ ] **Step 8.2: Reviewer display entry**

In `web/src/pages/PRDetail.tsx`, after the `gemini` entry (lines 102-108), add an `antigravity` entry reusing the existing icon (it is Google's mark):

```ts
  antigravity: {
    label: "Antigravity",
    // same visual family as Gemini; reuse the Google four-pointed star mark
    icon: GeminiIcon,
  },
```

Match the exact field shape of the `gemini` entry above it (copy all fields it defines, e.g. colors, changing only the label).

- [ ] **Step 8.3: Build check and commit**

Run: `cd /Users/inkvi/dev/code-reviewer/web && npm run build`
Expected: build succeeds.

```bash
cd /Users/inkvi/dev/code-reviewer
git add web/src/components/Badge.tsx web/src/pages/PRDetail.tsx
git commit -m "feat(web): display antigravity reviewer (keep gemini for history)"
```

---

### Task 9: Dockerfile

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 9.1: Swap gemini install for agy**

1. Line 21 comment: `# Install Node.js (required for Claude, Codex CLIs)`
2. Delete lines 32-33:
   ```dockerfile
   # Install Gemini CLI
   RUN npm install -g @google/gemini-cli
   ```
3. Delete the extension block (lines 67-70, after `USER 1000`):
   ```dockerfile
   # Install Gemini code-review extension (clone directly to avoid CLI bugs in Docker)
   RUN mkdir -p /home/appuser/.gemini/extensions \
       && git clone https://github.com/gemini-cli-extensions/code-review /home/appuser/.gemini/extensions/code-review \
       && printf '{"code-review":{"overrides":["/*"]}}\n' > /home/appuser/.gemini/extensions/extension-enablement.json
   ```
4. In its place (still after `USER 1000`), add:
   ```dockerfile
   # Install Antigravity CLI (agy) and seed headless-safe settings
   RUN curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --skip-aliases --skip-path \
       && mkdir -p /home/appuser/.gemini/antigravity-cli \
       && printf '{"toolPermission": "always-proceed", "enableTelemetry": false}\n' \
           > /home/appuser/.gemini/antigravity-cli/settings.json
   ENV PATH="/home/appuser/.local/bin:${PATH}"
   ```

- [ ] **Step 9.2: Build and verify**

Run: `cd /Users/inkvi/dev/code-reviewer && docker build -t code-reviewer:agy-test . && docker run --rm --entrypoint agy code-reviewer:agy-test --version`
Expected: image builds; `agy --version` prints a version string. If the install script needs different flags or target dir, adapt (check `curl -fsSL https://antigravity.google/cli/install.sh | head -50` for its options) — the invariant is: `agy` on PATH for UID 1000 and settings.json seeded.

- [ ] **Step 9.3: Commit**

```bash
git add Dockerfile
git commit -m "build: install antigravity CLI (agy) instead of gemini CLI"
```

---

### Task 10: Docs and example config

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `agents.md`, `config.example.toml`

- [ ] **Step 10.1: config.example.toml**

- Replace the gemini tuning block (lines ~27-33) with:
  ```toml
  # Optional Antigravity tuning (requires `agy` CLI installed/authenticated).
  # To enable: add "antigravity" to enabled_reviewers, e.g. ["claude", "codex", "antigravity"]
  # antigravity_model = ""        # leave unset to use the agy default model
  # antigravity_fallback_model = ""  # used when primary model hits quota limits
  antigravity_timeout_seconds = 900
  ```
- Backend list examples (lines 13, 17, 68-70, 77-79): `"gemini"` -> `"antigravity"`; drop `triage_model`/`lightweight_review_model` gemini values (comment them out as unset-by-default).
- Note: the antigravity reviewer requires `full_review_prompt_path` to be set.

- [ ] **Step 10.2: README.md**

- Prerequisites: replace the gemini line with `agy` authenticated (Antigravity CLI) — drop the extension install note.
- Line ~208 (gemini extension behavior note): replace with a note that antigravity only supports prompt execution and requires `full_review_prompt_path`.
- Flow diagram (line ~343 area): relabel the Gemini node to Antigravity.
- Update any backend lists mentioning gemini.

- [ ] **Step 10.3: CLAUDE.md and agents.md**

- Reviewer module list: `gemini_cli` -> `antigravity_cli`.
- Runner list: `run_gemini_prompt` -> `run_antigravity_prompt`.
- Backend support line: claude/codex/antigravity/opencode.
- Dockerfile notes (lines ~63-64): replace gemini CLI + extension-clone notes with the agy install + settings seeding note.

- [ ] **Step 10.4: Sweep, verify, commit**

Run: `grep -rn -i gemini README.md CLAUDE.md agents.md config.example.toml`
Expected: no output (or only intentional historical mentions).

Run: `uv run pytest -q`
Expected: PASS (config.example.toml is referenced by tests in some repos — confirm nothing broke).

```bash
git add -u README.md CLAUDE.md agents.md config.example.toml
git commit -m "docs: document antigravity backend, drop gemini"
```

---

### Task 11: Final verification

- [ ] **Step 11.1: Full repo sweep**

```bash
cd /Users/inkvi/dev/code-reviewer
grep -rn -i gemini src/ tests/ web/src/ Dockerfile README.md CLAUDE.md agents.md config.example.toml
```

Expected remnants ONLY:
- `history_server.py`: legacy `"gemini"` / `"gemini.prompt"` stage names
- `tests/test_history_server.py`: legacy-stage test
- `web/src/components/Badge.tsx` + `web/src/pages/PRDetail.tsx`: legacy gemini display entries + `GeminiIcon` const
- `Dockerfile`: the `.gemini/antigravity-cli` path (agy's own config dir name)

Anything else is a missed rename — fix it.

- [ ] **Step 11.2: Full suite + lint**

```bash
uv run pytest -q
uv run ruff check src/ tests/
```

Expected: both PASS.

- [ ] **Step 11.3: Local smoke test (requires agy installed + authenticated locally)**

```bash
agy -p "say hi" -o json
```

Expected: JSON output containing a response. Then verify the JSON shape against `_extract_antigravity_review_text` expectations: the parser scans any JSON object for `response`/`text`/`output`/`result`/`content` string keys with a plain-text fallback. If agy's actual schema nests differently (e.g. under a `message` key), extend the key tuple in `_extract_markdown_from_payload` and add a matching test case in `tests/test_antigravity_cli.py` using real captured output.

---

## Out of scope (infra repo, separate PR — do not do here)

1. `infra/overlays/devnet/configmaps/code-reviewer/config.toml`: backend chains gemini -> antigravity, `gemini_timeout_seconds` -> `antigravity_timeout_seconds`, drop `triage_model`/`lightweight_review_model` pins. Must deploy together with the new image (breaking config rename).
2. `infra/overlays/devnet/code-reviewer.yaml` auth-setup init container: remove gemini extension seeding; seed `/auth-data/gemini/antigravity-cli/settings.json` (the PVC mount shadows the image's `.gemini`, so the Dockerfile-seeded settings are invisible at runtime — the init container must write it on the PVC).
3. In-pod agy auth via the remote URL+code flow; verify token persistence without an OS keyring.
4. `infra/docs/code-reviewer.md`: agy re-auth procedure; fix stale PVC names.
