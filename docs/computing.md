<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Computing

The `computing` app runs calculations on the data of a sequencing run through the cluster's job
scheduler (Slurm), and keeps what is needed to understand and repeat each one: the tool and its
version, the parameters, the exact script, the input files with their checksums, and the results.
All tables are audited (see [Architecture](architecture.md)).

## Kinds of job

| Kind | Tool | Reads | Writes (in `output/`) |
|---|---|---|---|
| Basecalling | `dorado basecaller` | the POD5 files of the run | `calls.bam`, an unaligned BAM |
| Demultiplexing | `dorado demux` | the BAM files of a successful basecalling | one BAM per barcode and a summary |
| FAST5 to POD5 conversion | `pod5 convert fast5` | the FAST5 files of the run | `converted.pod5` |

The kinds and their commands are fixed in the code (`poresira.computing.jobtypes`). A person only
chooses validated parameters:

- **Basecalling**: `model` (a speed such as `sup`, or a model name such as `sup@v5.0.0`),
  `kit_name` (optional) and `min_qscore` (optional).
- **Demultiplexing**: `kit_name`, proposed from the kit of the library of the run.
- **Conversion**: none.

Parameters accept letters, digits and `. _ @ -` only, so nothing typed in a form can become shell
syntax; every value in the script is also quoted.

!!! note "Command options"
    The options follow the Dorado and POD5 documentation. They were not run against the real tools
    during development: check `dorado demux --help` and `pod5 convert fast5 --help` on your cluster,
    as options can change between versions.

## Domain model

```text
SequencingRun ──< ComputeJob ──< JobInput  >── DataFile
                      └────────< JobOutput >── DataFile
```

| Model | Identifier | Purpose |
|---|---|---|
| `ComputeJob` | `WBF-J26-00001-5` | One calculation: kind, status, parameters, script, tool and version, scheduler job ID, dates, exit code |
| `JobInput` | none | A file the job reads, with the SHA-256 it had when the job was prepared |
| `JobOutput` | none | A file the job produced, registered in place |

## A job, step by step

1. **Draft.** From a sequencing run, **Basecall**, **Demultiplex** or **Convert FAST5 to POD5**.
   Choose the parameters and the input files (by default all those the job normally reads).
   Files that changed, are missing, are voided, on an offline medium, or of another project are
   refused. You can download the script to read it.
2. **Submitted.** **Submit** hands the job to the Slurm agent, which creates the working folder,
   writes `job.sh`, runs `sbatch` and stores the script as it was submitted. If Slurm refuses, the
   job stays a draft and the message is added to its notes.
3. **Running.** The agent asks Slurm how the job is doing at intervals.
4. **Succeeded, failed or cancelled.** A job that succeeded gets everything under `output/`
   registered in place (see [Storage](storage.md)) and linked to the job and the run. A failed job
   keeps its exit code and log path.

**Cancel the job** stops a draft directly and a queued or running job through `scancel`. A finished
job never changes: run the calculation again as a new job.

## The script

Each script, in order: the `#SBATCH` resources, `set -euo pipefail`, your setup line, the working
folder, the tool version in the log, then for every input a `sha256sum` check against the recorded
checksum and a link under `input/`, then the tool. **A single changed input stops the job before
the tool starts.** Results are left in `output/`.

## Configuration

All variables are optional except what your cluster needs; see `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `PORESIRA_COMPUTE_OUTPUT_DIR` | `<PORESIRA_DATA_DIR>/computing` | Folder of the working folders, one per job |
| `PORESIRA_COMPUTE_SETUP` | empty | A shell line run before the tools, for example `module load dorado/0.9.0` (trusted: written by the administrator) |
| `PORESIRA_DORADO_COMMAND` | `dorado` | The Dorado command, which may be several words |
| `PORESIRA_DORADO_VERSION` | empty | Recorded in each job |
| `PORESIRA_DORADO_MODELS_DIR` | empty | Passed as `--models-directory`; models must already be on the cluster |
| `PORESIRA_POD5_COMMAND`, `PORESIRA_POD5_VERSION` | `pod5`, empty | The same for POD5 |
| `PORESIRA_SLURM_PARTITION`, `PORESIRA_SLURM_ACCOUNT` | empty | `#SBATCH --partition`, `--account` |
| `PORESIRA_SLURM_GPUS`, `_CPUS`, `_MEM_GB`, `_TIME` | `1`, `8`, `64`, `24:00:00` | Resources (only basecalling asks for a GPU) |
| `PORESIRA_SLURM_BIN_DIR` | empty | Folder of `sbatch`, `squeue`, `sacct`, `scancel` if not on the `PATH` |
| `PORESIRA_COMPUTE_POLL_SECONDS` | `60` | Seconds between two questions to Slurm |

### Dorado in a container

Make the command a prefix that runs the container, and bind the data folder, which Apptainer does
not mount by default:

```bash
PORESIRA_DORADO_COMMAND="apptainer exec --nv --bind /srv/poresira-data /opt/dorado.sif dorado"
```

## The Slurm agent

The agent is the Celery worker on the `slurm` queue. It must run **where `sbatch` exists** (the
cluster's login node) and see the data and results folders **at the same paths** as the web
server, as every storage backend does:

```bash
celery -A config worker --queues slurm --hostname slurm@%h
```

The `slurm-agent` container of the `plateforme` profile mounts the data folder read-write but has no
Slurm client tools, so it can only serve as a placeholder; run the worker on the login node to
submit for real. If its follow-up is interrupted (a restart), `python manage.py refresh_jobs`
brings every submitted or running job up to date and can be run from cron. The job page does not
reload itself: reload it to see progress.

## Rules enforced by the database

- A job belongs to the project of its sequencing run; its files belong to that project too.
- Run, project and kind never change. After the draft, **parameters and script never change**.
- A finished job stays finished.
- A submitted job has its script and submission date; a finished job has an end date.
- Inputs are chosen while the job is a draft; outputs come from a running or succeeded job.
- A scheduler job ID is unique among active jobs.
- Void, never delete: a queued or running job must be cancelled first, and a job with results cannot
  be voided.

## Permissions

| Action | Who |
|---|---|
| See jobs and results | members of the project, administrators |
| Prepare, submit, cancel, void | project lead or contributor, administrator |

## Not included

Alignment, assembly and variant calling, and a local executor without Slurm, are not part of this
phase. The "Download the script" button lets you run a job by hand on a machine without the agent.
