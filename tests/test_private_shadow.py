"""Synthetic offline acceptance tests; no real keys, profiles or provider requests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_plugin_middleware import plugin, REPO
from test_install import run_installer, CONFIG


class PrivateShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=REPO)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'profiles' / 'private-test'
        self.home.mkdir(parents=True)
        self.config = {**plugin.route.DEFAULT_CONFIG, 'private_profiles': ['private-test'],
                       'tiers': {'medium': {'general': ['test:medium']}},
                       'escalation': {'enabled': True, 'rungs': ['synthetic-seat']}}
        self.settings = dict(routing='shadow', skills='on', notice='on', memory='off',
                             compaction='off', actions='off', supervision='off', escalation='off')
        self.sent = []
        self.fail = False
        self.old_ctx = plugin._CTX
        self.addCleanup(setattr, plugin, '_CTX', self.old_ctx)
        plugin._TURNS.clear()
        plugin.route._DECISIONS.clear()
        for patch in (
            mock.patch.object(plugin, '_home', return_value=self.home),
            mock.patch.object(plugin, '_setting', side_effect=lambda n, d: self.settings.get(n, d)),
            mock.patch.object(plugin, '_default_model', return_value='original'),
            mock.patch.object(plugin.route, 'load_config', side_effect=lambda: dict(self.config)),
            mock.patch.object(plugin.catalog, 'models', return_value=[]),
            mock.patch.object(plugin.route.client.keystore, 'resolve', return_value='synthetic-test-key'),
            mock.patch.object(plugin.route.client, '_http_transport', side_effect=self.transport),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def transport(self, body, headers, timeout):
        self.sent.append(body)
        if self.fail:
            raise plugin.route.client.JevError('auth_failed')
        return json.dumps({'answers': {
            'difficulty': {'type': 'score', 'score': 1, 'confidence': 0.99},
            'kind': {'type': 'choice', 'choice': 'general', 'confidence': 0.99,
                     'probabilities': {'general': 1}},
            'costly_mistake': {'type': 'noul', 'noul': 0.1},
        }}).encode()

    def turn(self, turn_id='local-turn-id'):
        text = 'Synthetic confidential orchard plan: purple pears for Monday.'
        request = {'model': 'original', 'messages': [{'role': 'user', 'content': text}]}
        before = json.dumps(request).encode()
        with mock.patch.object(plugin.skillpick, 'discover', side_effect=AssertionError('private catalog read')):
            self.assertIsNone(plugin._on_pre_llm_call('local-session-id', turn_id, text))
        self.assertIsNone(plugin._on_llm_request(request, 'local-session-id', turn_id, 'original', 'test'))
        self.assertEqual(before, json.dumps(request).encode())
        return plugin._on_transform_output('synthetic response body', 'local-session-id')

    def logs(self):
        return [json.loads(line) for line in (self.home / 'logs/jev-decisions.jsonl').read_text().splitlines()]

    def test_private_shadow_no_turn_catalog_or_ids_on_wire_and_matching_logs(self):
        notice = self.turn()
        self.assertIn('WOULD route to test:medium; no model changed.', notice)
        self.assertEqual(len(self.sent), 1)
        payload = json.loads(self.sent[0])
        self.assertEqual(set(payload['state']), {'turn_features'})
        for marker in ('orchard', 'purple', 'local-session-id', 'local-turn-id', 'synthetic response body'):
            self.assertNotIn(marker, self.sent[0].decode())
        logs = self.logs()
        self.assertEqual([e['kind'] for e in logs], ['skill', 'route'])
        for entry in logs:
            self.assertEqual(entry['session_id'], 'local-session-id')
            self.assertEqual(entry['turn_id'], 'local-turn-id')
        self.assertEqual(logs[0]['skipped'], 'private_profile')
        self.assertNotIn('orchard', json.dumps(logs))
        self.assertNotIn('synthetic response body', json.dumps(logs))
        self.turn('next-local-turn')
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.logs()[-1]['cached'])
        self.assertEqual(self.logs()[-1]['turn_id'], 'next-local-turn')

    def test_private_skill_library_skips_entire_catalog_request(self):
        transport = mock.Mock(side_effect=AssertionError('must not send'))
        result = plugin.skillpick.pick('Confidential orchard plan',
            [{'name': 'private-catalog-marker', 'description': 'private description', 'path': 'synthetic'}],
            profile='private-test', config=self.config, transport=transport)
        self.assertEqual(result['skipped'], 'private_profile')
        transport.assert_not_called()

    def test_failed_routing_is_correlated_without_response(self):
        self.fail = True
        self.assertIn('WOULD keep', self.turn())
        entry = self.logs()[-1]
        self.assertIn('auth_failed', entry['reason'])
        self.assertEqual(entry['turn_id'], 'local-turn-id')

    def test_off_gates_block_explicit_handlers_and_prompt_and_route_escalation(self):
        ctx = mock.Mock()
        plugin.register(ctx)
        rule = ctx.register_system_prompt_section.call_args.args[1]
        for call in ctx.register_tool.call_args_list:
            name = call.kwargs['name']
            result = json.loads(call.kwargs['handler']({}, session_id='s', turn_id='t'))
            self.assertEqual(result['status'], 'disabled', name)
            self.assertNotIn(name, rule)
        self.assertEqual(self.sent, [])
        self.assertFalse(plugin._routing_config()['escalation']['enabled'])
        for entry in self.logs():
            self.assertEqual((entry['session_id'], entry['turn_id']), ('s', 't'))

    def test_privacy_or_escalation_config_change_invalidates_cache(self):
        self.turn()
        self.config['min_confidence'] = 0.95
        self.turn('changed-config')
        self.assertEqual(len(self.sent), 2)


class ScopedInstallerTests(unittest.TestCase):
    def test_exact_home_install_check_uninstall_leave_other_agents_untouched(self):
        with tempfile.TemporaryDirectory(dir=REPO) as tmp:
            home = Path(tmp)
            target = home / 'hermes'
            other = target / 'profiles/other'
            other.mkdir(parents=True)
            (target / 'config.yaml').write_text(CONFIG)
            (other / 'config.yaml').write_text(CONFIG)
            sentinels = []
            for folder in (home / '.claude/skills', home / '.codex/skills', home / '.agents/skills',
                           home / '.local/bin', target / 'plugins/hermes-handoff',
                           target / 'skills/jev/jev-memory', target / 'scripts'):
                folder.mkdir(parents=True, exist_ok=True)
                sentinel = folder / 'keep'
                sentinel.write_text('untouched')
                sentinels.append(sentinel)
            flags = ['--hermes-only', '--hermes-home', str(target), '--plugins', 'hermes-jev',
                     '--skills', 'none', '--scripts', 'none', '--enable', 'all']
            before = {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}
            _, report = run_installer(flags + ['--check'], home)
            self.assertEqual(report['plugins'], ['hermes-jev'])
            self.assertEqual(report['skills'], [])
            self.assertFalse(report['cli'])
            self.assertEqual(before, {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()})
            run_installer(flags, home)
            self.assertTrue((target / 'plugins/hermes-jev/jevkit/skillpick.py').is_file())
            self.assertFalse((other / 'plugins').exists())
            installed = (target / 'config.yaml').read_bytes()
            run_installer(flags + ['--uninstall', '--check'], home)
            self.assertEqual(installed, (target / 'config.yaml').read_bytes())
            run_installer(flags + ['--uninstall'], home)
            self.assertFalse((target / 'plugins/hermes-jev').exists())
            self.assertEqual((target / 'config.yaml').read_text(), CONFIG)
            self.assertEqual((other / 'config.yaml').read_text(), CONFIG)
            for sentinel in sentinels:
                self.assertEqual(sentinel.read_text(), 'untouched')

    def test_scoped_symlink_escape_refused(self):
        with tempfile.TemporaryDirectory(dir=REPO) as tmp:
            home = Path(tmp)
            target = home / 'target'
            target.mkdir()
            (target / 'config.yaml').write_text(CONFIG)
            shared = home / 'shared'
            shared.mkdir()
            (target / 'plugins').symlink_to(shared, target_is_directory=True)
            with self.assertRaises(SystemExit):
                run_installer(['--hermes-only', '--hermes-home', str(target), '--plugins', 'hermes-jev',
                               '--skills', 'none', '--scripts', 'none'], home)
            self.assertEqual(list(shared.iterdir()), [])
