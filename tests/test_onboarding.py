import copy
import json
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import economy
from workspace_temp import WorkspaceTempDirectory


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTempDirectory(ROOT, prefix='test-onboard-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest, self.digest = economy._load_manifest(ROOT / 'manifest.json')
        self.policy, _ = economy._load_policy(self.manifest)
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        (self.root / 'policy.md').write_text(self.policy, encoding='utf-8')

    def test_public_install_preserves_security_search_and_root_preferences(self):
        self.assertEqual(self.manifest['runtime_defaults'], {})
        home = self.root / 'home'
        home.mkdir()
        original = ('model="personal-choice"\nweb_search="cached"\napproval_policy="on-request"\n'
                    'sandbox_mode="workspace-write"\napprovals_reviewer="user"\n'
                    '[windows]\nsandbox="unelevated"\nsandbox_private_desktop=true\n')
        (home / 'config.toml').write_text(original, encoding='utf-8')
        result = economy._install_home(home, self.manifest, self.digest, self.policy)
        parsed = economy.tomllib.loads((home / 'config.toml').read_text())
        for key, value in economy.tomllib.loads(original).items():
            self.assertEqual(parsed[key], value)
        self.assertTrue(economy._verify_home(home, self.manifest, self.digest, self.policy)['ok'])
        economy._rollback_home(home, self.digest, result['transaction'])
        self.assertEqual((home / 'config.toml').read_text(), original)

    def test_custom_account_models_populate_routes_and_roles_without_home_writes(self):
        home = self.root / 'not-created'
        choices = {'light': ('available-small', 'low'), 'balanced': ('available-mid', 'medium'),
                   'deep': ('available-large', 'high')}
        target = economy._init_manifest(str(home), 'main', self.root, choices)
        actual, _ = economy._load_manifest(target)
        self.assertEqual(actual['routing']['QUICK']['model'], 'available-large')
        self.assertEqual(actual['routing']['QUICK']['model_reasoning_effort'], 'medium')
        self.assertEqual(actual['routing']['DEFAULT']['model'], 'available-large')
        self.assertEqual(actual['routing']['DEEP']['model'], 'available-large')
        self.assertEqual(actual['routing']['DIRECT']['model'], 'available-small')
        self.assertEqual(actual['routing']['CONTINUITY']['model'], 'available-mid')
        self.assertEqual(actual['agents']['worker']['model_reasoning_effort'], 'low')
        self.assertEqual(actual['agents']['sol-worker']['model'], 'available-mid')
        self.assertEqual(actual['agents']['sol-worker-high']['model_reasoning_effort'], 'high')
        self.assertFalse(home.exists())

    def test_custom_deep_xhigh_does_not_change_default_owner_effort(self):
        choices = {'light': ('small', 'high'), 'balanced': ('middle', 'medium'),
                   'deep': ('frontier', 'xhigh')}
        actual_path = economy._init_manifest(str(self.root / 'home'), 'main', self.root, choices)
        actual, _ = economy._load_manifest(actual_path)
        self.assertEqual(actual['routing']['QUICK']['model_reasoning_effort'], 'medium')
        self.assertEqual(actual['routing']['DEFAULT']['model_reasoning_effort'], 'high')
        self.assertEqual(actual['routing']['DEEP']['model_reasoning_effort'], 'xhigh')

    def test_partial_or_invalid_custom_models_leave_no_local_manifest(self):
        choices = {'light': ('small', 'low')}
        with self.assertRaisesRegex(economy.EconomyError, 'all three'):
            economy._init_manifest(str(self.root / 'home'), 'main', self.root, choices)
        for model, effort in [('model; do-something', 'high'), ('valid-model', 'unsupported')]:
            bad = {tier: (model, effort) for tier in ('light', 'balanced', 'deep')}
            with self.assertRaises(economy.EconomyError):
                economy._init_manifest(str(self.root / 'home'), 'main', self.root, bad)
        self.assertFalse((self.root / 'manifest.local.json').exists())

    def test_doctor_does_not_create_home_or_claim_model_availability(self):
        home = self.root / 'absent'
        stream = StringIO()
        with redirect_stdout(stream):
            code = economy.main(['doctor', '--manifest', str(self.root / 'manifest.json'), '--home', str(home)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stream.getvalue())['model_availability'], 'not_checked')
        self.assertFalse(home.exists())


if __name__ == '__main__':
    unittest.main()
