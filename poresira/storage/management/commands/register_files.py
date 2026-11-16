# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from poresira.core.models import User
from poresira.pcr.models import PcrRun
from poresira.samples.models import Sample
from poresira.sequencing.models import SequencingRun
from poresira.storage.importing import DirectoryError, register_directory
from poresira.storage.models import FileKind, StorageBackend

LINK_MODELS = {"sequencing_run": SequencingRun, "pcr_run": PcrRun, "sample": Sample}


class Command(BaseCommand):
    help = (
        "Register in place every file of a directory, computing size, SHA-256 and MD5. Give the "
        "record the files belong to with one of --sequencing-run, --pcr-run or --sample. "
        "Files already registered are skipped, so an interrupted run can be repeated."
    )

    def add_arguments(self, parser):
        parser.add_argument("directory", type=Path)
        parser.add_argument("--backend", required=True, help="Name of the storage backend.")
        parser.add_argument("--user", required=True, help="Username recorded in the audit log.")
        parser.add_argument("--sequencing-run", dest="sequencing_run", help="Run identifier.")
        parser.add_argument("--pcr-run", dest="pcr_run", help="PCR run identifier.")
        parser.add_argument("--sample", help="Sample identifier.")
        parser.add_argument(
            "--only",
            help=f"Comma-separated kinds to register ({', '.join(FileKind.values)}).",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="List what would be registered; read no file."
        )

    def _link(self, options) -> tuple[dict, object]:
        given = {name: options[name] for name in LINK_MODELS if options[name]}
        if len(given) != 1:
            raise CommandError("Give exactly one of --sequencing-run, --pcr-run, --sample.")
        name, identifier = next(iter(given.items()))
        try:
            record = LINK_MODELS[name].objects.active().get(identifier=identifier)
        except LINK_MODELS[name].DoesNotExist as error:
            raise CommandError(f"No {name.replace('_', ' ')} {identifier!r}.") from error
        return {name: record}, record

    def handle(self, *args, directory, backend, user, only, dry_run, **options):
        link, record = self._link(options)
        kinds = set(only.split(",")) if only else None
        if kinds and not kinds <= set(FileKind.values):
            raise CommandError(f"Unknown kind in --only: {sorted(kinds - set(FileKind.values))}")
        try:
            backend_record = StorageBackend.objects.active().get(name=backend)
            acting_user = User.objects.get(username=user)
        except (StorageBackend.DoesNotExist, User.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        try:
            report = register_directory(
                user=acting_user,
                project=record.project,
                backend=backend_record,
                directory=directory,
                link=link,
                only_kinds=kinds,
                dry_run=dry_run,
                progress=self.stdout.write,
            )
        except DirectoryError as error:
            raise CommandError(str(error)) from error
        if dry_run:
            megabytes = report.would_register_bytes / 1e6
            self.stdout.write(
                f"Would register {report.would_register} file(s), {megabytes:.1f} MB."
            )
        else:
            megabytes = report.registered_bytes / 1e6
            self.stdout.write(f"Registered {len(report.registered)} file(s), {megabytes:.1f} MB.")
        self.stdout.write(
            f"Already registered: {report.already_registered}. Left out by --only: "
            f"{report.filtered_out}."
        )
        for path, problem in report.failed:
            self.stderr.write(f"{path}: {problem}")
        if report.failed:
            raise CommandError(f"{len(report.failed)} file(s) could not be registered.")
