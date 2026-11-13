# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load the demonstration data described in ``data/README.md`` through the normal services."""

import csv
import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import product
from pathlib import Path

from django.db import transaction

from poresira.core.models import GlobalRole, Project, ProjectMembership, ProjectRole, User
from poresira.core.reason import acting_as
from poresira.samples import services
from poresira.samples.importing import import_field_samples
from poresira.samples.models import Sample, SampleKind, SequenceRecord, StorageLocationKind

DATA_DIR = Path(__file__).parent / "data"
DEMO_PROJECT_CODE = "DEMO-SPBF"
SYNTHETIC_PROJECT_CODE = "DEMO-SYNTHETIC"
BIOPROJECT = "PRJNA1460897"
HOST_NAME, HOST_TAX_ID = "Ipomoea batatas", "4120"
GREENHOUSE_LOCALITY = "kamboinse"
DEMO_PLACEMENT_REASON = "Demonstration placement"
SYNTHETIC_SEED = 2026

DEMO_USERS = {
    "demo_platform": (GlobalRole.PLATFORM_MANAGER, None),
    "demo_lead": (GlobalRole.PROJECT_LEAD, ProjectRole.LEAD),
    "demo_technician": (GlobalRole.TECHNICIAN, ProjectRole.CONTRIBUTOR),
    "demo_viewer": (GlobalRole.GUEST, ProjectRole.VIEWER),
}
SYMPTOM_NAMES = {
    "A": "asymptomatic",
    "B": "blistering",
    "C": "chlorosis",
    "Cp": "chlorotic points",
    "I": "interveinal chlorosis",
    "Lc": "leaf curling",
    "Ld": "leaf distortion",
    "Np": "necrotic points",
    "S": "stunting",
    "Vc": "vein-clearing",
    "Vn": "vein necrosis",
}
PROJECT_DESCRIPTION = (
    "Sweetpotato virus survey in Burkina Faso, rebuilt from published data: BioProject "
    f"{BIOPROJECT} (NCBI) and Tibiri et al. 2020, Plant Pathology, doi:10.1111/ppa.13190 "
    "(Table 3). Sample names, places, dates and symptoms are published; the storage tree and box "
    "positions are illustrative. See poresira/samples/demo/data/README.md."
)


@dataclass
class LoadSummary:
    already_loaded: bool = False
    counts: dict[str, int] = field(default_factory=dict)


