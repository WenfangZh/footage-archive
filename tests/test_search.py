"""Matching and search (SPEC.md §7): the clips a case finds, and only those."""

import datetime

import h3.api.basic_int as h3
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases.models import Case
from cases.search import find, match_of
from clips import grid
from clips.models import Clip, Precision
from tests.conftest import BASTILLE, log_in_with_second_factor, make_case

pytestmark = pytest.mark.django_db

User = get_user_model()
CENTRE = grid.cell_for_point(*BASTILLE, 11)
START = datetime.datetime(2026, 6, 15, 16, tzinfo=datetime.UTC)
WINDOW = {'starts_at': START, 'ends_at': START + datetime.timedelta(hours=1)}
codes = iter(range(10**6))


def make_lawyer(email):
    profile = LawyerProfile.objects.create(user=User.objects.create_user(email), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return profile


@pytest.fixture
def lawyer():
    return make_lawyer('lawyer@example.org')


@pytest.fixture
def case(lawyer):
    # Two rings of area around the centre, one ring and ten minutes of margin.
    return make_case(lawyer, rings=2, margin_rings=1, margin_minutes=10, **WINDOW)


def ring_cell(rings):
    return h3.grid_ring(CENTRE, rings)[0]


def make_clip(minutes=10, seconds=60, cell=CENTRE, precision=Precision.EXACT_SPOT, **fields):
    """A ready clip stated `minutes` after the window starts, lasting `seconds`, in `cell`."""
    values = {
        'code_hash': f'{next(codes):064d}', 'status': Clip.Status.READY,
        'stated_time': START + datetime.timedelta(minutes=minutes),
        'duration': datetime.timedelta(seconds=seconds) if seconds is not None else None,
        'stated_cell': cell, 'stated_precision': precision if cell else '',
    }
    return Clip.objects.create(**{**values, **fields})


def test_a_clip_in_the_window_and_area_is_a_core_match(case):
    clip = make_clip()
    assert find(case) == ([clip], []) and match_of(case, clip) == 'core'


def test_a_clip_in_the_margin_ring_is_a_margin_match(case):
    clip = make_clip(cell=ring_cell(3))
    assert find(case) == ([], [clip]) and match_of(case, clip) == 'margin'


def test_a_clip_beyond_the_margin_ring_does_not_match(case):
    clip = make_clip(cell=ring_cell(4))
    assert find(case) == ([], []) and match_of(case, clip) is None


@pytest.mark.parametrize('minutes, seconds, expected', [
    (65, 60, 'margin'),     # starts 5 minutes after the window
    (71, 60, None),         # starts 11 minutes after: past the margin
    (-5, 60, 'margin'),     # ends 4 minutes before the window
    (-12, 60, None),        # ends 11 minutes before it
    (-12, 180, 'margin'),   # ends 9 minutes before: in the margin
    (-30, 31 * 60, 'core'), # starts before the window, runs into it
    (59, None, 'core'),     # no known duration: its stated time alone
    (60, 0, 'core'),        # at the window's last moment
    (70, 0, 'margin'),      # at the margin's last moment
    (-11, 60, 'margin'),    # ends at the margin's first moment
    (-1, 60, 'core'),       # ends as the window starts
])
def test_any_part_of_the_clip_counts_by_its_stated_time_and_duration(case, minutes, seconds, expected):
    clip = make_clip(minutes=minutes, seconds=seconds)
    assert match_of(case, clip) == expected


def test_a_clip_in_the_margin_of_both_time_and_place_is_a_margin_match(case):
    clip = make_clip(minutes=65, cell=ring_cell(3))
    assert match_of(case, clip) == 'margin'


def test_a_coarse_clip_matches_any_area_inside_its_cell(case):
    coarse = make_clip(cell=grid.coarsen(CENTRE, 9), precision=Precision.NEIGHBOURHOOD)
    elsewhere = make_clip(cell=grid.coarsen(grid.cell_for_point(BASTILLE[0] + 0.1, BASTILLE[1], 11), 9),
                          precision=Precision.NEIGHBOURHOOD)
    assert find(case) == ([coarse], [])
    assert match_of(case, elsewhere) is None


def test_a_coarse_clip_touching_only_the_margin_is_a_margin_match(lawyer):
    # A case of one cell, whose margin ring crosses into a neighbouring coarse cell.
    margin_cell = next(cell for cell in h3.grid_ring(CENTRE, 1) if grid.coarsen(cell, 10) != grid.coarsen(CENTRE, 10))
    case = make_case(lawyer, cells={CENTRE}, margin_rings=1, margin_minutes=0, **WINDOW)
    clip = make_clip(cell=grid.coarsen(margin_cell, 10), precision=Precision.STREET)
    assert match_of(case, clip) == 'margin'


@pytest.mark.parametrize('fields', [
    {'status': Clip.Status.PROCESSING},
    {'status': Clip.Status.FAILED},
    {'stated_time': None},
    {'stated_cell': None, 'stated_precision': ''},
])
def test_only_ready_clips_with_a_stated_time_and_place_can_match(case, fields):
    make_clip(**fields)
    assert find(case) == ([], [])


def test_the_metadata_time_and_place_are_never_used(case):
    far = grid.cell_for_point(BASTILLE[0] + 0.1, BASTILLE[1], 11)
    make_clip(minutes=24 * 60, metadata_time=START + datetime.timedelta(minutes=10), metadata_cell=CENTRE)
    make_clip(cell=far, metadata_cell=CENTRE, metadata_time=START)
    assert find(case) == ([], [])


def test_matches_are_in_order_of_stated_time(case):
    later, earlier = make_clip(minutes=40), make_clip(minutes=20)
    margin_later, margin_earlier = make_clip(minutes=68), make_clip(minutes=-3)
    assert find(case) == ([earlier, later], [margin_earlier, margin_later])


def test_a_clip_finer_than_a_coarser_grid_s_case_still_matches(lawyer, settings):
    # A case made after the grid was made coarser (ADR 0003), and a clip from before.
    settings.GRID_H3_RESOLUTION = 10
    case = make_case(lawyer, cells={grid.coarsen(CENTRE, 10)}, margin_rings=1, margin_minutes=0, **WINDOW)
    inside, neighbour = make_clip(cell=CENTRE), make_clip(cell=ring_cell(30))
    margin_clip = make_clip(cell=h3.cell_to_center_child(h3.grid_ring(grid.coarsen(CENTRE, 10), 1)[0], 11))
    assert find(case) == ([inside], [margin_clip])
    assert match_of(case, neighbour) is None


# The search page.

@pytest.fixture
def lawyer_client(lawyer):
    return log_in_with_second_factor(Client(), lawyer.user)


def search(client, case):
    return client.post(reverse('lawyer:search', kwargs={'case_id': case.pk}))


def test_a_search_is_never_run_by_a_link(case, lawyer_client):
    assert lawyer_client.get(reverse('lawyer:search', kwargs={'case_id': case.pk})).status_code == 405
    assert not AuditEntry.objects.filter(action=Action.SEARCH).exists()


def test_a_search_shows_the_matches_and_logs_their_numbers(case, lawyer_client):
    make_clip(), make_clip(minutes=20), make_clip(cell=ring_cell(3)), make_clip(cell=ring_cell(5))
    response = search(lawyer_client, case)
    assert response.status_code == 200
    assert (len(response.context['core']), len(response.context['margin'])) == (2, 1)
    entry = AuditEntry.objects.get(action=Action.SEARCH)
    assert (entry.case, entry.lawyer_profile, entry.core_count, entry.margin_count) == (case, case.lawyer_profile, 2, 1)


def test_each_search_is_logged_even_when_it_finds_nothing(case, lawyer_client):
    search(lawyer_client, case)
    search(lawyer_client, case)
    entries = AuditEntry.objects.filter(action=Action.SEARCH)
    assert list(entries.values_list('core_count', 'margin_count')) == [(0, 0), (0, 0)]


def test_an_approved_larger_case_can_search(lawyer, lawyer_client):
    admin = User.objects.create_superuser('admin@example.org')
    larger = make_case(lawyer, starts_at=START, ends_at=START + datetime.timedelta(hours=3), approval_reason='Two charges.')
    larger.approval.decide(True, admin)
    make_clip(minutes=150)
    response = search(lawyer_client, larger)
    assert response.status_code == 200 and len(response.context['core']) == 1


def test_the_results_show_only_time_duration_precision_and_a_link(case, lawyer_client):
    clip = make_clip(metadata_time=START - datetime.timedelta(hours=5), metadata_cell=CENTRE)
    page = search(lawyer_client, case).content.decode()
    assert clip.code_hash not in page and '13:00' not in page
    assert reverse('lawyer:clip', kwargs={'case_id': case.pk, 'clip_id': clip.pk}) in page


def test_a_closed_case_allows_no_search(case, lawyer_client):
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    assert search(lawyer_client, case).status_code == 404
    assert not AuditEntry.objects.filter(action=Action.SEARCH).exists()


def test_a_case_waiting_for_approval_allows_no_search(lawyer, lawyer_client):
    waiting = make_case(lawyer, starts_at=START, ends_at=START + datetime.timedelta(hours=3), approval_reason='Two charges.')
    assert search(lawyer_client, waiting).status_code == 404


def test_a_lawyer_cannot_search_another_lawyer_s_case(case):
    other = log_in_with_second_factor(Client(), make_lawyer('other@example.org').user)
    assert search(other, case).status_code == 404
    assert not AuditEntry.objects.filter(action=Action.SEARCH).exists()


def test_one_clip_s_match_is_the_same_as_the_search_s(lawyer):
    """match_of() checks a clip from its own side: it must agree with find() everywhere."""
    import random
    rng = random.Random(7)
    case = make_case(lawyer, rings=3, margin_rings=2, margin_minutes=10, **WINDOW)
    clips = []
    for _ in range(150):
        cell = rng.choice(list(h3.grid_disk(CENTRE, 8)))
        precision, resolution = rng.choice([(Precision.EXACT_SPOT, 11), (Precision.STREET, 10), (Precision.NEIGHBOURHOOD, 9)])
        clips.append(make_clip(minutes=rng.randint(-40, 100), seconds=rng.choice([None, 0, 30, 900]),
                               cell=grid.coarsen(cell, resolution), precision=precision))
    found = find(case)
    expected = {clip.pk: 'core' for clip in found.core} | {clip.pk: 'margin' for clip in found.margin}
    assert {clip.pk: match_of(case, clip) for clip in clips} == {clip.pk: expected.get(clip.pk) for clip in clips}
    assert found.core and found.margin and len(expected) < len(clips)
