// The map for the stated location (SPEC.md §8, §12 M6.5; ADR 0006 and its addendum), on the
// upload form and the code holder's edit page. Loaded only when asked for. Once zoomed in, a
// click fills the latitude and longitude, from which the server keeps only the cell; the map
// draws that cell, as lawyers' picked cells are drawn, for the precision chosen. Nothing else of
// the map (its view, its zoom) leaves the page.

const box = document.getElementById('location-map');
const opener = document.getElementById('location-map-open');
const status = document.getElementById('location-map-status');

if (box && opener) {
  const form = box.closest('form');
  const { latitude, longitude, precision } = form.elements;
  const resolutions = JSON.parse(document.getElementById('stated-resolutions').textContent);
  const pickingZoom = Number(box.dataset.pickingZoom);
  // On the edit page, the fields start at the kept cell's centre, which a finer precision cannot
  // start from (clips.forms.EditForm): it needs a new position.
  const kept = box.dataset.keptResolution ? {
    latitude: Number(box.dataset.keptLatitude),
    longitude: Number(box.dataset.keptLongitude),
    resolution: Number(box.dataset.keptResolution),
  } : null;
  let map = null;

  const say = (text) => { status.textContent = text || ''; };

  function point() {
    const lat = Number(latitude.value);
    const lng = Number(longitude.value);
    if (latitude.value.trim() === '' || longitude.value.trim() === '') return null;
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
    return [lat, lng];
  }

  // The cell the server will keep, from the same rounded coordinates the form sends.
  function chosenCell() {
    const position = point();
    const resolution = resolutions[precision.value];
    if (position === null || resolution === undefined) return null;
    const fromKeptCentre = kept !== null
      && Math.abs(position[0] - kept.latitude) < 1e-7 && Math.abs(position[1] - kept.longitude) < 1e-7;
    if (fromKeptCentre && resolution > kept.resolution) {
      say(box.dataset.finer);
      return null;
    }
    return h3.latLngToCell(position[0], position[1], resolution);
  }

  function redraw() {
    if (map === null || !map.getSource('chosen')) return;
    say('');
    const cell = chosenCell();
    map.getSource('chosen').setData({
      type: 'FeatureCollection',
      features: cell === null ? [] : [{
        type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [h3.cellToBoundary(cell, true)] },
      }],
    });
  }

  function start(maplibregl) {
    maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile);
    const position = point();
    box.hidden = false;
    map = new maplibregl.Map({
      container: box.querySelector('.area-map-canvas'),
      style: box.dataset.styleUrl,
      center: position ? [position[1], position[0]] : [2.35, 48.86],
      zoom: position ? 16 : 11,
      maxZoom: 19,
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
    map.on('load', () => {
      map.addSource('chosen', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'chosen-fill', type: 'fill', source: 'chosen', paint: { 'fill-color': '#d9480f', 'fill-opacity': 0.35 } });
      map.addLayer({ id: 'chosen-outline', type: 'line', source: 'chosen', paint: { 'line-color': '#d9480f', 'line-width': 1 } });
      redraw();
    });
    // The map pass has expired (maps.passes): the map can load no more, the fields still work.
    map.on('error', (event) => {
      if (event.error && event.error.status === 404) say(box.dataset.expired);
    });
    map.on('click', (event) => {
      if (map.getZoom() < pickingZoom) {
        say(box.dataset.zoomIn);
        return;
      }
      // As the form's own pre-filled positions are rounded (clips.views.initial_values).
      latitude.value = event.lngLat.lat.toFixed(6);
      longitude.value = event.lngLat.lng.toFixed(6);
      // A click is no exact spot unless the uploader says so.
      if (!precision.value) form.querySelector('input[name=precision][value=street]').checked = true;
      redraw();
    });
    for (const field of [latitude, longitude, ...precision]) field.addEventListener('input', redraw);
  }

  function load(tag, attributes) {
    return new Promise((resolve, reject) => {
      const element = Object.assign(document.createElement(tag), attributes, { onload: resolve, onerror: reject });
      document.head.append(element);
    });
  }

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
      start(await import(box.dataset.maplibre));
      opener.hidden = true;
    } catch {
      // Without WebGL2, or if the map's files do not load: the coordinates still work.
      box.hidden = true;
      opener.hidden = true;
    }
  });
}
