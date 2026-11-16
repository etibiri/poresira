# SPDX-License-Identifier: AGPL-3.0-or-later
"""Describe a deposit as a Research Object Crate: what the data are, where they come from and
how they were made. The crate describes the files (name, size, SHA-256) without containing them,
and names no person. No network and no dependency: it is JSON-LD."""

import io
import json
import zipfile
from datetime import date

from django.conf import settings

from poresira.computing.models import ComputeJob, JobStatus

from .collect import sample_spec
from .models import Deposit, ItemKind

CONTEXT = "https://w3id.org/ro/crate/1.1/context"
PROFILE = "https://w3id.org/ro/crate/1.1"
ENA_VIEW = "https://www.ebi.ac.uk/ena/browser/view/"
METADATA_NAME = "ro-crate-metadata.json"
MENTIONED = ("#sample-", "#sequencing-", "#job-")


def _ref(identifier: str) -> dict:
    return {"@id": identifier}


def _property(name: str, value, units: str = "") -> dict:
    return _clean({"@type": "PropertyValue", "name": name, "value": value, "unitText": units})


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _clean(entity: dict) -> dict:
    return {key: value for key, value in entity.items() if value not in (None, "", [], {})}


def _file_entity(data_file) -> dict:
    return _clean(
        {
            "@id": f"#file-{data_file.identifier}",
            "@type": "File",
            "name": data_file.name,
            "identifier": data_file.identifier,
            "contentSize": data_file.size_bytes,
            "sha256": data_file.sha256,
            "encodingFormat": data_file.get_kind_display(),
        }
    )


def _accession_urls(*accessions: str) -> list[str]:
    return [f"{ENA_VIEW}{accession}" for accession in accessions if accession]


def build_ro_crate(deposit: Deposit) -> dict:
    """The ``ro-crate-metadata.json`` of a deposit, as a dictionary."""
    items = list(
        deposit.items.active().select_related(
            "sample__collection_event",
            "sequencing_run__instrument",
            "sequencing_run__library__kit",
            "sequencing_run__flow_cell__type",
        )
    )
    entities: dict[str, dict] = {}
    parts: list[str] = []

    def add(entity: dict) -> dict:
        entities[entity["@id"]] = _clean(entity)
        return _ref(entity["@id"])

    sample_refs = {}
    for item in (i for i in items if i.kind == ItemKind.SAMPLE):
        spec = sample_spec(item)
        sample_refs[item.pk] = add(
            {
                "@id": f"#sample-{item.sample.identifier}",
                "@type": "BioSample",
                "name": spec.title,
                "identifier": item.sample.identifier,
                "sameAs": _accession_urls(item.accession),
                "taxonomicRange": spec.scientific_name,
                "additionalProperty": [
                    _clean(
                        {
                            "@type": "PropertyValue",
                            "name": a.tag,
                            "value": a.value,
                            "unitText": a.units,
                        }
                    )
                    for a in spec.attributes
                ],
            }
        )

    run_ids: set = set()
    for item in (i for i in items if i.kind == ItemKind.READS):
        run = item.sequencing_run
        run_ids.add(run.pk)
        instrument = add(
            {
                "@id": f"#instrument-{run.instrument_id}"
                if run.instrument
                else "#instrument-unknown",
                "@type": "IndividualProduct",
                "name": run.instrument.model if run.instrument else "Unknown instrument",
                "serialNumber": run.instrument.serial_number if run.instrument else "",
            }
        )
        file_refs = []
        for row in item.files.active().select_related("file").order_by("file__path"):
            file_refs.append(add(_file_entity(row.file)))
            parts.append(f"#file-{row.file.identifier}")
        add(
            {
                "@id": f"#sequencing-{run.identifier}-{item.sample.identifier}",
                "@type": "CreateAction",
                "name": f"Sequencing of {item.sample.label or item.sample.identifier}",
                "description": (
                    f"{run.library.kit.code} on {run.flow_cell.type.code}; "
                    f"basecalled with {run.basecaller} {run.basecaller_version} "
                    f"{run.basecall_model}".strip()
                ),
                "identifier": [value for value in (run.identifier, run.run_id) if value],
                "sameAs": _accession_urls(item.accession, item.secondary_accession),
                "startTime": _iso(run.started_on),
                "instrument": instrument,
                "object": sample_refs.get(item.sample_item_id),
                "result": file_refs,
            }
        )

    jobs = ComputeJob.objects.active().filter(
        sequencing_run__in=run_ids, status=JobStatus.SUCCEEDED
    )
    for job in jobs.order_by("created_at"):
        software = add(
            {
                "@id": f"#software-{job.identifier}",
                "@type": "SoftwareApplication",
                "name": job.tool,
                "softwareVersion": job.tool_version,
            }
        )
        inputs = job.inputs.active().select_related("file")
        outputs = job.outputs.active().select_related("file")
        add(
            {
                "@id": f"#job-{job.identifier}",
                "@type": "CreateAction",
                "name": f"{job.get_kind_display()} ({job.identifier})",
                "instrument": software,
                "startTime": _iso(job.started_at),
                "endTime": _iso(job.finished_at),
                "additionalProperty": [
                    {"@type": "PropertyValue", "name": name, "value": value}
                    for name, value in job.parameters.items()
                ],
                "object": [add(_file_entity(row.file)) for row in inputs],
                "result": [add(_file_entity(row.file)) for row in outputs],
            }
        )

    root = _clean(
        {
            "@id": "./",
            "@type": "Dataset",
            "name": deposit.title,
            "description": deposit.description or deposit.title,
            "identifier": deposit.identifier,
            "datePublished": date.today().isoformat(),
            "license": getattr(settings, "PORESIRA_CRATE_LICENSE", ""),
            "hasPart": [_ref(part) for part in sorted(set(parts))],
            "mentions": [
                _ref(key)
                for key in entities
                if key.startswith(("#sample-", "#sequencing-", "#job-"))
            ],
        }
    )
    descriptor = {
        "@id": METADATA_NAME,
        "@type": "CreativeWork",
        "conformsTo": _ref(PROFILE),
        "about": _ref("./"),
    }
    return {"@context": CONTEXT, "@graph": [descriptor, root, *entities.values()]}


def ro_crate_zip(deposit: Deposit) -> bytes:
    """The crate as a zip file holding ``ro-crate-metadata.json``."""
    document = json.dumps(build_ro_crate(deposit), indent=2, ensure_ascii=False) + "\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(METADATA_NAME, document)
    return buffer.getvalue()
