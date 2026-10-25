// Takes part in a rotation of the group key when the server drew this lawyer for a role in it
// (docs/m5-encryption-design.md §5.5). The rotator's browser makes the new group key, seals it
// to every lawyer holding the old one, and reseals each data key to it; a checker's or
// confirmer's browser checks that work and reports the result. Loaded after unlock.js on the
// pages that unlock the personal key, it asks for the key passphrase only when it has
// something to do. The server keeps where a rotation stands, so a tab closed halfway loses
// nothing: the next page carries on.
(function () {
  'use strict';

  const status = document.getElementById('rotation');
  if (!status) {
    return;
  }
  const keys = ArchiveKeys;
  const urls = status.dataset;

  // The server answers 409 when the rotation moved on meanwhile: another attempt, a list of
  // lawyers that changed, or a role given to someone else.
  class MovedOn extends Error {}

  function show(id) {
    for (const message of status.children) {
      message.hidden = message.id !== id;
    }
  }

  async function fetched(response) {
    if (response.status === 409) {
      throw new MovedOn();
    }
    if (!response.ok) {
      throw new Error(`The rotation request failed: ${response.status}.`);
    }
    return response;
  }

  async function get(url) {
    return (await fetched(await fetch(url, { credentials: 'same-origin' }))).json();
  }

  async function post(url, fields) {
    return fetched(await ArchiveUnlock.post(url, fields));
  }

  // What the request gives, or null if the rotation moved on.
  async function unlessMovedOn(request) {
    try {
      return await request;
    } catch (error) {
      if (error instanceof MovedOn) {
        return null;
      }
      throw error;
    }
  }

  const decode = keys.b64.decode;
  const encode = keys.b64.encode;

  // Step 2: a new group key pair, its private half sealed to each lawyer key the server lists.
  // Returns the new key pair, or null if the list changed meanwhile (then the role is fetched
  // again).
  async function makeNewKey(role) {
    const pair = sodium.crypto_box_keypair();
    const grants = {};
    for (const recipient of role.seal_to) {
      grants[recipient.lawyer_key] = encode(keys.seal(pair.privateKey, decode(recipient.public_key)));
    }
    const made = await unlessMovedOn(post(urls.newKey, { public_key: encode(pair.publicKey), grants: JSON.stringify(grants) }));
    return made && pair;
  }

  // Step 3: every data key still sealed only to the old group key, in batches: opened, checked
  // against its key check value, and sealed to the new one; one that does not open, or is not
  // its item's key, is reported instead. The server refuses batches once resealing is over:
  // when the last data key is in, or if the attempt ended meanwhile.
  async function reseal(oldPrivateKey, newPublicKey) {
    let after = 0;
    for (;;) {
      const batch = await unlessMovedOn(get(`${urls.reseal}?after=${after}`));
      if (!batch) {
        return;
      }
      if (!batch.items.length) {
        // Nothing left, yet resealing is not over: this report tells the server, which draws
        // the checkers now rather than at its next hourly round.
        await unlessMovedOn(post(urls.unopenable, { ids: '[]' }));
        return;
      }
      const resealed = {};
      const unopenable = [];
      for (const item of batch.items) {
        const dataKey = keys.openDataKey(decode(item.sealed_data_key), oldPrivateKey, decode(item.key_check));
        if (dataKey) {
          resealed[item.id] = encode(keys.seal(dataKey, newPublicKey));
        } else {
          unopenable.push(item.id);
        }
        after = item.id;
      }
      if (unopenable.length) {
        await post(urls.unopenable, { ids: JSON.stringify(unopenable) });
      }
      if (Object.keys(resealed).length) {
        await post(urls.resealed, { resealed: JSON.stringify(resealed) });
      }
    }
  }

  async function rotate(role, groupKeys) {
    const oldPrivateKey = groupKeys.get(role.old_public_key);
    let newPublicKey;
    if (role.stage === 'new_key') {
      // Without the old key, nothing could be resealed: no new key either.
      if (!oldPrivateKey) {
        throw new Error('The old group key did not open.');
      }
      const pair = await makeNewKey(role);
      if (!pair) {
        return false;
      }
      newPublicKey = pair.publicKey;
    } else {
      // Resealed only to a key this lawyer holds a grant on. That does not prove it is the key
      // their browser made at step 2 (anyone can seal a grant to their public key), but a
      // database writer could as well add a lawyer key of their own to the list at step 2
      // (design §11).
      if (!oldPrivateKey || !groupKeys.has(role.new_public_key)) {
        throw new Error('The old or the new group key did not open.');
      }
      newPublicKey = decode(role.new_public_key);
    }
    show('rotation-resealing');
    await reseal(oldPrivateKey, newPublicKey);
    show('rotation-resealed');
    return true;
  }

  // Steps 5 and 6: the lawyer's own grant on the new key, each data key the rotator resealed,
  // and each one the rotator said they could not open, which must not open with the old key
  // either. No content is fetched, only sealed keys and key check values.
  async function check(role, groupKeys) {
    show('rotation-checking');
    const newPrivateKey = groupKeys.get(role.new_public_key);
    const oldPrivateKey = groupKeys.get(role.old_public_key);
    const failed = [];
    if (newPrivateKey) {
      const toCheck = await get(urls.toCheck);
      // A claim that a data key does not open can be checked only with the old key: without it,
      // no result, rather than a pass that would let those items be lost unchecked. The slot is
      // drawn again in time.
      if (toCheck.unopenable.length && !oldPrivateKey) {
        throw new Error('The old group key did not open.');
      }
      for (const item of toCheck.items) {
        if (!keys.openDataKey(decode(item.sealed_data_key), newPrivateKey, decode(item.key_check))) {
          failed.push(item.id);
        }
      }
      for (const item of toCheck.unopenable) {
        if (keys.openDataKey(decode(item.sealed_data_key), oldPrivateKey, decode(item.key_check))) {
          failed.push(item.id);
        }
      }
    }
    const passed = Boolean(newPrivateKey) && !failed.length;
    // Naming the key checked: the server refuses a result meant for another attempt's.
    await post(urls.checked, {
      new_public_key: role.new_public_key, result: passed ? 'passed' : 'failed', failed_items: JSON.stringify(failed),
    });
    show('rotation-checked');
    return true;
  }

  sodium.ready.then(async () => {
    try {
      // A lawyer may hold a role only while they hold the group key: anyone else gets a 404,
      // and nothing to do.
      const response = await fetch(urls.role, { credentials: 'same-origin' });
      if (response.status === 404) {
        return;
      }
      const shown = await (await fetched(response)).json();
      if (!shown.role) {
        return;
      }
      show(shown.role === 'rotator' ? 'rotation-rotator' : 'rotation-checking');
      const groupKeys = await ArchiveUnlock.groupKeys(await ArchiveUnlock.personalKey());
      // At most a few times round: the server's list of lawyer keys may change while the new
      // key is made. The role is fetched again once the key is unlocked, which may be long
      // after the page was shown.
      for (let round = 0; round < 3; round += 1) {
        const role = await get(urls.role);
        if (!role.role) {
          show('');
          return;
        }
        const done = role.role === 'rotator' ? await rotate(role, groupKeys) : await check(role, groupKeys);
        if (done) {
          return;
        }
      }
      show('rotation-failed');
    } catch (error) {
      // The attempt moved on in the meantime: nothing more for this page.
      show(error instanceof MovedOn ? '' : 'rotation-failed');
    }
  });
}());
