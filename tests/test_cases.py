import datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from accounts.models import LawyerProfile
from cases.models import Case, CaseCell
from cases.size import CaseTooLarge
from clips import grid
from tests.conftest import BASTILLE, make_case

pytestmark = pytest.mark.django_db

CENTRE = grid.cell_for_point(*BASTILLE, 11)


@pytest.fixture
def lawyer():
    user = get_user_model().objects.create_user('lawyer@example.org')
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return profile


def test_a_case_keeps_its_time_window_area_and_margin(lawyer):
    case = make_case(lawyer, rings=1, margin_minutes=15, margin_rings=2)
    stored = Case.objects.get(pk=case.pk)
    assert stored.ends_at - stored.starts_at == datetime.timedelta(hours=1)
    assert (stored.margin_minutes, stored.margin_rings) == (15, 2)
    assert stored.area() == grid.widen([CENTRE], 1) and len(stored.area()) == 7


@pytest.mark.parametrize('cells', [
    [],
    [grid.coarsen(CENTRE, 10)],
    [CENTRE, 12345],
])
def test_an_area_is_a_set_of_the_grid_s_finest_cells(lawyer, cells):
    with pytest.raises(ValueError, match='finest cells'):
        make_case(lawyer, cells=cells)
    assert not Case.objects.exists()


def test_a_time_window_ends_after_it_starts(lawyer):
    now = datetime.datetime.now(datetime.UTC)
    with pytest.raises(ValueError, match='ends after it starts'):
        make_case(lawyer, starts_at=now, ends_at=now)
    with pytest.raises(IntegrityError), transaction.atomic():
        # Past create_with_area's own checks, to reach the database's.
        Case(
            lawyer_profile=lawyer, label='A case', starts_at=now, ends_at=now, margin_minutes=0, margin_rings=0,
        ).save(_checked=True)


def test_a_time_window_has_time_zones(lawyer):
    naive = datetime.datetime(2026, 6, 15, 18)
    with pytest.raises(ValueError, match='time zones'):
        make_case(lawyer, starts_at=naive, ends_at=naive + datetime.timedelta(hours=1))


@pytest.mark.parametrize('margin', [{'margin_minutes': -1}, {'margin_rings': -1}, {'margin_minutes': 1.5}, {'margin_rings': True}])
def test_a_margin_is_a_whole_number_zero_or_more(lawyer, margin):
    with pytest.raises(ValueError, match='whole number'):
        make_case(lawyer, **margin)
    assert not Case.objects.exists()


def test_a_case_is_made_only_with_its_size_checked(lawyer):
    now = datetime.datetime.now(datetime.UTC)
    fields = {
        'lawyer_profile': lawyer, 'label': 'A case', 'starts_at': now - datetime.timedelta(days=300), 'ends_at': now,
        'margin_minutes': 0, 'margin_rings': 0,
    }
    with pytest.raises(ValueError, match='create_with_area'):
        Case.objects.create(**fields)
    with pytest.raises(ValueError, match='create_with_area'):
        Case.objects.bulk_create([Case(**fields)])
    with pytest.raises(ValueError, match='create_with_area'):
        Case(**fields).save()
    assert not Case.objects.exists()


def window(minutes):
    starts_at = datetime.datetime(2026, 6, 15, 18, tzinfo=datetime.UTC)
    return {'starts_at': starts_at, 'ends_at': starts_at + datetime.timedelta(minutes=minutes)}


def test_the_window_cap_counts_the_margin_at_both_ends(lawyer):
    make_case(lawyer, **window(100), margin_minutes=10)
    with pytest.raises(CaseTooLarge) as refused:
        make_case(lawyer, **window(101), margin_minutes=10)
    assert not refused.value.beyond_ceiling
    make_case(lawyer, **window(120), margin_minutes=0)


