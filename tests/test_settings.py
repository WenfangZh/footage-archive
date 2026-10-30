import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection

from config.settings import env_bool, env_colour, env_int, env_list

BASE_DIR = Path(__file__).resolve().parent.parent


def test_system_checks_pass():
    call_command('check', fail_level='WARNING')


@pytest.mark.django_db
def test_django_admin_site_is_not_installed(client):
    # It can show and change any table; the admin manages accounts, not content (SPEC.md §10).
    assert not apps.is_installed('django.contrib.admin')
    assert client.get('/admin/').status_code == 404


@pytest.mark.django_db
def test_every_model_change_has_a_migration():
    call_command('makemigrations', '--check', '--dry-run', verbosity=0)


@pytest.mark.django_db
def test_sqlite_zeroes_deleted_data():
    # Ubuntu's SQLite is built with this on, so also check that we ask for it: other
    # builds default to off.
    assert 'PRAGMA secure_delete = ON' in settings.DATABASES['default']['OPTIONS']['init_command']
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA secure_delete')
        assert cursor.fetchone() == (1,)


def load_settings(**variables):
    """Import the real settings in a fresh Python with these environment variables set."""
    return subprocess.run(
        [sys.executable, '-c', 'import config.settings'],
        cwd=BASE_DIR, env={**os.environ, **variables}, capture_output=True, text=True,
    )


@pytest.mark.parametrize('name', ['DJANGO_SECRET_KEY', 'UPLOAD_CODE_KEY'])
def test_missing_key_stops_startup(name):
    # An empty value counts as missing, and stops a .env file from filling it in.
    result = load_settings(**{name: ''})
    assert result.returncode != 0
    assert f'Set the {name} environment variable' in result.stderr


def test_short_upload_code_key_stops_startup():
    result = load_settings(UPLOAD_CODE_KEY='too-short')
    assert result.returncode != 0
    assert 'UPLOAD_CODE_KEY must be at least 32 characters' in result.stderr


@pytest.mark.parametrize('value', ['', 'http://archive.example.org'])
def test_an_archive_address_other_than_https_stops_startup_outside_development(value):
    # Notification emails link to it (SPEC.md §7).
    result = load_settings(ARCHIVE_URL=value, DJANGO_DEBUG='false')
    assert result.returncode != 0
    assert 'Set ARCHIVE_URL' in result.stderr


def test_grid_finer_than_resolution_11_stops_startup():
    result = load_settings(GRID_H3_RESOLUTION='12')
    assert result.returncode != 0
    assert 'GRID_H3_RESOLUTION must be at most 11' in result.stderr


@pytest.mark.parametrize('name, value', [('CASE_WINDOW_CAP_MINUTES', '1441'), ('CASE_AREA_CAP_METRES', '10001')])
def test_case_caps_beyond_the_ceiling_stop_startup(name, value):
    result = load_settings(**{name: value})
    assert result.returncode != 0
    assert f'{name} must be at most {int(value) - 1}' in result.stderr


def test_case_caps_default_to_2_hours_and_2_km_under_a_ceiling_of_24_hours_and_10_km():
    assert (settings.CASE_WINDOW_CAP_MINUTES, settings.CASE_AREA_CAP_METRES) == (120, 2_000)
    assert (settings.CASE_WINDOW_CEILING_MINUTES, settings.CASE_AREA_CEILING_METRES) == (1_440, 10_000)


def test_an_inactivity_period_past_a_century_stops_startup():
    result = load_settings(CASE_INACTIVITY_DAYS='36501')
    assert result.returncode != 0
    assert 'CASE_INACTIVITY_DAYS must be at most 36500' in result.stderr


@pytest.mark.parametrize('name, value, most', [
    ('ALERT_RAISE_MAX_DAYS', '31', 30), ('ALERT_DOWNLOADS_MINUTES', '10081', 10080),
    ('ALERT_CASES_HOURS', '8761', 8760), ('ALERT_DOWNLOADS', '100001', 100000),
])
def test_absurd_alert_limits_stop_startup(name, value, most):
    result = load_settings(**{name: value})
    assert result.returncode != 0
    assert f'{name} must be at most {most}' in result.stderr


def test_upload_defaults():
    assert settings.UPLOAD_MAX_BYTES == 2_000_000_000
    assert settings.UNCONFIRMED_ORIGINAL_MINUTES == 120
    assert settings.GRID_H3_RESOLUTION == 11
    assert settings.INGEST_ATTEMPT_MINUTES == 10


