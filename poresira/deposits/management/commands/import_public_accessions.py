# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from poresira.core.models import Project, User
from poresira.deposits.importing import AccessionImportError, import_public_accessions
from poresira.samples.importing import read_csv


class Command(BaseCommand):
    help = (
        "Record accessions that already exist in a public archive (columns: sample, kind, "
        "accession, released_on, notes). kind is bioproject, biosample, sra_experiment or "
        "sra_run; sample is a field sample or living plant (empty for a bioproject). "
        "All or nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=Path)
        parser.add_argument("--project", required=True, help="Project code.")
        parser.add_argument("--user", required=True, help="Username recorded in the audit log.")
        parser.add_argument("--dry-run", action="store_true", help="Validate, then roll back.")

    def handle(self, *args, csv_file, project, user, dry_run, **options):
        try:
            project_record = Project.objects.get(code=project)
            acting_user = User.objects.get(username=user)
        except (Project.DoesNotExist, User.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        try:
            with transaction.atomic():
                report = import_public_accessions(
                    user=acting_user, project=project_record, rows=read_csv(csv_file)
                )
                if dry_run:
                    transaction.set_rollback(True)
        except AccessionImportError as error:
            for problem in error.problems:
                self.stderr.write(problem)
            message = f"Import refused, nothing saved ({len(error.problems)} problem(s))."
            raise CommandError(message) from error
        verb = "Would record" if dry_run else "Recorded"
        self.stdout.write(f"{verb} {len(report.created)} accession(s).")
