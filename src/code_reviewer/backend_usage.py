from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.shell import run_command, run_json

_SUPPORTED_BACKENDS = {"claude", "codex", "gemini", "opencode"}
_PREFERRED_LIMIT_ORDER = ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus")
_CODEX_LIMIT_MAP = {
    "primary": "five_hour",
    "secondary": "seven_day",
}
_GEMINI_AUTH_TYPE_MAP = {
    "oauth-personal": "LOGIN_WITH_GOOGLE",
    "compute-default-credentials": "COMPUTE_ADC",
}
_GEMINI_QUOTA_PROBE_SCRIPT = """
import { pathToFileURL } from "node:url";

const coreRoot = process.argv[1];
const cwd = process.argv[2];
const model = process.argv[3] || "gemini-3-flash-preview";
const authTypeName = process.argv[4];
const core = await import(pathToFileURL(coreRoot).href);
const authType = core.AuthType[authTypeName];

if (!authType) {
  throw new Error(`Unsupported Gemini auth type: ${authTypeName}`);
}

const config = new core.Config({
  usageStatisticsEnabled: true,
  debugMode: false,
  sessionId: "quota-probe",
  proxy: undefined,
  model,
  targetDir: cwd,
  cwd,
});
const client = await core.getOauthClient(authType, config);
const userData = await core.setupUser(client, config.getValidationHandler?.(), {});
const server = new core.CodeAssistServer(
  client,
  userData.projectId,
  {},
  "quota-probe",
  userData.userTier,
  userData.userTierName,
  userData.paidTier,
  config,
);
const quota = await server.retrieveUserQuota({ project: userData.projectId });

console.log(JSON.stringify({
  seenAt: new Date().toISOString(),
  selectedModel: model,
  authType: authTypeName,
  userData,
  quota,
}));
""".strip()


@dataclass(slots=True)
class BackendUsageWindow:
    backend: str
    limit_key: str
    seen_at: datetime
    resets_at: datetime | None
    used_percent: float | None
    status: str | None
    source: Path
    raw_limit_key: str | None = None
    overage_status: str | None = None
    is_using_overage: bool = False
    window_minutes: int | None = None

    @property
    def remaining_percent(self) -> float | None:
        if self.used_percent is None:
            return None
        return max(0.0, 100.0 - self.used_percent)

    @property
    def is_active(self) -> bool:
        return self.resets_at is None or self.resets_at > datetime.now(UTC)


@dataclass(slots=True)
class BackendUsageSnapshot:
    backend: str
    events_scanned: int
    latest_by_limit: dict[str, BackendUsageWindow]
    account_type: str | None = None


@dataclass(slots=True)
class BackendUsageDecision:
    should_use_backend: bool
    reason: str
    window: BackendUsageWindow | None = None


@dataclass(slots=True)
class BackendUsageAnswer:
    backend: str
    question: str
    answer: str
    decision: BackendUsageDecision


def _normalize_backend_name(backend: str) -> str:
    normalized = backend.strip().lower()
    if normalized not in _SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported backend: {backend}")
    return normalized


def _display_backend_name(backend: str) -> str:
    return backend.capitalize()


def _parse_iso8601_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _parse_resets_at(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value, UTC)


def _parse_used_percent(value: object) -> float | None:
    if not isinstance(value, int | float):
        return None
    used_percent = float(value)
    if used_percent < 0:
        return None
    return used_percent


def _parse_remaining_fraction_used_percent(value: object) -> float | None:
    if not isinstance(value, int | float):
        return None
    remaining_fraction = float(value)
    if remaining_fraction < 0:
        return None
    remaining_fraction = min(1.0, remaining_fraction)
    return max(0.0, (1.0 - remaining_fraction) * 100.0)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _default_claude_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude"


def _default_codex_home() -> Path:
    return Path.home() / ".codex"


def _default_gemini_home() -> Path:
    return Path.home() / ".gemini"


def _load_claude_account_type(
    auth_status_loader: Callable[[list[str]], object] | None = None,
) -> str | None:
    loader = auth_status_loader or run_json
    try:
        payload = loader(["claude", "auth", "status"])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("subscriptionType")
    return value if isinstance(value, str) else None


