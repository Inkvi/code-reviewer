# Triage-First Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the hard-skip logic with a triage-first pipeline that routes simple PRs to a lightweight checklist review and complex PRs to the full multi-reviewer + reconciler pipeline.

**Architecture:** A triage model classifies every PR as "simple" or "full_review". Simple PRs get a single-model checklist review. Complex PRs continue through the existing pipeline unchanged. Both triage and lightweight review use existing CLI/SDK runners — no new API integrations.

**Tech Stack:** Python, Pydantic, asyncio, existing Claude SDK / Codex CLI / Gemini CLI runners

---

### Task 1: Add triage and lightweight review config fields

**Files:**
- Modify: `src/pr_reviewer/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_load_config_triage_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.triage_backend == "gemini"
    assert cfg.triage_model is None
    assert cfg.triage_timeout_seconds == 60


def test_load_config_lightweight_review_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.lightweight_review_backend == "claude"
    assert cfg.lightweight_review_model is None
    assert cfg.lightweight_review_reasoning_effort is None
    assert cfg.lightweight_review_timeout_seconds == 300


def test_load_config_rejects_invalid_triage_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ntriage_backend = "invalid"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_lightweight_review_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nlightweight_review_backend = "invalid"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_lightweight_review_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nlightweight_review_reasoning_effort = "invalid"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_triage_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ntriage_model = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_lightweight_review_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nlightweight_review_model = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v -k "triage or lightweight"`
Expected: FAIL — fields don't exist yet

**Step 3: Implement the config fields**

Add these fields to the `AppConfig` class in `src/pr_reviewer/config.py` after the existing `slash_command_enabled` field:

```python
    # Triage
    triage_backend: str = "gemini"
    triage_model: str | None = None
    triage_timeout_seconds: int = Field(default=60, ge=10)

    # Lightweight review
    lightweight_review_backend: str = "claude"
    lightweight_review_model: str | None = None
    lightweight_review_reasoning_effort: str | None = None
    lightweight_review_timeout_seconds: int = Field(default=300, ge=30)
```

Add validators (follow existing patterns):

```python
    @field_validator("triage_backend")
    @classmethod
    def validate_triage_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"claude", "codex", "gemini"}:
            raise ValueError("triage_backend must be one of: claude, codex, gemini")
        return normalized

    @field_validator("triage_model")
    @classmethod
    def validate_triage_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("triage_model cannot be empty")
        return cleaned

    @field_validator("lightweight_review_backend")
    @classmethod
    def validate_lightweight_review_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"claude", "codex", "gemini"}:
            raise ValueError("lightweight_review_backend must be one of: claude, codex, gemini")
        return normalized

    @field_validator("lightweight_review_model")
    @classmethod
    def validate_lightweight_review_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("lightweight_review_model cannot be empty")
        return cleaned

    @field_validator("lightweight_review_reasoning_effort")
    @classmethod
    def validate_lightweight_review_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high", "max"}:
            raise ValueError(
                "lightweight_review_reasoning_effort must be one of: low, medium, high, max"
            )
        return normalized
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v -k "triage or lightweight"`
Expected: PASS

**Step 5: Run full config test suite**

Run: `uv run pytest tests/test_config.py -v`
Expected: All PASS (existing tests unaffected)

**Step 6: Commit**

```bash
git add src/pr_reviewer/config.py tests/test_config.py
git commit -m "feat: add triage and lightweight review config fields"
```

---

### Task 2: Create triage module

**Files:**
- Create: `src/pr_reviewer/reviewers/triage.py`
- Test: `tests/test_triage.py`

**Step 1: Write the failing tests**

