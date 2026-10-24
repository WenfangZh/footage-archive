// On a clip's page: opens its context and contact details with the group key and shows them
// (docs/m5-encryption-design.md §7.1); opens the data keys of its proxy and evidence copy,
// and hands each to the server, which returns it in a cookie for the preview or the download
// (§6.3). The evidence copy's is handed over only when the lawyer asks for the download.
const files = document.getElementById('clip-files');
if (files) {
  sodium.ready.then(async () => {
    const keys = ArchiveKeys;
    const opened = await ArchiveUnlock.groupKeys(await ArchiveUnlock.personalKey());
    const failed = document.getElementById('clip-keys-failed');

    function dataKeys(sealedKeys) {
      return sealedKeys.map((sealed) => {
        const groupPrivateKey = opened.get(sealed.group_public_key);
        return groupPrivateKey && keys.openSealed(keys.b64.decode(sealed.sealed_data_key), groupPrivateKey);
      }).filter(Boolean);
    }

    for (const element of document.querySelectorAll('.vault-text')) {
      const encrypted = JSON.parse(element.dataset.text);
      const text = dataKeys(encrypted.sealed_keys).map((dataKey) => keys.openText(
        keys.b64.decode(encrypted.ciphertext), dataKey, keys.b64.decode(encrypted.additional_data),
      )).find((opened) => opened !== null);
      if (text === undefined) {
        failed.hidden = false;
        continue;
      }
      // As text, line by line: never as markup.
      // Browsers send a textarea's line breaks as CRLF.
      text.split(/\r?\n/).forEach((line, index) => {
        if (index) {
          element.append(document.createElement('br'));
        }
        element.append(line);
      });
    }

    // Opens the data key with whichever group key this browser holds, and hands it over.
    async function handOver(url, sealedKeys) {
      for (const dataKey of dataKeys(JSON.parse(sealedKeys))) {
        try {
          const response = await ArchiveUnlock.post(url, { data_key: keys.b64.encode(dataKey) });
          if (response.ok) {
            return true;
          }
        } catch (error) {
          // The network failed: said below, like a key that did not open.
        }
      }
      failed.hidden = false;
      return false;
    }

    const preview = document.getElementById('preview');
    if (preview && await handOver(files.dataset.proxyKeyUrl, files.dataset.proxyKeys)) {
      preview.src = preview.dataset.src;
      preview.hidden = false;
    }
    const download = document.getElementById('download');
    download.hidden = false;
    download.addEventListener('click', async (event) => {
      event.preventDefault();
      if (await handOver(files.dataset.evidenceKeyUrl, files.dataset.evidenceKeys)) {
        window.location.href = download.href;
      }
    });
  });
}
