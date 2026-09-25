import json
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import observe
import smoke
from workspace_temp import WorkspaceTempDirectory


THREAD = "01abcdef-0000-0000-0000-000000000001"
PARENT = "01abcdef-0000-0000-0000-000000000000"
TURN = "01abcdef-1000-0000-0000-000000000001"


def event(kind, payload):
    return {"type": kind, "payload": payload}


def rollout_events(model="gpt-6-luna", effort="high", *, complete=True, error=None,
                   usage=True, inherited=False, duplicate=False):
    rows = []
    if inherited:
        rows += [event("session_meta", {"id": PARENT}),
                 event("turn_context", {"turn_id": "parent-turn", "model": "gpt-5.6-sol", "effort": "medium"})]
    rows += [event("session_meta", {"id": THREAD, "agent_role": "explorer"}),
             event("turn_context", {"turn_id": TURN, "model": model, "effort": effort})]
    if usage:
        usage_event = event("token_usage_record", {
            "thread_id": THREAD, "turn_id": TURN, "response_id": "resp-1",
            "usage": {"input_tokens": 3, "output_tokens": 2, "reasoning_output_tokens": 1, "total_tokens": 5},
            "thread_token_usage": {"total_tokens": 999},
        })
        rows.append(usage_event)
        if duplicate:
            rows.append(usage_event)
    if complete:
        payload = {"type": "task_complete", "turn_id": TURN}
        if error is not None:
            payload["error"] = error
        rows.append(event("event_msg", payload))
    return rows


