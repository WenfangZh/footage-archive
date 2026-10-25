from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from cases import labels
from clips import vault


class Command(BaseCommand):
    help = ('Encrypt the vault files, texts and case labels stored before encryption, sealing their keys '
            "to the group key, or a label's to its lawyer's key (development data; docs/m5-encryption-design.md §10).")

    def handle(self, *args, **options):
        try:
            files = vault.encrypt_plaintext_files()
            texts = vault.encrypt_plaintext_texts()
            case_labels, labels_left = labels.encrypt_plaintext_labels()
        except vault.NoGroupKey:
            raise CommandError('No group key yet: the first lawyer must unlock their key first.') from None
        # Rewrites the database file, so that no free page keeps a plaintext text.
        with connection.cursor() as cursor:
            cursor.execute('VACUUM')
        self.stdout.write(f'Encrypted {files} files, {texts} texts and {case_labels} case labels.')
        if texts:
            self.stdout.write(
                'Those texts have no copy for their code holder, who sees them empty until they enter them again.'
            )
        if labels_left:
            self.stdout.write(f'Left {labels_left} case labels as they were: their lawyer has no personal key.')
