import importlib.util
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('release_publish',Path(__file__).resolve().parents[2]/'deploy/publish-desktop.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)

class ReleasePermissionsTests(unittest.TestCase):
    def test_publishing_already_uploaded_private_file_makes_it_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'galaxy-ai-Setup-0.8.10.exe'
            package.write_bytes(b'MZfixture');package.chmod(0o600)
            result=publisher.publish(package,root,'Release notes')
            self.assertEqual(package.stat().st_mode & 0o777,0o644)
            self.assertEqual(result['size'],package.stat().st_size)
            self.assertEqual(result['version'],'0.8.10')
    def test_existing_identical_destination_permissions_are_repaired(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);downloads=root/'downloads';downloads.mkdir()
            package=root/'galaxy-ai-Setup-0.8.10.exe';package.write_bytes(b'MZfixture')
            destination=downloads/package.name;destination.write_bytes(package.read_bytes());destination.chmod(0o600)
            publisher.publish(package,downloads,'Notes')
            self.assertEqual(destination.stat().st_mode & 0o777,0o644)
            self.assertEqual(destination.read_bytes(),b'MZfixture')

if __name__=='__main__':unittest.main()
