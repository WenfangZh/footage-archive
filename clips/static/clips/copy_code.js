// The code card's Copy button (SPEC.md §12 M8.5): shown only where the browser can copy.
const line = document.getElementById('copy-code-line');
const button = document.getElementById('copy-code');
const status = document.getElementById('copy-code-status');
const code = document.querySelector('.card code');

if (navigator.clipboard) {
  line.hidden = false;
  button.addEventListener('click', () => {
    navigator.clipboard.writeText(code.textContent.trim()).then(() => {
      status.textContent = button.dataset.copied;
    });
  });
}