Create `tests/test_triage.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pr_reviewer.models import PRCandidate
from pr_reviewer.reviewers.triage import run_triage, TriageResult


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="bump redis image to 7.2",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
        additions=3,
        deletions=1,
        changed_file_paths=["docker-compose.yaml"],
    )


def test_triage_returns_simple_when_model_says_simple(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "simple"}', None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.SIMPLE


def test_triage_returns_full_review_when_model_says_full(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "full_review"}', None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.FULL_REVIEW


def test_triage_falls_back_to_full_review_on_parse_error(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return "not valid json", None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.FULL_REVIEW


def test_triage_falls_back_to_full_review_on_exception(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        raise RuntimeError("timeout")

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.FULL_REVIEW


def test_triage_gemini_backend(tmp_path: Path) -> None:
    async def fake_gemini_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "simple"}'

    with patch("pr_reviewer.reviewers.triage.run_gemini_prompt", side_effect=fake_gemini_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="gemini")
        )
    assert result == TriageResult.SIMPLE


def test_triage_codex_backend(tmp_path: Path) -> None:
    async def fake_codex_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "simple"}'

    with patch("pr_reviewer.reviewers.triage.run_codex_prompt", side_effect=fake_codex_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="codex")
        )
    assert result == TriageResult.SIMPLE


def test_triage_extracts_json_from_markdown_code_block(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return '```json\n{"classification": "simple"}\n```', None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.SIMPLE
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_triage.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement triage module**

Create `src/pr_reviewer/reviewers/triage.py`:

```python
from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path

from pr_reviewer.logger import info, warn
from pr_reviewer.models import PRCandidate
from pr_reviewer.reviewers.claude_sdk import _run_claude_prompt
from pr_reviewer.reviewers.codex_cli import run_codex_prompt
from pr_reviewer.reviewers.gemini_cli import run_gemini_prompt


class TriageResult(Enum):
    SIMPLE = "simple"
    FULL_REVIEW = "full_review"


_TRIAGE_PROMPT_TEMPLATE = """You are a PR triage classifier. Analyze this pull request and classify it as either "simple" or "full_review".

PR:
- URL: {url}
- Title: {title}
- Base: {base_ref}
- Files changed: {changed_files}
- Lines added: {additions}, deleted: {deletions}

A PR is "simple" if ALL of the following are true:
- Changes are limited to configuration values, version bumps, image tags, feature flags, environment variables, or dependency versions
- No new files containing business logic, application code, or algorithms
- No security-sensitive changes (secrets, authentication, authorization, permissions, network policies, cryptographic settings)
- No changes to CI/CD pipeline logic (adding/removing steps, changing build commands — simple value changes like image tags are fine)

If ANY of those conditions is NOT met, classify as "full_review".

Respond with ONLY a JSON object, no other text:
{{"classification": "simple"}} or {{"classification": "full_review"}}"""


def _build_triage_prompt(pr: PRCandidate) -> str:
    changed_files = ", ".join(pr.changed_file_paths) if pr.changed_file_paths else "unknown"
    return _TRIAGE_PROMPT_TEMPLATE.format(
        url=pr.url,
        title=pr.title,
        base_ref=pr.base_ref,
        changed_files=changed_files,
        additions=pr.additions,
        deletions=pr.deletions,
    )


def _parse_triage_response(text: str) -> TriageResult:
    # Try to extract JSON from markdown code blocks first
    code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block_match:
        text = code_block_match.group(1)

    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        # Try to find a JSON object in the response
        json_match = re.search(r"\{[^}]+\}", text)
        if json_match:
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                return TriageResult.FULL_REVIEW
        else:
            return TriageResult.FULL_REVIEW

    classification = data.get("classification", "").strip().lower()
    if classification == "simple":
        return TriageResult.SIMPLE
    return TriageResult.FULL_REVIEW


