# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from poresira.core.models import User
from poresira.storage.demo import create_demo_storage


class Command(BaseCommand):
    help = (
        "Write a fictitious data folder for the demonstration runs and register its files. "
        "Run load_demo_data first. Every file is a small placeholder, not real data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--root",
            type=Path,
            default=Path(settings.BASE_DIR) / "demo-data",
            help="Folder to fill (default: demo-data in the project folder).",
        )

    def handle(self, *args, root, **options):
        try:
            platform = User.objects.get(username="demo_platform")
            technician = User.objects.get(username="demo_technician")
        except User.DoesNotExist as error:
            raise CommandError("Load the demonstration data first (load_demo_data).") from error
        storage = create_demo_storage(root, platform=platform, technician=technician)
        self.stdout.write(
            f"{storage.root}: {storage.written} file(s) written, {storage.registered} registered."
        )
