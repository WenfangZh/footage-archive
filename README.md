# Footage archive (learning build)

> **Learning build: not deployed, not for real footage.** This repository is a learning project. It holds no real videos and has no real users, and it has not had a security or legal review. Do not use it to store or share real footage.

A footage archive for defence lawyers: people upload short phone videos of arrests and police operations at protests, and verified defence lawyers search them, one case at a time, to check police reports. Privacy by design comes first: the people most at risk are those who filmed and those in frame.

- [SPEC.md](SPEC.md): what we are building and why, and the milestones.
- [GLOSSARY.md](GLOSSARY.md): what the archive's concepts are called, in English and French.
- [docs/adr/](docs/adr/): decisions that are hard to reverse.

## Quick setup with an AI assistant

Instead of following the steps below one by one, you can paste this into an AI coding assistant that can run commands in your terminal (such as Claude Code). Read what it proposes before approving: it will ask for `sudo` and change your shell configuration.

```text
Set up the footage-archive project on my machine (Ubuntu or WSL2, bash or zsh) by following
the "Setup from scratch" section of its README:
https://github.com/WenfangZh/footage-archive#setup-from-scratch

- First check what is already installed (apt packages, pyenv, pyenv-virtualenv, the
  footage-archive virtualenv, an existing clone) and skip the steps that are done.
- Before running any command that uses sudo or changes ~/.bashrc or ~/.zshrc, show it
  to me and wait for my OK.
- Create .env from .env.example and generate its two keys (DJANGO_SECRET_KEY and
  UPLOAD_CODE_KEY, different from each other) locally, writing them straight into .env
  in single quotes. Never print the keys, show them to me or send them anywhere, and
  never commit .env.
- If a step fails, stop and tell me what failed instead of working around it.
- Finish by running `python manage.py check` and `pytest`, report the results, and
  give me the command to start the dev server.
```

## Setup from scratch

These steps assume Ubuntu (including WSL2) and bash or zsh. Keep the repository in the Linux filesystem.

### 1. System packages

Python build dependencies (for pyenv), ffmpeg and ffprobe (video checks and processing), and gettext (translations):

```sh
sudo apt update
sudo apt install build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
  libsqlite3-dev curl git libncursesw5-dev xz-utils tk-dev libxml2-dev \
  libxmlsec1-dev libffi-dev liblzma-dev ffmpeg gettext
```

### 2. pyenv and Python 3.12

Skip this if you already have pyenv with the pyenv-virtualenv plugin.

```sh
curl -fsSL https://pyenv.run | bash
```

Then add these lines to your shell's startup file and open a new terminal. For bash, in `~/.bashrc`:

```sh
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"
eval "$(pyenv virtualenv-init -)"
```

For zsh, in `~/.zshrc`:

```sh
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - zsh)"
eval "$(pyenv virtualenv-init -)"
```

Install Python and create the project's virtualenv:

```sh
pyenv install 3.12
pyenv virtualenv 3.12 footage-archive
```

### 3. Clone and install

The repository's `.python-version` selects the `footage-archive` virtualenv automatically inside the folder.

```sh
git clone https://github.com/WenfangZh/footage-archive.git
cd footage-archive
python --version   # should print Python 3.12.x
pip install -r requirements-dev.txt
```

### 4. Configuration

Settings come from environment variables; a local `.env` file (never committed) fills them in.

```sh
cp .env.example .env
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Run the `python -c` command twice: one key for Django, a different one for hashing upload codes. Open `.env` and set them in single quotes, since they can contain `#` or `$`:

```
DJANGO_SECRET_KEY='paste-the-first-generated-key-here'
UPLOAD_CODE_KEY='paste-the-second-generated-key-here'
```

### 5. Database and translations

```sh
python manage.py migrate
python manage.py compilemessages
```

The SQLite database, the task queue, the quarantine and the vault go in `var/` (gitignored).

To log in as the admin, create the admin's account; it asks for an email address and a password:

```sh
python manage.py createsuperuser
```

### 6. Run

```sh
python manage.py runserver
```

In a second terminal, start the background worker, which ingests uploaded clips and deletes originals whose form was never confirmed:

```sh
python manage.py run_huey
```

Open http://127.0.0.1:8000/: the page is in French, or in English if your browser prefers English, with a switch between the two.

```sh
pytest
```

All tests should pass. `manage.py` refuses to start if ffmpeg or ffprobe is missing.

## Licence

No licence has been chosen yet, so the code is all rights reserved. Choosing one is milestone M7 (SPEC.md §12), before any outside contribution is accepted.
