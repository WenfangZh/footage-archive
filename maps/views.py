"""The map's data (M4.5, ADR 0006): its style, tiles and fonts, only for lawyers.

The browser reads the tiles in byte ranges, as the pmtiles library does; these requests are
never logged (maps.apps), and no URL in what is served points to another origin.
"""

import json
import os
import re

from django.http import FileResponse, Http404, JsonResponse
from django.urls import reverse

from accounts.roles import lawyer_required
from clips.streaming import file_response

from . import data

GLYPH_RANGE = re.compile(r'\d{1,5}-\d{1,5}')


@lawyer_required
def style(request):
    """The map's style, pointing to the tiles and fonts on this archive's own origin."""
    style = json.loads(data.STYLE_FILE.read_text())
    style['sources']['protomaps']['url'] = 'pmtiles://' + request.build_absolute_uri(reverse('lawyer:map_tiles'))
    glyphs = reverse('lawyer:map_glyphs', kwargs={'fontstack': 'FONTSTACK', 'glyph_range': 'RANGE'})
    style['glyphs'] = request.build_absolute_uri(glyphs).replace('FONTSTACK', '{fontstack}').replace('RANGE', '{range}')
    return JsonResponse(style)


@lawyer_required
def tiles(request):
    try:
        file = open(data.tiles_file(), 'rb')
    except FileNotFoundError:
        raise Http404('No map data: run fetch_map_data.') from None
    return file_response(request, file, os.fstat(file.fileno()).st_size, 'application/octet-stream')


@lawyer_required
def glyphs(request, fontstack, glyph_range):
    """One range of a font's glyphs, which the map asks for as it draws place names."""
    if fontstack not in data.FONTS or not GLYPH_RANGE.fullmatch(glyph_range):
        raise Http404
    try:
        file = open(data.fonts_dir() / fontstack / f'{glyph_range}.pbf', 'rb')
    except FileNotFoundError:
        raise Http404 from None
    return FileResponse(file, content_type='application/x-protobuf')
