// The case-area map (SPEC.md §7, §12 M4.5; ADR 0006). A click adds or removes one grid
// cell; Shift-drag adds every cell it crosses. Only the cells' ids leave the page, in the
// form's `cells` field: never a click's position or where the map was looking. The server
// checks the cells again, and measures the area itself.
// pmtiles and h3 are the globals of the two classic scripts loaded before this one.

import * as maplibregl from './vendor/maplibre-gl.mjs';

const box = document.getElementById('area-map');
const field = document.querySelector('form input[name=cells]');
const rings = field && field.form.elements.margin_rings;
const readout = document.getElementById('area-map-size');
const resolution = box && Number(box.dataset.resolution);

// The area picked: the only state. Restored from the field after a refused form.
const picked = new Set(field ? field.value.split(/[\s,]+/).filter((cell) => h3.isValidCell(cell)) : []);
let map;

function cellsChanged() {
  field.value = [...picked].join(' ');
  map.getSource('picked')?.setData({
    type: 'FeatureCollection',
    features: [...picked].map((cell) => ({
      type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [h3.cellToBoundary(cell, true)] },
    })),
  });
  showSize();
}

function cellAt(lngLat) {
  return h3.latLngToCell(lngLat.lat, lngLat.lng, resolution);
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
  if (picked.size === 0) {
    readout.textContent = readout.dataset.none;
    return;
  }
  const across = Math.round(spanMetres([...picked], Math.max(0, Number(rings.value) || 0)));
  let text = readout.dataset.size.replace('{cells}', picked.size).replace('{metres}', across);
  if (across > Number(box.dataset.ceilingMetres)) text += ` ${readout.dataset.beyondCeiling}`;
  else if (across > Number(box.dataset.capMetres)) text += ` ${readout.dataset.overCaps}`;
  readout.textContent = text;
}

function toggle(cell) {
  if (!picked.delete(cell)) picked.add(cell);
  cellsChanged();
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

  map.on('click', (event) => {
    if (!event.originalEvent.shiftKey) toggle(cellAt(event.lngLat));
  });

  // While Shift is held, dragging paints instead of moving the map.
  let lastCell = null;
  const setShift = (event) => {
    if (event.key === 'Shift') map.dragPan[event.type === 'keydown' ? 'disable' : 'enable']();
  };
  window.addEventListener('keydown', setShift);
  window.addEventListener('keyup', setShift);
  map.on('mousedown', (event) => {
    if (!event.originalEvent.shiftKey) return;
    lastCell = cellAt(event.lngLat);
    picked.add(lastCell);
    cellsChanged();
  });
  map.on('mousemove', (event) => {
    if (lastCell === null) return;
    const cell = cellAt(event.lngLat);
    if (cell === lastCell) return;
    // A fast drag skips cells between two events: fill in the line between them.
    let path;
    try {
      path = h3.gridPathCells(lastCell, cell);
    } catch {
      path = [cell];
    }
    path.forEach((step) => picked.add(step));
    lastCell = cell;
    cellsChanged();
  });
  window.addEventListener('mouseup', () => {
    lastCell = null;
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
