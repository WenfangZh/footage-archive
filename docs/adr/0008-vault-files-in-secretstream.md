# Vault files are encrypted with libsodium's secretstream, and byte ranges decrypt from the start

Each evidence copy and proxy is encrypted with its own random data key using libsodium's `crypto_secretstream_xchacha20poly1305`, in 64 KiB chunks with the last one marked final, so that a cut-off or altered file is detected (design in docs/m5-encryption-design.md). Secretstream can only be decrypted in order, so a request for a byte range (a video player seeking, a resumed download) decrypts from the start of the file and discards what precedes the range. Measured on the development server: about 0.07 s to the end of a 50 MB proxy and 2.6 s to the end of a 2 GB evidence copy, acceptable for previews and the rare resumed download. The plaintext size is stored with the file, so the server can answer a range request without decrypting the whole file first.

Changing the format later means re-encrypting every file in the vault, and the server would need every data key from a lawyer's browser to do it, which is why this is recorded.

## Considered Options

- **Independent encrypted chunks that can be read from any point** (the STREAM construction used by age and Tink): seeking costs nothing, but no maintained Python library offers it (pyrage and Tink's Python streams cannot seek), so we would write the chunk format ourselves, which the hard rule against custom cryptography forbids.
- **age, through pyrage**: a published format, but no seeking in Python, another dependency, and not compatible with PyNaCl.
- **Many short secretstreams per file**, each covering a few megabytes: shorter waits when seeking, but binding the pieces together against reordering or truncation is again a construction of our own.

## Consequences

- Every range request costs a decryption up to its start; a video player asking for many pieces of a large file makes the server decrypt the same beginning many times. Proxies are small, so this stays cheap; evidence copies are downloaded, not played.
- A failed authentication can only be found when the stream reaches the bad chunk, after part of the response has been sent: the response is cut off, never completed with wrong bytes.
- The plaintext size on the vault's row must stay in step with the file; both are written together, by the vault alone.
