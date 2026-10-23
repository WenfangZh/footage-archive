# libsodium.js, for lawyers' browsers (ADR 0007)

libsodium compiled for the browser: the same library as PyNaCl on the server, so what one encrypts the other opens. The "sumo" build is the one with Argon2id (`crypto_pwhash`), which unlocks a lawyer's personal key.

Copied unchanged from the npm registry's tarballs, with no package manager or build step.
Each tarball matched the registry's sha512 integrity value before its files were copied.

| File | Package | Licence |
|---|---|---|
| `libsodium-sumo.js` | libsodium-sumo 0.8.4 (`dist/modules-sumo/`), libsodium 1.0.22 | ISC, `libsodium.js.LICENSE.txt` |
| `libsodium-wrappers.js` | libsodium-wrappers-sumo 0.8.4 (`dist/modules-sumo/`) | ISC, the same file |

SHA-256 of the copied files:

```
a3d0b7eb2c24ba6b5401d88717e65bc6a92786d211bc8b91ed746d24226180f3  libsodium-sumo.js
4e2c5442899321d7d9aea69bef71a3c24a6eab5911b74a5c1ad8bc8c733b0a15  libsodium-wrappers.js
```

Both are classic scripts, loaded in this order: the first defines `libsodium`, the second wraps it as `sodium`, whose `ready` promise resolves once the WebAssembly module has started. Running WebAssembly needs `'wasm-unsafe-eval'` in the page's Content Security Policy; without it, libsodium.js falls back to a much slower JavaScript version.

To upgrade, copy the new versions' files the same way, update this table and the hashes, and run both halves of the interop test vectors (`tests/vectors/README.md`).
