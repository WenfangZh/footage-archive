// On a clip's page: opens the data keys of its proxy and evidence copy with the group key,
// and hands each to the server, which returns it in a cookie for the preview or the download
// (docs/m5-encryption-design.md §6.3). The evidence copy's is handed over only when the
// lawyer asks for the download.
const files = document.getElementById('clip-files');
if (files) {
  sodium.ready.then(async () => {
    const keys = ArchiveKeys;
    const opened = await ArchiveUnlock.groupKeys(await ArchiveUnlock.personalKey());
    const failed = document.getElementById('clip-keys-failed');

    // Opens the data key with whichever group key this browser holds, and hands it over.
    async function handOver(url, sealedKeys) {
      for (const sealed of JSON.parse(sealedKeys)) {
        const groupPrivateKey = opened.get(sealed.group_public_key);
        const dataKey = groupPrivateKey && keys.openSealed(keys.b64.decode(sealed.sealed_data_key), groupPrivateKey);
        if (dataKey) {
          const response = await ArchiveUnlock.post(url, { data_key: keys.b64.encode(dataKey) });
          if (response.ok) {
            return true;
          }
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
