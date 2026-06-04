# Replace Gemini CLI with Antigravity CLI (agy)

**Date:** 2026-06-03
**Status:** Approved direction: full replacement (no parallel gemini backend)

## Why

Google is sunsetting Gemini CLI for Google AI Pro/Ultra and free tiers on
2026-06-18, transitioning these tiers to the Antigravity CLI (`agy`). The
deployed code-reviewer authenticates Gemini via oauth-personal (free tier),
which stops working on that date. Full replacement chosen over running both
backends: gemini-CLI-with-paid-API-key remains possible upstream but is not a
path we want to maintain.

## Antigravity CLI facts (researched 2026-06-03)

- Binary: `agy`, written in Go. Install: `curl -fsSL https://antigravity.google/cli/install.sh | bash`
  (installs to `~/.local/bin/agy`).
- Headless mode: `agy -p <prompt>`, `-m <model>`, `-o/--output-format text|json|stream-json`,
  `--raw-output`, `--policy`.
- Config dir: `~/.gemini/antigravity-cli/` (settings.json, keybindings.json).
  Nested under the existing `.gemini` dir, which is already PVC-mounted in K8s.
- Auth: OS keyring preferred (Apple Keychain / Linux Secret Service); remote
  (SSH/no-browser) flow prints a URL, user authorizes in a browser, pastes a
  code back. No API-key auth documented for agy yet.
- Permission presets in settings.json: `toolPermission`:
  `request-review` (default) | `proceed-in-sandbox` | `always-proceed` | `strict`.
  No CLI flag equivalent of gemini's `--approval-mode yolo` confirmed; rely on
  settings.json `"toolPermission": "always-proceed"`.
- Gemini extensions are replaced by "plugins" (`agy plugin import gemini`).
  We do NOT use this: production review runs in prompt mode
  (`full_review_prompt_path` is always set). The gemini `-e code-review`
  extension path is removed without replacement.

## Scope: code-reviewer repo (full rename gemini -> antigravity)

### 1. Reviewer module

- Delete `src/code_reviewer/reviewers/gemini_cli.py`; add
  `src/code_reviewer/reviewers/antigravity_cli.py`.
- `run_antigravity_review(pr, workspace, timeout_seconds, *, model=None, prompt_path=None)`
  -> `ReviewerOutput(reviewer="antigravity")`, and
  `run_antigravity_prompt(prompt, workspace, timeout_seconds, *, model=None)`.
- Command: `agy -p <prompt> -o json` (+ `-m <model>` when set). Prompt mode
  only; `prompt_path=None` is a config error caught in preflight (see below).
- Keep the defensive JSON-payload extraction (`_iter_json_payloads`,
  markdown-key scanning) and stderr error summarization from gemini_cli.py;
  they are output-format agnostic. Adjust error strings to say `agy`.

### 2. Config (`config.py`)

- `_ALLOWED_BACKENDS` and the `enabled_reviewers` validator:
  `{"claude", "codex", "antigravity", "opencode"}`.
- Rename fields: `gemini_model` -> `antigravity_model`,
  `gemini_fallback_model` -> `antigravity_fallback_model`,
  `gemini_timeout_seconds` -> `antigravity_timeout_seconds` (default 900),
  with equivalent validators.
- Default `triage_backend` / `lightweight_review_backend`: `["antigravity"]`.
- Default `triage_model` / `lightweight_review_model`: `None` (agy default
  model; `gemini-3-flash-preview` is not a known agy model identifier).

### 3. Wiring

- `processor.py`: rename gemini branches and helpers
  (`_resolve_gemini_review_model` -> `_resolve_antigravity_review_model`,
  timeout map, reconciler model resolution, status payload model display,
  `_usage_snapshot_for_model` gemini special-case removed).
- `reviewers/triage.py`, `reviewers/lightweight.py`, `reviewers/reconcile.py`:
  replace gemini dispatch branches with antigravity ones, including the
  primary/fallback-model circuit-breaker logic (renamed).
- `reviewers/__init__.py`: export the new functions.
- `preflight.py`: when antigravity appears in any chain, require the `agy`
  binary (`agy --version`). Remove gemini extension checks. New check: if
  `"antigravity"` is in `enabled_reviewers` and `full_review_prompt_path` is
  unset, fail preflight with a clear message (prompt mode is the only mode).
- `cli.py`: `--gemini-model`/`--gemini-fallback-model` ->
  `--antigravity-model`/`--antigravity-fallback-model`; update allowed-backend
  help texts and the config summary table.
