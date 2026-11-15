import os
import tempfile
import unittest
import uuid
from pathlib import Path

from galaxy_cli.file_scope import source_file
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.file.publish import publish, normalize


class FileTests(unittest.TestCase):
    def test_relative_path_validation(self):
        for path in ('/etc/passwd', '../private', 'a/../../x', '.', './x', 'a//x', 'a/', '.work/x',
                     'C:/Windows/a', r'C:\Windows\a', 'https://example.org/a', '%2e%2e/a', 'a%252fb', '~/.ssh/x', 'a\x00b'):
            with self.subTest(path=path), self.assertRaises(ToolError): normalize({'path': path})
        with self.assertRaises(ToolError): normalize({'path': 'a', 'workspace_directory': '/tmp'})

    def test_normal_file_is_copied_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); work = root / 'work'; hosted = root / 'hosted'
            work.mkdir(); hosted.mkdir(); (work / '报告.txt').write_text('demo')
            result = publish({'path': '报告.txt'}, {'workspace_directory': str(work), 'hosting_directory': str(hosted), 'publication_id': str(uuid.uuid4())})
            self.assertEqual(result['media_type'], 'text/plain')
            self.assertEqual(result['size'], 4)
            self.assertEqual(next(hosted.iterdir()).read_bytes(), b'demo')
            self.assertEqual((work / '报告.txt').read_text(), 'demo')
            self.assertEqual(next(hosted.iterdir()).stat().st_nlink, 1)
            self.assertEqual(next(hosted.iterdir()).stat().st_mode & 0o777, 0o600)

    def test_symlinks_hardlinks_special_files_and_linked_ancestors_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); work = root / 'work'; outside = root / 'outside'
            work.mkdir(); outside.mkdir(); (outside / 'secret').write_text('private')
            (work / 'leaf').symlink_to(outside / 'secret')
            (work / 'parent').symlink_to(outside, target_is_directory=True)
            os.link(outside / 'secret', work / 'hard')
            os.mkfifo(work / 'fifo')
            (root / 'alias').symlink_to(work, target_is_directory=True)
            for directory, relative in ((work, 'leaf'), (work, 'parent/secret'), (work, 'hard'), (work, 'fifo'), (root / 'alias', 'hard')):
                with self.subTest(relative=relative), self.assertRaises(ToolError):
                    with source_file(directory, relative): self.fail('must reject')

    def test_missing_server_context_and_overwrite_are_rejected(self):
        with self.assertRaises(ToolError): publish({'path': 'a.txt'}, {})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); work=root/'work'; hosted=root/'hosted'; work.mkdir(); hosted.mkdir()
            (work/'a.txt').write_text('a')
            context = {'workspace_directory': str(work), 'hosting_directory': str(hosted), 'publication_id': str(uuid.uuid4())}
            publish({'path':'a.txt'}, context)
            (work/'a.txt').write_text('b')
            with self.assertRaises(ToolError): publish({'path':'a.txt'}, context)
            self.assertEqual(next(hosted.iterdir()).read_text(), 'a')
            self.assertFalse(any(p.name.startswith('.') for p in hosted.iterdir()))
