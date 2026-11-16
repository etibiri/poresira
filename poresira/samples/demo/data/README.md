<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Demonstration data: provenance

These files are the real data behind `manage.py load_demo_data`: public data (NCBI, open-access articles) and the maintainer's own laboratory records (`thesis_98_samples.csv`). Nothing here is invented.

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
and on the grafted *I. setosa* are kept here; its molecular results are in `article_table3_pcr_results.csv`
(below); the serological (NCM-ELISA) results are not loaded. Transcribed by hand from the published table,
with two typographical normalisations (`VC, B` written `Vc, B`, a trailing comma removed).

Symptom codes: A asymptomatic, B blistering, C chlorosis, Cp chlorotic points, I interveinal
chlorosis, Lc leaf curling, Ld leaf distortion, Np necrotic points, S stunting, Vc vein-clearing,
Vn vein necrosis. The code `P` appears once in the table without being defined in its legend and is
kept as written.

## `thesis_98_samples.csv`

The 98 sweetpotato samples collected in 2016 and followed in the maintainer's laboratory, from two
spreadsheets of the thesis work of the maintainer (`98Samples_selected.xlsx`, field data; and
`98Samples_Extraction_PCR_RCA_Information.xlsx`, laboratory results), joined on the sample code.
The spreadsheets themselves, the gel images and the slides stay outside the repository.

| Column | Meaning |
|---|---|
| `sample_code`, `field_no` | Sample code (`BFA81`) and the number written in the field notebook |
| `village`, `district`, `province`, `region` | Place; the district is the locality used in the BioSamples |
| `latitude`, `longitude` | **Rounded to 0.01°** (about 1 km), the precision NCBI already publishes; the GPS readings in the spreadsheet are exact to the plot and are not versioned |
| `collected_on` | Date of the GPS reading |
| `variety` | Sweetpotato variety, when known (`Inconnu` is left blank) |
| `symptom_scale` | Column `Echelle`, the 1-9 symptom severity scale (confirmed by the author) |
| `dna_ng_ul`, `a260_280`, `a260_230` | NanoDrop reading of the CTAB DNA extract |
| `spg_pcr` | Sweepovirus detection by PCR with the SPG1/SPG2 primers |
| `templiphi_*`, `equiphi29_*` | Whether rolling circle amplification was tried with each kit, and whether the product migrated on agarose gel (positive/negative) |
| `sequenced` | Selected for Oxford Nanopore sequencing |

Checked against the other files: the 53 samples selected for sequencing are exactly the 53 field
BioSamples of 2016; each of them had a successful amplification (51 with TempliPhi, 2 with
EquiPhi29 after TempliPhi failed); all 98 collection dates are 2016, and every date agrees with the
BioSample. The spreadsheet also holds a second date column (`ltime`) that differs from the GPS date
for five samples (BFA251, BFA316, BFA1093, BFA1094, BFA1107); it is not loaded. It explains the date
2016-08-18 of the GenBank record PV405577, whose BioSample date 2016-08-16 is the right one.

What this settles about earlier questions: the collection year is 2016; the 98 samples are those of
the laboratory file, of which 53 are in the BioSamples; `Tou_BFA932` has the same coordinates
(11.39 N, 5.05 W) in the laboratory file as in the BioSample, so the oddity is in the GPS reading,
not in the NCBI record; `Pampangou` is the village and `Dianpagou` the district of BFA496.

The SPG1/SPG2 PCR follows the protocol of the 2020 article (confirmed by the author). The village
`Boura -GH` is near the Ghana border; the loader writes `Boura` and notes the border. Place names are
loaded without accents, as the BioSamples spell them (`Tiebele`, `Kenedougou`).

Not recorded in the file, and therefore not invented: the dates of extraction, PCR and RCA, the PCR
plate layout and controls, the thermocycler program, and the template volumes.

## What the loader adds, and what it does not