def _parse_claude_rate_limit_event(line: str, source: Path) -> BackendUsageWindow | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None

    if payload.get("type") != "rate_limit_event":
        return None

    info = payload.get("rate_limit_info")
    if not isinstance(info, dict):
        return None

    limit_key = info.get("rateLimitType")
    status = info.get("status")
    seen_at = payload.get("_audit_timestamp")
    if (
        not isinstance(limit_key, str)
        or not isinstance(status, str)
        or not isinstance(seen_at, str)
    ):
        return None

    utilization = info.get("utilization")
    used_percent = None
    if isinstance(utilization, int | float):
        used_percent = max(0.0, float(utilization) * 100.0)

    return BackendUsageWindow(
        backend="claude",
        limit_key=limit_key,
        raw_limit_key=limit_key,
        seen_at=_parse_iso8601_utc(seen_at),
        resets_at=_parse_resets_at(info.get("resetsAt")),
        used_percent=used_percent,
        status=status,
        source=source,
        overage_status=(
            info.get("overageStatus") if isinstance(info.get("overageStatus"), str) else None
        ),
        is_using_overage=bool(info.get("isUsingOverage", False)),
    )


def _scan_claude_usage_snapshot(
    support_dir: Path,
    *,
    auth_status_loader: Callable[[list[str]], object] | None = None,
) -> BackendUsageSnapshot:
    sessions_dir = support_dir / "local-agent-mode-sessions"
    latest_by_limit: dict[str, BackendUsageWindow] = {}
    events_scanned = 0

    if sessions_dir.exists():
        for audit_path in sessions_dir.rglob("audit.jsonl"):
            with audit_path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    window = _parse_claude_rate_limit_event(line, audit_path)
                    if window is None:
                        continue
                    events_scanned += 1
                    existing = latest_by_limit.get(window.limit_key)
                    if existing is None or window.seen_at > existing.seen_at:
                        latest_by_limit[window.limit_key] = window

    return BackendUsageSnapshot(
        backend="claude",
        events_scanned=events_scanned,
        latest_by_limit=latest_by_limit,
        account_type=_load_claude_account_type(auth_status_loader),
    )


def _parse_codex_usage_windows(
    line: str, source: Path
) -> tuple[list[BackendUsageWindow], str | None]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return [], None

    if payload.get("type") != "event_msg":
        return [], None

    body = payload.get("payload")
    if not isinstance(body, dict) or body.get("type") != "token_count":
        return [], None

    rate_limits = body.get("rate_limits")
    timestamp = payload.get("timestamp")
    if not isinstance(rate_limits, dict) or not isinstance(timestamp, str):
        return [], None

    seen_at = _parse_iso8601_utc(timestamp)
    windows: list[BackendUsageWindow] = []
    for raw_limit_key, limit_key in _CODEX_LIMIT_MAP.items():
        window_info = rate_limits.get(raw_limit_key)
        if not isinstance(window_info, dict):
            continue
        windows.append(
            BackendUsageWindow(
                backend="codex",
                limit_key=limit_key,
                raw_limit_key=raw_limit_key,
                seen_at=seen_at,
                resets_at=_parse_resets_at(window_info.get("resets_at")),
                used_percent=_parse_used_percent(window_info.get("used_percent")),
                status=None,
                source=source,
                window_minutes=(
                    int(window_info["window_minutes"])
                    if isinstance(window_info.get("window_minutes"), int | float)
                    else None
                ),
            )
        )

    plan_type = rate_limits.get("plan_type")
    return windows, plan_type if isinstance(plan_type, str) else None


def _iter_codex_log_files(codex_home: Path) -> Iterable[Path]:
    for relative in ("sessions", "archived_sessions"):
        base_dir = codex_home / relative
        if not base_dir.exists():
            continue
        yield from base_dir.rglob("*.jsonl")


def _scan_codex_usage_snapshot(codex_home: Path) -> BackendUsageSnapshot:
    latest_by_limit: dict[str, BackendUsageWindow] = {}
    events_scanned = 0
    latest_plan_type: tuple[datetime, str] | None = None

    for log_path in _iter_codex_log_files(codex_home):
        with log_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                windows, plan_type = _parse_codex_usage_windows(line, log_path)
                if not windows:
                    continue
                events_scanned += 1
                seen_at = windows[0].seen_at
                if plan_type is not None and (
                    latest_plan_type is None or seen_at > latest_plan_type[0]
                ):
                    latest_plan_type = (seen_at, plan_type)
                for window in windows:
                    existing = latest_by_limit.get(window.limit_key)
                    if existing is None or window.seen_at > existing.seen_at:
                        latest_by_limit[window.limit_key] = window

    return BackendUsageSnapshot(
        backend="codex",
        events_scanned=events_scanned,
        latest_by_limit=latest_by_limit,
        account_type=latest_plan_type[1] if latest_plan_type is not None else None,
    )


