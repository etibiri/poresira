# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from poresira.core.models import Project, User
from poresira.pcr.importing import PcrImportError, import_pcr_run
from poresira.pcr.models import Assay, Container
from poresira.samples.importing import read_csv


class Command(BaseCommand):
    help = (
        "Import one PCR run from a CSV file (columns: sample, position, role, result, ct, "
        "band_size_bp, template_volume_ul, notes). All or nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=Path)
        parser.add_argument("--project", required=True, help="Project code.")
        parser.add_argument("--assay", required=True, help="Assay name.")
        parser.add_argument("--user", required=True, help="Username recorded in the audit log.")
        parser.add_argument("--tubes", type=int, help="Use this many PCR tubes instead of a plate.")
        parser.add_argument("--performed-on", type=date.fromisoformat, help="Date, YYYY-MM-DD.")
        parser.add_argument(
            "--reconstructed", action="store_true", help="Taken from a publication."
        )
        parser.add_argument("--close", action="store_true", help="Close the run after the results.")
        parser.add_argument("--dry-run", action="store_true", help="Validate, then roll back.")

    def handle(self, *args, csv_file, project, assay, user, tubes, dry_run, **options):
        try:
            project_record = Project.objects.get(code=project)
            assay_record = Assay.objects.active().get(name=assay)
            acting_user = User.objects.get(username=user)
        except (Project.DoesNotExist, Assay.DoesNotExist, User.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        try:
            with transaction.atomic():
                report = import_pcr_run(
                    user=acting_user,
                    project=project_record,
                    assay=assay_record,
                    rows=read_csv(csv_file),
                    container=Container.TUBES if tubes else Container.PLATE_96,
                    tube_count=tubes,
                    performed_on=options["performed_on"],
                    reconstructed=options["reconstructed"],
                    close=options["close"],
                )
                if dry_run:
                    transaction.set_rollback(True)
        except PcrImportError as error:
            for problem in error.problems:
                self.stderr.write(problem)
            message = f"Import refused, nothing saved ({len(error.problems)} problem(s))."
            raise CommandError(message) from error
        verb = "Would create" if dry_run else "Created"
        self.stdout.write(
            f"{verb} run {report.run.identifier} with {len(report.reactions)} reaction(s)."
        )
