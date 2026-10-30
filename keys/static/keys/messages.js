// Shows a lawyer's messages: opens each text with the personal key (docs/m5-encryption-design.md
// §7.3). The additional data is rebuilt from what the page shows of the message (its clip, place in
// the thread, direction, kind and naming), as clips.vault.message_additional_data builds it, so a
// text moved to another message does not open there. Loaded after unlock.js.
(function () {
  'use strict';

  const texts = [...document.querySelectorAll('.message-text[data-sealed]')];
  if (!texts.length || !document.getElementById('unlock')) {
    return;
  }
  sodium.ready.then(async () => {
    const keys = ArchiveKeys;
    const personalKey = await ArchiveUnlock.personalKey();
    for (const element of texts) {
      const sealed = JSON.parse(element.dataset.sealed);
      const shown = element.dataset;
      const additionalData = sodium.from_string([
        'fa-message-v1', shown.clip, shown.id, shown.lawyer, shown.position, shown.direction, shown.kind, shown.identified,
      ].join(':'));
      const dataKey = keys.openSealed(keys.b64.decode(sealed.sealed_key), personalKey);
      const text = dataKey ? keys.openText(keys.b64.decode(sealed.ciphertext), dataKey, additionalData) : null;
      if (text === null) {
        element.textContent = shown.unreadable;
        continue;
      }
      // Line by line, as text: nothing in a message is ever read as HTML.
      element.replaceChildren(...text.split('\n').flatMap((line, index) => (
        index ? [document.createElement('br'), document.createTextNode(line)] : [document.createTextNode(line)]
      )));
    }
  });
}());
