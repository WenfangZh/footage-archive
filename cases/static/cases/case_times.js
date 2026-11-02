// A new case's times (SPEC.md §12 M8.7): a calendar beside each date's boxes, and the end
// filled in an hour after the start, until the lawyer gives an end of their own. Without this
// script the boxes work alone.
(function () {
  'use strict';

  const boxes = (id) => [0, 1, 2, 3].map((part) => document.getElementById(`${id}_${part}`));
  const twoDigits = (number) => String(number).padStart(2, '0');

  for (const calendar of document.querySelectorAll('input.calendar[data-for]')) {
    const [year, month, day] = boxes(calendar.dataset.for);
    const showDate = () => {
      calendar.value = year.value && month.value && day.value
        ? `${year.value.padStart(4, '0')}-${twoDigits(month.value)}-${twoDigits(day.value)}` : '';
    };
    calendar.addEventListener('change', () => {
      if (!calendar.value) return;
      const [picked, ...rest] = calendar.value.split('-');
      [year.value, month.value, day.value] = [picked, ...rest.map(Number)];
      year.dispatchEvent(new Event('input', { bubbles: true }));
    });
    for (const box of [year, month, day]) box.addEventListener('input', showDate);
    showDate();
    calendar.hidden = false;
  }

  const start = boxes('id_starts_at');
  const end = boxes('id_ends_at');
  if (start.includes(null) || end.includes(null)) return;
  // An end the lawyer typed, or one a refused form sends back, is theirs: never replaced. The
  // page opens with the same date in both, and no time.
  let endChosen = end[3].value !== '' || [0, 1, 2].some((part) => end[part].value !== start[part].value);
  // Set while this script fills the end itself, so that only someone else's input counts.
  let filling = false;
  const choose = () => { if (!filling) endChosen = true; };
  for (const box of end) box.addEventListener('input', choose);
  document.querySelector('input.calendar[data-for="id_ends_at"]')?.addEventListener('change', choose);

  const followStart = () => {
    const [year, month, day, time] = start.map((box) => box.value.trim());
    if (endChosen || !/^\d{4}$/.test(year) || !/^\d{1,2}$/.test(month) || !/^\d{1,2}$/.test(day) || !/^\d{1,2}:\d{2}$/.test(time)) return;
    const [hours, minutes] = time.split(':').map(Number);
    // Only a date that exists: the server says what is wrong with any other.
    const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
    if (date.getUTCMonth() + 1 !== Number(month) || date.getUTCDate() !== Number(day)) return;
    // An hour later on the clock, into the next day if need be.
    const later = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), hours + 1, minutes));
    filling = true;
    end[0].value = String(later.getUTCFullYear());
    end[1].value = String(later.getUTCMonth() + 1);
    end[2].value = String(later.getUTCDate());
    end[3].value = `${twoDigits(later.getUTCHours())}:${twoDigits(later.getUTCMinutes())}`;
    end[0].dispatchEvent(new Event('input', { bubbles: true }));
    filling = false;
  };
  for (const box of start) {
    box.addEventListener('input', followStart);
    box.addEventListener('change', followStart);
  }
})();