async def run_triage(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    backend: str = "gemini",
    model: str | None = None,
) -> TriageResult:
    prompt = _build_triage_prompt(pr)
    info(f"running triage (backend={backend}, model={model or 'default'}) {pr.url}")

    try:
        if backend == "claude":
            text, _ = await _run_claude_prompt(
                prompt,
                workspace,
                timeout_seconds,
                system_prompt="You are a PR triage classifier. Respond only with JSON. Do not use tools.",
                max_turns=1,
                model=model,
            )
        elif backend == "codex":
            text = await run_codex_prompt(
                prompt, workspace, timeout_seconds, model=model,
            )
        elif backend == "gemini":
            text = await run_gemini_prompt(
                prompt, workspace, timeout_seconds, model=model,
            )
        else:
            warn(f"unsupported triage backend: {backend} {pr.url}")
            return TriageResult.FULL_REVIEW
    except Exception as exc:  # noqa: BLE001
        warn(f"triage failed, falling back to full review: {exc} {pr.url}")
        return TriageResult.FULL_REVIEW

    result = _parse_triage_response(text)
    info(f"triage result: {result.value} {pr.url}")
    return result
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_triage.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/reviewers/triage.py tests/test_triage.py
git commit -m "feat: add triage module for PR classification"
```

---

### Task 3: Create lightweight review module

**Files:**
- Create: `src/pr_reviewer/reviewers/lightweight.py`
- Test: `tests/test_lightweight.py`

**Step 1: Write the failing tests**

Create `tests/test_lightweight.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import patch

from pr_reviewer.models import PRCandidate, TokenUsage
from pr_reviewer.reviewers.lightweight import run_lightweight_review


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="bump redis image to 7.2",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
        additions=3,
        deletions=1,
        changed_file_paths=["docker-compose.yaml"],
    )


def test_lightweight_review_claude_returns_formatted_output(tmp_path: Path) -> None:
    review_text = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."
    token_usage = TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.001)

    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return review_text, token_usage

    with patch(
        "pr_reviewer.reviewers.lightweight._run_claude_prompt",
        side_effect=fake_claude_prompt,
    ):
        text, usage = asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="claude"
            )
        )

    assert "### Findings" in text
    assert "### Test Gaps" in text
    assert usage == token_usage


def test_lightweight_review_gemini_backend(tmp_path: Path) -> None:
    review_text = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    async def fake_gemini_prompt(prompt, cwd, timeout, **kwargs):
        return review_text

    with patch(
        "pr_reviewer.reviewers.lightweight.run_gemini_prompt",
        side_effect=fake_gemini_prompt,
    ):
        text, usage = asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="gemini"
            )
        )

    assert "### Findings" in text
    assert usage is None


def test_lightweight_review_codex_backend(tmp_path: Path) -> None:
    review_text = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    async def fake_codex_prompt(prompt, cwd, timeout, **kwargs):
        return review_text

    with patch(
        "pr_reviewer.reviewers.lightweight.run_codex_prompt",
        side_effect=fake_codex_prompt,
    ):
        text, usage = asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="codex"
            )
        )

    assert "### Findings" in text
    assert usage is None


def test_lightweight_review_prompt_contains_checklist_items(tmp_path: Path) -> None:
    captured_prompts: list[str] = []

    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        captured_prompts.append(prompt)
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.", None

    with patch(
        "pr_reviewer.reviewers.lightweight._run_claude_prompt",
        side_effect=fake_claude_prompt,
    ):
        asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="claude"
            )
        )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0].lower()
    assert "syntax" in prompt or "well-formed" in prompt
    assert "secret" in prompt
    assert "breaking" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lightweight.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement lightweight review module**

Create `src/pr_reviewer/reviewers/lightweight.py`:

