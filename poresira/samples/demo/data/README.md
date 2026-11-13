<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Demonstration data: provenance

These files are the published data behind `manage.py load_demo_data`. Nothing here is invented.

## `biosamples_PRJNA1460897.csv`

The 66 BioSample records of NCBI BioProject
[PRJNA1460897](https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1460897) (sweetpotato samples from
Burkina Faso, Oxford Nanopore sequencing), retrieved from NCBI on 2026-11-12 through the public
E-utilities, columns `accession`, `isolate`, `collection_date`, `geo_loc_name`, `lat_lon`, `host`
unchanged. NCBI metadata is public domain.

- 53 records are field samples collected in 2016 (the samples whose rolling circle amplification
  worked, see the Frontiers in Microbiology 2026 study, PMID 41716274).
- 13 records were collected in 2024-2025 from plants in the INERA greenhouse at Kamboinsé
  (the fresh leaves used for the RNA virome in the same study).

Known oddities, kept as published: the sample `Tou_BFA932` (Tougan, province Sourou) carries
coordinates in the south-west (11.39 N, 5.05 W) that do not match Tougan; the numbers `BFA728`,
`BFA781` and `BFA880` each appear with two different locality prefixes.

## `article_table3_grafted_plants.csv`

Table 3 of Tibiri et al. 2020, *Plant Pathology* 69(6):1003-1017
([10.1111/ppa.13190](https://doi.org/10.1111/ppa.13190)), open access under the Creative Commons
Attribution licence (CC BY 4.0): the 50 sweetpotato samples indexed by grafting onto
*Ipomoea setosa*. Only the locality, sample number and the symptom codes observed on the sweetpotato
and on the grafted *I. setosa* are kept here; the serological and molecular results of that table
belong to the PCR phase and will be added then. Transcribed by hand from the published table,
with two typographical normalisations (`VC, B` written `Vc, B`, a trailing comma removed).

Symptom codes: A asymptomatic, B blistering, C chlorosis, Cp chlorotic points, I interveinal
chlorosis, Lc leaf curling, Ld leaf distortion, Np necrotic points, S stunting, Vc vein-clearing,
Vn vein necrosis. The code `P` appears once in the table without being defined in its legend and is
kept as written.

## What the loader adds, and what it does not

- **Real:** sample names, localities, provinces, collection dates and coordinates (BioSamples),
  symptom codes (Table 3), the lineage logic (field sample, DNA extract, RCA product; field sample,
  greenhouse plant, RNA extract; field sample, grafted *I. setosa* plant), storage temperatures
  (DNA at -20 C, RNA at -80 C, as described in the articles).
- **Illustrative:** the storage tree, the box positions (assigned in order) and the demo user
  accounts. Real positions and instrument readings (NanoDrop, Qubit) are not published, so none
  are generated.
- **Inferred:** nine greenhouse plants have no matching field sample in the published metadata; a
  field sample is created for each, flagged in its notes.
- Names Table 3 and the BioSamples spell differently (`Tiébélé` and `Tiebele`) are kept as
  published; provinces are filled only when a locality matches a BioSample locality ignoring
  accents.

## `sequences_PRJNA1460897.csv`

The 54 complete sequences deposited in GenBank for these samples, from the GenBank flat files
(retrieved 2026-11-13), with the publication each one belongs to:

| Accessions | Content | Publication |
|---|---|---|
| PV947737-PV947756 | 20 sweet potato leaf curl virus (circular DNA) | Front Microbiol 2026, doi:10.3389/fmicb.2026.1722370 |
| PV947757-PV947760 | 4 sweet potato feathery mottle virus (RNA) | same |
| PV947677-PV947699 | 23 sweet potato leaf curl deltasatellite | same (GenBank cites it) |
| PV405574-PV405580 | pepper yellow vein Mali virus, 3 CLCuGe alphasatellites, 3 betasatellites | Viruses 2025, doi:10.3390/v17091222 |

GenBank records carry no BioSample link, so the match is made on the isolate name, date and
coordinates (column `match`, with `match_note` where it is not exact):

- 43 records equal a BioSample isolate exactly, with identical date and coordinates.
- 7 records of the Viruses paper have no locality prefix (`BFA942`); each has a single BioSample
  with that number, the same date and the same coordinates, except `BFA1094`: GenBank gives
  2016-08-18 and the BioSample `Ban_BFA1094` 2016-08-16.
- 4 SPFMV records (`Tie_E1`, `Tie_E3`, `Heere_E1`, `Heere_E2`) are named differently from their
  BioSample (`Tieb_E1_BFA297`, `Tieb_E3_BFA265`, `Heere_E1_BFA728`, `Heere_E2_BFA880`) and are
  matched on the E number, the 2025-05-17 date and the Kamboinse coordinates.

Every inferred match is flagged in the notes of the sequence record and should be confirmed.
