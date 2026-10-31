# The case-area map loads nothing from elsewhere and picks cells in the browser

Lawyers can set a case's area on a map (SPEC §7, §12 M4.5). Everything the map loads comes from the archive itself: the map data is an extract of the Protomaps basemap (OpenStreetMap data) in one PMTiles file, read with HTTP byte ranges through a view open only to lawyers, along with its style and fonts; the MapLibre GL JS, pmtiles and h3-js libraries are copied into the repository. The browser turns the lawyer's clicks into grid cells with h3-js, and only cell ids reach the server, which checks them as it checks a typed area. A tile server run by someone else would learn, with the lawyer's IP address, where they are looking, and the place of a case is part of the defence's strategy (SPEC §3). Finding cells on the server would mean sending it the click, a position finer than the grid. Moving to a hosted tile service later would look like a small change and quietly undo both, which is why this is recorded.

## Considered Options

- **A hosted tile service** (OpenFreeMap, MapTiler, OpenStreetMap's own tile servers): nothing to download or serve, but every tile request tells a third party where a lawyer is looking.
- **A tile server of our own** (tileserver-gl or Martin, over MBTiles): one more service to run, for nothing a single PMTiles file served by Django lacks.
- **Leaflet with pre-drawn raster tiles**: simpler JavaScript and no WebGL, but the tiles for the same area are larger, and their style and language are fixed when they are drawn.
- **Cells found on the server**: the browser would have to send the click or the map's view, which is finer than the grid.

## Consequences

- The map data does not update itself: it is downloaded by a management command, out of git. Outside the extract (Île-de-France up to zoom 15 for now) the map is blank, though cells can still be picked there, and the typed place and radius work anywhere.
- The extract is OpenStreetMap data under the ODbL, and the drawn map is a Produced Work: "© OpenStreetMap contributors" stays visible on the map, with "OpenStreetMap" linking to openstreetmap.org/copyright, as OpenStreetMap's attribution guidelines ask, so that the licence can be found. It is the page's only link elsewhere, and it loads nothing unless the lawyer follows it. We don't change the data; if we ever did and offered it beyond the archive, the ODbL's share-alike terms would apply.
- Each request for map data still tells the archive's own server roughly where the lawyer is looking (a tile is under a kilometre across in Paris at zoom 15, the finest kept), though not for which case. The archive never logs these requests, in the audit log or in Django's own request log; a deployment's web server must not log them either.
- MapLibre GL JS 6 needs WebGL2. On a device without it, the typed place and radius still work.

## Addendum (2026-10-31): uploaders and code holders reach the map data with a map pass

From M6.5, the same map lets an uploader or code holder choose a clip's stated location (SPEC §8). They have no account, so the map data is also served, under routes of its own, to whoever holds a **map pass**: a signed time stamp that the upload form hands out for an unconfirmed original, and the code holder's edit page for a valid upload code, each time it is shown. A pass names no upload and is valid for as long as an unconfirmed original is kept; without one, or with an expired or forged one, the map data is refused. Everything above still holds: the map loads nothing from elsewhere, and its requests, pass included, are never logged. Because the pass names no upload, nothing the archive stores or logs ties map requests to a clip. As for lawyers, each request still tells the archive, while it is answered, roughly where its sender is looking, even an uploader who never submits the form.

Unlike a case area, the uploader's click reaches the server, as typed coordinates already do. Only its cell is kept (ADR 0002), and the click shows no more than the file's own GPS or typed coordinates would. Finding the cell in the browser and sending only its id was considered; coordinates have to stay for browsers without JavaScript, so that would be a second way in to validate, for a position the server already receives that way.

Considered for the map data: serving it to anyone (anyone could fetch the whole extract, about 300 MB, again and again), and serving it to anyone within a rate limit per connection. The pass is stronger, but not a cap: one upload, or one upload code, gives a new pass each time its page is opened. Caps on that are an open question (SPEC §13).