- **Real:** sample names, localities, provinces, collection dates and coordinates (BioSamples),
  symptom codes (Table 3), the lineage logic (field sample, DNA extract, RCA product; field sample,
  greenhouse plant, RNA extract; field sample, grafted *I. setosa* plant), storage temperatures
  (DNA at -20 C, RNA at -80 C, as described in the articles).
- **Illustrative:** the storage tree, the box positions (assigned in order) and the demo user
  accounts. Real box positions are not recorded, so none are invented. The NanoDrop readings of the
  98 DNA extracts are real (`thesis_98_samples.csv`).
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
  2016-08-18 and the BioSample `Ban_BFA1094` 2016-08-16. The BioSample date is the correct one
  (confirmed by the submitting author); the GenBank record carries the wrong date.
- 4 SPFMV records (`Tie_E1`, `Tie_E3`, `Heere_E1`, `Heere_E2`) are named differently from their
  BioSample (`Tieb_E1_BFA297`, `Tieb_E3_BFA265`, `Heere_E1_BFA728`, `Heere_E2_BFA880`) and are
  matched on the E number, the 2025-05-17 date and the Kamboinse coordinates; the submitting author
  confirmed this match (column `match` = `confirmed`).

The other inferred matches (the 7 without prefix) are flagged in the notes of the sequence record.

## `article_table3_pcr_results.csv`

The RT-PCR and PCR columns of Table 3 of Tibiri et al. 2020 for the same 50 samples: SPFMV, SPCSV
and SPLCV, `positive` or `negative`, transcribed by hand from the published table (25, 21 and 19
positive samples; the three samples marked as triple infections are positive for all three).
The NCM-ELISA columns are not included.

The loader builds three runs from it, one per assay (SPFMV and SPCSV RT-PCR, SPLCV PCR), each
with 50 reactions whose template is the field sample (the paper reports results per sample, not
per extract). The runs are marked as reconstructed from a publication: the paper gives no run
date, plate layout, controls, Ct or band sizes, so none is recorded and the runs are never
judged valid or invalid. Wells follow the order of the table.

The primers and cycling programs are those of section 2.3 of the paper (Prasanth and Hegde 2008,
Qin et al. 2014, Li et al. 2004), with the paper's reported amplicon sizes (about 945, 774 and
792 nucleotides). Details the paper leaves to be checked:

- The paper lists `CP1A` before `CP1S` and `SPG1` before `SPG2`. Their orientation was checked by
  aligning them to the GenBank genomes PV947757 (SPFMV) and PV947737 (SPLCV): `CP1S` and `SPG1`
  match the genome strand (forward), `CP1A` and `SPG2` its complement (reverse). `CP-F` starts
  with a start codon and `CP-R` ends with a stop codon, so they are forward and reverse.
- The paper assigns `CP1A`/`CP1S` to SPFMV and `CP-F`/`CP-R` to SPCSV; the author confirmed it.
- The SPLCV program prints a denaturation of 90 °C for 40 s; it is kept as published (94 °C is
  the usual value).
- Only the SPLCV reaction mix is given in full. For the two RT-PCR assays the paper gives the
  polymerase, the primer concentration and the cDNA volume, which are in the assay notes.

## sequencing_barcodes.csv

The barcode of each sequenced sample, flow cell and sequencing date, copied from the laboratory
sheet `Samples_Barcode_correspondance.xlsx` (kept outside the repository). 53 samples: 24 on
flow cell FAT93267 (BC01-BC24, 2023-11-09), 28 on FAV02927 (BC25-BC52, 2023-11-11) and BFA316 on
the Flongle AMF735 (2022-11-04, no barcode, as the laboratory confirmed: the thesis document calls it BC53,
which is not used here). The 53 codes are exactly the samples marked as sequenced in
`thesis_98_samples.csv`, with the same symptom scale. The run details come from the laboratory
e-mail (MinION runs) and from the MinKNOW files of the Flongle run.
