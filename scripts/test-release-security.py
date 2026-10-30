"""Проверки доверия к CI-артефактам и запрета пересборки при публикации."""
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tested_images',PROJECT/'scripts/release-tested-images.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ReleaseSecurity(unittest.TestCase):
    def promoter(self):
        spec=importlib.util.spec_from_file_location('promoter',PROJECT/'scripts/promote-stable-release.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_unpublished_release_cannot_promote_master(self):
        promoter=self.promoter()
        with mock.patch.object(promoter,'api',return_value={'draft':True,'prerelease':False}) as api,mock.patch.object(promoter,'urlopen') as fetch:
            with self.assertRaisesRegex(ValueError,'не опубликован'):promoter.promote('a-baader/wg-dual-easy','0.2.4','a'*40)
            self.assertEqual(api.call_count,1);fetch.assert_not_called()

    def test_published_manifest_and_fast_forward_are_required_before_promotion(self):
        promoter=self.promoter();calls=[]
        def api(repo,path,body=None):
            calls.append((path,body))
            if path.startswith('releases/'):return {'draft':False,'prerelease':False,'html_url':'https://github.com/a-baader/wg-dual-easy/releases/tag/v0.2.4'}
            if path=='git/ref/heads/master':return {'object':{'sha':'b'*40}}
            return {}
        manifest={'version':'0.2.4','source_commit':'a'*40}
        with mock.patch.object(promoter,'api',side_effect=api),mock.patch.object(promoter,'urlopen',return_value=io.BytesIO(json.dumps(manifest).encode())),mock.patch.object(promoter.subprocess,'run') as git:
            self.assertTrue(promoter.promote('a-baader/wg-dual-easy','0.2.4','a'*40)['promoted'])
        self.assertEqual(calls[-2][0],'statuses/'+'a'*40)
        self.assertEqual(calls[-1],('git/refs/heads/master',{'sha':'a'*40,'force':False}))
        self.assertIn('--is-ancestor',git.call_args.args[0])
    def test_archive_commit_and_checksum_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'images.tar').write_bytes('Тестовый архив'.encode())
            record={'source_commit':'a'*40,'images':{'awg':'sha256:'+'1'*64,'wg':'sha256:'+'2'*64},
                    'archive_sha256':hashlib.sha256((root/'images.tar').read_bytes()).hexdigest()}
            (root/'manifest.json').write_text(json.dumps(record))
            module.verify(root,'a'*40)
            with self.assertRaisesRegex(ValueError,'commit'):module.verify(root,'b'*40)
            (root/'images.tar').write_bytes('Подмена'.encode())
            with self.assertRaisesRegex(ValueError,'сумма'):module.verify(root,'a'*40)

    def test_publisher_never_rebuilds_or_clobbers_and_actions_are_pinned(self):
        publisher=(PROJECT/'.github/workflows/docker-publish.yml').read_text()
        self.assertNotIn('docker/build-push-action',publisher)
        self.assertNotIn('--clobber',publisher)
        self.assertIn('--name tested-images',publisher)
        self.assertIn('attest-build-provenance@',publisher)
        for path in (PROJECT/'.github/workflows').glob('*.yml'):
            for reference in re.findall(r'(?m)^\s*(?:-\s*)?uses:\s+([^\s]+)',path.read_text()):
                self.assertRegex(reference,r'^[\w.-]+/[\w.-]+@[a-f0-9]{40}$')


if __name__=='__main__':unittest.main()
