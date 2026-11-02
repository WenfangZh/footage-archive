// The code card's Copy button (SPEC.md §12 M8.5): shown only where the browser can copy.
const line = document.getElementById('copy-code-line');
const button = document.getElementById('copy-code');
const status = document.getElementById('copy-code-status');
const code = document.querySelector('.card code');

if (navigator.clipboard) {
  line.hidden = false;
  button.addEventListener('click', () => {
    // Emptied first, so that a second click is announced again.
    status.textContent = '';
    navigator.clipboard.writeText(code.textContent.trim()).then(
      () => { status.textContent = button.dataset.copied; },
      // The code is shown only once: never leave a refusal unsaid.
      () => { status.textContent = button.dataset.failed; },
    );
  });
}