```python
from __future__ import annotations

from pathlib import Path

from pr_reviewer.logger import info
from pr_reviewer.models import PRCandidate, TokenUsage
from pr_reviewer.reviewers.claude_sdk import _run_claude_prompt
from pr_reviewer.reviewers.codex_cli import run_codex_prompt
from pr_reviewer.reviewers.gemini_cli import run_gemini_prompt


_LIGHTWEIGHT_REVIEW_PROMPT_TEMPLATE = """You are reviewing a simple configuration or infrastructure pull request. Perform a focused checklist review.

PR:
- URL: {url}
- Title: {title}
- Base: {base_ref}
- Head SHA: {head_sha}
- Files changed: {changed_files}
- Lines added: {additions}, deleted: {deletions}

Review checklist — evaluate each item:
1. **Syntax & format**: Are the changed files valid and well-formed? (YAML indentation, JSON brackets, TOML syntax, etc.)
2. **Secrets & credentials**: Are there any hardcoded secrets, API keys, passwords, or tokens?
3. **Environment correctness**: Are there environment-specific values (hostnames, IPs, ports) that don't belong in this branch/environment?
4. **Breaking changes**: Are any keys removed, fields renamed, ports changed, or defaults altered that could break existing consumers?
5. **Version validity**: For version bumps or image tag changes, is the new version/tag a real, expected value?

Strict output rules:
- Keep total output under 150 words.
- No tables, no long summary, no praise/filler.
- Include only these sections in this exact order:
  1) `### Findings`
  2) `### Test Gaps`
- `### Findings`:
  - 0-5 bullets, highest severity first.
  - Severity: P1 (breaks production/security), P2 (correctness issue), P3 (minor/style).
  - Each bullet: `- [P1|P2|P3] path[:line] - issue. Impact. Fix.`
  - If no material issues: `- No material findings.`
- `### Test Gaps`:
  - 0-2 bullets with concrete missing tests.
  - If none: `- None noted.`
