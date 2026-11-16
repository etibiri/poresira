# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from poresira.computing.jobtypes import JOB_TYPES, Staged, Tool, clean_parameters
from poresira.computing.models import JobKind
from poresira.computing.scripts import InputFile, Resources, render_script


def staged(parameters, tool="dorado", models=""):
    return Staged(
        parameters=parameters,
        dorado=Tool((tool,), "0.9.0"),
        pod5=Tool(("pod5",), ""),
        models_directory=models,
    )


def render(kind, parameters, inputs, work_directory="/data/work/WBF-J26-00001-5", **options):
    return render_script(
        identifier="WBF-J26-00001-5",
        job_type=JOB_TYPES[kind],
        staged=options.pop("staged", None) or staged(parameters),
        inputs=inputs,
        work_directory=work_directory,
        resources=options.pop("resources", Resources()),
        **options,
    )


BASH = shutil.which("bash")
INPUT = [InputFile("/data/run/a.pod5", "a" * 64)]


class TestParameters:
    def test_known_parameters_are_converted_and_unknown_ones_refused(self):
        cleaned = clean_parameters(
            JOB_TYPES[JobKind.BASECALLING], {"model": " sup@v5.0.0 ", "min_qscore": "10"}
        )

        assert cleaned == {"model": "sup@v5.0.0", "min_qscore": 10}
        with pytest.raises(ValidationError) as error:
            clean_parameters(JOB_TYPES[JobKind.BASECALLING], {"model": "sup", "extra": "x"})
        assert "extra" in error.value.message_dict

    @pytest.mark.parametrize(
        "model", ["sup; rm -rf /", "$(id)", "a b", "`x`", "sup\nhac", "", "a" * 101, "../x"]
    )
    def test_a_model_cannot_carry_shell_syntax(self, model):
        with pytest.raises(ValidationError):
            clean_parameters(JOB_TYPES[JobKind.BASECALLING], {"model": model})

    @pytest.mark.parametrize("value", ["0", "61", "x", "1.5"])
    def test_a_quality_is_a_whole_number_in_range(self, value):
        with pytest.raises(ValidationError):
            clean_parameters(JOB_TYPES[JobKind.BASECALLING], {"model": "sup", "min_qscore": value})

    def test_demultiplexing_needs_a_kit(self):
        with pytest.raises(ValidationError):
            clean_parameters(JOB_TYPES[JobKind.DEMULTIPLEXING], {})
        assert clean_parameters(JOB_TYPES[JobKind.DEMULTIPLEXING], {"kit_name": "SQK-NBD114.96"})


