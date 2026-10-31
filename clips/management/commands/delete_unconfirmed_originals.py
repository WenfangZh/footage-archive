from django.core.management.base import BaseCommand

from clips.cleanup import delete_expired_partial_uploads, delete_unconfirmed_originals


class Command(BaseCommand):
    help = 'Delete originals whose form was never confirmed in time, partial uploads never finished in time, and stray quarantine files.'

    def handle(self, *args, **options):
        originals, stray = delete_unconfirmed_originals()
        partials = delete_expired_partial_uploads()
        # Counts only: file names and times say nothing useful and are better not logged.
        self.stdout.write(f'Deleted {originals} unconfirmed originals, {partials} partial uploads and {stray} stray files.')
