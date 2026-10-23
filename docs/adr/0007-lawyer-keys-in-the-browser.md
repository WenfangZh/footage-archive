# Lawyers' keys stay in the browser; the server decrypts files only with a key the browser hands over

The vault is encrypted with libsodium (SPEC.md §10; design in docs/m5-encryption-design.md). Each lawyer has a personal key pair made in their browser; the server stores its private key only encrypted under a key passphrase, separate from the login password, and under a recovery key the lawyer keeps on paper. All lawyers share one group key pair, handed to each of them as a key grant: a lawyer's browser seals the group private key to the newcomer's public key, after the admin and that lawyer have each compared the newcomer's safety number with them. Ingest seals each item's random data key to the group public key, so the server can encrypt without being able to decrypt. Texts are decrypted in the browser. For a file, the browser opens its data key and hands it to the server for that clip only, in a cookie, and the server decrypts while it streams. A revocation is followed by a new group key and the resealing of every data key, done by the next lawyer's browser. Code holders, who have no keys, get a key pair derived from their upload code.

The server never holds a private key, so a seized or leaked server holds nothing readable, and someone watching the running server sees only what passes through it at the time. Moving decryption back to the server, or letting the login password unlock the key, would look like a simplification and quietly undo both, which is why this is recorded.

## Considered Options

- **One key held by the server** (from an environment variable): protects only disks and backups stored apart from the key; the operator, and anyone who seizes the running server, reads everything.
- **Lawyers' keys on the server, unlocked by the login password**: protects the vault at rest, but the server sees every password at login, so anyone watching it gets the keys and every clip with them.
- **Everything decrypted in the browser, files included**: nothing readable passes through the server after ingest, but saving a decrypted 2 GB download works properly only in Chrome and Edge, and playing a video needs a Service Worker answering range requests, which Safari handles badly; much more JavaScript, for a small gain over this option.
- **A separate application that holds the keys** (as SecureDrop does): the only option a compromised server cannot defeat by sending different JavaScript, but no preview in the browser, a second program to build and install for every lawyer. Left for later, if that threat becomes the main one.
- **Each data key sealed to every lawyer** instead of a group key: a new lawyer would need every existing key resealed for them; the group key needs one grant.
- **Grants made automatically by any lawyer's browser** to whatever key the server lists: someone able to write to the database could add a key of their own.

## Consequences

- Encryption protects against the server, not between lawyers: every lawyer's key opens every clip, and case scoping remains the server's check.
- The server that stores the ciphertext also sends the JavaScript that holds the keys, so a server running malicious code can steal them; ingest also sees every upload in plaintext. M5's promise is about what the server stores, and SPEC.md §1 and §3 say so.
- Lawyers need JavaScript, libsodium.js (sumo build) and a second secret, the key passphrase, to read the vault. Lawyer pages carry a Content Security Policy allowing `'wasm-unsafe-eval'`.
- A lawyer who loses both their key passphrase and their recovery key needs a new key, confirmed by the admin and granted by another lawyer; what was sealed only to their old key (case labels, later their messages) is lost. If every lawyer lost both, the vault would be unreadable for good.
- The admin has no key and cannot read the vault, by construction.
- Grants and rotations trust the server's list of lawyer keys beyond the safety number checks; a signing key for the admin would remove that trust, at the cost of a key for the admin to keep and recover.
- Sealed boxes use X25519, which a future quantum computer could break. PyNaCl does not yet offer libsodium's post-quantum X-Wing; once it does, a new group key of that kind and a rotation reseal every data key without re-encrypting files. Copies taken before then stay exposed.
