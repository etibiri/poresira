# SPDX-License-Identifier: AGPL-3.0-or-later
from django.core.management.base import BaseCommand, CommandError

from poresira.core.models import Project, User
from poresira.deposits.public_sync import PublicArchiveError
from poresira.deposits.sync import sync_public_archives


class Command(BaseCommand):
    help = (
        "Read the public archives (NCBI, ENA) for a BioProject, record the BioSample and SRA "
        "accessions PoreSira lacks, and report differences with PoreSira (GenBank records, "
        "BioSample attributes). Reading only; a dry run unless --apply is given. Only public "
        "accession numbers leave this computer. Run it where the Internet is reachable."
    )

    def add_arguments(self, parser):
        parser.add_argument("project", help="Project code.")
        parser.add_argument("--bioproject", required=True, help="BioProject, such as PRJNA1460897.")
        parser.add_argument("--user", required=True, help="Username recorded in the audit log.")
        parser.add_argument("--source", choices=["auto", "ncbi", "ena"], default="auto")
        parser.add_argument("--apply", action="store_true", help="Record the new accessions.")

    def handle(self, *args, project, bioproject, user, source, apply, **options):
        try:
            project_record = Project.objects.get(code=project)
            acting_user = User.objects.get(username=user)
        except (Project.DoesNotExist, User.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        try:
            report = sync_public_archives(
                user=acting_user,
                project=project_record,
                bioproject=bioproject,
                source=source,
                apply=apply,
            )
        except PublicArchiveError as error:
            raise CommandError(str(error)) from error
        write = self.stdout.write
        write(f"Read from {report.source.upper()}: BioProject {report.bioproject}.")
        verb = "Recorded" if apply else "Would record"
        count = len(report.recorded) if apply else len(report.would_record)
        write(f"{verb} {count} accession(s); {report.already_known} already known.")
        sections = (
            ("SRA runs without a sample in PoreSira", [r.run for r in report.unmatched_runs]),
            ("Sequences not found at NCBI", report.genbank_missing),
            ("GenBank differences", report.genbank_differences),
            ("BioSample differences", report.biosample_differences),
            ("Sequenced samples with no SRA run", report.sequenced_without_sra),
            ("Not checked", report.errors),
        )
        for title, lines in sections:
            if lines:
                write(f"{title} ({len(lines)}):")
                for line in lines:
                    write(f"  {line}")
        if not report.has_differences and not report.errors:
            write("PoreSira and the archives agree.")
