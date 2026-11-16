# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write the batch script of a job. Pure functions: no database, no scheduler."""

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath

from .jobtypes import JobType, Staged

SAFE_DIRECTORY = re.compile(r"^/[A-Za-z0-9_./-]+$")
SAFE_WORD = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_TIME = re.compile(r"^(\d+-)?\d+:\d{2}:\d{2}$")


@dataclass(frozen=True)
class InputFile:
    path: str  # absolute, as the cluster sees it
    sha256: str


@dataclass(frozen=True)
class Resources:
    gpus: int = 1
    cpus: int = 8
    mem_gb: int = 64
    time: str = "24:00:00"
    partition: str = ""
    account: str = ""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def render_script(
    *,
    identifier: str,
    job_type: JobType,
    staged: Staged,
    inputs: list[InputFile],
    work_directory: str,
    resources: Resources,
    setup: str = "",
) -> str:
    """The bash script that checks the inputs, stages them under ``input/`` and runs the tool
    in ``work_directory``; results are left in ``output/``."""
    _check(
        bool(SAFE_DIRECTORY.match(work_directory)), f"Unsafe working directory: {work_directory}"
    )
    _check(bool(SAFE_TIME.match(resources.time)), f"Unsafe time limit: {resources.time}")
    for word in (resources.partition, resources.account, identifier):
        _check(not word or bool(SAFE_WORD.match(word)), f"Unsafe scheduler value: {word}")
    _check(bool(inputs), "A job needs at least one input file")

    lines = ["#!/bin/bash", f"#SBATCH --job-name={identifier}"]
    lines.append(f"#SBATCH --output={work_directory}/slurm-%j.log")
    if resources.partition:
        lines.append(f"#SBATCH --partition={resources.partition}")
    if resources.account:
        lines.append(f"#SBATCH --account={resources.account}")
    if job_type.gpu and resources.gpus:
        lines.append(f"#SBATCH --gres=gpu:{int(resources.gpus)}")
    lines += [
        f"#SBATCH --cpus-per-task={int(resources.cpus)}",
        f"#SBATCH --mem={int(resources.mem_gb)}G",
        f"#SBATCH --time={resources.time}",
        "set -euo pipefail",
    ]
    if setup.strip():
        lines.append(setup.strip())
    tool = staged.dorado if job_type.tool == "dorado" else staged.pod5
    lines += [
        f"mkdir -p {shlex.quote(work_directory)}",
        f"cd {shlex.quote(work_directory)}",
        f"echo {shlex.quote(f'PoreSira job {identifier} ({job_type.kind})')}",
        f"{tool.words()} --version 2>&1 || true",
        "mkdir -p input output",
        "# The inputs must still be the files that were registered.",
    ]
    for number, file in enumerate(inputs, start=1):
        link = f"input/{number:04d}_{PurePosixPath(file.path).name}"
        lines.append(
            f"printf '%s  %s\\n' {shlex.quote(file.sha256)} {shlex.quote(file.path)}"
            " | sha256sum --check --quiet -"
        )
        lines.append(f"ln -s {shlex.quote(file.path)} {shlex.quote(link)}")
    lines += job_type.build(staged)
    lines.append("echo finished")
    return "\n".join(lines) + "\n"
