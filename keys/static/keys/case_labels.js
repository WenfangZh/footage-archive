// Shows case labels: opens each with the personal key, and puts it in the page, and in its
// title in place of the case's number (docs/m5-encryption-design.md §7.4). Loaded after
// unlock.js, on pages that show labels.
const labels = [...document.querySelectorAll('.case-label[data-label]')].filter((element) => element.dataset.label);
if (labels.length && document.getElementById('unlock')) {
  sodium.ready.then(async () => {
    const keys = ArchiveKeys;
    const personalKey = await ArchiveUnlock.personalKey();
    for (const element of labels) {
      const label = JSON.parse(element.dataset.label);
      const dataKey = keys.openSealed(keys.b64.decode(label.sealed_key), personalKey);
      const text = dataKey ? keys.openText(keys.b64.decode(label.ciphertext), dataKey, keys.b64.decode(label.additional_data)) : null;
      if (text === null) {
        continue;  // The number stays.
      }
      element.textContent = text;
      document.title = document.title.replace(element.dataset.fallback, text);
    }
  });
}
