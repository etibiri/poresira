# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from poresira.core.models import Project, User
from poresira.samples.importing import read_csv
from poresira.sequencing.importing import LibraryImportError, import_library
from poresira.sequencing.models import LibraryKit


class Command(BaseCommand):
    help = (
        "Import one sequencing library from a CSV file (columns: sample, barcode, input_ng, "
        "notes). All or nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=Path)
        parser.add_argument("--project", required=True, help="Project code.")
        parser.add_argument("--kit", required=True, help="Library kit code, e.g. SQK-NBD114.96.")
        parser.add_argument("--user", required=True, help="Username recorded in the audit log.")
        parser.add_argument("--name", default="", help="Name of the library.")
        parser.add_argument("--prepared-on", type=date.fromisoformat, help="Date, YYYY-MM-DD.")
        parser.add_argument("--reconstructed", action="store_true", help="Taken from a record.")
        parser.add_argument("--seal", action="store_true", help="Seal the library afterwards.")
        parser.add_argument("--dry-run", action="store_true", help="Validate, then roll back.")

    def handle(self, *args, csv_file, project, kit, user, dry_run, **options):
        try:
            project_record = Project.objects.get(code=project)
            kit_record = LibraryKit.objects.active().get(code=kit.upper())
            acting_user = User.objects.get(username=user)
        except (Project.DoesNotExist, LibraryKit.DoesNotExist, User.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        try:
            with transaction.atomic():
                report = import_library(
                    user=acting_user,
                    project=project_record,
                    kit=kit_record,
                    rows=read_csv(csv_file),
                    seal=options["seal"],
                    name=options["name"],
                    prepared_on=options["prepared_on"],
                    reconstructed=options["reconstructed"],
                )
                if dry_run:
                    transaction.set_rollback(True)
        except LibraryImportError as error:
            for problem in error.problems:
                self.stderr.write(problem)
            message = f"Import refused, nothing saved ({len(error.problems)} problem(s))."
            raise CommandError(message) from error
        verb = "Would create" if dry_run else "Created"
        self.stdout.write(
            f"{verb} library {report.library.identifier} with {len(report.members)} sample(s)."
        )
