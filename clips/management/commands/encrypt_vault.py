from django.core.management.base import BaseCommand, CommandError

from clips import vault


class Command(BaseCommand):
    help = 'Encrypt the vault files stored before encryption, sealing their keys to the group key (development data).'

    def handle(self, *args, **options):
        try:
            encrypted = vault.encrypt_plaintext_files()
        except vault.NoGroupKey:
            raise CommandError('No group key yet: the first lawyer must unlock their key first.') from None
        self.stdout.write(f'Encrypted {encrypted} files.')
