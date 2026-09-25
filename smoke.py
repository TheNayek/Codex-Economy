"""Run and strictly verify a small Codex Economy routing smoke test.

Artifacts contain structural IDs and verification metadata only. Raw CLI events
and stderr are discarded because integrations may put private data there.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import tomllib
from typing import Any

BASE = pathlib.Path(__file__).resolve().parent


def restore_cli_trust(config_path, before, workspace):
    """Undo only a proven CLI-created trust entry; never overwrite other edits."""
    after = config_path.read_bytes()
    if after == before:
        return "unchanged"
    a = tomllib.loads(before.decode("utf-8-sig"))
    b = tomllib.loads(after.decode("utf-8-sig"))
    added = set(b.get("projects", {})) - set(a.get("projects", {}))
    if len(added) != 1:
        return "unrelated_change_preserved"
    key = next(iter(added))
    if os.path.normcase(os.path.normpath(key)) != os.path.normcase(str(workspace)):
        return "unrelated_change_preserved"
    if b["projects"][key] != {"trust_level": "trusted"}:
        return "unrelated_change_preserved"
    del b["projects"][key]
    if not b["projects"] and "projects" not in a:
        del b["projects"]
    if a != b:
        return "unrelated_change_preserved"
    text = after.decode("utf-8-sig")
    for match in re.finditer(r"(?m)^\[projects\.[^\r\n]+\]\r?$", text):
        following = re.search(r"(?m)^\[", text[match.end():])
        end = match.end() + following.start() if following else len(text)
        if key in tomllib.loads(text[match.start():end]).get("projects", {}):
            remaining = text[:match.start()] + text[end:]
            before_lines = [x for x in before.decode("utf-8-sig").splitlines() if x.strip()]
            if [x for x in remaining.splitlines() if x.strip()] != before_lines:
                return "unrelated_change_preserved"
            config_path.write_bytes(before)
            return "restored_cli_added_workspace_trust"
    return "unrelated_change_preserved"


def parse_cli_events(stdout: str) -> dict[str, Any]:
    """Extract only structural IDs and terminal state; discard all content."""
    thread_ids, turn_ids, terminal = [], [], []
    malformed = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        kind = event.get("type")
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_ids.append(event["thread_id"])
        if kind == "turn.started" and isinstance(event.get("turn_id"), str):
            turn_ids.append(event["turn_id"])
        if kind in {"turn.completed", "turn.failed", "turn.interrupted", "error"}:
            terminal.append(kind)
            if isinstance(event.get("turn_id"), str):
                turn_ids.append(event["turn_id"])
    return {"thread_ids": list(dict.fromkeys(thread_ids)), "turn_ids": list(dict.fromkeys(turn_ids)),
            "terminal_events": terminal, "malformed_lines": malformed}


def build_prompt(manifest: dict[str, Any], roles: list[str], fork_turns: str) -> str:
    if not roles:
        return "Return exactly ECONOMY_OK. Do not use tools."
    requests = []
    for role in roles:
        route = manifest["agents"][role]
        pair = "omit model and reasoning overrides" if fork_turns == "all" else (
            f"set model={route['model']} and reasoning_effort={route['model_reasoning_effort']}"
        )
        requests.append(
            f"spawn role {role} with task_name={role.replace('-', '_')}, fork_turns={fork_turns}, {pair}; "
            "ask it to return exactly ECONOMY_CHILD_OK and use no tools"
        )
    return ("Run these probes strictly one at a time in the listed order. Spawn one child, wait for it "
            "to finish, and only then spawn the next. After the final child finishes, return ECONOMY_OK: "
            + "; ".join(requests) + ".")


def verify_observation(data, root_thread, mode, roles, manifest, expected_root_turn=None):
    """Verify the exact root tree and selected roles; absence is never success."""
    expected = {"root": manifest["routing"][mode]}
    expected.update({role: manifest["agents"][role] for role in roles})
    rows = {}
    for row in data.get("threads", []):
        role = "root" if row.get("id") == root_thread else row.get("agent_role")
        if role not in expected:
            raise RuntimeError("unexpected_role:" + str(role))
        if role in rows:
            raise RuntimeError("duplicate_role:" + str(role))
        rows[role] = row
    missing = set(expected) - set(rows)
    if missing:
        raise RuntimeError("missing_roles:" + ",".join(sorted(missing)))
    checked = []
    for role, route in expected.items():
        row = rows[role]
        verdict = row.get("execution_verification") or {}
        if verdict.get("result") != "verified":
            raise RuntimeError(f"{role}:{verdict.get('result', 'unknown')}:{verdict.get('reason', 'no_reason')}")
        if role == "root" and expected_root_turn and verdict.get("turn_id") != expected_root_turn:
            raise RuntimeError("root:wrong_turn_id")
        actual = verdict.get("actual") or {}
        pair = (route.get("model"), route.get("model_reasoning_effort"))
        if (actual.get("model"), actual.get("effort")) != pair:
            raise RuntimeError("effective_routing_mismatch:" + role)
        checked.append({"role": role, "id": row["id"], "turn_id": verdict["turn_id"],
                        "model": pair[0], "effort": pair[1], "status": "completed",
                        "service_tier": "unknown"})
    return checked


def verify_routing(account, thread, mode, children, manifest, *, roles=None,
                   expected_root_turn=None, observed=None, manifest_path=None):
    """Compatibility wrapper; `observed` enables saved/offline verification."""
    import observe
    selected = list(roles or ([] if not children else list(manifest["agents"])[:3]))
    data = observed if observed is not None else observe.observe(
        argparse.Namespace(account=account, root=thread, ids=None, since=None, as_json=True,
                           manifest=manifest_path, home=None))
    return verify_observation(data, thread, mode, selected, manifest, expected_root_turn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="Explicit manifest; otherwise prefer manifest.local.json")
    parser.add_argument("--account")
    parser.add_argument("--mode", default="DEFAULT")
    parser.add_argument("--children", action="store_true", help="Legacy alias: first three manifest roles")
    parser.add_argument("--role", action="append", default=[])
    parser.add_argument("--roles", help="Comma-separated manifest role names; maximum three")
    parser.add_argument("--fork-turns", default="none", help="none, all, or a positive integer")
    parser.add_argument("--workspace", required=True, help="An isolated, disposable test directory")
    args = parser.parse_args()
    try:
        import economy
        manifest, _ = economy._load_manifest(economy._resolve_manifest_path(args.manifest, BASE))
    except economy.EconomyError as exc:
        parser.error(str(exc))
    if not manifest["profiles"]:
        parser.error("no profile homes configured; run economy.py init --home ABSOLUTE")
    if args.account is None:
        args.account = next(iter(manifest["profiles"]))
    if args.account not in manifest["profiles"]:
        parser.error(f"unknown account: {args.account}")
    if args.mode not in manifest["routing"]:
        parser.error(f"unknown mode: {args.mode}")
    roles = list(args.role)
    if args.roles:
        roles.extend(value.strip() for value in args.roles.split(",") if value.strip())
    if args.children and not roles:
        roles = list(manifest["agents"])[:3]
    roles = list(dict.fromkeys(roles))
    unknown = set(roles) - set(manifest["agents"])
    if unknown:
        parser.error("unknown roles: " + ", ".join(sorted(unknown)))
    if len(roles) > 3:
        parser.error("at most three roles may run in one bounded smoke")
    if args.fork_turns not in {"none", "all"} and not (args.fork_turns.isdigit() and int(args.fork_turns) > 0):
        parser.error("--fork-turns must be none, all, or a positive integer")
    executable = shutil.which("codex")
    if not executable and os.name == "nt":
        bin_root = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
        candidates = sorted(bin_root.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        executable = str(candidates[0]) if candidates else None
    if not executable:
        parser.error("No installed Codex CLI found")
    workspace = pathlib.Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("CODEX_", "NODE_REPL_", "SKY_", "BROWSER_USE_")):
            env.pop(key)
    env["CODEX_HOME"] = str(pathlib.Path(manifest["profiles"][args.account]).expanduser())
    prompt = build_prompt(manifest, roles, args.fork_turns)
    trust = "projects." + json.dumps(str(workspace).lower() if os.name == "nt" else str(workspace)) + '.trust_level="trusted"'
    cmd = [executable, "exec", "--profile", args.mode, "-c", trust, "--skip-git-repo-check", "--json", "-C", str(workspace), prompt]
    print(f"Running {args.account} {args.mode}; real model usage is incurred.", flush=True)
    started = int(time.time())
    config_path = pathlib.Path(env["CODEX_HOME"]) / "config.toml"
    before_config = config_path.read_bytes()
    try:
        run = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf8", timeout=420,
                             creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        print("TIMEOUT: smoke did not finish in 420s; inspect Codex for remaining children.", file=sys.stderr)
        print("Config cleanup:", restore_cli_trust(config_path, before_config, workspace))
        return 2
    cleanup = restore_cli_trust(config_path, before_config, workspace)
    summary = parse_cli_events(run.stdout)
    thread = summary["thread_ids"][0] if len(summary["thread_ids"]) == 1 else None
    root_turn = summary["turn_ids"][0] if len(summary["turn_ids"]) == 1 else None
    # Current CLI builds may omit a turn ID from exec JSON. The newly-created
    # thread ID still bounds the root exactly; the observer then resolves its
    # own turn ID from attributable rollout/history records.
    cli_success = (run.returncode == 0 and summary["terminal_events"] == ["turn.completed"]
                   and summary["malformed_lines"] == 0 and thread is not None)
    report = {"account": args.account, "mode": args.mode, "roles": roles, "fork_turns": args.fork_turns,
              "started": started, "thread_id": thread, "root_turn_id": root_turn,
              "exit_code": run.returncode, "config_cleanup": cleanup, "cli_event_summary": summary,
              "routing": [], "routing_verified": False}
    try:
        if not cli_success:
            raise RuntimeError("cli_terminal_state_unverified")
        report["routing"] = verify_routing(args.account, thread, args.mode, bool(roles), manifest,
                                            roles=roles, expected_root_turn=root_turn,
                                            manifest_path=args.manifest)
        report["routing_verified"] = True
    except Exception as error:
        report["routing_error"] = str(error)
    report_path = workspace / f"economy-smoke-{args.account}-{args.mode}-{started}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf8")
    print("Routing verified:", report["routing_verified"], report.get("routing_error", ""))
    print("Config cleanup:", cleanup)
    print("Report:", report_path)
    print("Thread:", thread, "exit:", run.returncode)
    if run.stderr:
        print("CLI stderr was discarded; nonempty_lines=", len(run.stderr.splitlines()), file=sys.stderr)
    if run.returncode:
        return run.returncode
    if cleanup == "unrelated_change_preserved":
        return 3
    return 0 if report["routing_verified"] else 4


if __name__ == "__main__":
    sys.exit(main())
