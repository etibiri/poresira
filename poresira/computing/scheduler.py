# SPDX-License-Identifier: AGPL-3.0-or-later
"""Talk to the job scheduler. Only Slurm for now, through its command-line tools.

The agent runs where ``sbatch`` exists. Commands are run without a shell and the job number is
checked before it is used.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .models import JobStatus

JOB_NUMBER = re.compile(r"^\d+$")
TIMEOUT_SECONDS = 60


class SchedulerError(Exception):
    """The scheduler refused or could not be reached."""


@dataclass(frozen=True)
class SchedulerState:
    status: str | None  # a JobStatus value, or None when the scheduler does not know the job
    exit_code: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


# Slurm job states, first word. Everything else counts as still waiting.
_RUNNING = {"RUNNING", "COMPLETING", "SUSPENDED", "STOPPED"}
_FAILED = {
    "FAILED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "BOOT_FAIL",
    "DEADLINE",
    "PREEMPTED",
    "REVOKED",
}


def status_of(slurm_state: str) -> str:
    word = slurm_state.strip().split()[0].rstrip("+") if slurm_state.strip() else ""
    if word == "COMPLETED":
        return JobStatus.SUCCEEDED
    if word == "CANCELLED":
        return JobStatus.CANCELLED
    if word in _FAILED:
        return JobStatus.FAILED
    return JobStatus.RUNNING if word in _RUNNING else JobStatus.SUBMITTED


def _timestamp(text: str) -> datetime | None:
    try:
        return timezone.make_aware(datetime.fromisoformat(text))
    except ValueError:  # "Unknown", "None" or empty
        return None


class SlurmScheduler:
    def __init__(self, bin_dir: str = ""):
        self.bin_dir = bin_dir

    def _run(self, program: str, *arguments: str) -> str:
        executable = str(Path(self.bin_dir) / program) if self.bin_dir else program
        try:
            done = subprocess.run(  # noqa: S603
                [executable, *arguments],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SchedulerError(f"{program} could not be run: {error}") from error
        if done.returncode != 0:
            raise SchedulerError(f"{program} failed: {done.stderr.strip() or done.stdout.strip()}")
        return done.stdout

    @staticmethod
    def _number(job_id: str) -> str:
        if not JOB_NUMBER.match(job_id):
            raise SchedulerError(f"Not a Slurm job number: {job_id!r}")
        return job_id

    def submit(self, script_path: Path) -> str:
        output = self._run("sbatch", "--parsable", str(script_path)).strip()
        job_id = output.split(";", 1)[0]  # "123" or "123;cluster"
        return self._number(job_id)

    def state(self, job_id: str) -> SchedulerState:
        number = self._number(job_id)
        waiting = self._run("squeue", "--noheader", "--jobs", number, "--format", "%T").strip()
        if waiting:
            return SchedulerState(status_of(waiting.splitlines()[0]))
        rows = self._run(
            "sacct", "--noheader", "--parsable2", "--jobs", number,
            "--format", "JobID,State,ExitCode,Start,End",
        ).strip()  # fmt: skip
        for row in rows.splitlines():
            fields = row.split("|")
            if len(fields) == 5 and fields[0] == number:
                _id, state, exit_code, start, end = fields
                return SchedulerState(
                    status_of(state),
                    int(exit_code.split(":")[0]) if re.match(r"^\d+:\d+$", exit_code) else None,
                    _timestamp(start),
                    _timestamp(end),
                )
        return SchedulerState(None)

    def cancel(self, job_id: str) -> None:
        self._run("scancel", self._number(job_id))


def scheduler_from_settings() -> SlurmScheduler:
    return SlurmScheduler(settings.COMPUTING["SLURM_BIN_DIR"])
