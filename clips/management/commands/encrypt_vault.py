from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from clips import vault


class Command(BaseCommand):
    help = ('Encrypt the vault files and texts stored before encryption, sealing their keys to the '
            'group key (development data; docs/m5-encryption-design.md §10).')

    def handle(self, *args, **options):
        try:
            files = vault.encrypt_plaintext_files()
            texts = vault.encrypt_plaintext_texts()
        except vault.NoGroupKey:
            raise CommandError('No group key yet: the first lawyer must unlock their key first.') from None
        # Rewrites the database file, so that no free page keeps a plaintext text.
        with connection.cursor() as cursor:
            cursor.execute('VACUUM')
        self.stdout.write(
            f'Encrypted {files} files and {texts} texts. The {texts} texts have no copy for their code holder, '
            'who sees them empty until they enter them again.'
        )