def test_quarantine_is_private_and_holds_incoming_files():
    for directory in (settings.QUARANTINE_DIR, settings.FILE_UPLOAD_TEMP_DIR):
        assert directory.is_dir()
        assert directory.stat().st_mode & 0o777 == 0o700
    assert settings.FILE_UPLOAD_TEMP_DIR.parent == settings.QUARANTINE_DIR
    assert settings.FILE_UPLOAD_PERMISSIONS == 0o600
    assert settings.FILE_UPLOAD_DIRECTORY_PERMISSIONS == 0o700


def test_the_vault_folder_is_private_and_apart_from_quarantine():
    assert settings.VAULT_DIR.is_dir()
    assert settings.VAULT_DIR.stat().st_mode & 0o777 == 0o700
    assert not settings.VAULT_DIR.is_relative_to(settings.QUARANTINE_DIR)


def test_tests_never_use_the_developer_data_folder():
    assert not settings.DATA_DIR.is_relative_to(BASE_DIR)


def test_new_passwords_are_hashed_with_argon2():
    auth_user = get_user_model()(email='someone@example.org')
    auth_user.set_password('a long test passphrase')
    assert auth_user.password.startswith('argon2$')
    assert check_password('a long test passphrase', auth_user.password)


@pytest.mark.parametrize('value, expected', [
    ('true', True), ('1', True), ('YES', True), ('on', True),
    ('false', False), ('0', False), ('no', False), ('', False),
])
def test_env_bool(monkeypatch, value, expected):
    monkeypatch.setenv('SOME_FLAG', value)
    assert env_bool('SOME_FLAG') is expected


def test_env_bool_rejects_typos(monkeypatch):
    monkeypatch.setenv('SOME_FLAG', 'ture')
    with pytest.raises(ImproperlyConfigured):
        env_bool('SOME_FLAG')


def test_env_list(monkeypatch):
    monkeypatch.setenv('SOME_LIST', ' a.example, ,b.example ')
    assert env_list('SOME_LIST') == ['a.example', 'b.example']


@pytest.mark.parametrize('value, expected', [('', 7), ('42', 42), ('0', 0)])
def test_env_int(monkeypatch, value, expected):
    monkeypatch.setenv('SOME_NUMBER', value)
    assert env_int('SOME_NUMBER', default=7) == expected


@pytest.mark.parametrize('value', ['2GB', '1.5', 'eleven'])
def test_env_int_rejects_non_numbers(monkeypatch, value):
    monkeypatch.setenv('SOME_NUMBER', value)
    with pytest.raises(ImproperlyConfigured, match='whole number'):
        env_int('SOME_NUMBER', default=7)


@pytest.mark.parametrize('value, message', [('0', 'at least 1'), ('12', 'at most 11')])
def test_env_int_enforces_bounds(monkeypatch, value, message):
    monkeypatch.setenv('SOME_NUMBER', value)
    with pytest.raises(ImproperlyConfigured, match=message):
        env_int('SOME_NUMBER', default=7, minimum=1, maximum=11)


def variables_read_by_settings():
    source = (BASE_DIR / 'config' / 'settings.py').read_text()
    # Every env_* helper, so that a new one is not missed.
    pattern = r"(?:env_[a-z]+|os\.environ\.get|os\.getenv)\(\s*'([A-Z0-9_]+)'"
    return set(re.findall(pattern, source))


def variables_in_env_example():
    lines = (BASE_DIR / '.env.example').read_text().splitlines()
    return {
        line.split('=', 1)[0].strip()
        for line in lines
        if '=' in line and not line.lstrip().startswith('#')
    }


def test_env_example_lists_exactly_the_variables_settings_read():
    read_by_settings = variables_read_by_settings()
    assert read_by_settings, 'no environment variables found in settings.py'
    assert read_by_settings == variables_in_env_example()


def test_env_example_holds_no_keys():
    lines = (BASE_DIR / '.env.example').read_text().splitlines()
    assert 'DJANGO_SECRET_KEY=' in lines
    assert 'UPLOAD_CODE_KEY=' in lines


@pytest.mark.parametrize('value', ['red', '#e69f0', '#e69f00; } body { display: none', 'url(x)'])
def test_a_tag_colour_must_be_rrggbb(monkeypatch, value):
    # It goes into a stylesheet, so nothing else may get in.
    monkeypatch.setenv('SOME_COLOUR', value)
    with pytest.raises(ImproperlyConfigured, match='#rrggbb'):
        env_colour('SOME_COLOUR', '#0072b2')
