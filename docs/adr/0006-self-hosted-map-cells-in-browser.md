# The case-area map loads nothing from elsewhere and picks cells in the browser

Lawyers can set a case's area on a map (SPEC §7, §12 M4.5). Everything the map loads comes from the archive itself: the map data is an extract of the Protomaps basemap (OpenStreetMap data) in one PMTiles file, read with HTTP byte ranges through a view open only to lawyers, along with its style and fonts; the MapLibre GL JS, pmtiles and h3-js libraries are copied into the repository. The browser turns the lawyer's clicks into grid cells with h3-js, and only cell ids reach the server, which checks them as it checks a typed area. A tile server run by someone else would learn, with the lawyer's IP address, where they are looking, and the place of a case is part of the defence's strategy (SPEC §3). Finding cells on the server would mean sending it the click, a position finer than the grid. Moving to a hosted tile service later would look like a small change and quietly undo both, which is why this is recorded.

## Considered Options

- **A hosted tile service** (OpenFreeMap, MapTiler, OpenStreetMap's own tile servers): nothing to download or serve, but every tile request tells a third party where a lawyer is looking.
- **A tile server of our own** (tileserver-gl or Martin, over MBTiles): one more service to run, for nothing a single PMTiles file served by Django lacks.
- **Leaflet with pre-drawn raster tiles**: simpler JavaScript and no WebGL, but the tiles for the same area are many times larger, and their style and language are fixed when they are drawn.
- **Cells found on the server**: the browser would have to send the click or the map's view, which is finer than the grid.

## Consequences

- The map data does not update itself: it is downloaded by a management command, out of git. Outside the extract (Île-de-France for now) the map shows no streets; the typed place and radius work anywhere.
- The map data is an OpenStreetMap Produced Work under the ODbL: "© OpenStreetMap contributors" stays visible on the map.
- Each request for map data still tells the archive's own server roughly where the lawyer is looking (a tile is under a kilometre across in Paris at the finest zoom kept), though not for which case. The archive never logs these requests; a deployment's web server must not log them either.
- MapLibre GL JS 6 needs WebGL2. On a device without it, the typed place and radius still work.
