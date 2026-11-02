// Writes the map's style from @protomaps/basemaps, once, on a developer's machine (README):
//   node maps/generate_style.mjs <unpacked @protomaps/basemaps package> > maps/style.json
// The archive itself never runs Node.

const [packageDir] = process.argv.slice(2);
const { layers, namedFlavor } = await import(`${packageDir}/dist/esm/index.js`);

// Icons would need a sprite sheet, which is not served: no points of interest, one-way
// arrows or road number shields, and town names without their dot. Landcover is drawn
// only below zoom 7, far below the zooms where cells show, and drawing it would call
// for crediting its source, ESA WorldCover (CC BY 4.0), on the map.
const flavor = { ...namedFlavor('light'), pois: undefined };
const withoutIcons = layers('protomaps', flavor, { lang: 'fr' })
  .filter((layer) => !['roads_oneway', 'roads_shields', 'landcover'].includes(layer.id))
  .map((layer) => {
    if (!layer.layout) return layer;
    const layout = Object.fromEntries(Object.entries(layer.layout).filter(([key]) => !key.startsWith('icon-')));
    return { ...layer, layout };
  });

const style = {
  version: 8,
  // maps.views.style fills in both URLs, on the archive's own origin.
  sources: { protomaps: { type: 'vector', url: '', attribution: '© OpenStreetMap contributors' } },
  glyphs: '',
  layers: withoutIcons,
};
process.stdout.write(`${JSON.stringify(style, null, 1)}\n`);
