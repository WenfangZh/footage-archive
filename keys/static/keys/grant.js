// Hands the group key to a colleague: shows their safety number, computed from exactly the
// public key the grant is sealed to, then seals each group key they miss to it
// (docs/m5-encryption-design.md §3.4, §5.3).
const page = document.getElementById('grant-page');
if (page) {
  sodium.ready.then(async () => {
    const keys = ArchiveKeys;
    const recipientPublicKey = keys.b64.decode(page.dataset.recipientPublicKey);
    document.getElementById('recipient-safety-number').textContent = keys.safetyNumber(recipientPublicKey);
    const opened = await ArchiveUnlock.groupKeys(await ArchiveUnlock.personalKey());
    const needed = JSON.parse(page.dataset.groupKeys);
    if (!needed.every((groupKey) => opened.has(groupKey.public_key))) {
      document.getElementById('cannot-grant').hidden = false;
      return;
    }
    const form = document.getElementById('grant');
    form.hidden = false;
    form.addEventListener('submit', () => {
      const sealed = {};
      for (const groupKey of needed) {
        sealed[groupKey.id] = keys.b64.encode(keys.seal(opened.get(groupKey.public_key), recipientPublicKey));
      }
      form.elements.grants.value = JSON.stringify(sealed);
    });
  });
}
