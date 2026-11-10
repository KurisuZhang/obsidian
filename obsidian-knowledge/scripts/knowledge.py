#!/usr/bin/env python3
"""Small file helper. Approval is enforced by the Skill, not by this CLI."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

DEFAULT_VAULT = '/Users/lin/Desktop/obsidian'
NOTES = (
    '01.实验记录',
    '02.操作系统',
    '03.k8s',
    '04.模型训练',
    '05.模型推理',
    '06.AI-infra实操',
    '07.CATL',
    '08.其他',
)
RAW = Path('00.草稿箱/原始资料')


def resolve_vault(explicit=None, environ=None):
    environ = os.environ if environ is None else environ
    raw = explicit or environ.get('OBSIDIAN_VAULT') or DEFAULT_VAULT
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise ValueError(f'Vault 不存在：{path}')
    return path.resolve()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def contained(vault, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('目标必须是 Vault 内的相对路径')
    path = vault / relative
    if not path.resolve().is_relative_to(vault.resolve()):
        raise ValueError('目标不能通过符号链接指向 Vault 外')
    if path.is_symlink():
        raise ValueError('不写入符号链接文件')
    return path


def target_path(vault, relative):
    path = contained(vault, relative)
    if Path(relative).parts[0] not in NOTES or path.suffix.lower() != '.md':
        raise ValueError('正式笔记必须位于八个知识目录内，且为 .md 文件')
    return path


def state(path):
    return digest(path.read_bytes()) if path.exists() else 'missing'


def source_relative(source, data=None):
    data = source.read_bytes() if data is None else data
    return RAW / f'{source.stem}-{digest(data)[:12]}{source.suffix}'


def write_new_or_same(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as f:
            f.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f'已有不同内容，未覆盖：{path}')


def save_draft(args):
    """Persist a review draft without authorizing publication or overwriting files."""
    name = Path(args.name)
    if name.name != args.name or name.suffix.lower() != '.md':
        raise ValueError('草稿名称必须是单个 .md 文件名，不含目录')
    body = sys.stdin.buffer.read() if str(args.input) == '-' else args.input.read_bytes()
    text = body.decode('utf-8')
    if not text.strip():
        raise ValueError('草稿正文为空')
    target = contained(args.vault, Path('00.草稿箱') / name)
    if target.exists() and target.read_bytes() != body:
        target = contained(args.vault, Path('00.草稿箱') / f'{name.stem}-{digest(body)[:12]}.md')
    copies = {}
    if args.media:
        if not args.media.is_dir():
            raise ValueError('media 目录不存在')
        for file in sorted(args.media.iterdir()):
            if file.is_symlink() or not file.is_file():
                raise ValueError('media 只接受平铺的普通文件')
            dest = contained(args.vault, Path('assets') / file.name)
            data = file.read_bytes()
            if dest.exists() and dest.read_bytes() != data:
                raise ValueError(f'图片同名且内容不同，请先改名和修正草稿引用：{file.name}')
            copies[dest] = data
    for dest, data in copies.items():
        write_new_or_same(dest, data)
    write_new_or_same(target, body)
    if target.read_bytes() != body:
        raise ValueError('草稿保存后校验失败')
    missing = [relative for relative in re.findall(r'!\[\[(assets/[^\]|]+)(?:\|[^\]]*)?\]\]', text)
               if not contained(args.vault, relative).is_file()]
    return {'path': str(target.resolve()), 'status': 'draft', 'body_sha256': digest(body),
            'media': [str(p.resolve()) for p in copies], 'missing_images': missing}


def proposal(args):
    vault = args.vault.resolve()
    target = target_path(vault, args.target)
    if state(target) != args.expected:
        raise ValueError('目标已变化或新建路径已存在，请重新预览确认')
    body = args.draft.read_bytes()
    if not body.decode('utf-8').strip():
        raise ValueError('正文为空')
    copies = {}
    if args.source:
        source = args.source.resolve(strict=True)
        original = source.read_bytes()
        relative = source_relative(source, original)
        copies[str(relative)] = original
        if args.extracted:
            copies[str(RAW / f'{relative.stem}.extracted.md')] = args.extracted.read_bytes()
    elif args.extracted:
        raise ValueError('--extracted 必须与 --source 一起使用')
    if args.media:
        if not args.media.is_dir():
            raise ValueError('media 目录不存在')
        for file in sorted(args.media.iterdir()):
            if file.is_symlink() or not file.is_file():
                raise ValueError('media 只接受平铺的普通文件')
            copies[str(Path('assets') / file.name)] = file.read_bytes()
    for relative, data in copies.items():
        dest = contained(vault, relative)
        if dest.exists() and dest.read_bytes() != data:
            raise ValueError(f'附属文件已存在且内容不同：{relative}')
    for relative in re.findall(r'!\[\[(assets/[^\]|]+)(?:\|[^\]]*)?\]\]', body.decode('utf-8')):
        if relative not in copies and not contained(vault, relative).is_file():
            raise ValueError(f'正文引用的图片不存在：{relative}')
    summary = {
        'target': args.target,
        'operation': 'create' if args.expected == 'missing' else 'update',
        'expected': args.expected,
        'body_sha256': digest(body),
        'files': {relative: digest(data) for relative, data in sorted(copies.items())},
    }
    # Bind all bytes and destinations; this detects stale previews, not consent.
    summary['approval'] = digest(json.dumps(summary, sort_keys=True, ensure_ascii=False).encode())
    return target, body, copies, summary


def save(args):
    target, body, copies, summary = proposal(args)
    if args.approval != summary['approval']:
        raise ValueError('预览内容或保存范围已变化，请重新确认；未写入')
    completed = []
    backup = None
    try:
        for relative, data in copies.items():
            dest = contained(args.vault, relative)
            write_new_or_same(dest, data)
            completed.append(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.expected == 'missing':
            # Exclusive creation prevents clobbering an existing note.
            with target.open('xb') as f:
                f.write(body)
        else:
            old = target.read_bytes()
            if digest(old) != args.expected:
                raise ValueError('保存前旧笔记发生变化，未覆盖')
            backup = contained(args.vault, RAW / '备份' / f'{target.stem}-{args.expected[:12]}.md')
            write_new_or_same(backup, old)
            fd, temp = tempfile.mkstemp(prefix='.knowledge-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                if state(target) != args.expected:
                    raise ValueError('保存前旧笔记发生变化，未覆盖')
                os.replace(temp, target)
            finally:
                Path(temp).unlink(missing_ok=True)
        completed.append(args.target)
        if target.read_bytes() != body:
            raise ValueError('保存后的正文与批准版本不一致')
    except Exception as exc:
        raise RuntimeError(f'{exc}；已完成文件：{completed}；备份：{backup}') from exc
    return {'path': str(target), 'operation': summary['operation'],
            'body_sha256': summary['body_sha256'], 'files': completed,
            'backup': str(backup) if backup else None}


def media_file(directory, data, extension):
    name = f'{digest(data)[:20]}.{extension.lower()}'
    write_new_or_same(directory / name, data)
    return f'assets/{name}'


def extract_pdf(source, output):
    try:
        import pymupdf
    except ImportError as exc:
        raise ValueError('PDF 需要本 Skill 的 .venv Python 和 requirements.txt 中的依赖') from exc
    parts, warnings = [], ['PDF 按页抽取；需核对阅读顺序、代码缩进、公式与矢量图。表格可能与页内文本重复。']
    has_text = False
    with pymupdf.open(source) as doc:
        if doc.needs_pass:
            raise ValueError('PDF 已加密，请提供可读取版本')
        for number, page in enumerate(doc, 1):
            text = page.get_text('text', sort=True)
            parts.extend([f'## 原文第 {number} 页', text])
            if text.strip():
                has_text = True
            else:
                warnings.append(f'第 {number} 页无可提取文本，需检查空白页或扫描件；未 OCR')
            for item in page.get_images(full=True):
                image = doc.extract_image(item[0])
                if not image:
                    warnings.append(f'第 {number} 页图像 {item[0]} 未提取')
                    continue
                link = media_file(output / 'media', image['image'], image['ext'])
                parts.append(f'![[{link}|第 {number} 页图像]]')
            try:
                tables = page.find_tables().tables
                for index, table in enumerate(tables, 1):
                    parts.extend([f'### 第 {number} 页表格 {index}（核对用）', table.to_markdown()])
            except Exception as exc:
                warnings.append(f'第 {number} 页表格识别失败：{exc}')
    if not has_text:
        raise ValueError('全文无可提取文本，未生成可入库正文；请提供文本版或另行处理 OCR')
    return '\n\n'.join(parts), warnings


def extract_docx(source, output):
    pandoc = shutil.which('pandoc')
    if not pandoc and Path('/opt/homebrew/bin/pandoc').is_file():
        pandoc = '/opt/homebrew/bin/pandoc'
    if not pandoc:
        raise ValueError('DOCX 转换需要 Pandoc；当前未安装')
    # A private extraction dir keeps Pandoc media paths relative and predictable.
    with tempfile.TemporaryDirectory(prefix='knowledge-docx-') as tmp:
        result = subprocess.run([pandoc, str(source), '--from=docx',
                                 '--to=markdown', '--wrap=none', '--extract-media=media'],
                                cwd=tmp, capture_output=True, text=True, check=True, timeout=120)
        text = result.stdout
        for file in sorted(Path(tmp).rglob('*')):
            if file.is_file():
                relative = file.relative_to(tmp).as_posix()
                link = media_file(output / 'media', file.read_bytes(), file.suffix.lstrip('.'))
                pattern = r'!\[([^\]]*)\]\(' + re.escape(relative) + r'\)(?:\{[^}]*\})?'
                text = re.sub(pattern, lambda m: f'![[{link}]]', text)
                # Preserve unusual Pandoc image forms for explicit review.
                text = text.replace(relative, link)
    warnings = ['DOCX 转换后需核对复杂表格、修订、公式及代码格式。']
    if result.stderr.strip():
        warnings.append(result.stderr.strip())
    return text, warnings


def extract(args):
    source = args.source.resolve(strict=True)
    suffix = source.suffix.lower()
    if suffix not in ('.md', '.txt', '.pdf', '.docx'):
        raise ValueError('支持 .md、.txt、.pdf、.docx；其他格式需另行确认')
    source_hash = state(source)
    args.output.mkdir(parents=True, exist_ok=False)
    if suffix == '.pdf':
        diagnostics = io.StringIO()
        with redirect_stdout(diagnostics):
            text, warnings = extract_pdf(source, args.output)
        if diagnostics.getvalue().strip():
            warnings.append(diagnostics.getvalue().strip())
    elif suffix == '.docx':
        text, warnings = extract_docx(source, args.output)
    else:
        text, warnings = source.read_text(encoding='utf-8-sig'), []
    if state(source) != source_hash:
        raise ValueError('提取过程中原件发生变化，请使用稳定原件重新提取')
    content = args.output / 'content.md'
    content.write_text(text, encoding='utf-8')
    media = args.output / 'media'
    return {'content': str(content.resolve()), 'media': str(media.resolve()) if media.exists() else None,
            'source_sha256': source_hash, 'archive': str(source_relative(source)), 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, default=None,
                        help='Vault 根目录；默认取 OBSIDIAN_VAULT，否则 /Users/lin/Desktop/obsidian')
    sub = parser.add_subparsers(dest='command', required=True)
    status = sub.add_parser('status', help='只读：预览前读取目标版本')
    status.add_argument('--target', required=True)
    source = sub.add_parser('source-path', help='只读：获取原件归档路径')
    source.add_argument('--source', type=Path, required=True)
    extraction = sub.add_parser('extract', help='提取到指定的新目录，通常是 00.草稿箱/原始资料/<资料名>-抽取')
    extraction.add_argument('--source', type=Path, required=True)
    extraction.add_argument('--output', type=Path, required=True)
    draft = sub.add_parser('draft', help='自动保存待审正文到草稿箱，不正式入库')
    draft.add_argument('--input', type=Path, default=Path('-'), help='默认从标准输入直接写草稿；也可读取已有文件')
    draft.add_argument('--name', required=True, help='草稿文件名，例如 PD 分离架构.md')
    draft.add_argument('--media', type=Path, help='按需持久保存草稿图片，不覆盖或删除已有文件')
    for command in ('preview', 'save'):
        p = sub.add_parser(command)
        p.add_argument('--draft', type=Path, required=True)
        p.add_argument('--target', required=True)
        p.add_argument('--expected', required=True, help='status 返回的 missing 或 SHA-256')
        p.add_argument('--source', type=Path)
        p.add_argument('--extracted', type=Path)
        p.add_argument('--media', type=Path)
        if command == 'save':
            p.add_argument('--approval', required=True)
    args = parser.parse_args()
    try:
        args.vault = resolve_vault(args.vault)
        if args.command == 'status':
            result = {'target': args.target, 'expected': state(target_path(args.vault, args.target))}
        elif args.command == 'source-path':
            result = {'archive': str(source_relative(args.source)), 'sha256': state(args.source)}
        elif args.command == 'extract':
            result = extract(args)
        elif args.command == 'draft':
            result = save_draft(args)
        elif args.command == 'preview':
            result = proposal(args)[3]
        else:
            result = save(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
