from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from code_reviewer.backend_usage import (
    BackendUsageSnapshot,
    BackendUsageWindow,
    ask_backend_usage_question,
    decide_backend_usage,
    has_enough_backend_usage,
    load_backend_usage_snapshot,
)
from code_reviewer.claude_usage import ask_claude_usage_question, has_enough_claude_usage


def _write_jsonl(path: Path, *events: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def _claude_event(
    *,
    audit_timestamp: str,
    status: str,
    rate_limit_type: str,
    resets_at: int,
    utilization: float | None = None,
) -> dict:
    rate_limit_info: dict[str, object] = {
        "status": status,
        "rateLimitType": rate_limit_type,
        "resetsAt": resets_at,
        "overageStatus": "rejected",
        "isUsingOverage": False,
    }
    if utilization is not None:
        rate_limit_info["utilization"] = utilization
    return {
        "type": "rate_limit_event",
        "rate_limit_info": rate_limit_info,
        "_audit_timestamp": audit_timestamp,
    }


def _codex_event(
    *,
    timestamp: str,
    primary_used_percent: float,
    primary_resets_at: int,
    secondary_used_percent: float,
    secondary_resets_at: int,
    plan_type: str = "team",
) -> dict:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {
                    "used_percent": primary_used_percent,
                    "window_minutes": 300,
                    "resets_at": primary_resets_at,
                },
                "secondary": {
                    "used_percent": secondary_used_percent,
                    "window_minutes": 10080,
                    "resets_at": secondary_resets_at,
                },
                "credits": None,
                "plan_type": plan_type,
            },
        },
    }


def _gemini_quota_payload(
    *,
    seen_at: str = "2026-03-23T18:44:11Z",
    selected_model: str = "gemini-3-flash-preview",
    auth_type: str = "oauth-personal",
    tier_name: str = "Gemini Code Assist in Google One AI Pro",
) -> dict:
    return {
        "seenAt": seen_at,
        "selectedModel": selected_model,
        "authType": auth_type,
        "userData": {
            "projectId": "augmented-element-p5ctx",
            "userTier": "g1-pro-tier",
            "userTierName": tier_name,
        },
        "quota": {
            "buckets": [
                {
                    "resetTime": "2026-03-24T17:44:11Z",
                    "tokenType": "REQUESTS",
                    "modelId": "gemini-3-flash-preview",
                    "remainingFraction": 0.907,
                },
                {
                    "resetTime": "2026-03-24T17:44:33Z",
                    "tokenType": "REQUESTS",
                    "modelId": "gemini-3-pro-preview",
                    "remainingFraction": 0.9266667,
                },
            ]
        },
    }


def test_load_backend_usage_snapshot_reads_latest_claude_event_per_limit(tmp_path: Path) -> None:
    support_dir = tmp_path / "Claude"
    _write_jsonl(
        support_dir / "local-agent-mode-sessions" / "env" / "org" / "run" / "audit.jsonl",
        _claude_event(
            audit_timestamp="2026-03-23T05:00:00Z",
            status="allowed",
            rate_limit_type="five_hour",
            resets_at=1774249200,
        ),
        _claude_event(
            audit_timestamp="2026-03-23T06:00:00Z",
            status="allowed_warning",
            rate_limit_type="five_hour",
            resets_at=1774249200,
            utilization=0.91,
        ),
        _claude_event(
            audit_timestamp="2026-03-23T04:00:00Z",
            status="allowed",
            rate_limit_type="seven_day",
            resets_at=1774800000,
        ),
    )

    snapshot = load_backend_usage_snapshot(
        "claude",
        support_dir,
        auth_status_loader=lambda args: {"subscriptionType": "max"},
    )

    assert snapshot.backend == "claude"
    assert snapshot.events_scanned == 3
    assert snapshot.account_type == "max"
    assert snapshot.latest_by_limit["five_hour"].used_percent == 91.0
    assert snapshot.latest_by_limit["five_hour"].status == "allowed_warning"
    assert snapshot.latest_by_limit["seven_day"].limit_key == "seven_day"


