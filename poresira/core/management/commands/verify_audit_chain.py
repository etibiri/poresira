# SPDX-License-Identifier: AGPL-3.0-or-later
from django.core.management.base import BaseCommand, CommandError

from poresira.core.audit import verify_audit_chain


class Command(BaseCommand):
    help = "Verify the audit hash chain and report any break (exit status 1 if one is found)."

    def handle(self, *args, **options):
        report = verify_audit_chain()
        for link_seq, problem in report.problems:
            location = f"link {link_seq}" if link_seq is not None else "chain"
            self.stderr.write(f"BREAK at {location}: {problem}")
        if not report.is_intact:
            raise CommandError(f"Audit chain is broken ({len(report.problems)} problem(s)).")
        self.stdout.write(
            f"Audit chain OK: {report.link_count} link(s), head hash {report.head_hash or '-'}. "
            "Record the head hash elsewhere to detect truncation."
        )
