# SPDX-License-Identifier: AGPL-3.0-or-later
"""PCR demo data: the primers and protocols of Tibiri et al. 2020 and the results of its Table 3.

Everything here is as published. What the paper does not give (run date, plate layout, controls,
band sizes) is not invented: the three runs are marked as reconstructed from a publication.
"""

import csv
from pathlib import Path

from poresira.core.models import Project, User
from poresira.pcr import services
from poresira.pcr.importing import import_pcr_run
from poresira.pcr.models import Assay, PcrRun, Primer
from poresira.samples.models import Sample

DATA_DIR = Path(__file__).parent / "data"
PUBLICATION = "Tibiri et al. 2020, Plant Pathology 69(6):1003-1017, doi:10.1111/ppa.13190"

# Orientation (forward/reverse) was checked by aligning each primer to the GenBank genomes
# PV947757 (SPFMV) and PV947737 (SPLCV); the paper lists them in a different order.
PRIMERS = [
    ("CP1S", "AGTGGGAAGGCACCATACATAGC", "forward", "SPFMV coat protein", "Prasanth and Hegde 2008"),
    (
        "CP1A",
        "GCAGAGGATGTCCTATTGCACACC",
        "reverse",
        "SPFMV coat protein",
        "Prasanth and Hegde 2008",
    ),
    ("CP-F", "ATGGCTGATAGCACTAAAGTCGA", "forward", "SPCSV coat protein", "Qin et al. 2014"),
    ("CP-R", "TCAACAGTGAAGACCTGTTCCAG", "reverse", "SPCSV coat protein", "Qin et al. 2014"),
    ("SPG1", "CCCCKGTGCGWRAATCCAT", "forward", "Sweepovirus coat protein", "Li et al. 2004"),
    ("SPG2", "ATCCVAAYWTYCAGGGAGCTAA", "reverse", "Sweepovirus coat protein", "Li et al. 2004"),
]


def _program(annealing, extension_s, *, initial_s, denaturation="94", cycle_s=30):
    """Initial step, three cycle steps (``cycle_s`` seconds each except extension), final step."""

    def step(stage, label, temperature, seconds):
        return {
            "stage": stage,
            "label": label,
            "temperature_c": temperature,
            "duration_seconds": seconds,
        }

    return [
        step("initial", "Initial denaturation", "94", initial_s),
        step("cycle", "Denaturation", denaturation, cycle_s),
        step("cycle", "Annealing", annealing, cycle_s),
        step("cycle", "Extension", "72", extension_s),
        step("final", "Final extension", "72", 600),
    ]


# One entry per assay: protocol of the paper's section 2.3. The SPLCV recipe is the only one given
# in full; for the other two the paper gives primer concentration and cDNA volume only.
ASSAYS = [
    {
        "name": "SPFMV CP RT-PCR",
        "target": "Sweet potato feathery mottle virus, coat protein gene",
        "primers": ("CP1S", "CP1A"),
        "program": ("SPFMV RT-PCR, 56.3 °C annealing", _program("56.3", 60, initial_s=180)),
        "reverse_transcription": True,
        "expected_amplicon_bp": 945,
        "template_volume_ul": "2.5",
        "notes": "Maximo Taq DNA polymerase (GeneON), 50 µL, 0.2 µM of each primer, 2.5 µL cDNA "
        "(random-hexamer reverse transcription, MMLV, 42 °C for 1 h). Amplicon size as reported.",
        "sample_column": "pcr_spfmv",
    },
    {
        "name": "SPCSV CP RT-PCR",
        "target": "Sweet potato chlorotic stunt virus, coat protein gene",
        "primers": ("CP-F", "CP-R"),
        "program": ("SPCSV RT-PCR, 58 °C annealing", _program("58", 45, initial_s=180)),
        "reverse_transcription": True,
        "expected_amplicon_bp": 774,
        "template_volume_ul": "3",
        "notes": "LA Taq DNA polymerase (TaKaRa), 50 µL, 0.2 µM of each primer, 3 µL cDNA "
        "(random-hexamer reverse transcription, MMLV, 42 °C for 1 h). Amplicon size as reported.",
        "sample_column": "pcr_spcsv",
    },
    {
        "name": "SPLCV CP PCR",
        "target": "Sweet potato leaf curl virus (sweepoviruses), coat protein gene",
        "primers": ("SPG1", "SPG2"),
        "program": (
            "SPLCV PCR, 60 °C annealing",
            _program("60", 60, initial_s=300, denaturation="90", cycle_s=40),
        ),
        "mix": {
            "name": "Maximo Taq 50 µL, SPLCV (Tibiri et al. 2020)",
            "polymerase": "Maximo Taq DNA polymerase (GeneON)",
            "reaction_volume_ul": 50,
            "components": [
                {"name": "Primer SPG1", "concentration": "10 µM", "volume_ul": 1},
                {"name": "Primer SPG2", "concentration": "10 µM", "volume_ul": 1},
                {"name": "dNTP mix", "concentration": "10 mM", "volume_ul": 1},
                {"name": "Taq reaction buffer", "concentration": "10x", "volume_ul": 5},
                {"name": "Maximo Taq DNA polymerase", "concentration": "1 U", "volume_ul": "0.2"},
                {"name": "Water", "volume_ul": "39.8"},
            ],
        },
        "reverse_transcription": False,
        "expected_amplicon_bp": 792,
        "template_volume_ul": "2",
        "notes": "DNA extracted by the CTAB method. The denaturation step is 90 °C for 40 s as "
        "printed in the paper (94 °C is usual). Amplicon size as reported.",
        "sample_column": "pcr_splcv",
    },
]


