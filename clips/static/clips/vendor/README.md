# tus-js-client, for resumable uploads (SPEC.md §8)

The browser side of the tus protocol, whose server side is `clips/resumable.py`: the upload page sends a video in pieces, and resumes where it stopped after a lost connection or a reloaded page.

Copied unchanged from the npm registry's tarball, with no package manager or build step.
The tarball matched the registry's sha512 integrity value before its file was copied.

| File | Package | Licence |
|---|---|---|
| `tus.min.js` | tus-js-client 4.3.1 (`dist/tus.min.js`) | MIT, `tus-js-client.LICENSE.txt` |

The file is a bundle: it also holds buffer, base64-js and ieee754, js-base64, url-parse with querystringify and requires-port, and regenerator-runtime, under the MIT or BSD-3-Clause licences. Their notices are not all in the bundle; gathering them belongs with choosing the archive's own licence (SPEC.md §12, M7).

SHA-256 of the copied file:

```
8cbb1b63fccc3bba0ae73ad1deb160ce046c3750851d0c3e94921ae3ef070eb8  tus.min.js
```

It contains no URL, so it contacts nothing but the archive. Its last line names a source map that is not copied; browsers fetch it only while their developer tools are open.

To upgrade, copy the new version's file the same way, update this table and the hash, and repeat the browser check of an upload cut off and resumed, in Firefox and Chrome.
