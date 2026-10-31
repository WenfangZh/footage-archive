"""The map's data for uploaders and code holders, behind a map pass (maps.passes). Never logged
(maps.apps): keep every route of it under the prefix in maps.data."""

from django.urls import path

from . import views

app_name = 'maps'

urlpatterns = [
    path('<str:map_pass>/style.json', views.style_with_pass, name='style'),
    path('<str:map_pass>/tiles.pmtiles', views.tiles_with_pass, name='tiles'),
    path('<str:map_pass>/fonts/<str:fontstack>/<str:glyph_range>.pbf', views.glyphs_with_pass, name='glyphs'),
]
