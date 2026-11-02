"""Every third-party piece the repository holds or pins is listed with its licence (SPEC.md §12, M7).

THIRD_PARTY_NOTICES.md is the index; each vendor folder's README lists its own files. These
fail when a file or package is added or upgraded without its entry, so that its licence and
notices are looked at then.
"""

import hashlib
import re

from django.conf import settings

ROOT = settings.BASE_DIR
INDEX = ROOT / 'THIRD_PARTY_NOTICES.md'
# The folders that hold files, at any depth under a static folder's vendor/.
VENDOR_FOLDERS = sorted({path.parent for path in ROOT.glob('*/static/**/vendor/**/*') if path.is_file()})
HASH_LINE = re.compile(r'^([0-9a-f]{64})  (\S+)$', re.MULTILINE)
LINK = re.compile(r'\]\(([^)#]+)\)')
PINNED = re.compile(r'^([A-Za-z0-9._-]+)==(\S+)$', re.MULTILINE)


def test_every_vendor_folder_is_listed_in_the_index():
    assert [folder.relative_to(ROOT).parts[0] for folder in VENDOR_FOLDERS] == ['clips', 'core', 'maps']
    links = {(ROOT / target).resolve() for target in LINK.findall(INDEX.read_text())}
    for folder in VENDOR_FOLDERS:
        assert folder / 'README.md' in links, folder


def test_every_vendored_file_is_listed_in_its_folders_readme():
    for folder in VENDOR_FOLDERS:
        readme = (folder / 'README.md').read_text()
        for path in folder.iterdir():
            if path.name != 'README.md':
                assert f'`{path.name}`' in readme, path
        # Nothing the README names as a file is missing.
        for name in re.findall(r'`([\w.-]+\.(?:txt|js|mjs|css))`', readme):
            assert (folder / name).is_file(), folder / name


def test_every_vendored_script_matches_its_recorded_hash():
    for folder in VENDOR_FOLDERS:
        recorded = {name: digest for digest, name in HASH_LINE.findall((folder / 'README.md').read_text())}
        scripts = {path.name for path in folder.iterdir() if path.suffix in ('.js', '.mjs', '.css')}
        assert set(recorded) == scripts, folder
        for name, digest in recorded.items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest, folder / name


def test_every_file_the_index_links_to_exists():
    for target in LINK.findall(INDEX.read_text()):
        assert (ROOT / target).is_file(), target


def test_every_pinned_python_package_is_listed_at_its_version():
    rows = INDEX.read_text().splitlines()
    for requirements in ('requirements.txt', 'requirements-dev.txt'):
        for name, version in PINNED.findall((ROOT / requirements).read_text()):
            assert any(re.match(rf'\| {re.escape(name)}( \(development\))? \| {re.escape(version)} \|', row) for row in rows), name
