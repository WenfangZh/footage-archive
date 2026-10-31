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

  function show(status) {
    for (const element of document.querySelectorAll('#upload-status [data-status]')) {
      element.hidden = element.dataset.status !== status;
    }
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
    event.preventDefault();
    const file = input.files[0];
    if (file === undefined || submit.disabled) {
      return;
    }
    submit.disabled = true;
    input.disabled = true;
    show('sending');
    if (upload !== null && upload.file === file) {
      upload.start(); // It asks the server how much arrived, and goes on from there.
      return;
    }
    upload = new tus.Upload(file, {
      endpoint: form.dataset.resumableUrl,
      chunkSize: PIECE_BYTES,
      // A phone's connection at a protest comes and goes: keep trying for about two minutes.
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000, 30000, 60000],
      headers: { 'X-CSRFToken': token },
      fingerprint,
      removeFingerprintOnSuccess: true,
      onProgress(sent, total) {
        progressLine.hidden = false;
        progress.max = total;
        progress.value = sent;
        percent.textContent = asPercent.format(sent / total);
      },
      onError(error) {
        const status = error.originalResponse ? error.originalResponse.getStatus() : 0;
        show({ 429: 'rate-limited', 503: 'closed' }[status] || 'stopped');
        submit.disabled = false;
        input.disabled = false;
      },
      onSuccess() {
        show('checking');
        // The quick read, then the form, as for a file sent in one piece.
        finishForm.action = new URL('finish/', upload.url).href;
        finishForm.submit();
      },
    });
    upload.findPreviousUploads().then((previous) => {
      if (previous.length) {
        upload.resumeFromPreviousUpload(previous[0]);
      }
      upload.start();
    });
  });
}());
