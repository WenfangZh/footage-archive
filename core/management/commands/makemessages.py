"""Django's makemessages, without the POT-Creation-Date header line.

Django stamps the time of each run into that line, so any two branches that each added a
translation conflicted there, and nothing reads it.
"""

import os
import re
from pathlib import Path

from django.core.management.commands import makemessages

CREATION_DATE = re.compile(r'^"POT-Creation-Date: [^"\n]*\\n"\n', re.MULTILINE)


class Command(makemessages.Command):
    def write_po_file(self, potfile, locale):
        super().write_po_file(potfile, locale)
        pofile = Path(potfile).parent / locale / 'LC_MESSAGES' / f'{self.domain}.po'
        text = pofile.read_text(encoding='utf-8')
        partial = pofile.with_suffix('.po.tmp')
        partial.write_text(CREATION_DATE.sub('', text), encoding='utf-8')
        os.replace(partial, pofile)
