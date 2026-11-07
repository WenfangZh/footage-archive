// The place lookup with JavaScript (SPEC.md §12 M9, ADR 0011), shared by the stated location's
// map (clips/static/clips/location_map.js) and the case map (area_map.js). "Look up", or Enter in
// any field of the form, asks the map's lookup route, with only what is typed, in the request's
// body; the matches are listed in the page, and the map's own script decides what choosing one
// does. Should the route refuse or be out of reach, the form asks its own page instead
// (maps.form_lookup), which answers without JavaScript.

// Sets the lookup up on `form`, and returns `tell(key)`, which shows one of the box's messages
// (its data attributes); null when the page has no lookup (no data downloaded).
export function setUpPlaceLookup(form, choose) {
  const lookup = document.getElementById('place-lookup');
  const query = form.elements.place;
  if (!lookup?.dataset.url || !query) return null;
  const result = document.getElementById('place-lookup-result');
  let askPage = false;
  // Each lookup and each choice counts one: an answer overtaken by either is not shown.
  let latest = 0;

  const tell = (key) => {
    const line = document.createElement('p');
    line.textContent = lookup.dataset[key];
    result.replaceChildren(line);
  };

  function list(matches) {
    const choices = Object.assign(document.createElement('ul'), { className: 'place-matches' });
    choices.append(...matches.map((match) => {
      const button = Object.assign(document.createElement('button'), { type: 'button', textContent: match.label });
      button.addEventListener('click', () => {
        latest += 1;
        choose(match);
      });
      const item = document.createElement('li');
      item.append(button);
      return item;
    }));
    tell('choose');
    result.append(choices);
  }

  form.addEventListener('submit', async (event) => {
    if (event.submitter?.name !== 'lookup' || askPage) return;
    event.preventDefault();
    const typedQuery = query.value.trim();
    if (!typedQuery) {
      tell('empty');
      return;
    }
    const asked = ++latest;
    const button = event.submitter;
    button.disabled = true;
    let answer = null;
    try {
      const response = await fetch(lookup.dataset.url, {
        method: 'POST',
        body: new URLSearchParams({ place: typedQuery, csrfmiddlewaretoken: form.elements.csrfmiddlewaretoken.value }),
      });
      // A 404 is a map pass refused (maps.views.pass_required): the page has its own lookup.
      if (response.status !== 404) answer = await response.json();
    } catch (error) {
      console.error(error);
    }
    button.disabled = false;
    if (asked !== latest) return;
    if (answer === null) {
      askPage = true;
      form.requestSubmit(button);
    } else if (answer.matches?.length) {
      list(answer.matches);
    } else {
      const problems = { too_long: 'tooLong', too_many: 'tooMany', unavailable: 'unavailable' };
      tell(problems[answer.error] ?? 'noneFound');
    }
  });
  return tell;
}