- `backend_usage.py`: remove the gemini quota machinery (gemini-cli core
  node-module discovery, `~/.gemini/settings.json` parsing, quota subprocess).
  `_SUPPORTED_BACKENDS = {"claude", "codex", "opencode"}`. Antigravity has no
  known quota API; the existing "usage check unavailable -> proceed" path
  covers it.
- `history_server.py`: add `"antigravity"` and `"antigravity.prompt"` stage
  names. Keep `"gemini"`/`"gemini.prompt"` in the known-stage lists so
  historical review records still render.

### 4. Dockerfile

- Remove `npm install -g @google/gemini-cli` and the code-review extension
  clone + enablement file.
- Add agy install via the official install script; ensure
  `/home/appuser/.local/bin` is on PATH for the runtime user.
- Seed `/home/appuser/.gemini/antigravity-cli/settings.json` with
  `{"toolPermission": "always-proceed", "enableTelemetry": false}` so headless
  runs never block on permission prompts.

### 5. Tests

- `tests/test_gemini_cli.py` -> `tests/test_antigravity_cli.py`: command
  building (`agy -p ... -o json`, model flag), JSON extraction, error
  summarization, timeout path.
- Update gemini references in: `test_config.py`, `test_triage.py`,
  `test_lightweight.py`, `test_reconcile.py`, `test_preflight.py`
  (including the new prompt-path-required check), `test_processor.py`,
  `test_cli.py`, `test_backend_usage.py` (drop gemini quota tests),
  `test_history_server.py`, `test_fallback.py`, `test_circuit_breaker.py`
  as needed.

### 6. Docs and examples

- `README.md`: prerequisites (`agy` authenticated), backend lists, flow
  diagram label.
- `CLAUDE.md` / `agents.md`: reviewer module list, runner names, Dockerfile
  notes (gemini extension workaround note removed).
- `config.example.toml`: gemini tuning block -> antigravity block; backend
  list examples; remove gemini model name examples.
- `web/src/components/Badge.tsx`, `web/src/pages/PRDetail.tsx`: add an
  antigravity badge/display variant. Keep the existing gemini variant so
  historical review records still render correctly.

## Out of scope (infra repo follow-up, separate PR)

1. `overlays/devnet/configmaps/code-reviewer/config.toml`: swap gemini ->
   antigravity in `enabled_reviewers`, `reconciler_backend`, `triage_backend`,
   `lightweight_review_backend`; drop `triage_model`/`lightweight_review_model`
   pins (use agy default); rename `gemini_timeout_seconds`.
   Must land together with the new image tag (config keys are renamed).
2. `overlays/devnet/code-reviewer.yaml` auth-setup init container: remove
   gemini extension seeding; seed antigravity settings.json on the PVC if the
   Docker-image copy is insufficient (PVC mount shadows the image's `.gemini`).
3. In-pod auth: run the agy remote URL+code flow via `kubectl exec -it`
   (analog of codex device auth). Verify token persistence on the PVC given
   no OS keyring in the pod. This is the main deployment risk; do it before
   the config cutover.
4. `infra/docs/code-reviewer.md`: replace the Gemini OAuth section with the
   agy procedure; also fix stale PVC names (doc says three PVCs; reality is
   one `code-reviewer-auth-data`).

## Risks

- **agy auth persistence in containers is unverified** (keyring vs file
  fallback). Mitigation: test in pod before cutover; PVC already covers
  `~/.gemini`.
- **agy `-o json` schema unverified.** Mitigation: extraction helpers are
  defensive (scan any JSON object for response/text/output/result/content
  keys) with plain-text fallback.
- **Model identifiers unknown.** Mitigation: ship with model unset (agy
  default); `antigravity_model` config field allows pinning later.
- **Free-tier agy quota reportedly very small** (community reports). May need
  a paid plan / Google Cloud project binding for review volume. Surfaces as
  errors -> circuit breaker -> fallback to claude, same as today.
- **Renamed config keys are a breaking change** for any existing config.toml.
  Deployed config and image must move together (infra follow-up item 1).

## Test plan

- `uv run pytest` green locally.
- Local smoke test: `agy -p "say hi" -o json` on a dev machine, then a local
  review run with `enabled_reviewers = ["antigravity"]` against a real PR.
- Deployment: build image, in-pod `agy` auth, one manual review trigger,
  then config cutover.