def test_load_backend_usage_snapshot_reads_codex_primary_and_secondary(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    _write_jsonl(
        codex_home / "sessions" / "2026" / "03" / "23" / "rollout.jsonl",
        _codex_event(
            timestamp="2026-03-23T17:47:35.256Z",
            primary_used_percent=14.0,
            primary_resets_at=1774298398,
            secondary_used_percent=54.0,
            secondary_resets_at=1774400615,
        ),
        _codex_event(
            timestamp="2026-03-23T17:52:04.643Z",
            primary_used_percent=27.0,
            primary_resets_at=1774298398,
            secondary_used_percent=58.0,
            secondary_resets_at=1774400615,
        ),
    )

    snapshot = load_backend_usage_snapshot("codex", codex_home)

    assert snapshot.backend == "codex"
    assert snapshot.events_scanned == 2
    assert snapshot.account_type == "team"
    assert snapshot.latest_by_limit["five_hour"].raw_limit_key == "primary"
    assert snapshot.latest_by_limit["five_hour"].used_percent == 27.0
    assert snapshot.latest_by_limit["seven_day"].raw_limit_key == "secondary"
    assert snapshot.latest_by_limit["seven_day"].used_percent == 58.0


def test_load_backend_usage_snapshot_reads_gemini_selected_model_quota(tmp_path: Path) -> None:
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir()
    (gemini_home / "settings.json").write_text(
        json.dumps(
            {
                "security": {"auth": {"selectedType": "oauth-personal"}},
                "model": {"name": "gemini-3-flash-preview"},
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_backend_usage_snapshot(
        "gemini",
        gemini_home,
        gemini_quota_loader=lambda home, model, auth: _gemini_quota_payload(
            selected_model=model or "gemini-3-flash-preview",
            auth_type=auth or "oauth-personal",
        ),
    )

    assert snapshot.backend == "gemini"
    assert snapshot.events_scanned == 1
    assert snapshot.account_type == "Gemini Code Assist in Google One AI Pro"
    assert round(snapshot.latest_by_limit["gemini-3-flash-preview"].used_percent or 0.0, 1) == 9.3


def test_load_backend_usage_snapshot_handles_unsupported_gemini_auth(tmp_path: Path) -> None:
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir()
    (gemini_home / "settings.json").write_text(
        json.dumps(
            {
                "security": {"auth": {"selectedType": "gemini-api-key"}},
                "model": {"name": "gemini-3-flash-preview"},
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_backend_usage_snapshot("gemini", gemini_home)

    assert snapshot.backend == "gemini"
    assert snapshot.events_scanned == 0
    assert snapshot.latest_by_limit == {}
    assert snapshot.account_type == "gemini-api-key"


def test_decide_backend_usage_rejects_active_exhausted_window() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="codex",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="codex",
                limit_key="five_hour",
                raw_limit_key="primary",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=1),
                used_percent=100.0,
                status=None,
                source=Path("/tmp/rollout.jsonl"),
            )
        },
    )

    decision = decide_backend_usage(snapshot, now=now)

    assert decision.should_use_backend is False
    assert "exhausted" in decision.reason


def test_decide_backend_usage_warns_when_window_is_low() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="codex",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="codex",
                limit_key="five_hour",
                raw_limit_key="primary",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=1),
                used_percent=92.0,
                status=None,
                source=Path("/tmp/rollout.jsonl"),
            )
        },
    )

    decision = decide_backend_usage(snapshot, now=now)

    assert decision.should_use_backend is False
    assert "8% < 10%" in decision.reason


def test_has_enough_backend_usage_uses_default_ten_percent_threshold() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="codex",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="codex",
                limit_key="five_hour",
                raw_limit_key="primary",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=1),
                used_percent=91.0,
                status=None,
                source=Path("/tmp/rollout.jsonl"),
            )
        },
    )

    assert has_enough_backend_usage("codex", snapshot=snapshot, now=now) is False


def test_has_enough_backend_usage_accepts_custom_threshold() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="codex",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="codex",
                limit_key="five_hour",
                raw_limit_key="primary",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=1),
                used_percent=91.0,
                status=None,
                source=Path("/tmp/rollout.jsonl"),
            )
        },
    )

    assert (
        has_enough_backend_usage(
            "codex",
            snapshot=snapshot,
            now=now,
            minimum_remaining_percent=5.0,
        )
        is True
    )


def test_ask_backend_usage_question_reports_codex_remaining_percent() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="codex",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="codex",
                limit_key="five_hour",
                raw_limit_key="primary",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=2),
                used_percent=27.0,
                status=None,
                source=Path("/tmp/rollout.jsonl"),
            )
        },
        account_type="team",
    )

    answer = ask_backend_usage_question(
        "codex",
        "how much usage is left?",
        snapshot=snapshot,
        now=now,
    )

    assert "73% remains" in answer.answer
    assert answer.decision.should_use_backend is True


def test_has_enough_backend_usage_accepts_gemini_current_model_quota(tmp_path: Path) -> None:
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir()
    (gemini_home / "settings.json").write_text(
        json.dumps(
            {
                "security": {"auth": {"selectedType": "oauth-personal"}},
                "model": {"name": "gemini-3-flash-preview"},
            }
        ),
        encoding="utf-8",
    )

    assert (
        has_enough_backend_usage(
            "gemini",
            support_dir=gemini_home,
            minimum_remaining_percent=10.0,
            gemini_quota_loader=lambda home, model, auth: _gemini_quota_payload(
                selected_model=model or "gemini-3-flash-preview",
                auth_type=auth or "oauth-personal",
            ),
        )
        is True
    )


def test_ask_backend_usage_question_answers_claude_backend_decision() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="claude",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="claude",
                limit_key="five_hour",
                raw_limit_key="five_hour",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=2),
                used_percent=95.0,
                status="allowed_warning",
                source=Path("/tmp/audit.jsonl"),
            )
        },
    )

    answer = ask_backend_usage_question(
        "claude",
        "should I use the claude backend right now?",
        snapshot=snapshot,
        now=now,
    )

    assert answer.answer.startswith("No:")
    assert answer.decision.should_use_backend is False


def test_claude_wrapper_uses_generic_backend_api() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="claude",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="claude",
                limit_key="five_hour",
                raw_limit_key="five_hour",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=2),
                used_percent=None,
                status="allowed",
                source=Path("/tmp/audit.jsonl"),
            )
        },
    )

    answer = ask_claude_usage_question("how much usage is left?", snapshot=snapshot, now=now)

    assert "unknown" in answer.answer.lower()


def test_has_enough_claude_usage_allows_unknown_remaining_when_status_is_allowed() -> None:
    now = datetime(2026, 3, 23, 6, 30, tzinfo=UTC)
    snapshot = BackendUsageSnapshot(
        backend="claude",
        events_scanned=1,
        latest_by_limit={
            "five_hour": BackendUsageWindow(
                backend="claude",
                limit_key="five_hour",
                raw_limit_key="five_hour",
                seen_at=now - timedelta(minutes=1),
                resets_at=now + timedelta(hours=2),
                used_percent=None,
                status="allowed",
                source=Path("/tmp/audit.jsonl"),
            )
        },
    )

    assert has_enough_claude_usage(snapshot=snapshot, now=now) is True
