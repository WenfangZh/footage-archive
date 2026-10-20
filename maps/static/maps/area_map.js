// The case-area map (SPEC.md §7, §12 M4.5; ADR 0006). A click adds or removes one grid
// cell; Shift-drag adds every cell it crosses. Only the cells' ids leave the page, in the
// form's `cells` field: never a click's position or where the map was looking. The server
// checks the cells again, and measures the area itself.
// pmtiles and h3 are the globals of the two classic scripts loaded before this one.

import * as maplibregl from './vendor/maplibre-gl.mjs';

// Below this zoom a cell is a few pixels wide: too small to pick on purpose.
const PICKING_ZOOM = 14;
// A drag that jumps farther than this many cells between two mouse events is not filled in.
const LONGEST_FILL = 200;

const box = document.getElementById('area-map');
const field = document.querySelector('form input[name=cells]');
const rings = field && field.form.elements.margin_rings;
const readout = document.getElementById('area-map-size');
const resolution = box && Number(box.dataset.resolution);
const maxCells = box && Number(box.dataset.maxCells);

// The area picked: the only state. Restored from the field after a refused form.
const picked = new Set(
  field ? field.value.split(/[\s,]+/).filter((cell) => h3.isValidCell(cell) && h3.getResolution(cell) === resolution) : [],
);
let map;
let redrawing = false;

// The field follows each change at once; the drawing and the size, once per frame.
function cellsChanged() {
  field.value = [...picked].join(' ');
  if (redrawing) return;
  redrawing = true;
  requestAnimationFrame(() => {
    redrawing = false;
    map.getSource('picked')?.setData({
      type: 'FeatureCollection',
      features: [...picked].map((cell) => ({
        type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [h3.cellToBoundary(cell, true)] },
      })),
    });
    showSize();
  });
}

function cellAt(lngLat) {
  return h3.latLngToCell(lngLat.lat, lngLat.lng, resolution);
}

function canPick() {
  return map.getZoom() >= PICKING_ZOOM;
}

function add(cell) {
  if (picked.size < maxCells) picked.add(cell);
}

function metres(a, b) {
  return h3.greatCircleDistance(a, b, 'm');
}

// The same estimate as the server's (clips.grid.span_metres): the farthest two centres
// among the corners of their convex hull, plus half a cell at each end and one ring's
// spacing per ring of margin.
function spanMetres(cells, marginRings) {
  const centres = new Map(cells.map((cell) => [cell, h3.cellToLatLng(cell)]));
  const hull = convexHull(centres);
  let farthest = 0;
  for (let i = 0; i < hull.length; i += 1) {
    for (let j = i + 1; j < hull.length; j += 1) {
      farthest = Math.max(farthest, metres(centres.get(hull[i]), centres.get(hull[j])));
    }
  }
  let halfWidth = 0;
  let spacing = 0;
  for (const cell of hull) {
    for (const corner of h3.cellToBoundary(cell)) halfWidth = Math.max(halfWidth, metres(centres.get(cell), corner));
    for (const near of h3.gridDisk(cell, 1)) spacing = Math.max(spacing, metres(centres.get(cell), h3.cellToLatLng(near)));
  }
  return farthest + 2 * halfWidth + 2 * marginRings * spacing;
}

function convexHull(centres) {
  const stretch = Math.cos((centres.values().next().value[0] * Math.PI) / 180);
  const points = [...centres].map(([cell, [lat, lng]]) => [lng * stretch, lat, cell]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (points.length <= 2) return points.map((point) => point[2]);
  const turnsLeft = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]) > 0;
  const chain = (ordered) => {
    const kept = [];
    for (const point of ordered) {
      while (kept.length >= 2 && !turnsLeft(kept[kept.length - 2], kept[kept.length - 1], point)) kept.pop();
      kept.push(point);
    }
    return kept.slice(0, -1);
  };
  return [...chain(points), ...chain([...points].reverse())].map((point) => point[2]);
}

function showSize() {
  let text = canPick() ? '' : `${readout.dataset.zoomIn} `;
  if (picked.size === 0) {
    readout.textContent = text + readout.dataset.none;
    return;
  }
  const across = Math.round(spanMetres([...picked], Math.max(0, Number(rings.value) || 0)));
  text += readout.dataset.size.replace('{cells}', picked.size).replace('{metres}', across);
  if (across > Number(box.dataset.ceilingMetres)) text += ` ${readout.dataset.beyondCeiling}`;
  else if (across > Number(box.dataset.capMetres)) text += ` ${readout.dataset.overCaps}`;
  readout.textContent = text;
}

function start() {
  maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile);
  map = new maplibregl.Map({
    container: box.querySelector('.area-map-canvas'),
    style: box.dataset.styleUrl,
    center: [2.35, 48.86],
    zoom: 11,
    maxZoom: 19,
    // Shift-drag paints cells instead of zooming to a box; the map stays flat and north up.
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
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
  if (picked.size) {
    const corners = [...picked].flatMap((cell) => h3.cellToBoundary(cell, true));
    const lngs = corners.map((corner) => corner[0]);
    const lats = corners.map((corner) => corner[1]);
    map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 40, duration: 0 });
  }

  map.on('load', () => {
    map.addSource('picked', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'picked-fill', type: 'fill', source: 'picked', paint: { 'fill-color': '#d9480f', 'fill-opacity': 0.35 } });
    map.addLayer({ id: 'picked-outline', type: 'line', source: 'picked', paint: { 'line-color': '#d9480f', 'line-width': 1 } });
    cellsChanged();
  });
  map.on('zoomend', showSize);

  map.on('click', (event) => {
    if (event.originalEvent.shiftKey || !canPick()) return;
    const cell = cellAt(event.lngLat);
    if (!picked.delete(cell)) add(cell);
    cellsChanged();
  });

  // While Shift is held, dragging paints instead of moving the map. Keys pressed or let go
  // while the page had no focus are missed, so leaving the page ends any painting too.
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
  window.addEventListener('mouseup', () => {
    lastCell = null;
  });
  map.on('mousedown', (event) => {
    if (!event.originalEvent.shiftKey || !canPick()) return;
    map.dragPan.disable();
    lastCell = cellAt(event.lngLat);
    add(lastCell);
    cellsChanged();
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
    path.forEach(add);
    lastCell = cell;
    cellsChanged();
  });
  rings.addEventListener('input', showSize);
}

if (box && field) {
  try {
    box.hidden = false;
    readout.hidden = false;
    start();
  } catch {
    // Without WebGL2 there is no map: the typed place and radius still work.
    box.hidden = true;
    readout.hidden = true;
  }
}