def test_the_area_cap_counts_the_margin(lawyer):
    # Rings of finest cells about 50 m apart: 19 rings in all reach about 1.9 km across.
    fits = make_case(lawyer, rings=18, margin_rings=1)
    with pytest.raises(CaseTooLarge) as refused:
        make_case(lawyer, rings=19, margin_rings=1)
    assert not refused.value.beyond_ceiling and refused.value.area_metres > 2_000
    also_fits = make_case(lawyer, rings=19, margin_rings=0)
    assert Case.objects.count() == 2
    assert (fits.area(), also_fits.area()) == (grid.widen([CENTRE], 18), grid.widen([CENTRE], 19))


def test_beyond_the_ceiling_is_said_apart_from_over_the_caps(lawyer):
    with pytest.raises(CaseTooLarge) as refused:
        make_case(lawyer, **window(24 * 60 - 19), margin_minutes=10)
    assert refused.value.beyond_ceiling
    with pytest.raises(CaseTooLarge) as refused:
        make_case(lawyer, **window(24 * 60 - 20), margin_minutes=10)
    assert not refused.value.beyond_ceiling
    # 100 rings in all reach about 9.9 km across, 101 about 10 km.
    with pytest.raises(CaseTooLarge) as refused:
        make_case(lawyer, rings=99, margin_rings=2)
    assert refused.value.beyond_ceiling
    with pytest.raises(CaseTooLarge) as refused:
        make_case(lawyer, rings=99, margin_rings=1)
    assert not refused.value.beyond_ceiling


def test_a_huge_margin_is_refused_without_widening_the_area(lawyer):
    with pytest.raises(CaseTooLarge) as refused:
        make_case(lawyer, margin_rings=30_000)
    assert refused.value.beyond_ceiling


def test_lower_caps_refuse_smaller_cases(lawyer, settings):
    settings.CASE_WINDOW_CAP_MINUTES, settings.CASE_AREA_CAP_METRES = 60, 500
    with pytest.raises(CaseTooLarge):
        make_case(lawyer, **window(60), margin_minutes=1)
    with pytest.raises(CaseTooLarge):
        make_case(lawyer, **window(30), rings=5)
    make_case(lawyer, **window(30), rings=3)


def test_a_case_s_lawyer_window_and_margin_never_change(lawyer):
    case = make_case(lawyer)
    other = get_user_model().objects.create_user('other@example.org')
    other_profile = LawyerProfile.objects.create(user=other, bar='Lyon')
    for field, value in [
        ('lawyer_profile_id', other_profile.pk),
        ('starts_at', case.starts_at - datetime.timedelta(minutes=1)),
        ('ends_at', case.ends_at + datetime.timedelta(minutes=1)),
        ('margin_minutes', 11),
        ('margin_rings', 2),
    ]:
        changed = Case.objects.get(pk=case.pk)
        setattr(changed, field, value)
        with pytest.raises(ValueError, match='never change'):
            changed.save()
    Case.objects.get(pk=case.pk).save()
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    with pytest.raises(ValueError, match='closed case'):
        Case.objects.get(pk=case.pk).save()


def test_a_case_s_area_never_changes(lawyer):
    case = make_case(lawyer)
    cell = case.cells.first()
    cell.cell = grid.cell_for_point(0, 0, 11)
    with pytest.raises(ValueError):
        cell.save()
    with pytest.raises(ValueError):
        cell.delete()
    with pytest.raises(ValueError):
        case.cells.update(cell=1)
    with pytest.raises(ValueError):
        case.cells.all().delete()
    far = grid.cell_for_point(BASTILLE[0] + 0.1, BASTILLE[1], 11)
    with pytest.raises(ValueError, match='only with the case'):
        CaseCell.objects.create(case=case, cell=far)
    with pytest.raises(ValueError, match='only with the case'):
        CaseCell.objects.bulk_create([CaseCell(case=case, cell=far)])
    assert case.area() == grid.widen([CENTRE], 2)
