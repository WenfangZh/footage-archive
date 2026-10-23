// Unlocks the personal key in the browser and shows its safety number, computed from the
// unlocked key rather than from what the server says (docs/m5-encryption-design.md §3.4, §4).
const page = document.getElementById('key-page');
if (page) {
  sodium.ready.then(() => {
    const keys = ArchiveKeys;
    const form = document.getElementById('unlock');

    function show(privateKey) {
      document.getElementById('safety-number').textContent = keys.safetyNumber(sodium.crypto_scalarmult_base(privateKey));
      form.hidden = true;
      document.getElementById('unlocked').hidden = false;
    }

    const publicKey = keys.b64.decode(page.dataset.publicKey);
    const stored = keys.recall(page.dataset.loginId, publicKey);
    if (stored) {
      show(stored.personal);
      return;
    }
    form.hidden = false;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      for (const message of form.querySelectorAll('[role=alert]')) {
        message.hidden = true;
      }
      const passphrase = keys.normalise(document.getElementById('unlock-passphrase').value, keys.PASSPHRASE_LENGTH);
      if (passphrase === null) {
        document.getElementById('wrong-passphrase').hidden = false;
        return;
      }
      const response = await fetch(page.dataset.encryptedKeyUrl, { credentials: 'same-origin' });
      if (!response.ok) {
        document.getElementById('unlock-failed').hidden = false;
        return;
      }
      const key = await response.json();
      try {
        const wrappingKey = keys.passphraseKey(passphrase, keys.b64.decode(key.passphrase_salt), key.opslimit, key.memlimit);
        const privateKey = keys.decryptPrivateKey(keys.b64.decode(key.private_key_under_passphrase), wrappingKey, keys.b64.decode(key.public_key));
        keys.remember(page.dataset.loginId, { personal: privateKey });
        form.reset();
        show(privateKey);
        // Only a checked unlock counts for rotation draws (design §5.5).
        fetch(page.dataset.unlockedUrl, {
          method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': page.dataset.csrfToken },
        });
      } catch (error) {
        const id = error instanceof keys.KeyMismatch ? 'key-mismatch' : 'wrong-passphrase';
        document.getElementById(id).hidden = false;
      }
    });
  });
}
