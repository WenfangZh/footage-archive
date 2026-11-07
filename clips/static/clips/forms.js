// Small conveniences for the upload forms. The server enforces the same rules without
// them; they only spare the uploader a long wait or a lost upload code.

// Say at once that a file is over the size cap, before anything is sent (SPEC.md §8).
const fileInput = document.querySelector('input[type=file][data-max-bytes]');
if (fileInput) {
  const message = document.getElementById(fileInput.dataset.tooLargeMessage);
  const submit = fileInput.form.querySelector('[type=submit]');
  fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    const tooLarge = file !== undefined && file.size > Number(fileInput.dataset.maxBytes);
    message.hidden = !tooLarge;
    submit.disabled = tooLarge;
  });
}

// Send a form only once. A second click on "confirm" would replace the page showing
// the upload code, which can never be shown again, with one that cannot show it. A place
// lookup's buttons save nothing, and the map's script may answer them in the page: they leave
// the form free to be sent (maps/place_lookup.html), but never go once it is.
for (const form of document.querySelectorAll('form[data-submit-once]')) {
  form.addEventListener('submit', (event) => {
    if (event.submitter?.hasAttribute('data-lookup')) {
      if (form.dataset.sent) event.preventDefault();
      return;
    }
    if (form.dataset.sent) {
      event.preventDefault();
    }
    form.dataset.sent = 'yes';
  });
}
