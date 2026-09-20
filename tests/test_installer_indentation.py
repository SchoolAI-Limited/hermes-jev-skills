import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('trial_installer', Path(__file__).resolve().parents[1] / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class EnabledListIndentation(unittest.TestCase):
    def test_existing_sequence_indentation_and_uninstall(self):
        for indent in ('  ', '    ', '      '):
            with self.subTest(indent=indent), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'config.yaml'
                original = ('plugins:\n  enabled:\n    # retain this comment\n'
                            + indent + '- alpha\n' + indent + '- beta\n'
                            + '  disabled:\n    - gamma\nother: preserved\n')
                path.write_text(original)
                installer.enable_plugins(path, ['hermes-jev'], True)
                expected = original.replace('  enabled:\n', '  enabled:\n' + indent + '- hermes-jev\n')
                self.assertEqual(path.read_text(), expected)
                installer.enable_plugins(path, ['hermes-jev'], False)
                self.assertEqual(path.read_text(), original)
