"""Native output hook -> strict attachment filtering, with isolated profile scopes."""
from contextlib import contextmanager
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CORE = Path('C:/Users/win-10/AppData/Local/hermes/hermes-agent')
sys.path[:0] = [str(ROOT), str(CORE), str(CORE/'venv/Lib/site-packages')]
import tools
tools.__path__.append(str(CORE/'tools'))
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=')


class AdminTechnicalMediaTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override, hermes_home_key
        from hermes_cli.plugins import PluginManager, PluginManifest, PluginContext, LoadedPlugin
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder, \
                tempfile.TemporaryDirectory(prefix='media-test-', dir=ROOT/'artifacts') as generated:
            base = Path(folder)
            homes = {name: base/'profiles'/name for name in ('admin', 'maintenance')}
            for name, home in homes.items():
                home.mkdir(parents=True)
                (home/'image_cache').mkdir()
                config = {'plugins': {'enabled': ['komatso-technical-docs']},
                          'platform_toolsets': {'bale': ['komatso_technical_docs']},
                          'gateway': {'strict': name == 'admin', 'trust_recent_files': False,
                                      'media_delivery_exclusive_roots': name == 'admin',
                                      'media_delivery_allow_dirs': [str(home/'image_cache')]}}
                (home/'config.yaml').write_text(json.dumps(config), encoding='utf-8')
            source = ROOT/'integrations/hermes/plugins/komatso-technical-docs/__init__.py'
            spec = importlib.util.spec_from_file_location('media_plugin_fixture', source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            manager = PluginManager(hermes_home_key(homes['admin']))
            manifest = PluginManifest(name='komatso-technical-docs', source='project')
            manager._plugins[manifest.name] = LoadedPlugin(manifest=manifest, enabled=True)
            module.register(PluginContext(manifest, manager))
            manager._discovered = True
            with patch('hermes_cli.plugins.get_plugin_manager', return_value=manager):
                yield homes, Path(generated), set_hermes_home_override, reset_hermes_home_override

    def transform(self, text, session, turn):
        from agent.turn_finalizer import apply_llm_output_transform
        agent = SimpleNamespace(session_id=session, model='fixture', platform='bale')
        return apply_llm_output_transform(agent, text, turn_id=turn)[0]

    def deliverable(self, text):
        from gateway.platforms.base import BasePlatformAdapter
        files, _ = BasePlatformAdapter.extract_media(text)
        return BasePlatformAdapter.filter_media_delivery_paths(files)

    def test_registered_hook_delivers_generated_pages_only_in_restricted_profile(self):
        with self.fixture() as (homes, generated, bind, reset):
            for name in ('partbook-p588.png', 'manual_evidence-p10.png'):
                (generated/name).write_bytes(PNG)
            for profile in ('admin', 'maintenance', 'admin'):
                token = bind(homes[profile])
                try:
                    for marker in ('', '[[as_document]]\n'):
                        text = 'نقشهٔ درخواستی\n' + marker + '\n'.join(
                            'MEDIA:'+(generated/name).as_posix()
                            for name in ('partbook-p588.png', 'manual_evidence-p10.png'))
                        final = self.transform(text, generated.name, profile+marker)
                        files = self.deliverable(final)
                        self.assertEqual(len(files), 2, final)
                        self.assertEqual('[[as_document]]' in text, '[[as_document]]' in final)
                        for path, _ in files:
                            self.assertEqual(Path(path).read_bytes(), PNG)
                            self.assertEqual(Path(path).is_relative_to(homes[profile]), profile == 'admin')
                        if profile == 'maintenance':
                            self.assertEqual(final, text)
                        else:
                            self.assertEqual(self.transform(final, generated.name, 'repeat'), final)
                finally:
                    reset(token)

    def test_rejected_sources_and_cache_failure_return_clear_reply_without_attachment(self):
        with self.fixture() as (homes, generated, bind, reset):
            image = generated/'partbook-p588.png'
            image.write_bytes(PNG)
            token = bind(homes['admin'])
            try:
                for session, path in ((generated.name+'-other', image),
                                      ('../'+generated.name, image),
                                      (generated.name, generated/'private.png'),
                                      (generated.name, generated/'manual_evidence-p999.png')):
                    with self.subTest(session=session, path=path):
                        final = self.transform('پیوست می‌کنم\nMEDIA:'+path.as_posix(), session, str(path)+session)
                        self.assertNotIn('MEDIA:', final)
                        self.assertIn('آماده نشد', final)
                        self.assertFalse(self.deliverable(final))
                image.write_bytes(b'not a PNG')
                final = self.transform('MEDIA:'+image.as_posix(), generated.name, 'invalid-png')
                self.assertNotIn('MEDIA:', final)
                image.write_bytes(PNG)
                with patch('integrations.hermes.technical_media.os.replace', side_effect=PermissionError('fixture')):
                    final = self.transform('MEDIA:'+image.as_posix(), generated.name, 'unwritable')
                self.assertNotIn('MEDIA:', final)
                self.assertIn('آماده نشد', final)
                self.assertEqual(image.read_bytes(), PNG)
            finally:
                reset(token)


if __name__ == '__main__':
    unittest.main()
