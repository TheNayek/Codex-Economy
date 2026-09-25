import copy
import importlib.util
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("economy", ROOT / "economy.py")
economy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = economy
SPEC.loader.exec_module(economy)


class SimulatedCrash(BaseException):
    pass


class EconomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, _ = economy._load_manifest(ROOT / "manifest.json")
        cls.manifest = copy.deepcopy(cls.manifest)
        # Exercise legacy opt-in runtime management independently of the public
        # preset, which intentionally preserves sandbox and web-search settings.
        cls.manifest["runtime_defaults"] = {"windows_sandbox": "unelevated", "web_search": "live"}
        cls.manifest["retired_profiles"] = ["CRITICAL"]
        cls.manifest["profiles"] = {
            "main": str(ROOT / "synthetic-profiles" / "main"),
            "alt": str(ROOT / "synthetic-profiles" / "alt"),
        }
        cls.digest = economy._json_hash(cls.manifest)
        cls.policy, cls.policy_sha256 = economy._load_policy(cls.manifest)

    def setUp(self):
        self.temp = economy.WorkspaceTempDirectory(ROOT, prefix="test-economy-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        economy._AFTER_MANAGED_MUTATION = None
        self.temp.cleanup()

    def home(self, config=None):
        home = self.root / "home"
        home.mkdir()
        if config is not None:
            (home / "config.toml").write_text(config, encoding="utf-8")
        return home

    def install(self, home, manifest=None, digest=None):
        return economy._install_home(home, manifest or self.manifest, digest or self.digest, self.policy)

    def test_literal_secret_diagnostics_and_mutation_are_redacted_and_unbacked(self):
        marker = "SYNTHETIC-DO-NOT-PRINT-91f7"
        config = (
            'model = "user-model"\nmodel_reasoning_effort = "xhigh"\nservice_tier = "priority"\n'
            f'\n[mcp_servers.synthetic]\nenv_http_headers = {{ "X-Goog-Api-Key" = "{marker}" }}\n'
            '\n[agents]\ncustom = true\n'
        )
        home = self.home(config)
        out = StringIO()
        with redirect_stdout(out):
            plan = economy._plan_output(home, self.manifest, self.policy, False)
        self.assertNotIn(marker, out.getvalue())
        self.assertFalse(plan["root_preferences"]["matches_canonical"])
        installed = self.install(home)
        changed_config = (home / "config.toml").read_text(encoding="utf-8")
        self.assertIn(marker, changed_config)
        self.assertIn(f'env_http_headers = {{ "X-Goog-Api-Key" = "{marker}" }}', changed_config)
        txdir = home / economy.BACKUP_DIR_NAME / installed["transaction"]
        metadata = (txdir / "transaction.json").read_text(encoding="utf-8")
        self.assertNotIn(marker, metadata)
        self.assertFalse((txdir / "config.toml.before").exists())
        self.assertEqual(json.loads(metadata)["files"][0]["kind"], "owned_config")

    def test_policy_hash_is_recorded_and_legacy_transactions_remain_compatible(self):
        home = self.home('[agents]\ncustom=true\n')
        installed = self.install(home)
        txdir = home / economy.BACKUP_DIR_NAME / installed["transaction"]
        metadata_path = txdir / "transaction.json"
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(record["policy_sha256"], self.policy_sha256)
        self.assertTrue(economy._verify_home(home, self.manifest, self.digest, self.policy)["ok"])

        # Schema-2 transactions written before policy provenance was added do
        # not become invalid, and rollback still ignores the optional field.
        record.pop("policy_sha256")
        metadata_path.write_text(json.dumps(record), encoding="utf-8")
        self.assertTrue(economy._verify_home(home, self.manifest, self.digest, self.policy)["ok"])
        economy._rollback_home(home, self.digest, installed["transaction"])

    def test_verify_preserves_historical_manifest_on_noop_alias_migration(self):
        home = self.home('[agents]\ncustom=true\n')
        installed = self.install(home)
        metadata_path = home / economy.BACKUP_DIR_NAME / installed["transaction"] / "transaction.json"
        metadata_before = metadata_path.read_bytes()

        renamed = copy.deepcopy(self.manifest)
        renamed["profiles"]["codex-alt"] = renamed["profiles"].pop("alt")
        renamed_text = json.dumps(renamed, indent=2) + "\n"
        renamed_digest = economy._sha256_bytes(renamed_text.encode("utf-8"))
        verified = economy._verify_home(home, renamed, renamed_digest, self.policy)
        self.assertTrue(verified["ok"])
        self.assertIn("manifest differs from historical transaction", verified["notices"][0])
        self.assertEqual(metadata_path.read_bytes(), metadata_before)

        changed = copy.deepcopy(renamed)
        changed["routing"]["QUICK"]["model_reasoning_effort"] = "low"
        changed_text = json.dumps(changed, indent=2) + "\n"
        changed_digest = economy._sha256_bytes(changed_text.encode("utf-8"))
        rejected = economy._verify_home(home, changed, changed_digest, self.policy)
        self.assertFalse(rejected["ok"])
        self.assertIn("manifest hash differs from installed or no-op-verified transaction", rejected["failures"])
        self.assertEqual(metadata_path.read_bytes(), metadata_before)

    def test_noop_sync_does_not_backfill_legacy_policy_hash(self):
        home = self.home('[agents]\ncustom=true\n')
        installed = self.install(home)
        metadata_path = home / economy.BACKUP_DIR_NAME / installed["transaction"] / "transaction.json"
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
        record.pop("policy_sha256")
        metadata_path.write_text(json.dumps(record), encoding="utf-8")

        renamed = copy.deepcopy(self.manifest)
        renamed["profiles"]["codex-alt"] = renamed["profiles"].pop("alt")
        renamed_text = json.dumps(renamed, indent=2) + "\n"
        renamed_digest = economy._sha256_bytes(renamed_text.encode("utf-8"))
        synced = economy._install_home(home, renamed, renamed_digest, self.policy, refresh_provenance=True)
        self.assertTrue(synced["provenance_refreshed"])

        refreshed = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["manifest_sha256"], self.digest)
        self.assertEqual(refreshed["verified_manifest_sha256"], renamed_digest)
        self.assertNotIn("policy_sha256", refreshed)
        self.assertNotIn("verified_policy_sha256", refreshed)

    def test_sync_preserves_root_and_set_defaults_changes_it(self):
        original = 'model = "gpt-6-astra"\nmodel_reasoning_effort = "xhigh"\nservice_tier = "priority"\n\n[agents]\ncustom=true\n'
        home = self.home(original)
        first = self.install(home)
        parsed = economy.tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual((parsed["model_reasoning_effort"], parsed["service_tier"]), ("xhigh", "priority"))
        economy._install_home(home, self.manifest, self.digest, self.policy, include_defaults=True)
        parsed = economy.tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(parsed["model_reasoning_effort"], self.manifest["routing"]["DEFAULT"]["model_reasoning_effort"])
        self.assertEqual(parsed["service_tier"], "default")
        self.assertTrue(first["transaction"])

    def test_runtime_defaults_drift_is_detected_and_sync_corrects_it(self):
        home = self.home('[mcp_servers.synthetic]\nenabled=true\n')
        self.install(home)
        config_path = home / "config.toml"
        config = config_path.read_text(encoding="utf-8")
        config = config.replace('web_search = "live"', 'web_search = "disabled"')
        config = config.replace('sandbox = "unelevated"', 'sandbox = "elevated"')
        config_path.write_text(config, encoding="utf-8")

        drift = economy._verify_home(home, self.manifest, self.digest, self.policy)
        self.assertFalse(drift["ok"])
        self.assertIn("managed settings differ in config.toml", drift["failures"])
        synced = economy._install_home(home, self.manifest, self.digest, self.policy)
        self.assertFalse(synced["noop"])
        parsed = economy.tomllib.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["windows"]["sandbox"], "unelevated")
        self.assertEqual(parsed["web_search"], "live")
        self.assertTrue(economy._verify_home(home, self.manifest, self.digest, self.policy)["ok"])

    def test_runtime_defaults_roundtrip_preserves_unowned_config(self):
        original = ('[mcp_servers.synthetic]\nenabled=true\nenv_http_headers = '
                    '{ "X-Synthetic-Key" = "literal-placeholder" }\n')
        home = self.home(original)
        installed = self.install(home)
        config = economy.tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["windows"]["sandbox"], "unelevated")
        self.assertEqual(config["web_search"], "live")
        self.assertEqual(config["mcp_servers"]["synthetic"]["env_http_headers"]["X-Synthetic-Key"], "literal-placeholder")
        economy._rollback_home(home, self.digest, installed["transaction"])
        self.assertEqual((home / "config.toml").read_text(encoding="utf-8"), original)

    def test_runtime_defaults_recovery_removes_appended_windows_and_agents(self):
        original = '[mcp_servers.synthetic]\nenabled=true\n'
        home = self.home(original)
        economy._AFTER_MANAGED_MUTATION = lambda rel: (_ for _ in ()).throw(SimulatedCrash()) if rel == "config.toml" else None
        with self.assertRaises(SimulatedCrash): self.install(home)
        economy._AFTER_MANAGED_MUTATION = None
        changed = economy.tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(changed["windows"]["sandbox"], "unelevated")
        self.assertIn("agents", changed)
        economy._recover_home(home)
        self.assertEqual((home / "config.toml").read_text(encoding="utf-8"), original)

    def test_runtime_defaults_manifest_schema_is_exact(self):
        invalid_values = [None, [], "unelevated", {"windows_sandbox": "elevated", "web_search": "live"},
                          {"windows_sandbox": "unelevated", "web_search": "disabled"},
                          {"windows_sandbox": "unelevated", "web_search": "live", "extra": True}]
        for index, invalid in enumerate(invalid_values):
            manifest = copy.deepcopy(self.manifest)
            manifest["runtime_defaults"] = invalid
            path = self.root / f"bad-runtime-{index}.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(economy.EconomyError): economy._load_manifest(path)

    def test_rendered_tier_and_concurrency(self):
        home = self.home("")
        self.install(home)
        route = economy.tomllib.loads((home / "DEFAULT.config.toml").read_text(encoding="utf-8"))
        agent = economy.tomllib.loads((home / "agents" / "worker.toml").read_text(encoding="utf-8"))
        config = economy.tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(route["service_tier"], "default")
        self.assertEqual(agent["service_tier"], "default")
        self.assertEqual(config["agents"]["max_concurrent_threads_per_session"], 3)

    def test_recovery_after_each_managed_write(self):
        base = 'model = "keep"\n\n[agents]\ncustom = true\n'
        probe = self.home(base)
        count = len([x for x in economy._plan_home(probe, self.manifest, self.policy) if x.action != "noop"])
        for stop_after in range(1, count + 1):
            case = self.root / f"crash-{stop_after}"
            case.mkdir()
            (case / "config.toml").write_text(base, encoding="utf-8")
            calls = 0
            def crash(_rel):
                nonlocal calls
                calls += 1
                if calls == stop_after:
                    raise SimulatedCrash()
            economy._AFTER_MANAGED_MUTATION = crash
            with self.assertRaises(SimulatedCrash):
                self.install(case)
            economy._AFTER_MANAGED_MUTATION = None
            with self.assertRaisesRegex(economy.EconomyError, "pending transaction"):
                self.install(case)
            recovered = economy._recover_home(case)
            self.assertFalse(recovered["noop"])
            self.assertEqual((case / "config.toml").read_text(encoding="utf-8"), base)
            self.assertFalse(any((case / name).exists() for name in ["DEFAULT.config.toml", "QUICK.config.toml", "AGENTS.md"]))

    def test_rollback_interruption_is_recovered_after_each_write(self):
        original = 'model="keep"\n\n[agents]\ncustom=true\n'
        probe = self.home(original)
        installed = self.install(probe)
        count = len(json.loads((probe / economy.BACKUP_DIR_NAME / installed["transaction"] / "transaction.json").read_text())["files"])
        for stop_after in range(1, count + 1):
            case = self.root / f"rollback-{stop_after}"; case.mkdir(); (case / "config.toml").write_text(original)
            transaction = self.install(case)["transaction"]; calls = 0
            def crash(_rel):
                nonlocal calls
                calls += 1
                if calls == stop_after: raise SimulatedCrash()
            economy._AFTER_MANAGED_MUTATION = crash
            with self.assertRaises(SimulatedCrash): economy._rollback_home(case, self.digest, transaction)
            economy._AFTER_MANAGED_MUTATION = None
            self.assertEqual(json.loads((case / economy.BACKUP_DIR_NAME / transaction / "transaction.json").read_text())["status"], "rolling_back")
            economy._recover_home(case)
            self.assertEqual((case / "config.toml").read_text(), original)
            self.assertFalse((case / "DEFAULT.config.toml").exists())

    def test_recovery_refuses_unrelated_edit(self):
        home = self.home('model="keep"\n\n[agents]\ncustom=true\n')
        economy._AFTER_MANAGED_MUTATION = lambda _rel: (_ for _ in ()).throw(SimulatedCrash())
        with self.assertRaises(SimulatedCrash): self.install(home)
        economy._AFTER_MANAGED_MUTATION = None
        with (home / "config.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[projects.synthetic]\ntrust_level="trusted"\n')
        with self.assertRaisesRegex(economy.EconomyError, "concurrent edit"):
            economy._recover_home(home)
        self.assertIn("projects.synthetic", (home / "config.toml").read_text(encoding="utf-8"))

    def test_recovery_removes_new_agents_wrapper_without_changing_original(self):
        original = 'model = "keep" # choice\n'
        home = self.home(original)
        economy._AFTER_MANAGED_MUTATION = lambda _rel: (_ for _ in ()).throw(SimulatedCrash())
        with self.assertRaises(SimulatedCrash): self.install(home)
        economy._AFTER_MANAGED_MUTATION = None
        economy._recover_home(home)
        self.assertEqual((home / "config.toml").read_text(encoding="utf-8"), original)

    def test_manifest_names_and_paths_reject_traversal(self):
        for section, bad in (("routing", "../escape"), ("agents", "bad/name"), ("profiles", "..")):
            manifest = copy.deepcopy(self.manifest)
            source = next(iter(manifest[section]))
            manifest[section][bad] = manifest[section].pop(source)
            path = self.root / f"bad-{section}.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(economy.EconomyError): economy._load_manifest(path)
        home = self.home()
        for rel in ("../escape", "/absolute", "C:/absolute", "agents/../../escape", "foo:bar", "CON", "bad. "):
            with self.assertRaises(economy.EconomyError): economy._safe_home_path(home, rel)
        with self.assertRaises(economy.EconomyError): economy._validate_home(Path(home.anchor))
        with self.assertRaises(economy.EconomyError): economy._validate_home(Path.home())

    def test_symlink_escape_is_rejected(self):
        home, outside = self.home(), self.root / "outside"
        outside.mkdir()
        try: os.symlink(outside, home / "agents", target_is_directory=True)
        except OSError as exc: self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(economy.EconomyError, "escapes"):
            economy._safe_home_path(home, "agents/worker.toml")

    def test_internal_symlink_is_also_rejected(self):
        home = self.home(); target = home / "real-agents"; target.mkdir()
        try: os.symlink(target, home / "agents", target_is_directory=True)
        except OSError as exc: self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(economy.EconomyError, "reparse"):
            economy._safe_home_path(home, "agents/worker.toml")

    def test_regular_secret_file_is_never_raw_backed_up(self):
        home = self.home('[agents]\ncustom=true\n')
        (home / "AGENTS.md").write_text('api_key = "synthetic-secret"\n')
        with self.assertRaisesRegex(economy.EconomyError, "sensitive"):
            self.install(home)
        self.assertFalse((home / economy.BACKUP_DIR_NAME).exists())

    def test_metadata_free_transaction_orphan_is_safely_cleaned(self):
        home = self.home(); txid = "20000101T000000Z-deadbeef"
        txdir = home / economy.BACKUP_DIR_NAME / txid; txdir.mkdir(parents=True)
        (txdir / ".transaction.json.economy-0123456789abcdef0123456789abcdef.tmp").write_bytes(b"partial")
        recovered = economy._recover_home(home)
        self.assertEqual(recovered["recovered"], [txid])
        self.assertFalse(txdir.exists())

    def test_transaction_cannot_target_unmanaged_file(self):
        home = self.home(); outside = home / "auth.json"; outside.write_text("keep")
        txid = "malicious-record"; txdir = home / economy.BACKUP_DIR_NAME / txid; txdir.mkdir(parents=True)
        record = {"schema_version": 1, "status": "complete", "transaction": txid, "files": [{"path": "auth.json", "before_exists": False,
                  "before_hash": "MISSING", "after_hash": economy._sha256_bytes(b"keep")} ]}
        (txdir / "transaction.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(economy.EconomyError, "unmanaged path"):
            economy._rollback_home(home, "ignored", txid)
        self.assertEqual(outside.read_text(), "keep")

    def test_latest_uses_precise_created_at_not_inverse_lexical_suffix(self):
        home = self.home(); root = home / economy.BACKUP_DIR_NAME; root.mkdir()
        earlier_id = "20000101T170253Z-ffffffff"  # lexically greater
        later_id = "20000101T170253Z-00000000"    # lexically smaller
        for txid, created_at, digest in (
            (earlier_id, "2000-01-01T17:02:53.100000+00:00", "a" * 64),
            (later_id, "2000-01-01T17:02:53.900000+00:00", "b" * 64),
        ):
            txdir = root / txid; txdir.mkdir()
            record = {"schema_version": 2, "status": "complete", "transaction": txid, "created_at": created_at,
                      "files": [{"path": "agents/worker.toml", "after_hash": digest}]}
            (txdir / "transaction.json").write_text(json.dumps(record))
        self.assertEqual(economy._latest(home).name, later_id)
        self.assertEqual(economy._latest_snapshot_hash(home, "agents/worker.toml"), "b" * 64)

    def test_manifest_digest_is_identical_for_lf_and_crlf(self):
        text = json.dumps(self.manifest, indent=2) + "\n"
        lf, crlf = self.root / "lf.json", self.root / "crlf.json"
        lf.write_bytes(text.encode("utf-8")); crlf.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(economy._load_manifest(lf)[1], economy._load_manifest(crlf)[1])

    def test_noop_sync_refreshes_manifest_provenance_without_new_transaction(self):
        text = json.dumps(self.manifest, indent=2) + "\n"; crlf = text.replace("\n", "\r\n").encode("utf-8")
        normalized_digest = economy._sha256_bytes(text.encode("utf-8")); historical_raw_digest = economy._sha256_bytes(crlf)
        self.assertNotEqual(normalized_digest, historical_raw_digest)
        home = self.home('[agents]\ncustom=true\n')
        first = economy._install_home(home, self.manifest, historical_raw_digest, self.policy)
        config_before = (home / "config.toml").read_bytes(); txdirs_before = [path.name for path in economy._txdirs(home)]
        synced = economy._install_home(home, self.manifest, normalized_digest, self.policy, refresh_provenance=True)
        self.assertTrue(synced["noop"]); self.assertTrue(synced["provenance_refreshed"])
        self.assertEqual((home / "config.toml").read_bytes(), config_before)
        self.assertEqual([path.name for path in economy._txdirs(home)], txdirs_before)
        record = economy._read_tx(home / economy.BACKUP_DIR_NAME / first["transaction"])
        self.assertEqual(record["manifest_sha256"], historical_raw_digest)
        self.assertEqual(record["verified_manifest_sha256"], normalized_digest)
        self.assertIn("verified_at", record)
        self.assertTrue(economy._verify_home(home, self.manifest, normalized_digest, self.policy)["ok"])

    def test_legacy_same_second_order_refuses_to_guess(self):
        home = self.home(); root = home / economy.BACKUP_DIR_NAME; root.mkdir()
        for suffix in ("ffffffff", "00000000"):
            txid = f"20000101T170253Z-{suffix}"; txdir = root / txid; txdir.mkdir()
            (txdir / "transaction.json").write_text(json.dumps({"schema_version": 1, "status": "complete", "transaction": txid, "files": []}))
        with self.assertRaisesRegex(economy.EconomyError, "ambiguous within one second"):
            economy._latest(home)

    def test_recovery_compare_before_write_rejects_race(self):
        home = self.home('model="keep"\n\n[agents]\ncustom=true\n')
        economy._AFTER_MANAGED_MUTATION = lambda _rel: (_ for _ in ()).throw(SimulatedCrash())
        with self.assertRaises(SimulatedCrash): self.install(home)
        economy._AFTER_MANAGED_MUTATION = None
        original_assert = economy._assert_entry_state; raced = False
        def race_then_compare(target_home, entry, expected):
            nonlocal raced
            if not raced and entry.get("path") == "config.toml":
                raced = True
                with (target_home / "config.toml").open("a") as handle: handle.write("\n# concurrent\n")
            return original_assert(target_home, entry, expected)
        economy._assert_entry_state = race_then_compare
        try:
            with self.assertRaisesRegex(economy.EconomyError, "concurrent edit"):
                economy._recover_home(home)
        finally: economy._assert_entry_state = original_assert
        self.assertIn("# concurrent", (home / "config.toml").read_text())

    def test_retired_profile_requires_unchanged_snapshot(self):
        old = copy.deepcopy(self.manifest)
        old["routing"]["CRITICAL"] = {"model": "gpt-6-astra", "model_reasoning_effort": "high", "service_tier": "default"}
        old["retired_profiles"] = []
        digest = economy._json_hash(old)
        home = self.home('[agents]\ncustom=true\n')
        self.install(home, old, digest)
        critical = home / "CRITICAL.config.toml"
        self.assertTrue(critical.exists())
        retired = self.install(home)
        self.assertIn("CRITICAL.config.toml", retired["changed"])
        self.assertFalse(critical.exists())

        changed = self.root / "changed"; changed.mkdir(); (changed / "config.toml").write_text('[agents]\ncustom=true\n')
        self.install(changed, old, digest)
        path = changed / "CRITICAL.config.toml"; path.write_text(path.read_text() + "# user edit\n")
        with self.assertRaisesRegex(economy.EconomyError, "collision"):
            self.install(changed)
        self.assertTrue(path.exists())

        agent = home / "agents" / "worker.toml"
        agent.write_text(agent.read_text() + "# user edit\n")
        with self.assertRaisesRegex(economy.EconomyError, "collision"):
            self.install(home)
        self.assertTrue(agent.read_text().endswith("# user edit\n"))

    def test_retired_implementer_removes_only_owned_file_and_rolls_back(self):
        old = copy.deepcopy(self.manifest)
        old["agents"]["implementer"] = old["agents"].pop("sol-worker")
        old["agents"].pop("sol-worker-high")
        old.pop("retired_agents", None)
        digest = economy._json_hash(old)
        home = self.home('[agents]\ncustom=true\n')
        self.install(home, old, digest)
        implementer = home / "agents" / "implementer.toml"
        original = implementer.read_bytes()
        retired = self.install(home)
        self.assertIn("agents/implementer.toml", retired["changed"])
        self.assertFalse(implementer.exists())
        economy._rollback_home(home, self.digest, retired["transaction"])
        self.assertEqual(implementer.read_bytes(), original)

        other = self.root / "unowned"; other.mkdir()
        (other / "config.toml").write_text('[agents]\ncustom=true\n')
        path = other / "agents" / "implementer.toml"
        path.parent.mkdir(); path.write_text('user role\n')
        with self.assertRaisesRegex(economy.EconomyError, "collision"):
            self.install(other)
        self.assertEqual(path.read_text(), 'user role\n')

    def test_legacy_clean_transaction_rolls_back(self):
        home = self.home(); rel = "DIRECT.config.toml"; before = b'model="old"\n'; after = b'model="new"\n'
        (home / rel).write_bytes(after)
        txid = "legacy-clean"; txdir = home / economy.BACKUP_DIR_NAME / txid; txdir.mkdir(parents=True)
        backup = economy._backup_name(rel); (txdir / backup).write_bytes(before)
        record = {"schema_version": 1, "status": "complete", "transaction": txid, "files": [{"path": rel, "before_exists": True,
                  "before_hash": economy._sha256_bytes(before), "after_hash": economy._sha256_bytes(after), "backup": backup}]}
        (txdir / "transaction.json").write_text(json.dumps(record), encoding="utf-8")
        economy._rollback_home(home, "ignored", txid)
        self.assertEqual((home / rel).read_bytes(), before)

    def test_legacy_rollback_interruption_is_recoverable(self):
        home = self.home(); rel = "DIRECT.config.toml"; before = b'model="old"\n'; after = b'model="new"\n'
        (home / rel).write_bytes(after)
        txid = "legacy-crash"; txdir = home / economy.BACKUP_DIR_NAME / txid; txdir.mkdir(parents=True)
        backup = economy._backup_name(rel); (txdir / backup).write_bytes(before)
        record = {"schema_version": 1, "status": "complete", "transaction": txid, "files": [{"path": rel, "before_exists": True,
                  "before_hash": economy._sha256_bytes(before), "after_hash": economy._sha256_bytes(after), "backup": backup}]}
        (txdir / "transaction.json").write_text(json.dumps(record))
        economy._AFTER_MANAGED_MUTATION = lambda _rel: (_ for _ in ()).throw(SimulatedCrash())
        with self.assertRaises(SimulatedCrash): economy._rollback_home(home, "ignored", txid)
        economy._AFTER_MANAGED_MUTATION = None
        self.assertEqual(json.loads((txdir / "transaction.json").read_text())["status"], "rolling_back_legacy")
        economy._recover_home(home)
        self.assertEqual((home / rel).read_bytes(), before)

    def test_legacy_literal_secret_backup_is_refused(self):
        home = self.home(); rel = "config.toml"
        before = b'[headers]\napi_key="synthetic-secret"\n'; after = b'[headers]\napi_key="different"\n'
        (home / rel).write_bytes(after)
        txid = "legacy-secret"; txdir = home / economy.BACKUP_DIR_NAME / txid; txdir.mkdir(parents=True)
        backup = economy._backup_name(rel); (txdir / backup).write_bytes(before)
        record = {"schema_version": 1, "status": "complete", "transaction": txid, "files": [{"path": rel, "before_exists": True,
                  "before_hash": economy._sha256_bytes(before), "after_hash": economy._sha256_bytes(after), "backup": backup}]}
        (txdir / "transaction.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(economy.EconomyError, "literal sensitive"):
            economy._rollback_home(home, "ignored", txid)
        self.assertEqual((home / rel).read_bytes(), after)


if __name__ == "__main__":
    unittest.main()
