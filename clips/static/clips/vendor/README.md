# tus-js-client, for resumable uploads (SPEC.md §8)

The browser side of the tus protocol, whose server side is `clips/resumable.py`: the upload page sends a video in pieces, and resumes where it stopped after a lost connection or a reloaded page.

Copied unchanged from the npm registry's tarball, with no package manager or build step.
The tarball matched the registry's sha512 integrity value before its file was copied.

| File | Package | Licence |
|---|---|---|
| `tus.min.js` | tus-js-client 4.3.1 (`dist/tus.min.js`) | MIT, `tus-js-client.LICENSE.txt` |

The file is a bundle: it also holds buffer 5.2.1, base64-js 1.5.1, ieee754 1.2.1, js-base64 3.7.7, url-parse 1.5.10, querystringify 2.2.0 and requires-port 1.0.0, under the MIT or BSD-3-Clause licences, with none of their notices. Their licences are gathered in `tus-js-client.bundled.LICENSES.txt`, from their npm tarballs. The bundle's module list names them; the versions are those of tus-js-client's `yarn.lock`, except buffer, which comes with browserify.

SHA-256 of the copied file:

```
8cbb1b63fccc3bba0ae73ad1deb160ce046c3750851d0c3e94921ae3ef070eb8  tus.min.js
```

It contains no URL, so it contacts nothing but the archive. Its last line names a source map that is not copied; browsers fetch it only while their developer tools are open.

To upgrade, copy the new version's file the same way, update this table, the hash and the licence files (`tests/test_third_party_notices.py` fails until the hash matches), and repeat the browser check of an upload cut off and resumed, in Firefox and Chrome.