class RolloutTests(unittest.TestCase):
    def write_rollout(self, rows, truncated=False):
        temp = WorkspaceTempDirectory(HERE)
        path = Path(temp.name) / f"rollout-{THREAD}.jsonl"
        text = "".join(json.dumps(row) + "\n" for row in rows)
        if truncated:
            text += '{"type":"turn_context","payload":'
        path.write_text(text, encoding="utf8")
        self.addCleanup(temp.cleanup)
        return path

    def test_task_complete_error_is_failed(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events(error={"code": "quota"})), THREAD)
        verdict = observe.execution_verdict(parsed, [], "gpt-6-luna", "high")
        self.assertEqual("failed", verdict["result"])

    def test_later_error_without_context_or_usage_beats_prior_success(self):
        later = "01abcdef-3000-0000-0000-000000000003"
        rows = rollout_events()
        rows.append(event("event_msg", {"type": "task_complete", "turn_id": later,
                                        "error": {"code": "quota"}}))
        parsed = observe.parse_rollout(self.write_rollout(rows), THREAD)
        verdict = observe.execution_verdict(parsed, [], "gpt-6-luna", "high")
        self.assertEqual("failed", verdict["result"])
        self.assertEqual(later, verdict["turn_id"])
        self.assertFalse(verdict["has_attributable_inference"])

    def test_terminal_events_without_turn_id_block_pass(self):
        for terminal in (
            event("event_msg", {"type": "task_complete", "error": {"code": "quota"}}),
            event("event_msg", {"type": "error"}),
        ):
            with self.subTest(terminal=terminal["payload"]["type"]):
                parsed = observe.parse_rollout(
                    self.write_rollout(rollout_events() + [terminal]), THREAD)
                verdict = observe.execution_verdict(parsed, [], "gpt-6-luna", "high")
                self.assertEqual("unknown", verdict["result"])
                self.assertEqual("terminal_event_without_turn_id", verdict["reason"])

    def test_stale_completion_does_not_mask_later_failed_turn(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events()), THREAD)
        history = [{"turn_id": TURN, "status": "completed", "error": False},
                   {"turn_id": "later-turn", "status": "failed", "error": True}]
        verdict = observe.execution_verdict(parsed, history, "gpt-6-luna", "high")
        self.assertEqual("failed", verdict["result"])
        self.assertEqual("later-turn", verdict["turn_id"])

    def test_stale_history_does_not_mask_new_running_rollout_turn(self):
        later = "01abcdef-2000-0000-0000-000000000002"
        rows = rollout_events()
        rows.append(event("turn_context", {"turn_id": later, "model": "gpt-6-luna", "effort": "high"}))
        parsed = observe.parse_rollout(self.write_rollout(rows), THREAD)
        history = [{"turn_id": TURN, "status": "completed", "error": False}]
        verdict = observe.execution_verdict(parsed, history, "gpt-6-luna", "high")
        self.assertEqual("unknown", verdict["result"])
        self.assertEqual(later, verdict["turn_id"])

    def test_paginated_history_only_later_failed_beats_old_rollout_success(self):
        rows = rollout_events()
        for ordinal, row in enumerate(rows, 1):
            row["ordinal"] = ordinal
        parsed = observe.parse_rollout(self.write_rollout(rows), THREAD)
        history = [{"turn_id": "later-failed", "rollout_ordinal": 100,
                    "status": "failed", "error": True}]
        verdict = observe.execution_verdict(parsed, history, "gpt-6-luna", "high")
        self.assertEqual("failed", verdict["result"])
        self.assertEqual("later-failed", verdict["turn_id"])

    def test_paginated_history_only_newer_completed_without_usage_is_unknown(self):
        rows = rollout_events()
        for ordinal, row in enumerate(rows, 1):
            row["ordinal"] = ordinal
        parsed = observe.parse_rollout(self.write_rollout(rows), THREAD)
        history = [{"turn_id": "later-completed", "rollout_ordinal": 100,
                    "status": "completed", "error": False}]
        verdict = observe.execution_verdict(parsed, history, "gpt-6-luna", "high")
        self.assertEqual("unknown", verdict["result"])
        self.assertEqual("no_attributable_inference_for_latest_turn", verdict["reason"])

    def test_interrupted_latest_history_is_failed_without_new_usage(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events(usage=False, complete=False)), THREAD)
        history = [{"turn_id": TURN, "status": "interrupted", "error": False}]
        verdict = observe.execution_verdict(parsed, history, "gpt-6-luna", "high")
        self.assertEqual("failed", verdict["result"])
        self.assertFalse(verdict["has_attributable_inference"])

    def test_inherited_sol_context_is_not_child_execution(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events(inherited=True)), THREAD)
        self.assertEqual([{"model": "gpt-6-luna", "effort": "high", "count": 1}], parsed["actual_context_pairs"])
        self.assertEqual(2, len(parsed["all_context_pairs"]))
        self.assertEqual("verified", observe.execution_verdict(parsed, [], "gpt-6-luna", "high")["result"])

    def test_inherited_tools_are_not_reported_as_own_execution(self):
        rows = rollout_events(inherited=True)
        rows.insert(2, event("response_item", {"type": "function_call", "name": "parent_tool"}))
        rows.insert(-1, event("response_item", {"type": "function_call", "name": "child_tool"}))
        parsed = observe.parse_rollout(self.write_rollout(rows), THREAD)
        self.assertEqual({"child_tool": 1}, parsed["executed_tool_names"])
        self.assertEqual({"child_tool": 1, "parent_tool": 1}, parsed["all_history_tool_names"])

    def test_wrong_pair_is_mismatch(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events()), THREAD)
        self.assertEqual("mismatch", observe.execution_verdict(parsed, [], "gpt-5.6-sol", "medium")["result"])

    def test_no_usage_is_unknown(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events(usage=False)), THREAD)
        self.assertEqual("unknown", observe.execution_verdict(parsed, [], "gpt-6-luna", "high")["result"])

    def test_duplicate_response_is_deduplicated(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events(duplicate=True)), THREAD)
        self.assertEqual(1, parsed["own_usage_response_count"])
        self.assertEqual(1, parsed["duplicate_response_records"])
        self.assertEqual(5, parsed["own_turns"][0]["usage"]["total_tokens"])

    def test_truncated_record_is_unknown(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events(), truncated=True), THREAD)
        self.assertFalse(parsed["integrity_ok"])
        self.assertEqual("malformed_or_truncated_rollout",
                         observe.execution_verdict(parsed, [], "gpt-6-luna", "high")["reason"])

    def test_thread_identity_must_match_explicit_id(self):
        parsed = observe.parse_rollout(self.write_rollout(rollout_events()), PARENT)
        self.assertEqual("thread_identity_unverified",
                         observe.execution_verdict(parsed, [], "gpt-6-luna", "high")["reason"])