def _load_gemini_settings(gemini_home: Path) -> tuple[str | None, str | None]:
    settings_path = gemini_home / "settings.json"
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None

    auth_type = payload.get("security", {}).get("auth", {}).get("selectedType")
    model_name = payload.get("model", {}).get("name")
    return (
        model_name if isinstance(model_name, str) and model_name.strip() else None,
        auth_type if isinstance(auth_type, str) and auth_type.strip() else None,
    )


def _find_gemini_core_module() -> Path:
    candidates = (
        Path(
            "/opt/homebrew/opt/gemini-cli/libexec/lib/node_modules/@google/gemini-cli/"
            "node_modules/@google/gemini-cli-core/dist/src/index.js"
        ),
        Path(
            "/usr/local/opt/gemini-cli/libexec/lib/node_modules/@google/gemini-cli/"
            "node_modules/@google/gemini-cli-core/dist/src/index.js"
        ),
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    search_roots = (
        Path("/opt/homebrew/Cellar/gemini-cli"),
        Path("/usr/local/Cellar/gemini-cli"),
    )
    for search_root in search_roots:
        if not search_root.exists():
            continue
        matches = sorted(
            search_root.glob(
                "*/libexec/lib/node_modules/@google/gemini-cli/"
                "node_modules/@google/gemini-cli-core/dist/src/index.js"
            ),
            reverse=True,
        )
        if matches:
            return matches[0]

    raise FileNotFoundError("Unable to locate the installed Gemini CLI core module.")


def _load_gemini_quota_payload(
    gemini_home: Path,
    *,
    quota_loader: Callable[[Path, str | None, str | None], object] | None = None,
) -> tuple[object | None, str | None, str | None]:
    selected_model, auth_type = _load_gemini_settings(gemini_home)
    if auth_type not in _GEMINI_AUTH_TYPE_MAP:
        return None, selected_model, auth_type

    if quota_loader is not None:
        return quota_loader(gemini_home, selected_model, auth_type), selected_model, auth_type

    proc = run_command(
        [
            "node",
            "--input-type=module",
            "-e",
            _GEMINI_QUOTA_PROBE_SCRIPT,
            str(_find_gemini_core_module()),
            str(Path.cwd()),
            selected_model or "",
            _GEMINI_AUTH_TYPE_MAP[auth_type],
        ],
        cwd=Path.cwd(),
        timeout=30,
    )
    payload = None
    for line in reversed(proc.stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        raise RuntimeError("Invalid JSON from Gemini quota probe.")
    return payload, selected_model, auth_type


def _scan_gemini_usage_snapshot(
    gemini_home: Path,
    *,
    quota_loader: Callable[[Path, str | None, str | None], object] | None = None,
) -> BackendUsageSnapshot:
    latest_by_limit: dict[str, BackendUsageWindow] = {}
    payload, selected_model, auth_type = _load_gemini_quota_payload(
        gemini_home,
        quota_loader=quota_loader,
    )

    if not isinstance(payload, dict):
        return BackendUsageSnapshot(
            backend="gemini",
            events_scanned=0,
            latest_by_limit=latest_by_limit,
            account_type=auth_type,
        )

    user_data = payload.get("userData")
    account_type = auth_type
    if isinstance(user_data, dict):
        user_tier_name = user_data.get("userTierName")
        user_tier = user_data.get("userTier")
        if isinstance(user_tier_name, str):
            account_type = user_tier_name
        elif isinstance(user_tier, str):
            account_type = user_tier

    quota = payload.get("quota")
    buckets = quota.get("buckets") if isinstance(quota, dict) else None
    if not isinstance(buckets, list):
        return BackendUsageSnapshot(
            backend="gemini",
            events_scanned=0,
            latest_by_limit=latest_by_limit,
            account_type=account_type,
        )

    seen_at = payload.get("seenAt")
    if not isinstance(seen_at, str):
        return BackendUsageSnapshot(
            backend="gemini",
            events_scanned=0,
            latest_by_limit=latest_by_limit,
            account_type=account_type,
        )

    ordered_buckets: list[dict[str, object]] = []
    for bucket in buckets:
        if isinstance(bucket, dict) and bucket.get("modelId") == selected_model:
            ordered_buckets.append(bucket)
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        if bucket in ordered_buckets:
            continue
        ordered_buckets.append(bucket)

    for bucket in ordered_buckets:
        model_id = bucket.get("modelId")
        if not isinstance(model_id, str):
            continue
        latest_by_limit[model_id] = BackendUsageWindow(
            backend="gemini",
            limit_key=model_id,
            raw_limit_key=model_id,
            seen_at=_parse_iso8601_utc(seen_at),
            resets_at=(
                _parse_iso8601_utc(bucket["resetTime"])
                if isinstance(bucket.get("resetTime"), str)
                else None
            ),
            used_percent=_parse_remaining_fraction_used_percent(bucket.get("remainingFraction")),
            status="allowed",
            source=gemini_home / "settings.json",
        )

    if not latest_by_limit:
        return BackendUsageSnapshot(
            backend="gemini",
            events_scanned=0,
            latest_by_limit=latest_by_limit,
            account_type=account_type,
        )

    return BackendUsageSnapshot(
        backend="gemini",
        events_scanned=len(latest_by_limit),
        latest_by_limit=latest_by_limit,
        account_type=account_type,
    )


def load_backend_usage_snapshot(
    backend: str,
    support_dir: Path | None = None,
    *,
    auth_status_loader: Callable[[list[str]], object] | None = None,
    gemini_quota_loader: Callable[[Path, str | None, str | None], object] | None = None,
) -> BackendUsageSnapshot:
    normalized = _normalize_backend_name(backend)
    if normalized == "claude":
        return _scan_claude_usage_snapshot(
            support_dir or _default_claude_support_dir(),
            auth_status_loader=auth_status_loader,
        )
    if normalized == "codex":
        return _scan_codex_usage_snapshot(support_dir or _default_codex_home())
    return _scan_gemini_usage_snapshot(
        support_dir or _default_gemini_home(),
        quota_loader=gemini_quota_loader,
    )


def _iter_sorted_windows(snapshot: BackendUsageSnapshot) -> Iterable[BackendUsageWindow]:
    seen: set[str] = set()
    for limit_key in _PREFERRED_LIMIT_ORDER:
        window = snapshot.latest_by_limit.get(limit_key)
        if window is not None:
            seen.add(limit_key)
            yield window
    for limit_key, window in snapshot.latest_by_limit.items():
        if limit_key not in seen:
            yield window


def _is_exhausted(window: BackendUsageWindow, *, now: datetime) -> bool:
    if window.resets_at is not None and window.resets_at <= now:
        return False
    if window.status == "rejected":
        return True
    return window.used_percent is not None and window.used_percent >= 100.0


def _is_warning(
    window: BackendUsageWindow,
    *,
    now: datetime,
    warning_used_percent_threshold: float,
) -> bool:
    if window.resets_at is not None and window.resets_at <= now:
        return False
    if window.status == "allowed_warning" and (
        window.used_percent is None or window.used_percent >= warning_used_percent_threshold
    ):
        return True
    return window.used_percent is not None and window.used_percent >= warning_used_percent_threshold


def decide_backend_usage(
    snapshot: BackendUsageSnapshot,
    *,
    now: datetime | None = None,
    minimum_remaining_percent: float = 10.0,
) -> BackendUsageDecision:
    current_time = now or datetime.now(UTC)
    backend_name = _display_backend_name(snapshot.backend)
    windows = list(_iter_sorted_windows(snapshot))
    if not windows:
        return BackendUsageDecision(
            should_use_backend=False,
            reason=f"No {backend_name} usage data is available from local state or CLI signals.",
        )

    for window in windows:
        if _is_exhausted(window, now=current_time):
            return BackendUsageDecision(
                should_use_backend=False,
                reason=(
                    f"{backend_name} {window.limit_key} is exhausted until "
                    f"{_format_dt(window.resets_at)}."
                ),
                window=window,
            )

    for window in windows:
        remaining = window.remaining_percent
        if remaining is not None and remaining < minimum_remaining_percent:
            reason = (
                f"{backend_name} {window.limit_key} is below the configured minimum "
                f"remaining usage ({remaining:.0f}% < {minimum_remaining_percent:.0f}%) "
                f"and resets at {_format_dt(window.resets_at)}."
            )
            return BackendUsageDecision(
                should_use_backend=False,
                reason=reason,
                window=window,
            )
        if _is_warning(window, now=current_time, warning_used_percent_threshold=100.0):
            remaining = window.remaining_percent
            if remaining is not None:
                reason = (
                    f"{backend_name} {window.limit_key} reported a warning state "
                    f"with about {remaining:.0f}% remaining; "
                    f"resets at {_format_dt(window.resets_at)}."
                )
            else:
                reason = (
                    f"{backend_name} {window.limit_key} reported a warning state "
                    f"and resets at {_format_dt(window.resets_at)}."
                )
            return BackendUsageDecision(
                should_use_backend=False,
                reason=reason,
                window=window,
            )

    current_window = windows[0]
    if current_window.resets_at is not None and current_window.resets_at <= current_time:
        return BackendUsageDecision(
            should_use_backend=True,
            reason=(
                f"Last known {backend_name} {current_window.limit_key} window already reset at "
                f"{_format_dt(current_window.resets_at)}."
            ),
            window=current_window,
        )

    if current_window.remaining_percent is not None:
        reason = (
            f"{backend_name} {current_window.limit_key} has about "
            f"{current_window.remaining_percent:.0f}% remaining and resets at "
            f"{_format_dt(current_window.resets_at)}."
        )
    elif current_window.status is not None:
        reason = (
            f"{backend_name} {current_window.limit_key} is currently {current_window.status} "
            f"and resets at {_format_dt(current_window.resets_at)}."
        )
    else:
        reason = (
            f"{backend_name} {current_window.limit_key} appears usable and resets at "
            f"{_format_dt(current_window.resets_at)}."
        )
    return BackendUsageDecision(
        should_use_backend=True,
        reason=reason,
        window=current_window,
    )


def has_enough_backend_usage(
    backend: str,
    *,
    minimum_remaining_percent: float = 10.0,
    snapshot: BackendUsageSnapshot | None = None,
    support_dir: Path | None = None,
    now: datetime | None = None,
    auth_status_loader: Callable[[list[str]], object] | None = None,
    gemini_quota_loader: Callable[[Path, str | None, str | None], object] | None = None,
) -> bool:
    usage_snapshot = snapshot or load_backend_usage_snapshot(
        backend,
        support_dir,
        auth_status_loader=auth_status_loader,
        gemini_quota_loader=gemini_quota_loader,
    )
    decision = decide_backend_usage(
        usage_snapshot,
        now=now,
        minimum_remaining_percent=minimum_remaining_percent,
    )
    return decision.should_use_backend


def ask_backend_usage_question(
    backend: str,
    question: str,
    *,
    snapshot: BackendUsageSnapshot | None = None,
    support_dir: Path | None = None,
    now: datetime | None = None,
    minimum_remaining_percent: float = 10.0,
    auth_status_loader: Callable[[list[str]], object] | None = None,
    gemini_quota_loader: Callable[[Path, str | None, str | None], object] | None = None,
) -> BackendUsageAnswer:
    current_time = now or datetime.now(UTC)
    usage_snapshot = snapshot or load_backend_usage_snapshot(
        backend,
        support_dir,
        auth_status_loader=auth_status_loader,
        gemini_quota_loader=gemini_quota_loader,
    )
    decision = decide_backend_usage(
        usage_snapshot,
        now=current_time,
        minimum_remaining_percent=minimum_remaining_percent,
    )
    window = decision.window
    backend_name = _display_backend_name(usage_snapshot.backend)
    normalized = question.strip().lower()

    if "reset" in normalized:
        if window is None:
            answer = f"No {backend_name} rate-limit reset time is available from current signals."
        else:
            answer = (
                f"The latest {backend_name} {window.limit_key} reset is "
                f"{_format_dt(window.resets_at)}."
            )
    elif "left" in normalized or "remaining" in normalized:
        if window is None:
            answer = f"No {backend_name} usage data is available from current signals."
        elif window.remaining_percent is not None:
            answer = (
                f"About {window.remaining_percent:.0f}% remains in the current "
                f"{backend_name} {window.limit_key} window; "
                f"it resets at {_format_dt(window.resets_at)}."
            )
        else:
            answer = (
                f"Exact {backend_name} usage remaining is unknown. "
                f"The latest signal says {window.limit_key} "
                f"is {window.status or 'present'} and resets at {_format_dt(window.resets_at)}."
            )
    elif "use" in normalized or "backend" in normalized or "can i" in normalized:
        prefix = "Yes" if decision.should_use_backend else "No"
        answer = f"{prefix}: {decision.reason}"
    else:
        if window is None:
            answer = f"No {backend_name} usage data is available from current signals."
        else:
            suffix = (
                f", account_type={usage_snapshot.account_type}"
                if usage_snapshot.account_type
                else ""
            )
            answer = (
                f"Latest {backend_name} signal: {window.limit_key}, "
                f"resets at {_format_dt(window.resets_at)}{suffix}."
            )

    return BackendUsageAnswer(
        backend=usage_snapshot.backend,
        question=question,
        answer=answer,
        decision=decision,
    )
