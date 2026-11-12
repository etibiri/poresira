# SPDX-License-Identifier: AGPL-3.0-or-later
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from poresira.samples.demo.loader import DEMO_USERS, load_demo_data


class Command(BaseCommand):
    help = (
        "Load demonstration data rebuilt from published sweetpotato studies (see "
        "poresira/samples/demo/data/README.md). Safe to run twice. Records cannot be deleted, "
        "so there is no reset: start from an empty database to begin again."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help="Give the demo accounts this password. Without it they cannot sign in.",
        )
        parser.add_argument(
            "--synthetic-volume",
            type=int,
            default=0,
            metavar="N",
            help="Also create N generated field samples in a separate project, for load testing.",
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Required when DEBUG is off: demo accounts and data do not belong in production.",
        )

    def handle(self, *args, password, synthetic_volume, allow_production, **options):
        if not settings.DEBUG and not allow_production:
            raise CommandError("DEBUG is off; pass --allow-production to load demo data anyway.")
        summary = load_demo_data(password=password, synthetic_volume=synthetic_volume)
        self.stdout.write(
            "Demo data already present." if summary.already_loaded else "Demo data loaded."
        )
        for name, count in summary.counts.items():
            self.stdout.write(f"  {count:5d} {name}")
        usable = "with the password you gave" if password else "without a usable password"
        self.stdout.write(f"Accounts ({usable}): {', '.join(DEMO_USERS)}")
