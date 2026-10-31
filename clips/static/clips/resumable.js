// Sends the video in pieces through the tus protocol (clips/resumable.py), so an upload cut off by
// a lost connection or a reloaded page resumes where it stopped (SPEC.md §8). Without JavaScript,
// or in a browser tus-js-client does not support, the form sends the file in one piece as before.
(function () {
  'use strict';

  const form = document.querySelector('form[data-resumable-url]');
  if (!form || !window.tus || !tus.isSupported) {
    return;
  }
  // As decided for M6.3: a piece lost to a dropped connection costs at most this much again.
  const PIECE_BYTES = 20 * 1000 * 1000;
  // A partial upload is deleted on the server this long after it started.
  const LIFETIME_MS = Number(form.dataset.partialUploadMinutes) * 60 * 1000;
  const input = form.querySelector('input[type=file]');
  const submit = form.querySelector('[type=submit]');
  const token = form.querySelector('input[name=csrfmiddlewaretoken]').value;
  const progressLine = document.getElementById('upload-progress-line');
  const progress = document.getElementById('upload-progress');
  const percent = document.getElementById('upload-percent');
  const asPercent = new Intl.NumberFormat(document.documentElement.lang, { style: 'percent' });
  const finishForm = document.getElementById('finish-upload');
  // Whatever the browser stores names no file: only its size, type and date, enough to find it
  // again once the same file is chosen after a reload (tus-js-client's own key holds the name).
  const fingerprint = (file, options) => Promise.resolve(
    ['fa-upload', file.size, file.type, file.lastModified, options.endpoint].join(':'),
  );
  let upload = null;
  // Choosing the same file again gives a new object for it.
  const sameFile = (one, other) => ['name', 'size', 'type', 'lastModified'].every((key) => one[key] === other[key]);

  const statusLine = document.getElementById('upload-status');
  function show(status) {
    const text = document.querySelector(`#upload-texts [data-status="${status}"]`);
    statusLine.textContent = text ? text.textContent : '';
  }

  function stopped(status) {
    show(status);
    submit.disabled = false;
    input.disabled = false;
  }

  function failed(error) {
    const response = error && error.originalResponse;
    const status = response ? response.getStatus() : 0;
    // A 503 is the archive's "uploads are closed" only when it comes from the archive itself.
    const closed = status === 503 && response.getHeader('Tus-Resumable');
    stopped(status === 429 ? 'rate-limited' : closed ? 'closed' : 'stopped');
  }

  // Forget uploads the server has deleted for age, so nothing about them lingers in this browser;
  // say when one could still resume.
  const storage = tus.defaultOptions.urlStorage;
  storage.findAllUploads().then((previous) => {
    let resumable = false;
    for (const entry of previous) {
      if (Date.now() - Date.parse(entry.creationTime) < LIFETIME_MS) {
        resumable = true;
      } else {
        storage.removeUpload(entry.urlStorageKey);
      }
    }
    if (resumable) {
      show('cut-off');
    }
  });

  form.addEventListener('submit', (event) => {
    const file = input.files[0];
    // An empty file goes in one piece, and the server says why it is refused.
    if (file === undefined || file.size === 0) {
      return;
    }
    event.preventDefault();
    if (submit.disabled) {
      return;
    }
    submit.disabled = true;
    input.disabled = true;
    show('sending');
    if (upload !== null && sameFile(upload.file, file)) {
      upload.start(); // It asks the server how much arrived, and goes on from there.
      return;
    }
    if (upload !== null) {
      // Another file than the one cut off: the server deletes that partial upload, and the browser forgets it.
      upload.abort(true).catch(() => {});
    }
    upload = new tus.Upload(file, {
      endpoint: form.dataset.resumableUrl,
      chunkSize: PIECE_BYTES,
      // A phone's connection at a protest comes and goes: keep trying for about two minutes.
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000, 30000, 60000],
      headers: { 'X-CSRFToken': token },
      fingerprint,
      // Forgotten only once the form is reached (finish, below): if that last step fails, choosing
      // the video again finds every piece already there.
      removeFingerprintOnSuccess: false,
      onShouldRetry(error) {
        const response = error.originalResponse;
        const status = response ? response.getStatus() : 0;
        if (status === 503 && response.getHeader('Tus-Resumable')) {
          return false; // Uploads are closed: retrying would only delay saying so.
        }
        // As tus-js-client decides by default: not after a refusal, except a conflict or a lock.
        return (status < 400 || status >= 500 || status === 409 || status === 423) && navigator.onLine;
      },
      onProgress(sent, total) {
        progressLine.hidden = false;
        progress.max = total;
        progress.value = sent;
        percent.textContent = asPercent.format(sent / total);
      },
      onError: failed,
      onSuccess: finish,
    });
    upload.findPreviousUploads().then((previous) => {
      if (previous.length) {
        upload.resumeFromPreviousUpload(previous[0]);
      }
      upload.start();
    }).catch(failed);
  });

  // The quick read, then the form, as for a file sent in one piece.
  function finish() {
    show('checking');
    fetch(new URL('finish/', upload.url), { method: 'POST', body: new FormData(finishForm), credentials: 'same-origin' })
      .then((response) => {
        if (response.ok && response.redirected) {
          return forget().then(() => window.location.assign(response.url));
        }
        if (response.status === 400 || response.status === 404) {
          // Not a video, so deleted on the server; or gone already: nothing is left to resume.
          return forget().then(() => {
            upload = null;
            stopped(response.status === 400 ? 'not-a-video' : 'stopped');
          });
        }
        // Uploads closed meanwhile, or no answer: kept, and Send finds every piece there.
        stopped(response.status === 503 ? 'closed' : 'stopped');
        return null;
      })
      .catch(() => stopped('stopped'));
  }

  function forget() {
    return upload.findPreviousUploads().then((previous) => Promise.all(
      previous.map((entry) => storage.removeUpload(entry.urlStorageKey)),
    ));
  }

  // Back from the next page, a browser may show this one as it was left: sending, controls off.
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
      show(null);
      submit.disabled = false;
      input.disabled = false;
    }
  });
}());