def _get_or_create_reference(platform: User) -> dict[str, Assay]:
    primers = {}
    for name, sequence, direction, target, reference in PRIMERS:
        primers[name] = Primer.objects.active().filter(name=name).first() or services.create_primer(
            user=platform,
            name=name,
            sequence=sequence,
            direction=direction,
            target=target,
            reference=reference,
        )
    assays = {}
    for spec in ASSAYS:
        assay = Assay.objects.active().filter(name=spec["name"]).first()
        if assay is None:
            program_name, steps = spec["program"]
            program = services.create_cycling_program(
                user=platform,
                name=program_name,
                cycles=30,
                steps=steps,
                reference=PUBLICATION,
            )
            mix = None
            if "mix" in spec:
                mix = services.create_master_mix(
                    user=platform, reference=PUBLICATION, **spec["mix"]
                )
            assay = services.create_assay(
                user=platform,
                name=spec["name"],
                target=spec["target"],
                forward_primer=primers[spec["primers"][0]],
                reverse_primer=primers[spec["primers"][1]],
                cycling_program=program,
                master_mix=mix,
                reverse_transcription=spec["reverse_transcription"],
                expected_amplicon_bp=spec["expected_amplicon_bp"],
                reference=PUBLICATION,
                notes=spec["notes"],
            )
        assays[spec["name"]] = assay
    return assays


def load_pcr_demo(
    users: dict[str, User], project: Project, field_samples: dict[str, Sample]
) -> int:
    """Create the primers, programs and assays, and one reconstructed run per assay.

    ``field_samples`` maps each Table 3 sample number to its field sample. Returns the number of
    runs created (zero when they already exist).
    """
    assays = _get_or_create_reference(users["demo_platform"])
    with (DATA_DIR / "article_table3_pcr_results.csv").open(newline="", encoding="utf-8") as file:
        table = list(csv.DictReader(file))
    created = 0
    for spec in ASSAYS:
        assay = assays[spec["name"]]
        if PcrRun.objects.filter(project=project, assay=assay).exists():
            continue
        rows = [
            {
                "sample": field_samples[row["sample"]].identifier,
                "result": row[spec["sample_column"]],
                "template_volume_ul": spec["template_volume_ul"],
            }
            for row in table
        ]
        report = import_pcr_run(
            user=users["demo_technician"],
            project=project,
            assay=assay,
            rows=rows,
            reconstructed=True,
            close=True,
            notes=f"Results transcribed from Table 3 of {PUBLICATION}. Date, plate layout, "
            "controls and band sizes are not published; wells follow the order of the table.",
        )
        created += bool(report.reactions)
    return created