class TestScripts:
    def test_a_basecalling_script_carries_resources_checks_and_the_command(self):
        script = render(
            JobKind.BASECALLING,
            {"model": "sup", "kit_name": "SQK-NBD114.96", "min_qscore": 10},
            INPUT,
            resources=Resources(gpus=2, cpus=4, mem_gb=32, time="1-12:00:00", partition="gpu"),
            setup="module load dorado/0.9.0",
        )

        for expected in (
            "#SBATCH --job-name=WBF-J26-00001-5",
            "#SBATCH --output=/data/work/WBF-J26-00001-5/slurm-%j.log",
            "#SBATCH --partition=gpu",
            "#SBATCH --gres=gpu:2",
            "#SBATCH --cpus-per-task=4",
            "#SBATCH --mem=32G",
            "#SBATCH --time=1-12:00:00",
            "set -euo pipefail",
            "module load dorado/0.9.0",
            "sha256sum --check --quiet -",
            "ln -s /data/run/a.pod5 input/0001_a.pod5",
            "dorado basecaller sup input --device cuda:all --kit-name SQK-NBD114.96",
            "--min-qscore 10 > output/calls.bam",
        ):
            assert expected in script, expected
        assert "--account" not in script

    def test_only_basecalling_asks_for_a_gpu(self):
        demux = render(JobKind.DEMULTIPLEXING, {"kit_name": "K"}, [InputFile("/d/c.bam", "b" * 64)])

        assert "--gres" not in demux
        assert "dorado demux --kit-name K --output-dir output --emit-summary input" in demux

    def test_the_conversion_command_uses_the_cpus_it_was_given(self):
        script = render(JobKind.POD5_CONVERSION, {}, [InputFile("/d/x.fast5", "c" * 64)])

        assert 'pod5 convert fast5 --threads "$SLURM_CPUS_PER_TASK"' in script
        assert "--output output/converted.pod5 --recursive input" in script

    def test_a_configured_command_may_be_several_words_and_models_a_directory(self):
        script = render(
            JobKind.BASECALLING,
            {"model": "hac"},
            INPUT,
            staged=Staged(
                parameters={"model": "hac"},
                dorado=Tool(("apptainer", "exec", "--nv", "/opt/dorado sif.img", "dorado")),
                pod5=Tool(("pod5",)),
                models_directory="/opt/dorado models",
            ),
        )

        assert "apptainer exec --nv '/opt/dorado sif.img' dorado basecaller hac" in script
        assert "--models-directory '/opt/dorado models'" in script

    @pytest.mark.parametrize(
        "path",
        ["/d/a b.pod5", "/d/it's.pod5", "/d/$(touch pwned).pod5", "/d/`id`.pod5", "/d/;x.pod5"],
    )
    def test_awkward_file_names_are_quoted_and_the_script_stays_valid_bash(self, path):
        script = render(JobKind.BASECALLING, {"model": "sup"}, [InputFile(path, "d" * 64)])

        checked = subprocess.run(  # noqa: S603
            [BASH, "-n"], input=script, text=True, capture_output=True
        )
        assert checked.returncode == 0, checked.stderr

    @pytest.mark.parametrize(
        "work_directory", ["relative/dir", "/data/with space/x", "/data/$(x)/y", "/data/x;y"]
    )
    def test_an_unsafe_working_directory_is_refused(self, work_directory):
        with pytest.raises(ValueError, match="Unsafe working directory"):
            render(JobKind.BASECALLING, {"model": "sup"}, INPUT, work_directory=work_directory)

    def test_unsafe_scheduler_values_and_empty_inputs_are_refused(self):
        with pytest.raises(ValueError, match="time limit"):
            render(JobKind.BASECALLING, {"model": "sup"}, INPUT, resources=Resources(time="1h; ls"))
        with pytest.raises(ValueError, match="scheduler value"):
            render(
                JobKind.BASECALLING,
                {"model": "sup"},
                INPUT,
                resources=Resources(partition="gpu\n#SBATCH --x"),
            )
        with pytest.raises(ValueError, match="at least one input"):
            render(JobKind.BASECALLING, {"model": "sup"}, [])


class TestRunningTheScript:
    """Run the generated script with a fake tool that records what it is given."""

    @pytest.fixture
    def setup(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        signal = data / "my signal.pod5"
        signal.write_bytes(b"signal")
        tool = tmp_path / "dorado"
        tool.write_text(
            '#!/bin/bash\n[ "$1" = "--version" ] && exit 0\n'
            'echo "$@" > tool_args.txt\nls input >> tool_args.txt\n'
        )
        tool.chmod(0o755)
        work = tmp_path / "work" / "WBF-J26-00001-5"
        return signal, tool, work

    def run(self, signal, tool, work, sha256):
        script = render(
            JobKind.BASECALLING,
            {"model": "sup"},
            [InputFile(str(signal), sha256)],
            work_directory=str(work),
            staged=staged({"model": "sup"}, tool=str(tool)),
        )
        return subprocess.run(  # noqa: S603
            [BASH, "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "SLURM_CPUS_PER_TASK": "2"},
        )

    def test_the_tool_runs_on_the_staged_inputs(self, setup):
        signal, tool, work = setup

        done = self.run(signal, tool, work, hashlib.sha256(b"signal").hexdigest())

        assert done.returncode == 0, done.stderr
        recorded = Path(work, "tool_args.txt").read_text()
        assert "basecaller sup input --device cuda:all" in recorded
        assert "0001_my signal.pod5" in recorded
        assert Path(work, "input", "0001_my signal.pod5").is_symlink()

    def test_a_changed_input_stops_the_job_before_the_tool_runs(self, setup):
        signal, tool, work = setup

        done = self.run(signal, tool, work, "0" * 64)

        assert done.returncode != 0
        assert not Path(work, "tool_args.txt").exists()
