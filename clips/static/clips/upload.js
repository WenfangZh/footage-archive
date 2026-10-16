// Upload step 1: say at once that a file is over the size cap, before anything is
// sent. The server enforces the cap anyway (SPEC.md §8, clips/uploads.py); this only
// spares the uploader a long wait for a refusal.
const input = document.querySelector('input[type=file][data-max-bytes]');
if (input) {
  const message = document.getElementById(input.dataset.tooLargeMessage);
  const submit = input.form.querySelector('[type=submit]');
  input.addEventListener('change', () => {
    const file = input.files[0];
    const tooLarge = file !== undefined && file.size > Number(input.dataset.maxBytes);
    message.hidden = !tooLarge;
    submit.disabled = tooLarge;
  });
}
