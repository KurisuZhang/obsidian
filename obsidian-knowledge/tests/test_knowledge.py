"""End-to-end file tests use a temporary Vault only; no model or network calls."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/knowledge.py'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='knowledge-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.draft = self.root / 'draft.md'
        self.draft.write_text('# 测试\n\n```bash\nprintf hello\n```\n', encoding='utf-8')
        self.target = '02.操作系统/测试.md'

    def cli(self, *args, fail=False, input_text=None):
        result = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault), *map(str, args)],
                                capture_output=True, text=True, input=input_text)
        self.assertEqual(result.returncode != 0, fail, result.stdout + result.stderr)
        return json.loads(result.stderr if fail else result.stdout)

    def options(self, expected='missing'):
        return ['--draft', self.draft, '--target', self.target, '--expected', expected]

    def test_official_categories_and_reserved_directories(self):
        categories = (
            '01.实验记录', '02.操作系统', '03.k8s', '04.模型训练',
            '05.模型推理', '06.AI-infra实操', '07.CATL', '08.其他',
        )
        for folder in categories:
            with self.subTest(folder=folder):
                self.target = f'{folder}/验收.md'
                result = self.create()
                self.assertEqual(Path(result['path']).read_bytes(), self.draft.read_bytes())
        for folder in ('00.草稿箱', '99.垃圾箱', 'assets', '01.实验操作', '02.主题分类', '03.概念知识'):
            with self.subTest(folder=folder):
                self.target = f'{folder}/禁止入库.md'
                self.cli('preview', *self.options(), fail=True)
                self.cli('save', *self.options(), '--approval', 'invalid', fail=True)
                self.assertFalse((self.vault / self.target).exists())

    def create(self):
        preview = self.cli('preview', *self.options())
        return self.cli('save', *self.options(), '--approval', preview['approval'])

    def test_stdin_writes_only_draft_box_without_markdown_staging(self):
        self.draft.unlink()
        body = '# 中文草稿\n\n```bash\necho "$HOME"\nprintf \'$(date) `pwd`\'\n```\n'
        saved = self.cli('draft', '--name', '直接保存.md', input_text=body)
        path = Path(saved['path'])
        self.assertEqual(path.read_text(), body)
        self.assertEqual(path.parent, (self.vault / '00.草稿箱').resolve())
        self.assertEqual([p.resolve() for p in self.root.rglob('*.md')], [path])
        self.assertNotIn(body, json.dumps(saved, ensure_ascii=False))
        repeated = self.cli('draft', '--input', '-', '--name', '直接保存.md', input_text=body)
        self.assertEqual(saved['path'], repeated['path'])

    def test_draft_is_persistent_without_publishing_or_approval(self):
        body = self.draft.read_bytes()
        saved = self.cli('draft', '--input', self.draft, '--name', '会话整理.md')
        self.draft.unlink()
        self.assertEqual(Path(saved['path']).read_bytes(), body)
        self.assertEqual(saved['status'], 'draft')
        self.assertEqual(Path(saved['path']).parent, (self.vault / '00.草稿箱').resolve())
        self.assertFalse((self.vault / self.target).exists())
        self.assertFalse((self.vault / 'assets').exists())

    def test_draft_reuse_and_revision_preserve_previous_content(self):
        opts = ['--input', self.draft, '--name', '会话整理.md']
        first = self.cli('draft', *opts)
        self.assertEqual(self.cli('draft', *opts)['path'], first['path'])
        old = Path(first['path']).read_bytes()
        self.draft.write_text('# 修订正文\n')
        revised = self.cli('draft', *opts)
        self.assertNotEqual(revised['path'], first['path'])
        self.assertEqual(Path(first['path']).read_bytes(), old)
        self.assertEqual(Path(revised['path']).read_bytes(), self.draft.read_bytes())

    def test_draft_retains_media_and_reports_missing_images(self):
        media = self.root / 'media'
        media.mkdir()
        (media / 'figure.png').write_bytes(PNG)
        self.draft.write_text('# 图示\n![[assets/figure.png]]\n![[assets/missing.png|缺失]]\n')
        result = self.cli('draft', '--input', self.draft, '--name', '图示.md', '--media', media)
        shutil.rmtree(media)
        self.assertEqual((self.vault / 'assets/figure.png').read_bytes(), PNG)
        self.assertEqual(result['missing_images'], ['assets/missing.png'])
        self.assertTrue(Path(result['path']).is_file())
        self.cli('preview', *self.options(), fail=True)

    def test_draft_rejects_escape_and_conflicting_media(self):
        for name in ('../escape.md', '01.实验记录/file.md', '/tmp/file.md', 'note.txt'):
            self.cli('draft', '--input', self.draft, '--name', name, fail=True)
        media = self.root / 'media'
        media.mkdir()
        (media / 'existing.png').write_bytes(PNG)
        (self.vault / 'assets').mkdir()
        old = self.vault / 'assets/existing.png'
        old.write_bytes(b'user image')
        self.cli('draft', '--input', self.draft, '--name', 'note.md', '--media', media, fail=True)
        self.assertEqual(old.read_bytes(), b'user image')
        self.assertEqual((media / 'existing.png').read_bytes(), PNG)
        self.assertFalse((self.vault / '00.草稿箱/note.md').exists())

    def test_persistent_draft_can_be_published_only_with_matching_preview(self):
        draft = self.cli('draft', '--input', self.draft, '--name', '知识.md')
        self.draft = Path(draft['path'])
        preview = self.cli('preview', *self.options())
        self.assertFalse((self.vault / self.target).exists())
        self.cli('save', *self.options(), '--approval', 'not-approved', fail=True)
        self.cli('save', *self.options(), '--approval', preview['approval'])
        self.assertEqual((self.vault / self.target).read_bytes(), self.draft.read_bytes())
        self.assertTrue(self.draft.exists())

    def test_preview_does_not_write_and_create_matches_exact_bytes(self):
        result = self.cli('status', '--target', self.target)
        self.assertEqual(result['expected'], 'missing')
        self.cli('preview', *self.options())
        self.assertEqual(list(self.vault.iterdir()), [])
        saved = self.create()
        self.assertEqual(Path(saved['path']).read_bytes(), self.draft.read_bytes())
        self.assertFalse((self.vault / 'assets').exists())

    def test_missing_or_invalid_approval_does_not_write(self):
        self.cli('save', *self.options(), '--approval', 'invalid', fail=True)
        self.assertEqual(list(self.vault.iterdir()), [])
        result = subprocess.run([sys.executable, str(SCRIPT), 'save', *map(str, self.options())],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_changed_draft_or_destination_invalidates_preview(self):
        preview = self.cli('preview', *self.options())
        self.draft.write_text('# 修改过的正文\n')
        self.cli('save', *self.options(), '--approval', preview['approval'], fail=True)
        self.assertEqual(list(self.vault.iterdir()), [])
        preview = self.cli('preview', *self.options())
        self.target = '02.操作系统/另一个目标.md'
        self.cli('save', *self.options(), '--approval', preview['approval'], fail=True)

    def test_repeated_create_never_overwrites(self):
        self.create()
        self.cli('preview', *self.options(), fail=True)
        self.assertEqual((self.vault / self.target).read_bytes(), self.draft.read_bytes())

    def test_update_backup_and_conflict(self):
        self.create()
        old = self.draft.read_bytes()
        expected = hashlib.sha256(old).hexdigest()
        self.draft.write_text('# 新版\n')
        preview = self.cli('preview', *self.options(expected))
        saved = self.cli('save', *self.options(expected), '--approval', preview['approval'])
        self.assertEqual(Path(saved['backup']).read_bytes(), old)
        self.assertEqual(Path(saved['path']).read_bytes(), self.draft.read_bytes())
        expected = saved['body_sha256']
        preview = self.cli('preview', *self.options(expected))
        Path(saved['path']).write_text('来自 Obsidian 的修改')
        self.cli('save', *self.options(expected), '--approval', preview['approval'], fail=True)
        self.assertEqual(Path(saved['path']).read_text(), '来自 Obsidian 的修改')

    def test_path_escape_and_symlink_rejected(self):
        for target in ('../outside.md', '/tmp/outside.md', '00.草稿箱/file.md'):
            self.cli('status', '--target', target, fail=True)
        (self.vault / '02.操作系统').symlink_to(self.root, target_is_directory=True)
        self.cli('status', '--target', self.target, fail=True)

    def test_source_and_media_archived_once_and_bound_to_approval(self):
        source = self.root / '原文.txt'
        source.write_text('原文不能丢失')
        media = self.root / 'media'
        media.mkdir()
        image = media / 'example.png'
        image.write_bytes(PNG)
        opts = [*self.options(), '--source', source, '--media', media]
        preview = self.cli('preview', *opts)
        image.write_bytes(b'changed')
        self.cli('save', *opts, '--approval', preview['approval'], fail=True)
        self.assertEqual(list(self.vault.iterdir()), [])
        image.write_bytes(PNG)
        self.cli('save', *opts, '--approval', preview['approval'])
        raw = list((self.vault / '00.草稿箱/原始资料').glob('*.txt'))
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0].read_bytes(), source.read_bytes())
        self.assertEqual((self.vault / 'assets/example.png').read_bytes(), PNG)
        self.target = '02.操作系统/第二篇.md'
        opts = [*self.options(), '--source', source, '--media', media]
        preview = self.cli('preview', *opts)
        self.cli('save', *opts, '--approval', preview['approval'])
        self.assertEqual(len(list(raw[0].parent.glob('*.txt'))), 1)

    def test_changed_source_and_attachment_collision(self):
        source = self.root / '原文.txt'
        source.write_text('v1')
        opts = [*self.options(), '--source', source]
        preview = self.cli('preview', *opts)
        source.write_text('v2')
        self.cli('save', *opts, '--approval', preview['approval'], fail=True)
        self.assertEqual(list(self.vault.iterdir()), [])
        media = self.root / 'media'
        media.mkdir()
        (media / 'x.png').write_bytes(PNG)
        (self.vault / 'assets').mkdir()
        (self.vault / 'assets/x.png').write_bytes(b'existing')
        self.cli('preview', *self.options(), '--media', media, fail=True)
        self.assertEqual((self.vault / 'assets/x.png').read_bytes(), b'existing')

    def test_md_extraction_preserves_code_and_refuses_reuse(self):
        output = self.root / 'extracted'
        result = self.cli('extract', '--source', self.draft, '--output', output)
        self.assertEqual(Path(result['content']).read_bytes(), self.draft.read_bytes())
        self.cli('extract', '--source', self.draft, '--output', output, fail=True)
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_missing_image_stops_preview(self):
        self.draft.write_text('# 测试\n![[assets/missing.png]]\n')
        self.cli('preview', *self.options(), fail=True)
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_pdf_text_image_table_and_scan_boundary(self):
        import pymupdf
        source = self.root / 'sample.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 40), 'Condition: Linux only. Command: python --version')
            page.insert_image(pymupdf.Rect(40, 60, 70, 90), stream=PNG)
            for y in (120, 150, 180):
                page.draw_line((40, y), (240, y))
            for x in (40, 140, 240):
                page.draw_line((x, 120), (x, 180))
            for pos, text in [((50, 140), 'Key'), ((150, 140), 'Value'),
                              ((50, 170), 'batch'), ((150, 170), '16')]:
                page.insert_text(pos, text)
            doc.save(source)
        result = self.cli('extract', '--source', source, '--output', self.root / 'pdf-extracted')
        text = Path(result['content']).read_text()
        self.assertIn('Linux only', text)
        self.assertIn('原文第 1 页', text)
        self.assertIn('表格 1', text)
        self.assertIn('batch', text)
        self.assertTrue(list(Path(result['media']).iterdir()))
        scan = self.root / 'scan.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_image(pymupdf.Rect(0, 0, 100, 100), stream=PNG)
            doc.save(scan)
        error = self.cli('extract', '--source', scan, '--output', self.root / 'scan-out', fail=True)
        self.assertIn('全文无可提取文本', error['error'])

    def test_docx_to_preview_and_save_with_image_and_table(self):
        pandoc = shutil.which('pandoc') or '/opt/homebrew/bin/pandoc'
        image = self.root / 'image.png'
        image.write_bytes(PNG)
        md = self.root / 'input.md'
        md.write_text('# 条件\n\nLinux only.\n\n```python\nprint(42)\n```\n\n'
                      '| 参数 | 值 |\n|---|---|\n| batch | 16 |\n\n![图](image.png)\n')
        source = self.root / 'input.docx'
        subprocess.run([pandoc, str(md), '-o', str(source)], cwd=self.root, check=True)
        extracted = self.cli('extract', '--source', source, '--output', self.root / 'docx-out')
        text = Path(extracted['content']).read_text()
        for value in ('条件', 'Linux only', 'print(42)', 'batch', '![[assets/'):
            self.assertIn(value, text)
        self.draft.write_text(text)
        opts = [*self.options(), '--source', source, '--extracted', extracted['content'], '--media', extracted['media']]
        preview = self.cli('preview', *opts)
        self.assertEqual(list(self.vault.iterdir()), [])
        result = self.cli('save', *opts, '--approval', preview['approval'])
        self.assertEqual(Path(result['path']).read_text(), text)
        self.assertEqual(len(list((self.vault / '00.草稿箱/原始资料').iterdir())), 2)
        self.assertTrue(list((self.vault / 'assets').iterdir()))


    def test_skill_docs_match_cli(self):
        """Every command and flag shown in SKILL.md/files.md must exist in argparse."""
        skill = SCRIPT.parents[1]
        docs = (skill / 'SKILL.md').read_text(encoding='utf-8') + '\n' + \
            (skill / 'references/files.md').read_text(encoding='utf-8')
        blocks = ''.join(re.findall(r'```bash\n(.*?)```', docs, re.S))
        documented_commands = set(re.findall(r'<script>\s+([a-z][a-z-]*)', blocks))
        documented_flags = set(re.findall(r'(--[a-z][a-z-]*)', blocks))
        self.assertTrue(documented_commands, '文档缺少命令示例')
        top = subprocess.run([sys.executable, str(SCRIPT), '--help'], capture_output=True, text=True)
        choices = set(re.findall(r'\{([a-z,\-]+)\}', top.stdout)[0].split(','))
        self.assertEqual(documented_commands - choices, set(), '文档写了不存在的子命令')
        accepted = {'--vault'}
        for command in choices:
            help_text = subprocess.run([sys.executable, str(SCRIPT), command, '--help'],
                                       capture_output=True, text=True).stdout
            accepted |= set(re.findall(r'(--[a-z][a-z-]*)', help_text))
        self.assertEqual(documented_flags - accepted, set(), '文档写了不存在的参数')


def load_cli_module():
    spec = importlib.util.spec_from_file_location('knowledge_cli', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VaultResolutionTests(unittest.TestCase):
    """Vault 解析与目录位置无关：--vault → OBSIDIAN_VAULT → 内置默认值。"""

    def setUp(self):
        self.cli_module = load_cli_module()

    def test_default_vault_is_absolute_and_exists(self):
        vault = self.cli_module.resolve_vault(None, {})
        self.assertTrue(vault.is_absolute())
        self.assertTrue(vault.is_dir())
        self.assertEqual(vault, Path(self.cli_module.DEFAULT_VAULT).resolve())

    def test_env_then_explicit_vault_wins_and_missing_reports_path(self):
        with tempfile.TemporaryDirectory(prefix='knowledge-vault-') as temp:
            root = Path(temp)
            env_vault, explicit_vault = root / 'env-vault', root / 'explicit-vault'
            env_vault.mkdir()
            explicit_vault.mkdir()
            self.assertEqual(self.cli_module.resolve_vault(None, {'OBSIDIAN_VAULT': str(env_vault)}),
                             env_vault.resolve())
            self.assertEqual(self.cli_module.resolve_vault(explicit_vault, {'OBSIDIAN_VAULT': str(env_vault)}),
                             explicit_vault.resolve())
            missing = root / 'missing-vault'
            with self.assertRaises(ValueError) as caught:
                self.cli_module.resolve_vault(missing, {})
            self.assertIn(str(missing), str(caught.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
