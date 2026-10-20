"""Where the map's data lives (M4.5, ADR 0006): one PMTiles file and the fonts, in settings.MAP_DIR.

Both are downloaded by `python manage.py fetch_map_data` and kept out of git. The style is
small and lives in the code, beside this file.
"""

from pathlib import Path

from django.conf import settings

STYLE_FILE = Path(__file__).with_name('style.json')
# Where every route for the map's data starts (accounts.urls), as maps.apps drops their log records.
URL_PREFIX = '/lawyer/map/'
# The fonts the style names: no other is downloaded or served.
FONTS = ('Noto Sans Regular', 'Noto Sans Medium', 'Noto Sans Italic')


def tiles_file():
    return settings.MAP_DIR / 'tiles.pmtiles'


def fonts_dir():
    return settings.MAP_DIR / 'fonts'
