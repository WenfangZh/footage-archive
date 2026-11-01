// The map of a clip's stated location (SPEC.md §8, §12 M6.6; ADR 0006, ADR 0009), on the upload
// form and the code holder's edit page, made first for phones. Loaded only when asked for. The
// usual gestures move and zoom it: one finger drags; pinching, a double tap or the scroll wheel
// zooms. Once zoomed in far enough for a cell to take a fingertip, a faint grid shows the cells,
// and a tap adds or removes one; on a computer, dragging with Shift held down adds every cell it
// crosses, as on the lawyers' map. Only the cells' ids leave the page, in the form's `cells`
// field: never a tap's position or where the map was looking.

const box = document.getElementById('location-map');
const opener = document.getElementById('location-map-open');
const status = document.getElementById('location-map-status');

// A tap waits this long for a second one: two make a double tap, which zooms instead.
const DOUBLE_TAP_MS = 300;
// A drag that jumps farther than this many cells between two mouse events is not filled in.
const LONGEST_FILL = 200;

if (box && opener) {
  const form = box.closest('form');
  const field = form.elements.cells;
  const typed = document.getElementById('typed-place');
  const { latitude, longitude, precision } = form.elements;
  const resolutions = JSON.parse(document.getElementById('stated-resolutions').textContent);
  const finest = Number(box.dataset.resolution);
  const pickingZoom = Number(box.dataset.pickingZoom);
  const ceiling = Number(box.dataset.ceilingMetres);
  const maxCells = Number(box.dataset.maxCells);
  const instead = document.getElementById('typed-place-show');
  let map = null;
  let spanMetres = null;
  let picked = new Set();
  // Once the map data is refused, the status line keeps saying so.
  let expired = false;

  const say = (text) => { status.textContent = text || ''; };

  // What the status line says when nothing has gone wrong: how to pick, then the place's size.
  function describe() {
    if (map === null) return;
    if (expired) {
      say(box.dataset.expired);
    } else if (map.getZoom() < pickingZoom) {
      say(box.dataset.zoomIn);
    } else if (picked.size === 0) {
      say(box.dataset.tap);
    } else {
      const across = Math.round(spanMetres([...picked]));
      const size = box.dataset.size.replace('{cells}', picked.size).replace('{metres}', across);
      // Cells sent back by a refused form may be too many: the server says why, and so does this.
      say(across > ceiling ? `${size} ${box.dataset.tooWide}` : size);
    }
  }

  function changed() {
    field.value = [...picked].join(' ');
    map.getSource('picked')?.setData({
      type: 'FeatureCollection',
      features: [...picked].map((cell) => ({
        type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [h3.cellToBoundary(cell, true)] },
      })),
    });
    describe();
  }

  // Refused, with a word, when it would take the place past its ceiling (clips.forms checks again).
  function add(cell) {
    if (picked.has(cell)) return true;
    if (picked.size >= maxCells || spanMetres([...picked, cell]) > ceiling) return false;
    picked.add(cell);
    return true;
  }

  function cellAt(lngLat) {
    const { lat, lng } = lngLat.wrap(); // On a repeated copy of the world, as on the first.
    return h3.latLngToCell(lat, lng, finest);
  }

  // A place typed before the map was opened, or pre-filled from the file's GPS, becomes its cells,
  // as the server would keep it, and the typed fields give way to the map. Only once the map has
  // loaded, so that a map that fails leaves them as they were; and not a place the form refused
  // (its errors stay in sight), nor one the server would refuse.
  function takeOverTypedPlace() {
    if (typed.querySelector('.errorlist')) return;
    const given = latitude.value.trim() !== '' || longitude.value.trim() !== '';
    const lat = Number(latitude.value);
    const lng = Number(longitude.value);
    if (given) {
      if (!(Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180)) return;
      const hexagon = h3.latLngToCell(lat, lng, resolutions[precision.value] ?? finest);
      picked = new Set(h3.cellToChildren(hexagon, finest));
    }
    latitude.value = '';
    longitude.value = '';
    for (const choice of precision) choice.checked = false;
    typed.hidden = true;
    instead.hidden = false;
  }

  function fit() {
    const corners = [...picked].flatMap((cell) => h3.cellToBoundary(cell, true));
    const lngs = corners.map((corner) => corner[0]);
    const lats = corners.map((corner) => corner[1]);
    map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 40, duration: 0, maxZoom: 18 });
    if (map.getZoom() < pickingZoom) map.setZoom(pickingZoom);
  }

  function start(maplibregl, cellsModule) {
    ({ spanMetres } = cellsModule);
    picked = new Set(field.value.split(/[\s,]+/).filter((cell) => h3.isValidCell(cell) && h3.getResolution(cell) === finest));
    maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile);
    box.hidden = false;
    map = new maplibregl.Map({
      container: box.querySelector('.area-map-canvas'),
      style: box.dataset.styleUrl,
      center: [2.35, 48.86],
      zoom: 11,
      maxZoom: 19,
      // Shift-drag paints instead of zooming to a box; the map stays flat and north up.
      boxZoom: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      // Its own control links to MapLibre's site: the credit below the map is plain text.
      attributionControl: false,
      locale: {
        'Map.Title': box.dataset.mapTitle,
        'NavigationControl.ZoomIn': box.dataset.zoomInLabel,
        'NavigationControl.ZoomOut': box.dataset.zoomOutLabel,
      },
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    map.on('load', () => {
      takeOverTypedPlace();
      field.value = [...picked].join(' ');
      if (picked.size) fit();
      try {
        cellsModule.showGrid(map, finest, pickingZoom);
      } catch (error) {
        console.error(error); // Without the grid, cells can still be picked.
      }
      map.addSource('picked', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'picked-fill', type: 'fill', source: 'picked', paint: { 'fill-color': '#d9480f', 'fill-opacity': 0.35 } });
      map.addLayer({ id: 'picked-outline', type: 'line', source: 'picked', paint: { 'line-color': '#d9480f', 'line-width': 1 } });
      changed();
    });
    map.on('zoomend', describe);
    // The map pass has expired (maps.passes): the style or the tiles are refused, and the place
    // picked so far still goes with the form. A font range the archive lacks is a 404 too, and
    // costs only some labels: not said.
    map.on('error', (event) => {
      const error = event.error || {};
      const styleRefused = error.status === 404 && /\/style\.json$/.test(error.url || '');
      const tilesRefused = /Bad response code: 404/.test(error.message || '');
      if (styleRefused || tilesRefused) {
        expired = true;
        typed.hidden = false; // Coordinates given there replace the cells (clips.forms).
        describe();
      }
    });

    // A tap waits to see whether a second follows: a double tap zooms (MapLibre's own) and picks nothing.
    let waiting = null;
    // A slow double tap, or a double-tap-and-drag zoom, starts zooming before its second tap: the
    // first one picks nothing then either.
    const cancelTap = () => {
      clearTimeout(waiting);
      waiting = null;
    };
    map.on('zoomstart', cancelTap);
    map.on('movestart', cancelTap);
    map.on('click', (event) => {
      if (event.originalEvent.shiftKey) return;
      if (waiting !== null) {
        clearTimeout(waiting);
        waiting = null;
        return;
      }
      const cell = cellAt(event.lngLat);
      const zoomedIn = map.getZoom() >= pickingZoom;
      waiting = setTimeout(() => {
        waiting = null;
        if (!zoomedIn) {
          describe();
          return;
        }
        const added = picked.delete(cell) || add(cell);
        changed();
        if (!added) say(box.dataset.tooWide);
      }, DOUBLE_TAP_MS);
    });

    // On a computer: while Shift is held, dragging paints instead of moving the map. Keys pressed
    // or let go while the page had no focus are missed, so leaving the page ends any painting too.
    let lastCell = null;
    const stopPainting = () => {
      lastCell = null;
      map.dragPan.enable();
    };
    window.addEventListener('keydown', (event) => {
      if (event.key === 'Shift') map.dragPan.disable();
    });
    window.addEventListener('keyup', (event) => {
      if (event.key === 'Shift') stopPainting();
    });
    window.addEventListener('blur', stopPainting);
    window.addEventListener('mouseup', () => { lastCell = null; });
    map.on('mousedown', (event) => {
      if (!event.originalEvent.shiftKey || map.getZoom() < pickingZoom) return;
      map.dragPan.disable();
      lastCell = cellAt(event.lngLat);
      const added = add(lastCell);
      changed();
      if (!added) say(box.dataset.tooWide);
    });
    map.on('mousemove', (event) => {
      if (lastCell === null) return;
      if (event.originalEvent.buttons === 0) {
        lastCell = null;
        return;
      }
      const cell = cellAt(event.lngLat);
      if (cell === lastCell) return;
      // A fast drag skips cells between two events: fill in the line between them.
      let path = [cell];
      try {
        if (h3.gridDistance(lastCell, cell) <= LONGEST_FILL) path = h3.gridPathCells(lastCell, cell);
      } catch {
        // No path across a pentagon: just the cell under the mouse.
      }
      const added = path.every(add);
      lastCell = cell;
      changed();
      if (!added) say(box.dataset.tooWide);
    });
  }

  function load(tag, attributes) {
    return new Promise((resolve, reject) => {
      const element = Object.assign(document.createElement(tag), attributes, { onload: resolve, onerror: reject });
      document.head.append(element);
    });
  }

  // For whoever cannot pick on a map (a keyboard, a screen reader): the coordinates again, which
  // replace the cells when given.
  instead.addEventListener('click', () => {
    typed.hidden = false;
    instead.hidden = true;
    latitude.focus();
  });

  opener.hidden = false;
  opener.addEventListener('click', async () => {
    opener.disabled = true;
    try {
      await Promise.all([
        load('link', { rel: 'stylesheet', href: box.dataset.maplibreCss }),
        load('link', { rel: 'stylesheet', href: box.dataset.mapCss }),
        load('script', { src: box.dataset.pmtiles }),
        load('script', { src: box.dataset.h3 }),
      ]);
      const [maplibregl, cellsModule] = await Promise.all([import(box.dataset.maplibre), import(box.dataset.cells)]);
      start(maplibregl, cellsModule);
      opener.hidden = true;
      box.focus(); // The button pressed is gone: where the keyboard goes on from.
    } catch (error) {
      // Without WebGL2, or if the map's files do not load: the coordinates still work.
      console.error(error);
      box.hidden = true;
      opener.hidden = true;
      typed.hidden = false;
      say(box.dataset.unavailable);
    }
  });
}
