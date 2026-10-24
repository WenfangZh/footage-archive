// Unlocks the personal key on a page that includes keys/unlock.html, and opens the key grants
// it holds (docs/m5-encryption-design.md §4). Loaded after keys.js; defines the global
// ArchiveUnlock. Only the personal key is kept in the tab: it opens the grants again on
// each page, so a grant made or replaced since is never missed.
(function () {
  'use strict';

  const keys = ArchiveKeys;
  const form = document.getElementById('unlock');

  function post(url, fields) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': form.dataset.csrfToken },
      body: new URLSearchParams(fields),
    });
  }

  // The personal private key: from this tab if it was unlocked in this login, otherwise once
  // the person has typed their key passphrase.
  function personalKey() {
    const publicKey = keys.b64.decode(form.dataset.publicKey);
    const stored = keys.recall(form.dataset.loginId, publicKey);
    if (stored) {
      return Promise.resolve(stored.personal);
    }
    form.hidden = false;
    return new Promise((resolve) => {
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
        const response = await fetch(form.dataset.encryptedKeyUrl, { credentials: 'same-origin' });
        if (!response.ok) {
          document.getElementById('unlock-failed').hidden = false;
          return;
        }
        const key = await response.json();
        let privateKey;
        try {
          const wrappingKey = keys.passphraseKey(passphrase, keys.b64.decode(key.passphrase_salt), key.opslimit, key.memlimit);
          privateKey = keys.decryptPrivateKey(keys.b64.decode(key.private_key_under_passphrase), wrappingKey, keys.b64.decode(key.public_key));
        } catch (error) {
          const id = error instanceof keys.KeyMismatch ? 'key-mismatch' : 'wrong-passphrase';
          document.getElementById(id).hidden = false;
          return;
        }
        keys.remember(form.dataset.loginId, { personal: privateKey });
        form.reset();
        form.hidden = true;
        // Only a checked unlock counts for rotation draws (design §5.5).
        post(form.dataset.unlockedUrl, {});
        resolve(privateKey);
      });
    });
  }

  // The group private keys the person's grants hold, by group public key (base64). Each grant
  // not yet checked is reported, opened or not; a lawyer whose browser should make the first
  // group key makes it here, sealed to their own key (design §5.3).
  async function groupKeys(personalPrivateKey) {
    const response = await fetch(form.dataset.grantsUrl, { credentials: 'same-origin' });
    if (!response.ok) {
      throw new Error('The key grants could not be fetched.');
    }
    const held = await response.json();
    const opened = new Map();
    // A key replaced since this page was shown, in another tab: its grants are not this key's
    // to open, nor to judge.
    if (held.public_key !== form.dataset.publicKey) {
      return opened;
    }
    for (const grant of held.grants) {
      const groupPrivateKey = keys.openGrant(
        keys.b64.decode(grant.sealed_private_key), personalPrivateKey, keys.b64.decode(grant.group_public_key),
      );
      if (groupPrivateKey) {
        opened.set(grant.group_public_key, groupPrivateKey);
      }
      if (!grant.checked) {
        post(form.dataset.grantCheckedUrl, {
          grant: grant.id, public_key: held.public_key, result: groupPrivateKey ? 'passed' : 'failed',
        });
      }
    }
    if (held.may_start) {
      const pair = sodium.crypto_box_keypair();
      const started = await post(form.dataset.startUrl, {
        public_key: keys.b64.encode(pair.publicKey),
        sealed_private_key: keys.b64.encode(keys.sealGroupKey(pair.privateKey, keys.b64.decode(form.dataset.publicKey))),
      });
      // Refused if another lawyer's browser made one first: then a grant from them comes later.
      if (started.status === 201) {
        opened.set(keys.b64.encode(pair.publicKey), pair.privateKey);
      }
    }
    return opened;
  }

  globalThis.ArchiveUnlock = { personalKey, groupKeys };
}());