def _ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _read(filename: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def expand_symptoms(codes: str) -> str:
    names = [SYMPTOM_NAMES.get(code.strip(), code.strip()) for code in codes.split(",")]
    text = ", ".join(name for name in names if name)
    return text[:1].upper() + text[1:]


def parse_lat_lon(text: str) -> tuple[str, str]:
    latitude, north_south, longitude, east_west = text.split()
    return (
        str(Decimal(latitude) * (1 if north_south == "N" else -1)),
        str(Decimal(longitude) * (1 if east_west == "E" else -1)),
    )


def parse_geo_loc(text: str) -> tuple[str, str, str]:
    country, rest = text.split(": ")
    province, locality = rest.split(", ")
    return country, province, locality


def table3_stem(number: str, row: dict[str, str]) -> tuple[str, str]:
    """The same comparable stem for a Table 3 row: locality start and plant number."""
    return _ascii(row["locality"])[:3], number.removeprefix("BFA")


def split_isolate(isolate: str) -> tuple[str, str]:
    """``Tieb_E1_BFA297`` -> (``tie``, ``297``): the comparable locality stem and plant number."""
    prefix, number = isolate.split("_")[0], re.search(r"BFA(\d+)$", isolate).group(1)
    return _ascii(prefix)[:3], number


def grid_positions(rows: int = 9, columns: int = 9):
    for row, column in product("ABCDEFGHI"[:rows], range(1, columns + 1)):
        yield f"{row}{column}"


@dataclass
class _Plan:
    """Everything read from the data files, before anything is saved."""

    biosamples: list[dict[str, str]]
    table3: dict[str, dict[str, str]]
    field_biosamples: list[dict[str, str]] = field(default_factory=list)
    greenhouse_biosamples: list[dict[str, str]] = field(default_factory=list)
    table3_match: dict[str, str] = field(default_factory=dict)  # BioSample isolate -> Table 3 id


def _make_plan() -> _Plan:
    biosamples = _read("biosamples_PRJNA1460897.csv")
    plan = _Plan(
        biosamples=biosamples,
        table3={row["sample"]: row for row in _read("article_table3_grafted_plants.csv")},
    )
    for row in biosamples:
        locality = _ascii(parse_geo_loc(row["geo_loc_name"])[2])
        (
            plan.greenhouse_biosamples if locality == GREENHOUSE_LOCALITY else plan.field_biosamples
        ).append(row)
    table3_keys = {table3_stem(number, row): number for number, row in plan.table3.items()}
    for row in plan.field_biosamples:
        key = split_isolate(row["isolate"])
        if key in table3_keys:
            plan.table3_match[row["isolate"]] = table3_keys[key]
    return plan


def _create_users(password: str | None) -> dict[str, User]:
    users = {}
    for username, (global_role, _project_role) in DEMO_USERS.items():
        user, created = User.objects.get_or_create(
            username=username, defaults={"role": global_role}
        )
        if password:
            user.set_password(password)
            user.save(update_fields=["password"])
        elif created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        users[username] = user
    return users


def _create_project(code: str, name: str, description: str, users: dict[str, User]) -> Project:
    with acting_as(users["demo_lead"]):
        project = Project.objects.create(code=code, name=name, description=description)
        for username, (_global_role, project_role) in DEMO_USERS.items():
            if project_role:
                ProjectMembership.objects.create(
                    project=project, user=users[username], role=project_role
                )
    return project


def _build_storage(platform_user: User) -> dict[str, object]:
    def location(name, kind, parent=None, **attributes):
        return services.create_storage_location(
            user=platform_user, name=name, kind=kind, parent=parent, **attributes
        )

    room = location("LVBV laboratory, INERA Kamboinsé", StorageLocationKind.ROOM)
    cabinet = location("Biobank cabinet", StorageLocationKind.CABINET, room)
    freezer_20 = location(
        "Freezer -20 °C", StorageLocationKind.FREEZER, room, nominal_temperature_c=-20
    )
    freezer_80 = location(
        "Freezer -80 °C", StorageLocationKind.FREEZER, room, nominal_temperature_c=-80
    )
    grid = {"grid_rows": 9, "grid_columns": 9}
    return {
        "cabinet": cabinet,
        "dna_box": location("DNA box 1", StorageLocationKind.BOX, freezer_20, **grid),
        "rca_box": location("RCA box 1", StorageLocationKind.BOX, freezer_20, **grid),
        "rna_box": location("RNA box 1", StorageLocationKind.BOX, freezer_80, **grid),
    }


def _field_rows(plan: _Plan) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """CSV rows for the importer and, row for row, what each one stands for."""
    rows, origins = [], []
    host = {"host_taxon": HOST_NAME, "host_tax_id": HOST_TAX_ID}
    for biosample in plan.field_biosamples:
        country, province, locality = parse_geo_loc(biosample["geo_loc_name"])
        latitude, longitude = parse_lat_lon(biosample["lat_lon"])
        table3 = plan.table3.get(plan.table3_match.get(biosample["isolate"], ""))
        rows.append(
            {
                "label": biosample["isolate"],
                "collected_on": biosample["collection_date"],
                "country": country,
                "province": province,
                "locality": locality,
                "latitude": latitude,
                "longitude": longitude,
                "symptom_notes": expand_symptoms(table3["symptoms_ipomoea_batatas"])
                if table3
                else "",
                **host,
            }
        )
        origins.append({"kind": "biosample", "biosample": biosample, "table3": table3})

    provinces = {
        _ascii(parse_geo_loc(row["geo_loc_name"])[2]): parse_geo_loc(row["geo_loc_name"])[1]
        for row in plan.field_biosamples
    }
    matched_numbers = set(plan.table3_match.values())
    for number, table3 in plan.table3.items():
        if number in matched_numbers:
            continue
        rows.append(
            {
                "label": number,
                "country": "Burkina Faso",
                "province": provinces.get(_ascii(table3["locality"]), ""),
                "locality": table3["locality"],
                "event_notes": "Collection date and farm are not given in the source table.",
                "symptom_notes": expand_symptoms(table3["symptoms_ipomoea_batatas"]),
                **host,
            }
        )
        origins.append({"kind": "table3", "table3": table3})

    known_stems = {split_isolate(row["isolate"]) for row in plan.field_biosamples}
    known_stems |= {table3_stem(number, row) for number, row in plan.table3.items()}
    for biosample in plan.greenhouse_biosamples:
        stem = split_isolate(biosample["isolate"])
        if stem in known_stems:
            continue
        prefix = biosample["isolate"].split("_")[0]
        rows.append(
            {
                "label": f"{prefix}_BFA{stem[1]}",
                "country": "Burkina Faso",
                "event_notes": f"Locality abbreviated '{prefix}' in the sample name; "
                "collection details are not published.",
                "notes": "Field sample of the original accession, not described in the published "
                "metadata (inferred from the greenhouse plant).",
                **host,
            }
        )
        origins.append({"kind": "inferred", "stem": stem})
        known_stems.add(stem)
    return rows, origins


def _load_real_data(users: dict[str, User], project: Project, plan: _Plan) -> dict[str, int]:
    technician, platform = users["demo_technician"], users["demo_platform"]
    storage = _build_storage(platform)
    rows, origins = _field_rows(plan)
    report = import_field_samples(user=technician, project=project, rows=rows)

    by_stem: dict[tuple[str, str], Sample] = {}
    by_table3_number: dict[str, Sample] = {}
    biosample_fields: list[tuple[dict[str, str], Sample]] = []
    for sample, origin in zip(report.samples, origins, strict=True):
        if origin["kind"] == "biosample":
            by_stem[split_isolate(origin["biosample"]["isolate"])] = sample
            biosample_fields.append((origin["biosample"], sample))
            if origin["table3"]:
                by_table3_number[origin["table3"]["sample"]] = sample
        elif origin["kind"] == "table3":
            by_table3_number[origin["table3"]["sample"]] = sample
            number = origin["table3"]["sample"]
            by_stem[table3_stem(number, origin["table3"])] = sample
        else:
            by_stem[origin["stem"]] = sample

    def place(sample, location_key, positions):
        services.move_sample(
            user=technician,
            sample=sample,
            location=storage[location_key],
            position=next(positions) if location_key != "cabinet" else "",
            reason=DEMO_PLACEMENT_REASON,
        )

    for sample in report.samples:
        place(sample, "cabinet", None)

    dna_positions, rca_positions, rna_positions = (
        grid_positions(),
        grid_positions(),
        grid_positions(),
    )
    for biosample, field_sample in biosample_fields:
        label = biosample["isolate"]
        extract = services.derive_sample(
            user=technician,
            parent=field_sample,
            kind=SampleKind.DNA_EXTRACT,
            label=label,
            notes="Total DNA extracted from the dried leaf (CTAB method).",
        )
        amplified = services.derive_sample(
            user=technician,
            parent=extract,
            kind=SampleKind.AMPLIFICATION_PRODUCT,
            label=label,
            notes=f"Rolling circle amplification product, sequenced. BioSample "
            f"{biosample['biosample_accession']}, BioProject {BIOPROJECT}.",
        )
        place(extract, "dna_box", dna_positions)
        place(amplified, "rca_box", rca_positions)

    for number, table3 in plan.table3.items():
        parent = by_table3_number[number]
        services.derive_sample(
            user=technician,
            parent=parent,
            kind=SampleKind.LIVING_PLANT,
            label=f"{parent.label} graft",
            host_taxon="Ipomoea setosa",
            host_tax_id=None,
            symptom_notes=expand_symptoms(table3["symptoms_ipomoea_setosa"]),
            notes="Indicator plant grafted with scions of the sweetpotato sample.",
        )

    for biosample in plan.greenhouse_biosamples:
        parent = by_stem[split_isolate(biosample["isolate"])]
        plant = services.derive_sample(
            user=technician,
            parent=parent,
            kind=SampleKind.LIVING_PLANT,
            label=biosample["isolate"],
            notes="Greenhouse plant, INERA Kamboinsé; leaf sampled on "
            f"{biosample['collection_date']}.",
        )
        rna = services.derive_sample(
            user=technician,
            parent=plant,
            kind=SampleKind.RNA_EXTRACT,
            label=biosample["isolate"],
            notes=f"BioSample {biosample['biosample_accession']}, BioProject {BIOPROJECT}.",
        )
        place(rna, "rna_box", rna_positions)
    return {}


def _link_genomes(users: dict[str, User], project: Project) -> int:
    """Attach each published sequence to the sequenced material: the RCA product for DNA viruses,
    the RNA extract for RNA viruses. Samples are found by label, which is the BioSample isolate."""
    technician = users["demo_technician"]
    kinds = {"genomic DNA": SampleKind.AMPLIFICATION_PRODUCT, "genomic RNA": SampleKind.RNA_EXTRACT}
    created = 0
    for row in _read("sequences_PRJNA1460897.csv"):
        sample = Sample.objects.get(
            project=project,
            kind=kinds[row["mol_type"]],
            label=row["biosample_isolate"],
            voided_at__isnull=True,
        )
        if sample.sequence_records.filter(accession=row["accession"]).exists():
            continue
        note = f"Isolate {row['isolate']} in GenBank. Published in doi:{row['publication']}."
        if row["match_note"]:
            note += " " + row["match_note"]
        services.record_sequence(
            user=technician,
            sample=sample,
            accession=row["accession"],
            version=int(row["version"]),
            organism=row["organism"],
            isolate=row["isolate"],
            length_bp=int(row["length_bp"]),
            topology=row["topology"],
            assembly_method=row["assembly_method"],
            biosample_accession=row["biosample_accession"],
            bioproject_accession=BIOPROJECT,
            notes=note,
        )
        created += 1
    return created


def _load_synthetic(users: dict[str, User], count: int) -> None:
    technician = users["demo_technician"]
    project = _create_project(
        SYNTHETIC_PROJECT_CODE,
        "Synthetic volume test data",
        "Generated records for load testing. None of it is real.",
        users,
    )
    generator = random.Random(SYNTHETIC_SEED)  # noqa: S311 - reproducible test data
    events = [
        services.create_collection_event(
            user=technician,
            project=project,
            collected_on=date(2026, 1, 1 + number % 28),
            country="Nowhere",
            locality=f"Synthetic site {number}",
            notes="Synthetic demo record.",
        )
        for number in range(20)
    ]
    for number in range(count):
        field_sample = services.register_field_sample(
            user=technician,
            collection_event=generator.choice(events),
            label=f"SYN-{number:05d}",
            notes="Synthetic demo record.",
        )
        if generator.random() < 0.6:
            extract = services.derive_sample(
                user=technician, parent=field_sample, kind=SampleKind.DNA_EXTRACT
            )
            if generator.random() < 0.5:
                services.derive_sample(
                    user=technician, parent=extract, kind=SampleKind.AMPLIFICATION_PRODUCT
                )


def _counts(project: Project) -> dict[str, int]:
    samples = Sample.objects.filter(project=project)
    counts = {"collection events": project.collection_events.count()}
    for kind in SampleKind:
        counts[f"{kind.label}s"] = samples.filter(kind=kind).count()
    counts["sequences"] = SequenceRecord.objects.filter(sample__project=project).count()
    return counts


@transaction.atomic
def load_demo_data(*, password: str | None = None, synthetic_volume: int = 0) -> LoadSummary:
    """Create the demo accounts, project, storage tree and samples. Does nothing if present."""
    users = _create_users(password)
    summary = LoadSummary()
    project = Project.objects.filter(code=DEMO_PROJECT_CODE).first()
    if project is None:
        project = _create_project(
            DEMO_PROJECT_CODE,
            "Sweetpotato viruses, Burkina Faso (published data)",
            PROJECT_DESCRIPTION,
            users,
        )
        _load_real_data(users, project, _make_plan())
    else:
        summary.already_loaded = True
    _link_genomes(users, project)  # also completes a database loaded before genomes existed
    summary.counts = _counts(project)
    if synthetic_volume and not Project.objects.filter(code=SYNTHETIC_PROJECT_CODE).exists():
        _load_synthetic(users, synthetic_volume)
        summary.counts["synthetic field samples"] = synthetic_volume
    return summary
