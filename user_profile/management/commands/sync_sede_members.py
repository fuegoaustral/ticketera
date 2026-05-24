import logging

from django.core.management.base import BaseCommand

from user_profile.services.sede_mercadopago import run_full_sync

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync La Sede members from MercadoPago subscriptions (match by DNI or similar names)'

    def handle(self, *args, **options):
        summary = run_full_sync(log=logger)

        if summary.get('error'):
            self.stderr.write(self.style.ERROR(summary['error']))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Sync complete: {summary['total']} total, "
                f"{summary.get('authorized_total', 0)} authorized, "
                f"{summary.get('update_only_total', 0)} non-auth, "
                f"{summary['matched']} matched, "
                f"{summary['unmatched']} unmatched, "
                f"{summary.get('update_only_ignored', 0)} non-auth ignored, "
                f"{summary.get('soft_removed_skipped', 0)} soft-removed skipped, "
                f"{summary.get('soft_removed_reactivated', 0)} soft-removed reactivated, "
                f"{summary.get('detail_truth_updated', 0)} detail-truth updated, "
                f"{summary['conflicts']} conflicts, "
                f"{summary['errors']} errors, "
                f"{summary['active_members']} active members, "
                f"{summary['deactivated']} deactivated, "
                f"took {summary.get('duration_seconds', 0)}s"
            )
        )
