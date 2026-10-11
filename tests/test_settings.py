import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import User as AuthUser
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command

from config.settings import env_bool, env_list

BASE_DIR = Path(__file__).resolve().parent.parent


def test_system_checks_pass():
    call_command('check', fail_level='WARNING')


def test_missing_secret_key_stops_startup():
    # An empty value counts as missing, and stops a .env file from filling it in.
    env = {**os.environ, 'DJANGO_SECRET_KEY': ''}
    result = subprocess.run(
        [sys.executable, '-c', 'import config.settings'],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'Set the DJANGO_SECRET_KEY environment variable' in result.stderr


def test_new_passwords_are_hashed_with_argon2():
    auth_user = AuthUser(username='someone')
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


def variables_read_by_settings():
    source = (BASE_DIR / 'config' / 'settings.py').read_text()
    pattern = r"(?:env_required|env_bool|env_list|os\.environ\.get|os\.getenv)\(\s*'([A-Z0-9_]+)'"
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


def test_env_example_holds_no_secret_key():
    lines = (BASE_DIR / '.env.example').read_text().splitlines()
    assert 'DJANGO_SECRET_KEY=' in lines
