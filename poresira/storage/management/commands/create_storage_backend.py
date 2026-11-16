# SPDX-License-Identifier: AGPL-3.0-or-later
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from poresira.core.models import User
from poresira.storage import services
from poresira.storage.models import BackendKind


class Command(BaseCommand):
    help = "Declare a place where files live: a disk, a mounted share or an offline medium."

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("--kind", choices=BackendKind.values, required=True)
        parser.add_argument(
            "--root", required=True, help="Absolute directory, or the label of the medium."
        )
        parser.add_argument("--user", required=True, help="Username recorded in the audit log.")

    def handle(self, *args, name, kind, root, user, **options):
        try:
            acting_user = User.objects.get(username=user)
            backend = services.create_backend(user=acting_user, name=name, kind=kind, root=root)
        except User.DoesNotExist as error:
            raise CommandError(str(error)) from error
        except PermissionDenied as error:
            raise CommandError(f"{user} may not declare storage backends.") from error
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        self.stdout.write(f"Created backend {backend.name} ({backend.get_kind_display()}).")
