// Unlocks the personal key and shows its safety number, computed from the unlocked key
// rather than from what the server says (docs/m5-encryption-design.md §3.4, §4); for a
// lawyer, also says whether they hold the group key.
if (document.getElementById('unlock')) {
  sodium.ready.then(async () => {
    const privateKey = await ArchiveUnlock.personalKey();
    document.getElementById('safety-number').textContent = ArchiveKeys.safetyNumber(sodium.crypto_scalarmult_base(privateKey));
    document.getElementById('unlocked').hidden = false;
    const holds = document.getElementById('holds-group-key');
    if (holds) {
      const opened = await ArchiveUnlock.groupKeys(privateKey);
      (opened.size ? holds : document.getElementById('no-group-key')).hidden = false;
    }
  });
}
