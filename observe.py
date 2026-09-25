"""Bounded, read-only observability for the current economy task tree.

The script reads only the selected account's state_5.sqlite (SQLite URI
``mode=ro``), the rollout paths recorded for selected threads, and the
selected account's thread_history_1.sqlite for status fields.  It deliberately
does not read or print user messages, titles, previews, item_json, tool
arguments, tool output, encrypted content, or configuration files.

Usage (from the workspace):
    py work/observe.py --account main
    py work/observe.py --account main --since 1788775973
    py work/observe.py --account main --ids ID [ID ...] --json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable
import economy


# This is the current task that bounds the economy-task tree.  The default
# since value is obtained from this row, rather than from wall-clock time.
ECONOMY_ROOT_ID = None
ROOT_PASTED_MARKER = b"Pasted text contains the user's request."
UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")
SAFE_THREAD_COLUMNS = (
    "id",
    "rollout_path",
    "created_at",
    "created_at_ms",
    "source",
    "model",
    "reasoning_effort",
    "agent_role",
    "agent_path",
    "tokens_used",
    "cli_version",
    "thread_source",
)


def ro_connect(path: Path) -> sqlite3.Connection:
    """Open a database without creating, writing, or migrating it."""

    uri = f"file:{path.as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=0.25)
    db.row_factory = sqlite3.Row
    return db


def table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "select 1 from sqlite_master where type='table' and name=?", (name,)
    ).fetchone()
    return row is not None


def columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"pragma table_info({table})")}


def safe_thread_select(thread_cols: set[str]) -> str:
    """Build an allowlisted projection; never select message-bearing columns."""

    return ",".join(column for column in SAFE_THREAD_COLUMNS if column in thread_cols)


def clean_name(value: Any) -> str:
    """Return a short, line-safe metadata name; never expose arbitrary text."""

    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:120]


def source_class(value: Any) -> str | None:
    """Keep source fields categorical; never echo serialized metadata."""

    if value is None:
        return None
    text = str(value)
    if text in {"vscode", "cli", "app", "api", "user", "subagent", "guardian_review"}:
        return text
    if text.startswith("{\"subagent\""):
        return "subagent_metadata"
    return "other"


def as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def extract_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    allowed = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    result = {key: as_int(value.get(key)) for key in allowed if value.get(key) is not None}
    return result or None


def _usage_sum(records: Iterable[dict[str, int]]) -> dict[str, int] | None:
    """Sum independent response usage without deriving totals from components."""

    total: collections.Counter[str] = collections.Counter()
    for record in records:
        total.update(record)
    return dict(sorted(total.items())) or None


def parse_rollout(path: Path, expected_thread_id: str | None = None) -> dict[str, Any]:
    """Read structural rollout metadata only, line by line."""

    response_tool_counts: collections.Counter[str] = collections.Counter()
    tool_counts_by_turn: dict[str, collections.Counter[str]] = {}
    response_status_counts: collections.Counter[str] = collections.Counter()
    context_pairs: collections.Counter[tuple[str | None, str | None]] = collections.Counter()
    role_counts: collections.Counter[str] = collections.Counter()
    latest_context_usage: dict[str, int] | None = None
    token_usage_record_count = 0
    context_windows: set[int] = set()
    task_complete = False
    final_answer_items = 0
    event_error_count = 0
    marker_seen = False
    marker_occurrences = 0
    line_count = 0
    max_ordinal: int | None = None
    parse_errors = 0
    session_meta: dict[str, Any] = {}
    matching_session_meta: dict[str, Any] = {}
    matching_session_meta_count = 0
    session_meta_count = 0
    own_history_start_ordinal: int | None = None
    turn_context_count = 0
    active_turn_id: str | None = None
    turns: dict[str, dict[str, Any]] = {}
    own_responses: dict[str, dict[str, Any]] = {}
    duplicate_response_records = 0
    malformed_attribution_records = 0
    ambiguous_terminal_ordinals: list[int | None] = []
    identified_terminal_events: list[tuple[str, int | None]] = []

    def note_turn(turn: dict[str, Any]) -> None:
        previous = turn.get("first_line")
        turn["first_line"] = line_count if previous is None else min(previous, line_count)

    with path.open("rb") as stream:
        for raw in stream:
            line_count += 1
            marker_occurrences += raw.count(ROOT_PASTED_MARKER)
            if marker_occurrences:
                marker_seen = True
            try:
                event = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                parse_errors += 1
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            ordinal = as_int(event.get("ordinal"))
            if ordinal is not None:
                max_ordinal = ordinal if max_ordinal is None else max(max_ordinal, ordinal)
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue

            if kind == "session_meta":
                session_meta_count += 1
                for key in ("agent_path", "agent_role", "model_provider", "cli_version"):
                    if payload.get(key) is not None:
                        session_meta[key] = clean_name(payload[key])
                meta_id = payload.get("id") or payload.get("session_id")
                if expected_thread_id is not None and meta_id == expected_thread_id:
                    matching_session_meta_count += 1
                    own_history_start_ordinal = as_int(payload.get("subagent_history_start_ordinal"))
                    matching_session_meta = {
                        key: clean_name(payload[key])
                        for key in ("agent_path", "agent_role", "model_provider", "cli_version")
                        if payload.get(key) is not None
                    }
            elif kind == "turn_context":
                turn_context_count += 1
                model = clean_name(payload["model"]) if payload.get("model") is not None else None
                effort = clean_name(payload["effort"]) if payload.get("effort") is not None else None
                context_pairs[(model, effort)] += 1
                turn_id = payload.get("turn_id")
                if isinstance(turn_id, str):
                    active_turn_id = turn_id
                    turn = turns.setdefault(turn_id, {"turn_id": turn_id})
                    note_turn(turn)
                    turn.update({"model": model, "effort": effort, "context_line": line_count,
                                 "context_ordinal": as_int(event.get("ordinal"))})
                window = as_int(payload.get("model_context_window"))
                if window is not None:
                    context_windows.add(window)
            elif kind == "token_usage_record":
                token_usage_record_count += 1
                usage = extract_usage(payload.get("usage"))
                thread_id = payload.get("thread_id")
                turn_id = payload.get("turn_id")
                response_id = payload.get("response_id")
                if expected_thread_id is not None and thread_id == expected_thread_id:
                    if not all(isinstance(value, str) and value for value in (turn_id, response_id)) or usage is None:
                        malformed_attribution_records += 1
                    elif response_id in own_responses:
                        duplicate_response_records += 1
                    else:
                        own_responses[response_id] = {
                            "turn_id": turn_id,
                            "response_id": response_id,
                            "usage": usage,
                        }
                        turn = turns.setdefault(turn_id, {"turn_id": turn_id})
                        note_turn(turn)
                        turn.setdefault("response_ids", []).append(response_id)
            elif kind == "event_msg":
                event_type = payload.get("type")
                if event_type == "token_count":
                    info = payload.get("info")
                    if isinstance(info, dict):
                        usage = extract_usage(info.get("total_token_usage"))
                        if usage is not None:
                            latest_context_usage = usage
                        window = as_int(info.get("model_context_window"))
                        if window is not None:
                            context_windows.add(window)
                elif event_type == "task_complete":
                    task_complete = True
                    turn_id = payload.get("turn_id")
                    if isinstance(turn_id, str):
                        identified_terminal_events.append((turn_id, as_int(event.get("ordinal"))))
                        turn = turns.setdefault(turn_id, {"turn_id": turn_id})
                        note_turn(turn)
                        error_present = payload.get("error") is not None or payload.get("error_code") is not None
                        turn["task_complete"] = True
                        turn["task_complete_error"] = error_present
                        turn["completion_line"] = line_count
                        turn["completion_ordinal"] = as_int(event.get("ordinal"))
                    else:
                        ambiguous_terminal_ordinals.append(as_int(event.get("ordinal")))
                elif event_type == "error":
                    event_error_count += 1
                    turn_id = payload.get("turn_id")
                    if isinstance(turn_id, str):
                        identified_terminal_events.append((turn_id, as_int(event.get("ordinal"))))
                        turn = turns.setdefault(turn_id, {"turn_id": turn_id})
                        note_turn(turn)
                        turn["event_error"] = True
                        turn["error_ordinal"] = as_int(event.get("ordinal"))
                    else:
                        ambiguous_terminal_ordinals.append(as_int(event.get("ordinal")))
                elif event_type == "item_completed":
                    item = payload.get("item")
                    if isinstance(item, dict):
                        if item.get("type") == "AgentMessage" and item.get("phase") == "final_answer":
                            final_answer_items += 1
            elif kind == "response_item":
                item_type = payload.get("type")
                name = payload.get("name")
                # Tool names are metadata. Arguments, content, and outputs are
                # intentionally never inspected or emitted.
                if isinstance(name, str) and item_type in (
                    "custom_tool_call",
                    "function_call",
                ):
                    safe_name = clean_name(name)
                    response_tool_counts[safe_name] += 1
                    if active_turn_id is not None:
                        tool_counts_by_turn.setdefault(active_turn_id, collections.Counter())[safe_name] += 1
                status = payload.get("status")
                if isinstance(status, str):
                    response_status_counts[clean_name(status)] += 1
                role = payload.get("role")
                if isinstance(role, str):
                    role_counts[clean_name(role)] += 1

    own_turn_ids = {record["turn_id"] for record in own_responses.values()}
    if expected_thread_id is not None and matching_session_meta_count == 1:
        for turn_id, turn in turns.items():
            ordinals = [turn.get("context_ordinal"), turn.get("completion_ordinal"), turn.get("error_ordinal")]
            if ((own_history_start_ordinal is None and session_meta_count == 1) or
                    (own_history_start_ordinal is not None and any(
                        value is not None and value >= own_history_start_ordinal for value in ordinals))):
                own_turn_ids.add(turn_id)
    own_turns = []
    for turn_id in sorted(own_turn_ids, key=lambda value: turns.get(value, {}).get("first_line", 0)):
        turn = turns.get(turn_id, {"turn_id": turn_id})
        response_ids = turn.get("response_ids", [])
        usage_records = [own_responses[value]["usage"] for value in response_ids]
        completion_error = bool(turn.get("task_complete_error") or turn.get("event_error"))
        if completion_error:
            rollout_status = "failed"
        elif turn.get("task_complete"):
            rollout_status = "completed"
        else:
            rollout_status = "incomplete"
        own_turns.append({
            "turn_id": turn_id,
            "model": turn.get("model"),
            "effort": turn.get("effort"),
            "response_ids": response_ids,
            "response_count": len(response_ids),
            "usage": _usage_sum(usage_records),
            "task_complete_event": bool(turn.get("task_complete")),
            "error": completion_error,
            "rollout_status": rollout_status,
            "context_line": turn.get("context_line"),
            "context_ordinal": turn.get("context_ordinal"),
            "first_line": turn.get("first_line"),
        })
    own_pairs = collections.Counter(
        (turn.get("model"), turn.get("effort")) for turn in own_turns if turn.get("response_count")
    )
    own_tool_counts: collections.Counter[str] = collections.Counter()
    if expected_thread_id is None:
        own_tool_counts.update(response_tool_counts)
    else:
        for turn_id in own_turn_ids:
            own_tool_counts.update(tool_counts_by_turn.get(turn_id, {}))
    integrity_ok = parse_errors == 0 and malformed_attribution_records == 0
    ambiguous_terminal_events = 0
    if expected_thread_id is not None and matching_session_meta_count == 1:
        for ordinal in ambiguous_terminal_ordinals:
            if session_meta_count == 1 or own_history_start_ordinal is None or ordinal is None or ordinal >= own_history_start_ordinal:
                ambiguous_terminal_events += 1
        for turn_id, ordinal in identified_terminal_events:
            if turn_id in own_turn_ids:
                continue
            if own_history_start_ordinal is None or ordinal is None or ordinal >= own_history_start_ordinal:
                ambiguous_terminal_events += 1
    return {
        "rollout_read": True,
        "rollout_line_count": line_count,
        "rollout_byte_size": path.stat().st_size,
        "rollout_max_ordinal": max_ordinal,
        "rollout_parse_errors": parse_errors,
        "session_meta": matching_session_meta if expected_thread_id is not None else session_meta,
        "expected_thread_id": expected_thread_id,
        "matching_session_meta_count": matching_session_meta_count if expected_thread_id is not None else None,
        "turn_context_count": turn_context_count,
        "all_context_pairs": [
            {"model": model, "effort": effort, "count": count}
            for (model, effort), count in sorted(context_pairs.items(), key=lambda x: str(x[0]))
        ],
        # Compatibility name now deliberately means attributable execution only.
        "actual_context_pairs": [
            {"model": model, "effort": effort, "count": count}
            for (model, effort), count in sorted(own_pairs.items(), key=lambda x: str(x[0]))
        ],
        "own_turns": own_turns,
        "own_usage_response_count": len(own_responses),
        "duplicate_response_records": duplicate_response_records,
        "malformed_attribution_records": malformed_attribution_records,
        "ambiguous_terminal_events": ambiguous_terminal_events,
        "integrity_ok": integrity_ok,
        "model_context_windows": sorted(context_windows),
        "latest_rollout_token_usage": own_turns[-1]["usage"] if own_turns else None,
        "latest_context_token_usage": latest_context_usage,
        "token_usage_records": token_usage_record_count,
        "all_history_tool_names": dict(sorted(response_tool_counts.items())),
        "all_history_tool_invocation_count": sum(response_tool_counts.values()),
        "executed_tool_names": dict(sorted(own_tool_counts.items())),
        "executed_tool_invocation_count": sum(own_tool_counts.values()),
        "response_status_counts": dict(sorted(response_status_counts.items())),
        "response_role_counts": dict(sorted(role_counts.items())),
        "task_complete_event": task_complete,
        "final_answer_items": final_answer_items,
        "event_error_count": event_error_count,
        "root_pasted_request_marker": marker_seen,
        # Count is structural evidence only; the marker's surrounding content
        # is never returned.
        "root_pasted_request_marker_occurrences": marker_occurrences,
    }


def read_history(home: Path, ids: Iterable[str]) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    """Read allowlisted per-turn status metadata, including paginated offsets."""

    db_path = home / "thread_history_1.sqlite"
    if not db_path.exists():
        return {}, "thread_history_1.sqlite is absent"
    try:
        db = ro_connect(db_path)
    except (OSError, sqlite3.Error) as exc:
        return {}, f"thread_history_1.sqlite unavailable: {type(exc).__name__}"
    try:
        if not table_exists(db, "thread_turns"):
            return {}, "thread_turns table is absent"
        history_cols = columns(db, "thread_turns")
        allowed = [
            name for name in (
                "turn_id", "rollout_ordinal", "status", "error_json",
                "rollout_byte_offset", "rollout_end_ordinal", "rollout_end_byte_offset",
            ) if name in history_cols
        ]
        result: dict[str, list[dict[str, Any]]] = {}
        for thread_id in ids:
            rows = db.execute(
                f"select {','.join(allowed)} from thread_turns where thread_id=? order by rollout_ordinal",
                (thread_id,),
            ).fetchall()
            result[thread_id] = [{
                "turn_id": clean_name(row["turn_id"]) if "turn_id" in row.keys() else None,
                "rollout_ordinal": as_int(row["rollout_ordinal"]) if "rollout_ordinal" in row.keys() else None,
                "status": clean_name(row["status"]) if "status" in row.keys() and row["status"] is not None else None,
                # Never parse or emit error_json; presence is sufficient for verification.
                "error": bool(row["error_json"]) if "error_json" in row.keys() else None,
                "rollout_byte_offset": as_int(row["rollout_byte_offset"]) if "rollout_byte_offset" in row.keys() else None,
                "rollout_end_ordinal": as_int(row["rollout_end_ordinal"]) if "rollout_end_ordinal" in row.keys() else None,
                "rollout_end_byte_offset": as_int(row["rollout_end_byte_offset"]) if "rollout_end_byte_offset" in row.keys() else None,
            } for row in rows]
        return result, None
    except sqlite3.Error as exc:
        return {}, f"thread_history query unavailable: {type(exc).__name__}"
    finally:
        db.close()


def execution_verdict(
    rollout: dict[str, Any] | None,
    history: list[dict[str, Any]],
    expected_model: str | None,
    expected_effort: str | None,
) -> dict[str, Any]:
    """Classify the most recent intended turn without turning absence into PASS."""

    if rollout is None:
        return {"result": "unknown", "reason": "rollout_unavailable"}
    if not rollout.get("integrity_ok"):
        return {"result": "unknown", "reason": "malformed_or_truncated_rollout"}
    if rollout.get("expected_thread_id") and rollout.get("matching_session_meta_count") != 1:
        return {"result": "unknown", "reason": "thread_identity_unverified"}
    if rollout.get("ambiguous_terminal_events"):
        return {"result": "unknown", "reason": "terminal_event_without_turn_id"}

    own_by_id = {turn["turn_id"]: turn for turn in rollout.get("own_turns", [])}
    rollout_latest = rollout.get("own_turns", [])[-1] if rollout.get("own_turns") else None
    latest_history = history[-1] if history else None
    latest = None
    if rollout_latest and latest_history and rollout_latest.get("turn_id") != latest_history.get("turn_id"):
        history_turn = own_by_id.get(latest_history.get("turn_id"))
        rollout_order = rollout_latest.get("context_ordinal")
        history_order = latest_history.get("rollout_ordinal")
        if history_turn is not None:
            history_order = history_turn.get("context_ordinal")
            rollout_order = rollout_order if rollout_order is not None else rollout_latest.get("context_line")
            history_order = history_order if history_order is not None else history_turn.get("context_line")
        if isinstance(rollout_order, int) and isinstance(history_order, int):
            if rollout_order > history_order:
                latest, latest_history = rollout_latest, None
            else:
                latest = history_turn
        else:
            status = (latest_history.get("status") or "").lower()
            if latest_history.get("error") or status in {"failed", "interrupted", "cancelled", "canceled", "error"}:
                return {"result": "failed", "reason": "latest_history_turn_failed_order_unverified",
                        "turn_id": latest_history.get("turn_id"), "status": status or None,
                        "error": bool(latest_history.get("error")), "has_attributable_inference": False}
            return {"result": "unknown", "reason": "turn_order_unverified",
                    "turn_id": latest_history.get("turn_id"), "status": status or None}
    elif latest_history:
        latest = own_by_id.get(latest_history.get("turn_id"))
    else:
        latest = rollout_latest
    if latest_history:
        end_offset = latest_history.get("rollout_end_byte_offset")
        end_ordinal = latest_history.get("rollout_end_ordinal")
        if ((end_offset is not None and end_offset > rollout.get("rollout_byte_size", 0)) or
                (end_ordinal is not None and end_ordinal > (rollout.get("rollout_max_ordinal") or -1))):
            return {"result": "unknown", "reason": "malformed_or_truncated_rollout",
                    "turn_id": latest_history.get("turn_id")}
        status = (latest_history.get("status") or "").lower()
        error = bool(latest_history.get("error"))
        turn_id = latest_history.get("turn_id")
        if error or status in {"failed", "interrupted", "cancelled", "canceled", "error"}:
            return {"result": "failed", "reason": "latest_turn_failed", "turn_id": turn_id,
                    "status": status or None, "error": error,
                    "has_attributable_inference": bool(latest and latest.get("response_count"))}
        if status != "completed":
            return {"result": "unknown", "reason": "latest_turn_not_complete", "turn_id": turn_id,
                    "status": status or None, "error": error, "has_attributable_inference": latest is not None}
    elif rollout.get("own_turns"):
        latest = rollout["own_turns"][-1]
        turn_id = latest["turn_id"]
        status = latest.get("rollout_status")
    else:
        return {"result": "unknown", "reason": "no_attributable_inference"}

    if latest is None:
        return {"result": "unknown", "reason": "no_attributable_inference_for_latest_turn",
                "turn_id": latest_history.get("turn_id") if latest_history else None,
                "status": latest_history.get("status") if latest_history else None}
    if latest.get("error") or latest.get("rollout_status") == "failed":
        return {"result": "failed", "reason": "task_complete_or_event_error", "turn_id": latest["turn_id"],
                "status": latest.get("rollout_status"), "error": True,
                "has_attributable_inference": bool(latest.get("response_count"))}
    if not latest.get("response_count"):
        return {"result": "unknown", "reason": "no_attributable_inference_for_latest_turn",
                "turn_id": latest["turn_id"], "status": latest.get("rollout_status"),
                "error": False, "has_attributable_inference": False}
    if latest.get("rollout_status") != "completed" and not (latest_history and latest_history.get("status") == "completed"):
        return {"result": "unknown", "reason": "no_successful_terminal_event", "turn_id": latest["turn_id"],
                "status": latest.get("rollout_status"), "error": False, "has_attributable_inference": True}
    model, effort = latest.get("model"), latest.get("effort")
    actual = {"model": model, "effort": effort}
    if model is None or effort is None:
        return {"result": "unknown", "reason": "execution_context_missing", "turn_id": latest["turn_id"],
                "actual": actual, "has_attributable_inference": True}
    if expected_model is None or expected_effort is None:
        return {"result": "unknown", "reason": "expected_pair_missing", "turn_id": latest["turn_id"],
                "actual": actual, "has_attributable_inference": True}
    if (model, effort) != (expected_model, expected_effort):
        return {"result": "mismatch", "reason": "effective_routing_mismatch", "turn_id": latest["turn_id"],
                "actual": actual, "expected": {"model": expected_model, "effort": expected_effort},
                "has_attributable_inference": True}
    return {"result": "verified", "reason": "completed_attributable_turn_matches", "turn_id": latest["turn_id"],
            "actual": actual, "expected": {"model": expected_model, "effort": expected_effort},
            "status": "completed", "error": False, "has_attributable_inference": True,
            "response_count": latest.get("response_count", 0)}


def verify_rollout(path: Path, thread_id: str, model: str, effort: str,
                   history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Direct offline helper for a saved rollout and optional status rows."""
    return execution_verdict(parse_rollout(path, thread_id), history or [], model, effort)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account")
    parser.add_argument("--home", help="Absolute profile home for read-only observation")
    parser.add_argument("--manifest", help="Explicit manifest; otherwise prefer manifest.local.json")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--since", type=int, help="Unix timestamp in seconds")
    selector.add_argument("--ids", nargs="+", help="One or more economy task IDs")
    parser.add_argument(
        "--root",
        help="Optional root ID for a bounded spawn tree (defaults to the current economy root when no --since is given)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def observe(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = economy._resolve_manifest_path(getattr(args, "manifest", None))
    manifest, _ = economy._load_manifest(manifest_path)
    account = getattr(args, "account", None)
    if account is None and not getattr(args, "home", None) and manifest["profiles"]:
        account = next(iter(manifest["profiles"]))
    targets = economy._profile_paths(manifest, account, getattr(args, "home", None))
    account, home = targets[0]
    state_path = home / "state_5.sqlite"
    output: dict[str, Any] = {
        "account": account,
        "cli_version_expected": manifest.get("cli_version"),
        "state_db": str(state_path),
        "state_db_read_only": True,
        "economy_root_id": ECONOMY_ROOT_ID,
        "selection": {},
        "threads": [],
        "limitations": [
            "Configured values come from state_5.sqlite; actual values come from rollout turn_context metadata.",
            "Tool inheritance is inferred only from advertised/executed tool names; no backend cryptographic verification is claimed.",
            "User messages, titles, previews, arguments, outputs, encrypted content, and item bodies are omitted.",
        ],
    }
    if not state_path.exists():
        output["state_db_error"] = "state_5.sqlite is absent"
        return output
    try:
        db = ro_connect(state_path)
    except (OSError, sqlite3.Error) as exc:
        output["state_db_error"] = f"state_5.sqlite unavailable: {type(exc).__name__}"
        return output

    try:
        if not table_exists(db, "threads"):
            output["state_db_error"] = "threads table is absent"
            return output
        thread_cols = columns(db, "threads")
        safe_select = safe_thread_select(thread_cols)
        anchor_id = args.root
        if not anchor_id and not args.ids and args.since is None:
            latest = db.execute("select id from threads where agent_path is null and model not like 'codex-%' order by created_at_ms desc limit 1").fetchone()
            anchor_id = str(latest["id"]) if latest else None
        root = db.execute(
            f"select {safe_select} from threads where id=?", (anchor_id,)
        ).fetchone()
        if root is None and not args.ids and args.since is None:
            output["selection"] = {
                "mode": "latest_root_tree",
                "root_present": False,
                "root_id": anchor_id,
                "selected_ids": [],
                "unknown_ids": [],
                "reason": "economy root is not present in this account database",
            }
            return output

        root_created_ms = as_int(root["created_at_ms"] if root is not None and "created_at_ms" in thread_cols else None)
        if root_created_ms is None:
            root_created_ms = (as_int(root["created_at"]) or 0) * 1000 if root is not None else 0
        default_since_s = root_created_ms // 1000

        # Recursive CTE stays inside the current economy tree and does not
        # inspect historical user-message records.
        tree_rows = []
        if root is not None:
            tree_rows = db.execute(
                f"""
                with recursive tree(id, depth) as (
                    select ? as id, 0 as depth
                    union all
                    select e.child_thread_id, tree.depth + 1
                    from thread_spawn_edges e join tree on tree.id=e.parent_thread_id
                )
                select tree.depth, {', '.join('t.' + column for column in SAFE_THREAD_COLUMNS if column in thread_cols)}
                from tree join threads t on t.id=tree.id
                order by tree.depth, coalesce(t.created_at_ms, t.created_at*1000), t.id
                """,
                (anchor_id,),
            ).fetchall()
        tree_ids = {str(row["id"]) for row in tree_rows}
        if args.ids:
            requested = [value for value in args.ids if UUID_RE.match(value)]
            # Explicit IDs are a bounded allowlist supplied by the caller and
            # may point at a smoke-test task outside the economy root tree.
            # Only those rows are fetched; no user-message history is scanned.
            if requested:
                placeholders = ",".join("?" for _ in requested)
                rows_by_id = {
                    str(row["id"]): row
                    for row in db.execute(
                        f"select {safe_select} from threads where id in ({placeholders})", requested
                    ).fetchall()
                }
                selected_rows = [rows_by_id[value] for value in requested if value in rows_by_id]
            else:
                selected_rows = []
            unknown = [value for value in args.ids if value not in {str(row["id"]) for row in selected_rows}]
            output["selection"] = {
                "mode": "ids",
                "root_present": anchor_id in {str(row["id"]) for row in selected_rows},
                "root_id": anchor_id,
                "since_unix_seconds": None,
                "selected_ids": [str(row["id"]) for row in selected_rows],
                "unknown_ids": unknown,
            }
        elif args.since is not None:
            since_s = args.since if args.since is not None else default_since_s
            threshold_ms = since_s * 1000
            # A caller supplied --since is a metadata-only time window. It
            # includes independent smoke roots and their descendants while
            # filtering guardian review rows from the normal task stream.
            source_filter = " and (thread_source is null or thread_source != 'guardian_review')" if "thread_source" in thread_cols else ""
            if args.root and root is not None:
                selected_rows = [
                    row
                    for row in tree_rows
                    if (as_int(row["created_at_ms"]) or (as_int(row["created_at"]) or 0) * 1000)
                    >= threshold_ms
                ]
            else:
                selected_rows = db.execute(
                    f"select {safe_select} from threads where coalesce(created_at_ms, created_at*1000) >= ?{source_filter} order by coalesce(created_at_ms, created_at*1000), id",
                    (threshold_ms,),
                ).fetchall()
            output["selection"] = {
                "mode": "since",
                "root_present": root is not None,
                "root_id": anchor_id if args.root else None,
                "since_unix_seconds": since_s,
                "selected_ids": [str(row["id"]) for row in selected_rows],
                "unknown_ids": [],
            }
        else:
            # No selector means the current economy task tree from its root's
            # creation time. This is the intentionally bounded default.
            selected_rows = [
                row
                for row in tree_rows
                if (as_int(row["created_at_ms"]) or (as_int(row["created_at"]) or 0) * 1000)
                >= root_created_ms
            ]
            output["selection"] = {
                "mode": "latest_root_tree",
                "root_present": True,
                "root_id": anchor_id,
                "since_unix_seconds": default_since_s,
                "selected_ids": [str(row["id"]) for row in selected_rows],
                "unknown_ids": [],
            }

        selected_ids = [str(row["id"]) for row in selected_rows]
        history_statuses, history_error = read_history(home, selected_ids)
        output["thread_history_read_only"] = True
        if history_error:
            output["thread_history_error"] = history_error

        has_dynamic = table_exists(db, "thread_dynamic_tools")
        advertised: dict[str, list[str]] = {}
        if has_dynamic and selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            for row in db.execute(
                f"select thread_id, name, namespace from thread_dynamic_tools where thread_id in ({placeholders}) order by thread_id, position",
                selected_ids,
            ):
                name = clean_name(row["name"])
                if row["namespace"]:
                    name = f"{clean_name(row['namespace'])}:{name}"
                advertised.setdefault(str(row["thread_id"]), []).append(name)

        for row in selected_rows:
            thread_id = str(row["id"])
            rollout_path_value = row["rollout_path"] if "rollout_path" in thread_cols else None
            rollout = None
            rollout_error = None
            if isinstance(rollout_path_value, str) and rollout_path_value:
                rollout_path = Path(rollout_path_value)
                # The database associates this path with the selected ID. A
                # filename mismatch is skipped to prevent accidental broad reads.
                if thread_id not in rollout_path.name:
                    rollout_error = "associated rollout filename does not contain thread ID"
                elif not rollout_path.is_file():
                    rollout_error = "associated rollout file is absent"
                else:
                    try:
                        rollout = parse_rollout(rollout_path, thread_id)
                    except (OSError, UnicodeError) as exc:
                        rollout_error = f"associated rollout unavailable: {type(exc).__name__}"
            else:
                rollout_error = "no associated rollout path"

            configured_model = clean_name(row["model"]) if "model" in thread_cols and row["model"] else None
            configured_effort = clean_name(row["reasoning_effort"]) if "reasoning_effort" in thread_cols and row["reasoning_effort"] else None
            actual_pairs = (rollout or {}).get("actual_context_pairs", [])
            actual_set = {(item.get("model"), item.get("effort")) for item in actual_pairs}
            configured_pair = (configured_model, configured_effort)
            configured_matches_actual = configured_pair in actual_set if actual_pairs else None
            history_turns = history_statuses.get(thread_id, [])
            statuses = [entry.get("status") for entry in history_turns if entry.get("status") is not None]
            verdict = execution_verdict(
                rollout, history_turns, configured_model, configured_effort
            )
            item = {
                "id": thread_id,
                "depth": next((int(tree_row["depth"]) for tree_row in tree_rows if tree_row["id"] == thread_id), None),
                "created_at_unix_seconds": (as_int(row["created_at_ms"]) or (as_int(row["created_at"]) or 0) * 1000) // 1000,
                "cli_version": clean_name(row["cli_version"]) if "cli_version" in thread_cols and row["cli_version"] else None,
                "source": source_class(row["source"]) if "source" in thread_cols else None,
                "thread_source": source_class(row["thread_source"]) if "thread_source" in thread_cols else None,
                "agent_role": clean_name(row["agent_role"]) if "agent_role" in thread_cols and row["agent_role"] else None,
                "agent_path": clean_name(row["agent_path"]) if "agent_path" in thread_cols and row["agent_path"] else None,
                "configured": {
                    "model": configured_model,
                    "reasoning_effort": configured_effort,
                },
                "db_tokens_used": as_int(row["tokens_used"]) if "tokens_used" in thread_cols else None,
                "advertised_tool_names": advertised.get(thread_id, []),
                "history_statuses": statuses,
                "history_turns": history_turns,
                "rollout": rollout,
                "execution_verification": verdict,
            }
            if configured_matches_actual is not None:
                item["configured_matches_actual_context"] = configured_matches_actual
            if rollout_error:
                item["rollout_error"] = rollout_error
            output["threads"].append(item)
    except sqlite3.Error as exc:
        output["state_db_error"] = f"state_5.sqlite query unavailable: {type(exc).__name__}"
    finally:
        db.close()
    return output


def compact_text(result: dict[str, Any]) -> str:
    """Human-readable output with no arbitrary text fields."""

    lines = [
        f"account={result.get('account')} state_db_read_only={result.get('state_db_read_only')}",
        f"selection={json.dumps(result.get('selection', {}), ensure_ascii=True, separators=(',', ':'))}",
    ]
    if result.get("state_db_error"):
        lines.append(f"state_db_error={result['state_db_error']}")
        return "\n".join(lines)
    for item in result.get("threads", []):
        rollout = item.get("rollout") or {}
        lines.append(
            "thread="
            + item["id"]
            + " model="
            + str(item["configured"].get("model"))
            + " effort="
            + str(item["configured"].get("reasoning_effort"))
            + " path="
            + str(item.get("agent_path"))
            + " status="
            + str(item.get("history_statuses"))
            + " actual="
            + json.dumps(rollout.get("actual_context_pairs", []), separators=(",", ":"))
            + " verification="
            + json.dumps(item.get("execution_verification", {}), separators=(",", ":"))
            + " db_tokens="
            + str(item.get("db_tokens_used"))
            + " rollout_tokens="
            + str(rollout.get("latest_rollout_token_usage"))
            + " tools_executed="
            + json.dumps(rollout.get("executed_tool_names", {}), separators=(",", ":"))
            + " tools_advertised="
            + json.dumps(item.get("advertised_tool_names", []), separators=(",", ":"))
            + " pasted_marker="
            + str(rollout.get("root_pasted_request_marker"))
        )
    if result.get("thread_history_error"):
        lines.append(f"thread_history_error={result['thread_history_error']}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        result = observe(args)
    except (economy.EconomyError, OSError, UnicodeError) as exc:
        print(f"observe: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        print(compact_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
