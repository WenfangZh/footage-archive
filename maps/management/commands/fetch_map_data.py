"""Download the map's data into settings.MAP_DIR (M4.5, ADR 0006): tiles and fonts.

The tiles are an extract of the Protomaps basemap (OpenStreetMap data, ODbL) for
Île-de-France, made by the `pmtiles` command-line tool (README) from one of Protomaps'
daily builds; the fonts are Noto Sans glyphs (SIL Open Font Licence) from a fixed commit of
Protomaps' basemaps-assets. Whatever is already there is kept, unless --replace-tiles asks
for newer tiles; a run cut short leaves only a temporary file, which the next run replaces.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from maps import data

BUILDS_URL = 'https://build-metadata.protomaps.dev/builds.json'
BUILD_URL = 'https://build.protomaps.com/{}.pmtiles'
# Île-de-France, as west, south, east, north. Zoom 15 is enough to pick 50 m cells: the
# map enlarges its tiles beyond it.
BOUNDS = '1.45,48.12,3.56,49.25'
MAX_ZOOM = 15
FONTS_COMMIT = '028c18f713baecad011301ff7a69acc39bcc2ae7'
FONTS_URL = f'https://github.com/protomaps/basemaps-assets/archive/{FONTS_COMMIT}.zip'
FONTS_SHA256 = 'e942a417d94a12596842a20b53d6b785cbf6d47f2545e458538191ba6d74b305'
# Protomaps' build index refuses Python's default User-Agent.
HEADERS = {'User-Agent': 'footage-archive fetch_map_data'}
GLYPHS = re.compile(r'[^/]+/fonts/(?P<font>[^/]+)/(?P<range>\d{1,5}-\d{1,5})\.pbf')


class Command(BaseCommand):
    help = "Download the map's tiles and fonts (needs the pmtiles tool for the tiles)."

    def add_arguments(self, parser):
        parser.add_argument('--build', help='The Protomaps build to extract from, as YYYYMMDD (default: the latest).')
        parser.add_argument('--replace-tiles', action='store_true', help='Extract the tiles again, from a newer build.')

    def handle(self, *args, build=None, replace_tiles=False, **options):
        settings.MAP_DIR.mkdir(mode=0o700, exist_ok=True)
        if data.fonts_dir().exists():
            self.stdout.write('Fonts: already there.')
        else:
            self.fetch_fonts()
        if data.tiles_file().exists() and not replace_tiles:
            self.stdout.write('Tiles: already there (--replace-tiles to extract them again).')
        else:
            self.fetch_tiles(build or latest_build())

    def fetch_fonts(self):
        self.stdout.write(f'Fonts: downloading {FONTS_URL}')
        with urllib.request.urlopen(urllib.request.Request(FONTS_URL, headers=HEADERS), timeout=60) as response:
            archive = response.read()
        if hashlib.sha256(archive).hexdigest() != FONTS_SHA256:
            raise CommandError('The fonts archive is not the one expected: nothing was kept.')
        partial = tempfile.mkdtemp(dir=settings.MAP_DIR, prefix='fonts.')
        try:
            kept = 0
            with zipfile.ZipFile(io.BytesIO(archive)) as fonts:
                for name in fonts.namelist():
                    match = GLYPHS.fullmatch(name)
                    if match and match['font'] in data.FONTS:
                        # The path is built from the checked parts only, never from the archive's.
                        target = os.path.join(partial, match['font'], f"{match['range']}.pbf")
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with open(target, 'wb') as glyphs:
                            glyphs.write(fonts.read(name))
                        kept += 1
                    elif name.endswith('/fonts/OFL.txt'):
                        with open(os.path.join(partial, 'OFL.txt'), 'wb') as licence:
                            licence.write(fonts.read(name))
            if kept != 256 * len(data.FONTS):
                raise CommandError(f'Expected {256 * len(data.FONTS)} glyph files, found {kept}: nothing was kept.')
            os.rename(partial, data.fonts_dir())
        finally:
            shutil.rmtree(partial, ignore_errors=True)
        self.stdout.write(self.style.SUCCESS(f'Fonts: {", ".join(data.FONTS)}.'))

    def fetch_tiles(self, build):
        tool = shutil.which('pmtiles')
        if tool is None:
            raise CommandError('The pmtiles tool is not installed: see the README.')
        source = BUILD_URL.format(build)
        partial = data.tiles_file().with_suffix('.partial')
        partial.unlink(missing_ok=True)
        self.stdout.write(f'Tiles: extracting Île-de-France up to zoom {MAX_ZOOM} from {source}')
        subprocess.run(
            [tool, 'extract', source, str(partial), f'--bbox={BOUNDS}', f'--maxzoom={MAX_ZOOM}'], check=True,
        )
        os.replace(partial, data.tiles_file())
        self.stdout.write(self.style.SUCCESS(f'Tiles: {data.tiles_file().stat().st_size // 1_000_000} MB.'))


def latest_build():
    with urllib.request.urlopen(urllib.request.Request(BUILDS_URL, headers=HEADERS), timeout=60) as response:
        builds = json.load(response)
    return max(build['key'] for build in builds).removesuffix('.pmtiles')
