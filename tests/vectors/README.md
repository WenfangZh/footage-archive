# Test vectors: PyNaCl and libsodium.js agree

The server encrypts with PyNaCl and lawyers' browsers with libsodium.js (ADR 0007), so each must open what the other makes. Each side writes its own values once, and the other side checks them:

- `pynacl.json` is written by `python tests/vectors/make_pynacl.py`, and checked by `node tests/vectors/make_libsodium_js.mjs`;
- `libsodium_js.json` is written by `node tests/vectors/make_libsodium_js.mjs --write`, and checked by `tests/test_libsodium_vectors.py`, which also rechecks `pynacl.json`.

They cover every libsodium function the archive uses: seeded key pairs, public keys from private keys, sealed boxes, XChaCha20-Poly1305 with additional data, secretstream with its final tag, Argon2id with the interactive limits, BLAKE2b with a label and keyed, and padding.

CI runs only the Python half, since the archive has no Node.js. Run the Node half by hand, with Node.js from apt, whenever PyNaCl or the vendored libsodium.js (`core/static/core/vendor/libsodium/`) changes, after rewriting both files: first `make_pynacl.py`, then `make_libsodium_js.mjs --write`.
