// A forgotten key passphrase (docs/m5-encryption-design.md §5.6): opens the private key's copy
// under the recovery key, which only this page carries, checks it gives the public key the archive shows, and encrypts it under a
// new key passphrase, shown once and typed back. The server gets only the new encrypted copy;
// the recovery key and the passphrase never leave the browser.
const recoverForm = document.getElementById('recover');
if (recoverForm) {
  sodium.ready.then(() => {
    const keys = ArchiveKeys;
    const replaceForm = document.getElementById('new-passphrase');
    const publicKey = keys.b64.decode(recoverForm.dataset.publicKey);
    let privateKey = null;
    let passphrase = null;
    recoverForm.hidden = false;

    recoverForm.addEventListener('submit', (event) => {
      event.preventDefault();
      for (const message of recoverForm.querySelectorAll('[role=alert]')) {
        message.hidden = true;
      }
      const recoveryKey = keys.normalise(document.getElementById('recovery-key').value, keys.RECOVERY_KEY_LENGTH);
      if (recoveryKey === null) {
        document.getElementById('wrong-recovery-key').hidden = false;
        return;
      }
      try {
        // Checked against the public key this page shows, as an unlock is (design §3.4).
        privateKey = keys.decryptPrivateKey(
          keys.b64.decode(recoverForm.dataset.recoveryCopy), keys.recoveryWrappingKey(recoveryKey), publicKey,
        );
      } catch (error) {
        document.getElementById(error instanceof keys.KeyMismatch ? 'key-mismatch' : 'wrong-recovery-key').hidden = false;
        return;
      }
      recoverForm.reset();
      recoverForm.hidden = true;
      passphrase = keys.randomCode(keys.PASSPHRASE_LENGTH);
      document.getElementById('passphrase').textContent = keys.display(passphrase);
      replaceForm.hidden = false;
    });

    replaceForm.addEventListener('submit', (event) => {
      // Typed back, to be sure it was saved; this field has no name, so it is never sent.
      const typed = keys.normalise(document.getElementById('typed-passphrase').value, keys.PASSPHRASE_LENGTH);
      if (privateKey === null || typed !== passphrase) {
        event.preventDefault();
        document.getElementById('typed-mismatch').hidden = false;
        return;
      }
      const salt = sodium.randombytes_buf(16);
      const limits = [Number(replaceForm.dataset.opslimit), Number(replaceForm.dataset.memlimit)];
      const set = (name, bytes) => { replaceForm.elements[name].value = keys.b64.encode(bytes); };
      set('public_key', publicKey);
      set('passphrase_salt', salt);
      set('private_key_under_passphrase', keys.encryptPrivateKey(privateKey, keys.passphraseKey(passphrase, salt, ...limits)));
      // Unlocked in this tab from now on, as after typing the passphrase.
      keys.remember(recoverForm.dataset.loginId, { personal: privateKey });
    });
  });
}
