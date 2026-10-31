"""The map's data (M4.5, M6.5, ADR 0006): its style, tiles and fonts, for lawyers, and for
uploaders and code holders who hold a map pass (maps.passes).

The browser reads the tiles in byte ranges, as the pmtiles library does; these requests are
never logged (maps.apps), and no URL in what is served points to another origin. The style
points to the same routes the page loaded it from: a lawyer's, or a pass's.
"""

import functools
import json
import os
import re

from django.http import FileResponse, Http404, JsonResponse
from django.urls import reverse

from accounts.roles import lawyer_required
from clips.streaming import file_response

from . import data, passes

GLYPH_RANGE = re.compile(r'[0-9]{1,5}-[0-9]{1,5}')
# Stand-ins that MapLibre replaces, kept out of reverse(), which would escape the braces.
GLYPH_PLACEHOLDERS = {'fontstack': 'FONTSTACK', 'glyph_range': 'RANGE'}


def pass_required(view):
    """A view for someone who holds a valid map pass: anyone else finds nothing."""
    @functools.wraps(view)
    def checked(request, map_pass, **kwargs):
        if not passes.is_valid(map_pass):
            raise Http404
        return view(request, map_pass=map_pass, **kwargs)
    return checked


@lawyer_required
def style(request):
    return style_response(request, reverse('lawyer:map_tiles'), reverse('lawyer:map_glyphs', kwargs=GLYPH_PLACEHOLDERS))


@pass_required
def style_with_pass(request, map_pass):
    return style_response(
        request,
        reverse('maps:tiles', kwargs={'map_pass': map_pass}),
        reverse('maps:glyphs', kwargs={'map_pass': map_pass, **GLYPH_PLACEHOLDERS}),
    )


def style_response(request, tiles_path, glyphs_path):
    """The map's style, pointing to the tiles and fonts on this archive's own origin."""
    style = json.loads(data.STYLE_FILE.read_text(encoding='utf-8'))
    origin = request.build_absolute_uri('/').removesuffix('/')
    style['sources']['protomaps']['url'] = f'pmtiles://{origin}{tiles_path}'
    style['glyphs'] = origin + glyphs_path.replace('FONTSTACK', '{fontstack}').replace('RANGE', '{range}')
    return JsonResponse(style)


@lawyer_required
def tiles(request):
    return tiles_response(request)


@pass_required
def tiles_with_pass(request, map_pass):
    return tiles_response(request)


def tiles_response(request):
    try:
        file = open(data.tiles_file(), 'rb')
    except FileNotFoundError:
        raise Http404('No map data: run fetch_map_data.') from None
    return file_response(request, file, os.fstat(file.fileno()).st_size, 'application/octet-stream')


@lawyer_required
def glyphs(request, fontstack, glyph_range):
    return glyphs_response(fontstack, glyph_range)


@pass_required
def glyphs_with_pass(request, map_pass, fontstack, glyph_range):
    return glyphs_response(fontstack, glyph_range)


def glyphs_response(fontstack, glyph_range):
    """One range of a font's glyphs, which the map asks for as it draws place names."""
    if fontstack not in data.FONTS or not GLYPH_RANGE.fullmatch(glyph_range):
        raise Http404
    try:
        file = open(data.fonts_dir() / fontstack / f'{glyph_range}.pbf', 'rb')
    except FileNotFoundError:
        raise Http404 from None
    return FileResponse(file, content_type='application/x-protobuf')
