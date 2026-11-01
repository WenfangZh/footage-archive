// What the two maps of cells share: the case-area map (area_map.js, M4.5) and the map of a clip's
// stated location (clips/static/clips/location_map.js, M6.6). h3 is the global of the classic
// script loaded before either.

// A faint grid of the cells that can be picked, drawn once the map is zoomed in to `fromZoom`,
// where a cell is large enough to pick, so that a cell's size shows before any is picked. With
// more cells than `limit` in view, none is drawn.
export function showGrid(map, resolution, fromZoom, limit = 8000) {
  const empty = { type: 'FeatureCollection', features: [] };
  map.addSource('grid', { type: 'geojson', data: empty });
  map.addLayer({ id: 'grid', type: 'line', source: 'grid', paint: { 'line-color': '#333', 'line-opacity': 0.25, 'line-width': 0.5 } });
  const draw = () => {
    if (map.getZoom() < fromZoom) {
      map.getSource('grid').setData(empty);
      return;
    }
    // A little beyond the view, so that the cells along its edges are drawn whole.
    const bounds = map.getBounds();
    const padLat = (bounds.getNorth() - bounds.getSouth()) / 10;
    const padLng = (bounds.getEast() - bounds.getWest()) / 10;
    const [south, north] = [bounds.getSouth() - padLat, bounds.getNorth() + padLat];
    const [west, east] = [bounds.getWest() - padLng, bounds.getEast() + padLng];
    let cells = [];
    try {
      cells = h3.polygonToCells([[south, west], [north, west], [north, east], [south, east]], resolution);
    } catch {
      // A view h3 cannot fill (across the 180° meridian): no grid.
    }
    map.getSource('grid').setData(cells.length > limit ? empty : {
      type: 'FeatureCollection',
      features: cells.map((cell) => ({
        type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [h3.cellToBoundary(cell, true)] },
      })),
    });
  };
  map.on('moveend', draw);
  draw();
}

// The same estimate as the server's (clips.grid.span_metres): the farthest two centres
// among the corners of their convex hull, plus half a cell at each end and one ring's
// spacing per ring of margin.
export function spanMetres(cells, marginRings = 0) {
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

function metres(a, b) {
  return h3.greatCircleDistance(a, b, 'm');
}
