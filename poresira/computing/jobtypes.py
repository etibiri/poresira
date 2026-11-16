# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each kind of job accepts and runs. The commands are fixed here; a person only chooses
validated parameters, so nothing typed in a form can become shell syntax."""

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from poresira.storage.models import FileKind

from .models import JobKind

NAME_PATTERN = r"^[A-Za-z0-9_.@-]{1,100}$"
KIT_PATTERN = r"^[A-Za-z0-9._-]{1,40}$"


@dataclass(frozen=True)
class Parameter:
    name: str
    label: object
    integer: bool = False
    required: bool = False
    default: object = None
    pattern: str = ""
    minimum: int = 0
    maximum: int = 0
    help_text: object = ""


@dataclass(frozen=True)
class Tool:
    """The command of a tool as configured (it may be several words) and its version."""

    command: tuple[str, ...]
    version: str = ""

    def words(self) -> str:
        return " ".join(shlex.quote(word) for word in self.command)


@dataclass(frozen=True)
class Staged:
    """Everything a builder needs, with plain values so it can be tested without a database."""

    parameters: dict
    dorado: Tool
    pod5: Tool
    models_directory: str = ""


@dataclass(frozen=True)
class JobType:
    kind: str
    tool: str  # "dorado" or "pod5"
    input_kinds: tuple[str, ...]
    parameters: tuple[Parameter, ...]
    gpu: bool
    build: Callable[[Staged], list[str]]


def _flag(name: str, value: object) -> list[str]:
    return [name, shlex.quote(str(value))] if value not in (None, "") else []


def _basecalling(staged: Staged) -> list[str]:
    parameters = staged.parameters
    command = [staged.dorado.words(), "basecaller", shlex.quote(parameters["model"]), "input"]
    command += ["--device", "cuda:all"]
    command += _flag("--models-directory", staged.models_directory)
    command += _flag("--kit-name", parameters.get("kit_name"))
    command += _flag("--min-qscore", parameters.get("min_qscore"))
    return [" ".join(command) + " > output/calls.bam"]


def _demultiplexing(staged: Staged) -> list[str]:
    command = [staged.dorado.words(), "demux"]
    command += _flag("--kit-name", staged.parameters["kit_name"])
    command += ["--output-dir", "output", "--emit-summary", "input"]
    return [" ".join(command)]


def _pod5_conversion(staged: Staged) -> list[str]:
    command = [staged.pod5.words(), "convert", "fast5", "--threads", '"$SLURM_CPUS_PER_TASK"']
    command += ["--output", "output/converted.pod5", "--recursive", "input"]
    return [" ".join(command)]


JOB_TYPES = {
    JobKind.BASECALLING: JobType(
        kind=JobKind.BASECALLING,
        tool="dorado",
        input_kinds=(FileKind.POD5,),
        parameters=(
            Parameter(
                "model",
                _("Model"),
                required=True,
                default="sup",
                pattern=NAME_PATTERN,
                help_text=_("A speed (fast, hac, sup) or a model name, such as sup@v5.0.0."),
            ),
            Parameter(
                "kit_name",
                _("Barcoding kit"),
                pattern=KIT_PATTERN,
                help_text=_("Trims adapters and barcodes during basecalling; optional."),
            ),
            Parameter("min_qscore", _("Minimum quality"), integer=True, minimum=1, maximum=60),
        ),
        gpu=True,
        build=_basecalling,
    ),
    JobKind.DEMULTIPLEXING: JobType(
        kind=JobKind.DEMULTIPLEXING,
        tool="dorado",
        input_kinds=(FileKind.BAM,),
        parameters=(Parameter("kit_name", _("Barcoding kit"), required=True, pattern=KIT_PATTERN),),
        gpu=False,
        build=_demultiplexing,
    ),
    JobKind.POD5_CONVERSION: JobType(
        kind=JobKind.POD5_CONVERSION,
        tool="pod5",
        input_kinds=(FileKind.FAST5,),
        parameters=(),
        gpu=False,
        build=_pod5_conversion,
    ),
}


def clean_parameters(job_type: JobType, raw: dict) -> dict:
    """Keep only known parameters, converted and checked; raise ``ValidationError`` otherwise."""
    known = {parameter.name: parameter for parameter in job_type.parameters}
    problems = {name: _("Unknown parameter.") for name in set(raw) - set(known)}
    cleaned = {}
    for name, parameter in known.items():
        value = raw.get(name)
        value = value.strip() if isinstance(value, str) else value
        if value in (None, ""):
            if parameter.required:
                problems[name] = _("This parameter is required.")
            continue
        if parameter.integer:
            try:
                value = int(value)
            except (TypeError, ValueError):
                problems[name] = _("Give a whole number.")
                continue
            if not parameter.minimum <= value <= parameter.maximum:
                problems[name] = _("Give a number from %(low)s to %(high)s.") % {
                    "low": parameter.minimum,
                    "high": parameter.maximum,
                }
                continue
        elif not re.fullmatch(parameter.pattern, str(value)):
            problems[name] = _("Use letters, digits and . _ @ - only.")
            continue
        cleaned[name] = value
    if problems:
        raise ValidationError(problems)
    return cleaned