- Do not invent evidence. If uncertain, omit.
- Do not use tools."""


def _build_lightweight_prompt(pr: PRCandidate) -> str:
    changed_files = ", ".join(pr.changed_file_paths) if pr.changed_file_paths else "unknown"
    return _LIGHTWEIGHT_REVIEW_PROMPT_TEMPLATE.format(
        url=pr.url,
        title=pr.title,
        base_ref=pr.base_ref,
        head_sha=pr.head_sha,
        changed_files=changed_files,
        additions=pr.additions,
        deletions=pr.deletions,
    )


async def run_lightweight_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    backend: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> tuple[str, TokenUsage | None]:
    prompt = _build_lightweight_prompt(pr)
    info(
        f"running lightweight review "
        f"(backend={backend}, model={model or 'default'}) {pr.url}"
    )

    if backend == "claude":
        return await _run_claude_prompt(
            prompt,
            workspace,
            timeout_seconds,
            system_prompt=(
                "You are a lightweight code reviewer for configuration and infrastructure changes. "
                "Respond only with the requested markdown sections. Do not use any tools."
            ),
            max_turns=1,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    if backend == "codex":
        text = await run_codex_prompt(
            prompt,
            workspace,
            timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        return text, None
    if backend == "gemini":
        text = await run_gemini_prompt(
            prompt,
            workspace,
            timeout_seconds,
            model=model,
        )
        return text, None
    raise RuntimeError(f"Unsupported lightweight review backend: {backend}")
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lightweight.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/reviewers/lightweight.py tests/test_lightweight.py
git commit -m "feat: add lightweight review module with checklist prompt"
```

---

### Task 4: Export new functions from reviewers package

**Files:**
- Modify: `src/pr_reviewer/reviewers/__init__.py`

**Step 1: Update exports**

Add to `src/pr_reviewer/reviewers/__init__.py`:

```python
from pr_reviewer.reviewers.lightweight import run_lightweight_review
from pr_reviewer.reviewers.triage import TriageResult, run_triage
```

And add to `__all__`:

```python
__all__ = [
    "TriageResult",
    "run_claude_review",
    "run_codex_review",
    "run_codex_review_via_agents_sdk",
    "run_gemini_review",
    "run_lightweight_review",
    "run_triage",
    "reconcile_reviews",
]
```

**Step 2: Verify imports work**

Run: `uv run python -c "from pr_reviewer.reviewers import run_triage, run_lightweight_review, TriageResult; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add src/pr_reviewer/reviewers/__init__.py
git commit -m "feat: export triage and lightweight review from reviewers package"
```

---

### Task 5: Integrate triage into processor — remove skip logic, add triage routing

**Files:**
- Modify: `src/pr_reviewer/processor.py`
- Test: `tests/test_processor.py`

**Step 1: Write the failing tests**

Add new tests and update existing ones in `tests/test_processor.py`. First, update imports to include the new symbols:

```python
from pr_reviewer.reviewers.triage import TriageResult
```

Add new tests:

```python
def test_process_candidate_triage_simple_runs_lightweight(monkeypatch, tmp_path) -> None:
    """When triage says simple, should run lightweight review, not full pipeline."""
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    # Mock triage to return SIMPLE
    async def fake_triage(*args, **kwargs):
        return TriageResult.SIMPLE

    monkeypatch.setattr("pr_reviewer.processor.run_triage", fake_triage)

    # Mock lightweight review
    async def fake_lightweight(*args, **kwargs):
        return (
            "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
            None,
        )

    monkeypatch.setattr("pr_reviewer.processor.run_lightweight_review", fake_lightweight)

    # Full reviewers should NOT be called
    monkeypatch.setattr(
        "pr_reviewer.processor.run_claude_review",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.run_codex_review",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not run")),
    )

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["claude", "codex"])
    pr = _sample_pr(additions=3, deletions=1, changed_file_paths=["config.yaml"])

    changed = asyncio.run(process_candidate(cfg, client, store, workspace, pr))

    assert changed is True
    assert "lightweight" in store.state.last_status


def test_process_candidate_triage_full_runs_normal_pipeline(monkeypatch, tmp_path) -> None:
    """When triage says full_review, should run the normal multi-reviewer pipeline."""
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    async def fake_triage(*args, **kwargs):
        return TriageResult.FULL_REVIEW

    monkeypatch.setattr("pr_reviewer.processor.run_triage", fake_triage)

    # Mock the normal reviewers
    monkeypatch.setattr("pr_reviewer.processor.run_claude_review", _ok_output("claude"))
    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", _ok_output("codex"))

    async def fake_reconcile(*args, **kwargs):
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.", None

    monkeypatch.setattr("pr_reviewer.processor.reconcile_reviews", fake_reconcile)

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["claude", "codex"])
    pr = _sample_pr()

    changed = asyncio.run(process_candidate(cfg, client, store, workspace, pr))

    assert changed is True
    assert "lightweight" not in (store.state.last_status or "")


def test_process_candidate_triage_failure_falls_through_to_full(monkeypatch, tmp_path) -> None:
    """If triage itself errors, should fall through to full pipeline."""
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    async def fake_triage(*args, **kwargs):
        return TriageResult.FULL_REVIEW  # triage module handles errors internally

    monkeypatch.setattr("pr_reviewer.processor.run_triage", fake_triage)

    monkeypatch.setattr("pr_reviewer.processor.run_claude_review", _ok_output("claude"))
    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", _ok_output("codex"))

    async def fake_reconcile(*args, **kwargs):
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.", None

    monkeypatch.setattr("pr_reviewer.processor.reconcile_reviews", fake_reconcile)

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["claude", "codex"])
    pr = _sample_pr(additions=3, deletions=1, changed_file_paths=["config.yaml"])

    changed = asyncio.run(process_candidate(cfg, client, store, workspace, pr))

    assert changed is True
```

**Step 2: Run new tests to verify they fail**

Run: `uv run pytest tests/test_processor.py -v -k "triage"`
Expected: FAIL — triage not yet integrated

**Step 3: Modify processor.py**

Remove these functions/constants from `processor.py`:
- `_CONFIG_LIKE_SUFFIXES` (line 40-50)
- `_is_config_like_path` (line 149-156)
- `_SKIP_REASON_MESSAGES` (line 159-166)
- `_publish_skip_comment` (line 169-174)
- `_skip_reason_for_change_scope` (line 177-185)

Add imports at top of `processor.py`:

```python
from pr_reviewer.reviewers import run_triage, run_lightweight_review, TriageResult
```

Modify `process_candidate` (starting around line 473). Replace the skip-check block (lines 489-509) and add triage after workspace prep. The new flow in `process_candidate` after the slash command / trigger decision block:

```python
    workdir: Path | None = None
    restarts_remaining = config.max_mid_review_restarts
    try:
        info(f"preparing workspace {pr.url}")
        workdir = workspace_mgr.prepare(pr)
        info(f"workspace ready at {workdir} {pr.url}")

        # Triage: classify PR as simple or full_review
        triage_result = await run_triage(
            pr,
            workdir,
            config.triage_timeout_seconds,
            backend=config.triage_backend,
            model=config.triage_model,
        )

        if triage_result == TriageResult.SIMPLE:
            # Lightweight review path
            lightweight_text, lightweight_usage = await run_lightweight_review(
                pr,
                workdir,
                config.lightweight_review_timeout_seconds,
                backend=config.lightweight_review_backend,
                model=config.lightweight_review_model,
                reasoning_effort=config.lightweight_review_reasoning_effort,
            )
            lightweight_text = _validate_review_format(lightweight_text)

            if lightweight_usage is not None:
                info(
                    f"token usage [lightweight]: "
                    f"input={lightweight_usage.input_tokens:,} "
                    f"output={lightweight_usage.output_tokens:,}"
                    f"{f' cost=${lightweight_usage.cost_usd:.4f}' if lightweight_usage.cost_usd is not None else ''}"
                    f" {pr.url}"
                )

            info(f"writing lightweight review output {pr.url}")
            version_label = _output_version_label(pr)
            output_path = write_review_markdown(
                Path(config.output_dir), pr, lightweight_text, version_label=version_label,
            )
            info(f"Lightweight review ready: {output_path.resolve()}")

            _publish_and_persist(
                config, client, store, pr, output_path,
                lightweight_text,
                status_when_not_posted="lightweight_generated",
                previous=previous,
            )
            info(f"processing complete (lightweight) {pr.url}")
            return True

        # Full review path (existing code, unchanged)
        # Retry loop: restart reviewers when new commits are pushed mid-review.
        while True:
            # ... (existing code from here)
```

**Step 4: Update existing skip tests**

The old skip tests (`test_process_candidate_skips_small_change_set`, `test_process_candidate_skips_config_only_files`, `test_skip_publishes_reason_when_slash_command_triggered`, etc.) need to be either removed or rewritten to test the new triage flow. Replace them:

- Remove `test_process_candidate_skips_small_change_set` — no more hard skip
- Remove `test_process_candidate_skips_config_only_files` — no more hard skip
- Remove `test_skip_publishes_reason_when_slash_command_triggered` — no more skip comments
- Remove `test_skip_publishes_reason_when_rerequest_triggered` — no more skip comments
- Remove `test_skip_no_comment_when_no_user_trigger` — no more skip comments
- Remove `test_skip_no_comment_on_bootstrap_state` — no more skip comments
- Remove `test_skip_rerequest_advances_last_seen_rerequest_at` — rerequest advancement is still tested by other tests

Also update any imports that reference removed functions (e.g., remove `_skip_reason_for_change_scope` from import list if present).

**Step 5: Run all processor tests**

Run: `uv run pytest tests/test_processor.py -v`
Expected: All PASS

**Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/pr_reviewer/processor.py tests/test_processor.py
git commit -m "feat: replace skip logic with triage-first pipeline routing"
```

---

### Task 6: Add CLI overrides for triage and lightweight review

**Files:**
- Modify: `src/pr_reviewer/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Read existing CLI test patterns**

Check `tests/test_cli.py` for the pattern used to test CLI overrides.

**Step 2: Add CLI option types to cli.py**

Add these new option types after the existing ones (follow the same pattern as e.g. `ReconcilerBackendOption`):

```python
TriageBackendOption = Annotated[
    str | None,
    typer.Option(
        "--triage-backend",
        help="Override triage_backend from config. Allowed: claude, codex, gemini.",
    ),
]
TriageModelOption = Annotated[
    str | None,
    typer.Option(
        "--triage-model",
        help="Override triage_model from config.",
    ),
]
LightweightReviewBackendOption = Annotated[
    str | None,
    typer.Option(
        "--lightweight-review-backend",
        help="Override lightweight_review_backend from config. Allowed: claude, codex, gemini.",
    ),
]
LightweightReviewModelOption = Annotated[
    str | None,
    typer.Option(
        "--lightweight-review-model",
        help="Override lightweight_review_model from config.",
    ),
]
LightweightReviewReasoningEffortOption = Annotated[
    str | None,
    typer.Option(
        "--lightweight-review-reasoning-effort",
        help="Override lightweight_review_reasoning_effort from config. Allowed: low, medium, high, max.",
    ),
]
```

**Step 3: Add parameters to `_load_runtime`, `check_command`, `run_once_command`, `start_command`**

Add `triage_backend`, `triage_model`, `lightweight_review_backend`, `lightweight_review_model`, `lightweight_review_reasoning_effort` parameters to all three commands and `_load_runtime`. Apply overrides in `_load_runtime`:

```python
config = _apply_field_override(config, "triage_backend", triage_backend, "--triage-backend")
config = _apply_field_override(config, "triage_model", triage_model, "--triage-model")
config = _apply_field_override(
    config, "lightweight_review_backend", lightweight_review_backend, "--lightweight-review-backend"
)
config = _apply_field_override(
    config, "lightweight_review_model", lightweight_review_model, "--lightweight-review-model"
)
config = _apply_field_override(
    config,
    "lightweight_review_reasoning_effort",
    lightweight_review_reasoning_effort,
    "--lightweight-review-reasoning-effort",
)
```

**Step 4: Update check_command table**

Add rows for the new settings:

```python
table.add_row("Triage backend", cfg.triage_backend)
table.add_row("Triage model", cfg.triage_model or "default")
table.add_row("Triage timeout", str(cfg.triage_timeout_seconds))
table.add_row("Lightweight review backend", cfg.lightweight_review_backend)
table.add_row("Lightweight review model", cfg.lightweight_review_model or "default")
table.add_row("Lightweight review reasoning effort", cfg.lightweight_review_reasoning_effort or "default")
table.add_row("Lightweight review timeout", str(cfg.lightweight_review_timeout_seconds))
```

**Step 5: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/pr_reviewer/cli.py tests/test_cli.py
git commit -m "feat: add CLI overrides for triage and lightweight review settings"
```

---

### Task 7: Update config.example.toml

**Files:**
- Modify: `config.example.toml`

**Step 1: Add triage and lightweight review sections**

Add after the `slash_command_enabled` line:

```toml
# Triage — classifies PRs as simple (lightweight review) or complex (full review)
# Every PR goes through triage; no more hard skips.
triage_backend = "gemini"   # claude | codex | gemini
# triage_model = "gemini-3-flash"
triage_timeout_seconds = 60
# Lightweight review — single-model checklist review for simple PRs
lightweight_review_backend = "claude"   # claude | codex | gemini
# lightweight_review_model = "claude-sonnet-4-6"
# lightweight_review_reasoning_effort = "low"   # low | medium | high | max
lightweight_review_timeout_seconds = 300
```

**Step 2: Commit**

```bash
git add config.example.toml
git commit -m "docs: document triage and lightweight review config options"
```

---

### Task 8: Final integration test — full test suite

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

**Step 2: Run type checking if available**

Run: `uv run mypy src/pr_reviewer/ --ignore-missing-imports` (if mypy is configured)

**Step 3: Run linting**

Run: `uv run ruff check src/ tests/`
Expected: No errors

**Step 4: Final commit (if any fixes needed)**

Only if steps 2-3 required changes.
