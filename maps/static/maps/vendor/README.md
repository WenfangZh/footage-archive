# Libraries for the case-area map (ADR 0006)

Copied unchanged from the npm registry's tarballs, with no package manager or build step.
Each tarball matched the registry's sha512 integrity value before its files were copied.

| File | Package | Licence |
|---|---|---|
| `maplibre-gl.mjs`, `maplibre-gl-worker.mjs`, `maplibre-gl.css` | maplibre-gl 6.15.0 (`dist/`) | BSD-3-Clause, `maplibre-gl.LICENSE.txt` |
| `pmtiles.js` | pmtiles 4.5.0 (`dist/pmtiles.js`) | BSD-3-Clause, `pmtiles.LICENSE.txt` (from the PMTiles repository; the package has none) |
| `h3-js.umd.js` | h3-js 4.5.0 (`dist/h3-js.umd.js`) | Apache-2.0, `h3-js.LICENSE.txt` |

SHA-256 of the copied files:

```
41198b23679a7d8892183408bbef2eabdc74745beb90261f6be0f660b7aeb2fd  maplibre-gl.mjs
1c69066368331aae63967ed24a9affcf962cf55fcc6d8f532a8b040f736dbcfa  maplibre-gl-worker.mjs
a90798a1953017790e590f7e1bd35ff103a8a4263972cb47290b78adf86f8c20  maplibre-gl.css
caf981bc46f6327ee7e65d5dc964d89d38a69f60edca2bd4c5c890c21b554c6c  pmtiles.js
4180c39bcf5e6bb8f1f236d8a245e51416a0109a3195a475f442ef0a290ca08a  h3-js.umd.js
```

MapLibre finds its worker next to `maplibre-gl.mjs`, so the two stay in one folder.
To upgrade, copy the new versions' files the same way, update this table and the hashes,
and run `tests/test_map_page.py`: it lists every URL these files contain, so a new one
shows up there before it can reach a lawyer's browser.
