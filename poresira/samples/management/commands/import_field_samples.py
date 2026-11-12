# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from poresira.core.models import Project, User
from poresira.samples.importing import FieldSampleImportError, import_field_samples, read_csv


class Command(BaseCommand):
    help = (
        "Import field samples from a CSV file into a project, as a user who can edit it. "
        "Rows that share the collection columns share a collection event. All or nothing."
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
                report = import_field_samples(
                    user=acting_user, project=project_record, rows=read_csv(csv_file)
                )
                if dry_run:
                    transaction.set_rollback(True)
        except FieldSampleImportError as error:
            for problem in error.problems:
                self.stderr.write(problem)
            message = f"Import refused, nothing saved ({len(error.problems)} problem(s))."
            raise CommandError(message) from error
        verb = "Would import" if dry_run else "Imported"
        self.stdout.write(
            f"{verb} {len(report.samples)} field sample(s) in {len(report.events)} "
            "collection event(s)."
        )
