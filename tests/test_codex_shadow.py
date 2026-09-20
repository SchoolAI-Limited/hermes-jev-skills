"""Offline account-evidence adapter acceptance. All IDs and capabilities are synthetic."""
import json
import unittest
from unittest import mock

import test_private_shadow as private_fixture
from test_plugin_middleware import plugin
from jevkit import spend


def inventory():
    return {'source': 'operator-reviewed-synthetic-catalog', 'observed_at': '2026-01-02T03:04:05Z',
            'models': [{'id': 'synthetic-fit', 'context_window': 16000,
                        'text': True, 'vision': True, 'tool_call': True}]}


class CodexShadowTests(unittest.TestCase):
    transport = private_fixture.PrivateShadowTests.transport
    logs = private_fixture.PrivateShadowTests.logs

    def setUp(self):
        private_fixture.PrivateShadowTests.setUp(self)
        self.config['codex_shadow_inventory'] = inventory()
        self.config['tiers'] = {'medium': {'general': [
            'openai:synthetic-api', 'openai-codex:synthetic-unverified',
            'openai:synthetic-fit', 'openai-codex:synthetic-fit']}}
        for name in ('models', 'load_models_dev', '_env_names', '_hermes_logins'):
            patch = mock.patch.object(plugin.catalog, name, side_effect=AssertionError('discovery forbidden'))
            patch.start()
            self.addCleanup(patch.stop)

    def decide(self, **kwargs):
        args = dict(current='openai-codex:original', only_provider='openai-codex',
                    config=self.config, profile='private-test', shadow=True, context_tokens=100)
        args.update(kwargs)
        return plugin.route.decide('Synthetic confidential orchard plan.', **args)

    def request(self, mode='shadow', model='original', extra=None, fresh=True):
        self.settings['routing'] = mode
        text = 'Synthetic confidential orchard plan.'
        request = {'model': model, 'input': [{'role': 'user', 'content': text}],
                   'tools': [{'type': 'function', 'name': 'synthetic_tool'}],
                   'instructions': 'Synthetic private instructions.', 'stream': True}
        request.update(extra or {})
        before = json.dumps(request).encode()
        if fresh:
            with mock.patch.object(plugin.skillpick, 'discover', side_effect=AssertionError('private skills')):
                plugin._on_pre_llm_call('session-marker', 'turn-marker', text)
        result = plugin._on_llm_request(request, 'session-marker', 'turn-marker', model, 'openai-codex')
        self.assertIsNone(result)
        self.assertEqual(json.dumps(request).encode(), before)
        return (plugin._TURNS.get('session-marker') or {}).get('decision')

    def test_valid_inventory_intersects_pools_and_preserves_identity(self):
        result = self.decide()
        self.assertTrue(result['routed'])
        self.assertEqual(result['model'], 'openai-codex:synthetic-fit')
        self.assertEqual(result['provider'], 'openai-codex')
        self.assertEqual(result['tier'], 'medium')
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(plugin.catalog.codex_shadow_models(self.config)[0]['price'])

    def test_missing_and_malformed_evidence_never_calls_classifier(self):
        good = inventory()
        bad = [None, [], {}, {'source': 'x'}, {**good, 'source': ''},
               {**good, 'source': 3}, {**good, 'observed_at': 'yesterday'},
               {**good, 'observed_at': '2026-02-30T03:04:05Z'},
               {**good, 'observed_at': '2026-01-02T03:04:05'},
               {**good, 'observed_at': '2026-01-02T03:04:05+00:99'},
               {**good, 'models': {}}, {**good, 'models': []},
               {**good, 'models': ['synthetic-fit']},
               {**good, 'models': good['models'] * 2}]
        for field, value in [('id', 'openai:synthetic-fit'), ('id', ' synthetic-fit'),
                             ('id', ''), ('id', None), ('context_window', True),
                             ('context_window', -1), ('context_window', '16000'),
                             ('vision', 'true'), ('tool_call', 1), ('text', [])]:
            bad.append({**good, 'models': [{**good['models'][0], field: value}]})
        for evidence in bad:
            with self.subTest(evidence=evidence):
                self.config['codex_shadow_inventory'] = evidence
                self.assertFalse(self.decide()['routed'])
        self.config.pop('codex_shadow_inventory')
        self.assertFalse(self.decide()['routed'])
        self.assertEqual(self.sent, [])

    def test_unknown_context_and_required_capabilities_are_not_eligibility(self):
        for field in ('context_window', 'text', 'vision', 'tool_call'):
            with self.subTest(field=field):
                self.config['codex_shadow_inventory'] = inventory()
                self.config['codex_shadow_inventory']['models'][0].pop(field)
                self.assertFalse(self.decide(has_images=True)['routed'])
        self.assertEqual(self.sent, [])
        self.config['codex_shadow_inventory'] = inventory()
        spec = self.config['codex_shadow_inventory']['models'][0]
        spec.pop('tool_call')
        spec.pop('vision')
        self.assertTrue(self.decide(need_tools=False)['routed'])
        self.assertFalse(self.decide(need_tools=True)['routed'])

    def test_conservative_context_not_max_and_false_capabilities(self):
        spec = self.config['codex_shadow_inventory']['models'][0]
        spec['max_context_window'] = 999999
        self.assertFalse(self.decide(context_tokens=12801)['routed'])
        self.assertTrue(self.decide(context_tokens=12800)['routed'])
        for field in ('text', 'tool_call', 'vision'):
            spec[field] = False
            self.assertFalse(self.decide(has_images=True)['routed'])
            spec[field] = True

    def test_config_file_inventory_is_consumed_without_discovery(self):
        from jevkit import route as standalone_route
        path = self.home / 'routing.json'
        path.write_text(json.dumps(self.config), encoding='utf-8')
        config = standalone_route.load_config(path)
        self.assertEqual(config['codex_shadow_inventory'], inventory())
        self.assertTrue(self.decide(config=config)['routed'])

    def test_classifier_outage_keeps_original_and_features_floor_simple(self):
        self.transport_failed = True
        result = self.request()
        self.assertFalse(result['routed'])
        self.assertIn('auth_failed', result['reason'])
        self.transport_failed = False
        self.score = 0
        self.assertEqual(self.request()['tier'], 'medium')

    def test_absent_pool_intersection_and_exclusion_fail_closed(self):
        for refs in (['openai:synthetic-fit'], ['openai-codex:synthetic-unverified']):
            self.config['tiers'] = {'medium': {'general': refs}}
            self.assertFalse(self.decide()['routed'])
        self.config['tiers'] = {'medium': {'general': ['openai-codex:synthetic-fit']}}
        self.config['exclude'] = ['openai-codex:*']
        self.assertFalse(self.decide()['routed'])
        self.assertEqual(self.sent, [])

    def test_inventory_not_rows_is_authority_and_changes_invalidate_cache(self):
        self.assertTrue(self.decide(rows=[])['routed'])
        self.assertTrue(self.decide()['cached'])
        self.config['codex_shadow_inventory']['models'][0]['id'] = 'synthetic-other'
        self.assertFalse(self.decide()['routed'])
        self.config['codex_shadow_inventory'] = inventory()
        self.config['codex_shadow_inventory']['observed_at'] = '2026-01-03T03:04:05Z'
        self.assertNotIn('cached', self.decide())
        self.assertEqual(len(self.sent), 2)

    def test_shadow_request_private_disclosure_and_tool_loop(self):
        result = self.request(model='openai-codex:original')
        self.assertTrue(result['routed'])
        notice = plugin._on_transform_output('response-marker', 'session-marker', 'turn-marker')
        self.assertIn('WOULD route to openai-codex:synthetic-fit; no model changed.', notice)
        self.request(fresh=False)
        self.assertEqual(len(self.sent), 1)
        payload = json.loads(self.sent[0])
        self.assertEqual(set(payload['state']), {'turn_features'})
        for marker in ('orchard', 'Synthetic private instructions.', 'session-marker', 'turn-marker',
                       'response-marker', 'synthetic-fit', inventory()['source'], inventory()['observed_at']):
            self.assertNotIn(marker, self.sent[0].decode())
        logs = self.logs()
        self.assertEqual([e['kind'] for e in logs], ['skill', 'route'])
        self.assertEqual(logs[0]['skipped'], 'private_profile')
        self.assertEqual(logs[1]['from'], 'openai-codex:original')
        self.assertEqual(logs[1]['model'], 'openai-codex:synthetic-fit')
        self.assertNotIn('orchard', json.dumps(logs))
        self.assertNotIn('response-marker', json.dumps(logs))

    def test_off_active_and_shadow_to_active_never_route(self):
        self.assertIsNone(self.request(mode='off'))
        self.assertFalse(self.request(mode='on')['routed'])
        self.assertEqual(self.sent, [])
        self.assertTrue(self.request()['routed'])
        self.assertFalse(self.request(mode='on', fresh=False)['routed'])
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.decide(shadow=False)['routed'])
        self.assertFalse(self.decide(shadow='on')['routed'])
        self.assertFalse(self.decide(only_provider=None)['routed'])
        self.assertFalse(self.decide(only_provider='openai')['routed'])
        self.assertEqual(len(self.sent), 1)

    def test_pin_and_unknown_request_tools_keep(self):
        self.assertFalse(self.request(model='synthetic-pinned')['routed'])
        self.config['codex_shadow_inventory']['models'][0].pop('tool_call')
        self.assertFalse(self.request()['routed'])
        self.assertTrue(self.request(extra={'tools': []})['routed'])

    def test_images_in_history_and_responses_format_require_evidence(self):
        self.config['codex_shadow_inventory']['models'][0].pop('vision')
        for image_type in ('image_url', 'input_image'):
            messages = [{'role': 'user', 'content': [{'type': image_type, 'image_url': 'synthetic'}]},
                        {'role': 'user', 'content': 'Now describe it.'}]
            self.assertFalse(self.request(extra={'input': messages})['routed'])
        self.assertEqual(self.sent, [])

    def test_hard_classification_reuses_policy_without_escalation(self):
        self.score = 2.8
        self.config['tiers']['hard'] = self.config['tiers'].pop('medium')
        with mock.patch.object(plugin.ladder, 'choose', side_effect=AssertionError('no ladder')):
            self.assertEqual(self.decide()['tier'], 'hard')

    def test_codex_login_never_unlocks_openai_vendor_and_prices_stay_unknown(self):
        self.assertNotIn('openai-codex', plugin.catalog.HERMES_ALIASES)
        with mock.patch.object(plugin.catalog, '_env_names', return_value=set()), \
             mock.patch.object(plugin.catalog, '_hermes_logins', return_value={'openai-codex'}):
            self.assertEqual(plugin.catalog.available_providers({'openai': {'env': ['SYNTHETIC_KEY']}}), [])
        prices = {'openai:synthetic-fit': {'input': 5, 'output': 10}}
        self.assertIsNone(spend.cost_of(spend.Usage('openai-codex:synthetic-fit'),
                                      'openai-codex:synthetic-fit', prices))
        self.assertIsNone(spend.counterfactuals([], ['openai-codex:synthetic-fit'], prices=prices)[0]['cost_usd'])

    def test_unrestricted_route_cannot_select_codex_pin(self):
        self.config['tiers'] = {'medium': {'general': ['openai-codex:synthetic-fit']}}
        self.assertFalse(self.decide(current='test:original', only_provider=None, rows=[])['routed'])


if __name__ == '__main__':
    unittest.main()