class HistoryTests(unittest.TestCase):
    def test_paginated_status_metadata(self):
        with WorkspaceTempDirectory(HERE) as name:
            home = Path(name)
            db = sqlite3.connect(home / "thread_history_1.sqlite")
            db.execute("""create table thread_turns(
                thread_id text, turn_id text, rollout_ordinal integer, status text,
                error_json text, rollout_byte_offset integer, rollout_end_ordinal integer,
                rollout_end_byte_offset integer)""")
            db.execute("insert into thread_turns values(?,?,?,?,?,?,?,?)",
                       (THREAD, TURN, 7, "completed", None, 100, 20, 900))
            db.commit(); db.close()
            result, error = observe.read_history(home, [THREAD])
            self.assertIsNone(error)
            self.assertEqual("completed", result[THREAD][0]["status"])
            self.assertEqual(900, result[THREAD][0]["rollout_end_byte_offset"])
            self.assertNotIn("error_json", result[THREAD][0])


class SmokeTests(unittest.TestCase):
    def manifest(self):
        return {"routing": {"DEFAULT": {"model": "gpt-6-astra", "model_reasoning_effort": "high"}},
                "agents": {"explorer": {"model": "gpt-6-luna", "model_reasoning_effort": "high"}}}

    def row(self, role, model, effort, thread_id):
        return {"id": thread_id, "agent_role": role,
                "execution_verification": {"result": "verified", "reason": "ok", "turn_id": TURN,
                                           "actual": {"model": model, "effort": effort}}}

    def test_missing_role_rejected(self):
        data = {"threads": [self.row(None, "gpt-6-astra", "high", THREAD)]}
        with self.assertRaisesRegex(RuntimeError, "missing_roles"):
            smoke.verify_observation(data, THREAD, "DEFAULT", ["explorer"], self.manifest())

    def test_wrong_expected_root_turn_rejected(self):
        data = {"threads": [self.row(None, "gpt-6-astra", "high", THREAD)]}
        with self.assertRaisesRegex(RuntimeError, "wrong_turn_id"):
            smoke.verify_observation(data, THREAD, "DEFAULT", [], self.manifest(), "other-turn")

    def test_previous_generation_execution_is_not_a_successful_migration(self):
        for family in ("sol", "luna"):
            with self.subTest(family=family):
                manifest = {"routing": {"DIRECT": {
                    "model": f"gpt-6-{family}", "model_reasoning_effort": "high"}},
                    "agents": {"worker": {
                        "model": f"gpt-6-{family}", "model_reasoning_effort": "high"}}}
                root = self.row(None, f"gpt-6-{family}", "high", THREAD)
                child = self.row("worker", f"gpt-6-{family}", "high", PARENT)
                self.assertEqual(2, len(smoke.verify_observation(
                    {"threads": [root, child]}, THREAD, "DIRECT", ["worker"], manifest)))
                for role, row in (("root", root), ("worker", child)):
                    row["execution_verification"]["actual"]["model"] = f"gpt-5.6-{family}"
                    with self.assertRaisesRegex(RuntimeError, "effective_routing_mismatch:" + role):
                        smoke.verify_observation(
                            {"threads": [root, child]}, THREAD, "DIRECT", ["worker"], manifest)
                    row["execution_verification"]["actual"]["model"] = f"gpt-6-{family}"

    def test_verify_observation_rejects_task_complete_error(self):
        with WorkspaceTempDirectory(HERE) as name:
            path = Path(name) / f"rollout-{THREAD}.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rollout_events(
                model="gpt-6-astra", effort="high", error={"code": "quota"})), encoding="utf8")
            verdict = observe.execution_verdict(
                observe.parse_rollout(path, THREAD), [], "gpt-6-astra", "high")
        data = {"threads": [{"id": THREAD, "agent_role": None, "execution_verification": verdict}]}
        with self.assertRaisesRegex(RuntimeError, "failed"):
            smoke.verify_observation(data, THREAD, "DEFAULT", [], self.manifest())

    def test_cli_failed_or_malformed_is_visible(self):
        summary = smoke.parse_cli_events("\n".join([
            json.dumps({"type": "thread.started", "thread_id": THREAD}),
            json.dumps({"type": "turn.failed", "turn_id": TURN}), "truncated{"]))
        self.assertEqual(["turn.failed"], summary["terminal_events"])
        self.assertEqual(1, summary["malformed_lines"])

    def test_all_prompt_omits_pair(self):
        prompt = smoke.build_prompt(self.manifest(), ["explorer"], "all")
        self.assertIn("omit model and reasoning overrides", prompt)
        self.assertNotIn("gpt-6-luna", prompt)
        self.assertIn("strictly one at a time", prompt)


if __name__ == "__main__":
    unittest.main()
