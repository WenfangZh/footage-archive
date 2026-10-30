// A key passphrase or recovery key that may have been seen (docs/m5-encryption-design.md
// §5.6): with the old key unlocked, makes a new key as setup does, opens the data key of each
// case label and message (§7.3) with the old key, checks it against its key check value and
// seals it to the new key, and sends the new key, the labels and the messages in one request.
// The server refuses it if a case made or a message sent meanwhile changed them; they are then
// fetched again, and it is sent again.
const exposureForm = document.getElementById('exposure');
if (exposureForm) {
  sodium.ready.then(async () => {
    const keys = ArchiveKeys;
    const decode = keys.b64.decode;
    const encode = keys.b64.encode;
    const oldPrivateKey = await ArchiveUnlock.personalKey();
    const pair = sodium.crypto_box_keypair();
    const passphrase = keys.randomCode(keys.PASSPHRASE_LENGTH);
    const recoveryKey = keys.randomCode(keys.RECOVERY_KEY_LENGTH);
    document.getElementById('passphrase').textContent = keys.display(passphrase);
    document.getElementById('recovery-key').textContent = keys.display(recoveryKey);
    exposureForm.hidden = false;

    function show(id) {
      document.getElementById(id).hidden = false;
    }

    // Each label's and message's data key, resealed to the new key; the ids of those that did
    // not open.
    async function resealedItems() {
      const response = await fetch(exposureForm.dataset.sealedUrl, { credentials: 'same-origin' });
      if (!response.ok) {
        return null;
      }
      const held = await response.json();
      // A key replaced in another tab meanwhile: these are not this key's.
      if (held.public_key !== exposureForm.dataset.publicKey) {
        return null;
      }
      const fields = {};
      for (const name of ['labels', 'messages']) {
        const resealed = {};
        const unreadable = [];
        for (const item of held[name]) {
          const dataKey = keys.openDataKey(decode(item.sealed_key), oldPrivateKey, decode(item.key_check));
          if (dataKey) {
            resealed[item.id] = encode(keys.seal(dataKey, pair.publicKey));
          } else {
            unreadable.push(item.id);
          }
        }
        fields[`resealed_${name}`] = JSON.stringify(resealed);
        fields[`unreadable_${name}`] = JSON.stringify(unreadable);
      }
      return fields;
    }

    const button = exposureForm.querySelector('button[type=submit]');

    exposureForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      for (const message of exposureForm.querySelectorAll('[role=alert]')) {
        message.hidden = true;
      }
      // One report at a time: a second, sent while the first replaces the key, would be refused.
      button.disabled = true;
      try {
        const shown = await report();
        if (shown) {
          show(shown);
        }
      } catch (error) {
        show('exposure-failed');
      }
      button.disabled = false;
    });

    // Sends the report; returns the id of the message to show, or nothing once the page is left.
    async function report() {
      const typedPassphrase = keys.normalise(document.getElementById('typed-passphrase').value, keys.PASSPHRASE_LENGTH);
      const typedRecoveryKey = keys.normalise(document.getElementById('typed-recovery-key').value, keys.RECOVERY_KEY_LENGTH);
      if (typedPassphrase !== passphrase || typedRecoveryKey !== recoveryKey) {
        return 'typed-mismatch';
      }
      const salt = sodium.randombytes_buf(16);
      const limits = [Number(exposureForm.dataset.opslimit), Number(exposureForm.dataset.memlimit)];
      const newKey = {
        public_key: encode(pair.publicKey),
        passphrase_salt: encode(salt),
        private_key_under_passphrase: encode(keys.encryptPrivateKey(pair.privateKey, keys.passphraseKey(passphrase, salt, ...limits))),
        private_key_under_recovery_key: encode(keys.encryptPrivateKey(pair.privateKey, keys.recoveryWrappingKey(recoveryKey))),
        old_public_key: exposureForm.dataset.publicKey,
        password: exposureForm.elements.password.value,
      };
      // A few times at most: each try that the server refuses found a case made, or a message
      // sent, meanwhile.
      for (let round = 0; round < 3; round += 1) {
        const items = await resealedItems();
        if (!items) {
          break;
        }
        const response = await ArchiveUnlock.post(exposureForm.dataset.reportUrl, { ...newKey, ...items });
        if (response.status === 201) {
          // The old key is retired: forgotten here too, and the new one is unlocked with the
          // new passphrase, which shows it was saved.
          keys.forget();
          window.location.href = exposureForm.dataset.doneUrl;
          return null;
        }
        if (response.status === 400 && (await response.json()).wrong_password) {
          return 'wrong-password';
        }
        if (response.status === 403 && (await response.json()).last_holder) {
          return 'last-holder';
        }
        if (response.status === 429) {
          return 'too-many';
        }
        if (response.status !== 409) {
          break;
        }
      }
      return 'exposure-failed';
    }
  });
}
