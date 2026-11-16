# SPDX-License-Identifier: AGPL-3.0-or-later
import io
import json
import zipfile
from datetime import timedelta

import pytest
from django.utils import timezone

from poresira.computing.models import ComputeJob, JobStatus
from poresira.computing.tests.factories import (
    ComputeJobFactory,
    JobInputFactory,
    JobOutputFactory,
)
from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.deposits import services
from poresira.deposits.models import DepositStatus, ItemKind
from poresira.deposits.rocrate import build_ro_crate, ro_crate_zip
from poresira.deposits.tests.test_deposit_data import reads_file, sequenced

pytestmark = pytest.mark.django_db


@pytest.fixture
def deposit():
    project = ProjectFactory()
    editor = ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
    run, _members = sequenced(project, barcodes=(1, 2))
    reads_file(project, run, "pass/barcode01/a.fastq.gz")
    reads_file(project, run, "pass/barcode02/a.fastq.gz")
    return services.prepare_deposit(user=editor, run=run, title="Sweetpotato viromes")


def entities(crate):
    return {entity["@id"]: entity for entity in crate["@graph"]}


def test_the_crate_describes_the_deposit_its_samples_files_and_sequencing(deposit):
    crate = build_ro_crate(deposit)
    graph = entities(crate)

    assert crate["@context"] == "https://w3id.org/ro/crate/1.1/context"
    assert graph["ro-crate-metadata.json"]["about"] == {"@id": "./"}
    root = graph["./"]
    assert root["name"] == "Sweetpotato viromes" and root["identifier"] == deposit.identifier
    samples = [e for e in graph.values() if e.get("@type") == "BioSample"]
    assert len(samples) == 2
    properties = {p["name"]: p["value"] for p in samples[0]["additionalProperty"]}
    assert properties["geographic location (country and/or sea)"] == "Burkina Faso"
    files = [e for e in graph.values() if e.get("@type") == "File"]
    assert len(files) == 2 and all(len(f["sha256"]) == 64 for f in files)
    assert {part["@id"] for part in root["hasPart"]} == {f["@id"] for f in files}
    actions = [e for e in graph.values() if e["@id"].startswith("#sequencing-")]
    assert len(actions) == 2
    assert actions[0]["instrument"] == {
        "@id": next(i for i in graph if i.startswith("#instrument-"))
    }
    assert "Dorado 0.9.0" in actions[0]["description"]


def test_no_person_is_named_and_nothing_is_empty(deposit):
    event = deposit.items.filter(kind=ItemKind.SAMPLE).first().sample.collection_event
    event.collector = "A Farmer"
    event.save()

    text = json.dumps(build_ro_crate(deposit))

    assert "A Farmer" not in text and "collector" not in text.lower()
    assert '""' not in text and "null" not in text


def test_accessions_become_links_to_the_archive(deposit):
    item = deposit.items.filter(kind=ItemKind.SAMPLE).first()
    type(deposit).objects.filter(pk=deposit.pk).update(
        status=DepositStatus.SUBMITTING, submitted_at=timezone.now(), submitted_by=UserFactory()
    )
    type(item).objects.filter(pk=item.pk).update(accession="SAMEA1234567")

    graph = entities(build_ro_crate(deposit))

    biosample = graph[f"#sample-{item.sample.identifier}"]
    assert biosample["sameAs"] == ["https://www.ebi.ac.uk/ena/browser/view/SAMEA1234567"]


def test_a_succeeded_compute_job_is_described_as_software_provenance(deposit):
    run = deposit.items.filter(kind=ItemKind.READS).first().sequencing_run
    now = timezone.now()
    job = ComputeJobFactory(
        project=deposit.project,
        sequencing_run=run,
        parameters={"model": "sup"},
        tool="dorado",
        tool_version="0.9.0",
    )
    JobInputFactory(job=job, file=_draft_file(job))
    ComputeJob.objects.filter(pk=job.pk).update(
        status=JobStatus.SUCCEEDED,
        script="#!/bin/bash\n",
        submitted_at=now,
        started_at=now,
        finished_at=now + timedelta(hours=2),
    )
    job.refresh_from_db()
    JobOutputFactory(job=job, file=_any_file(job))

    graph = entities(build_ro_crate(deposit))

    action = graph[f"#job-{job.identifier}"]
    software = graph[action["instrument"]["@id"]]
    assert (software["name"], software["softwareVersion"]) == ("dorado", "0.9.0")
    assert action["additionalProperty"] == [
        {"@type": "PropertyValue", "name": "model", "value": "sup"}
    ]
    assert len(action["object"]) == 1 and len(action["result"]) == 1


def _draft_file(job):
    from poresira.storage.tests.factories import DataFileFactory

    return DataFileFactory(project=job.project, sequencing_run=job.sequencing_run, kind="pod5")


def _any_file(job):
    from poresira.storage.tests.factories import DataFileFactory

    return DataFileFactory(project=job.project, sequencing_run=job.sequencing_run, kind="bam")


def test_the_zip_holds_the_metadata_file(deposit):
    archive = zipfile.ZipFile(io.BytesIO(ro_crate_zip(deposit)))

    assert archive.namelist() == ["ro-crate-metadata.json"]
    document = json.loads(archive.read("ro-crate-metadata.json"))
    assert document["@graph"][1]["@id"] == "./"
