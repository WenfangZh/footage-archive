// Makes a personal key in the browser, and sends the server only what it may keep: the
// public half and the private half encrypted twice (docs/m5-encryption-design.md §5.1).
const form = document.getElementById('key-setup');
if (form) {
  sodium.ready.then(() => {
    const keys = ArchiveKeys;
    const pair = sodium.crypto_box_keypair();
    const passphrase = keys.randomCode(keys.PASSPHRASE_LENGTH);
    const recoveryKey = keys.randomCode(keys.RECOVERY_KEY_LENGTH);
    document.getElementById('passphrase').textContent = keys.display(passphrase);
    document.getElementById('recovery-key').textContent = keys.display(recoveryKey);
    form.hidden = false;

    form.addEventListener('submit', (event) => {
      // Typed back, to be sure both were saved; these fields have no name, so they are never sent.
      const typedPassphrase = keys.normalise(document.getElementById('typed-passphrase').value, keys.PASSPHRASE_LENGTH);
      const typedRecoveryKey = keys.normalise(document.getElementById('typed-recovery-key').value, keys.RECOVERY_KEY_LENGTH);
      const mismatch = document.getElementById('typed-mismatch');
      if (typedPassphrase !== passphrase || typedRecoveryKey !== recoveryKey) {
        event.preventDefault();
        mismatch.hidden = false;
        return;
      }
      const salt = sodium.randombytes_buf(16);
      const limits = [Number(form.dataset.opslimit), Number(form.dataset.memlimit)];
      const set = (name, bytes) => { form.elements[name].value = keys.b64.encode(bytes); };
      set('public_key', pair.publicKey);
      set('passphrase_salt', salt);
      set('private_key_under_passphrase', keys.encryptPrivateKey(pair.privateKey, keys.passphraseKey(passphrase, salt, ...limits)));
      set('private_key_under_recovery_key', keys.encryptPrivateKey(pair.privateKey, keys.recoveryWrappingKey(recoveryKey)));
    });
  });
}
