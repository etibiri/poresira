<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# PoreSira

PoreSira is an open-source laboratory information management system (LIMS) that traces every
biological sample from field collection to public data deposition.

!!! note "Status"
    Phases 1 (foundation) to 6 (computing) are complete: users, projects and access control,
    identifiers, the audit trail, collection events, samples with lineage, storage locations, QC,
    primers, assays, PCR runs, libraries, flow cells, sequencing runs, the registry of data files
    and compute jobs on Slurm. Deposits (ENA/SRA) are the next phase.

- [Installation](installation.md): run it on one computer, a lab server or an HPC platform.
- [Configuration](configuration.md): every environment variable.
- [Samples](samples.md): collection events, lineage, storage, demonstration data.
- [PCR](pcr.md): primers, assays, runs in plates or tubes, results.
- [Sequencing](sequencing.md): libraries and barcodes, reusable flow cells, runs, reads.
- [Storage](storage.md): data files registered in place, checksums, integrity checks.
- [Computing](computing.md): Dorado basecalling and demultiplexing on Slurm, with provenance.
- [Architecture](architecture.md): how traceability is enforced.
- [Backup and restore](backup-restore.md): protect your data and rebuild a lost server.

PoreSira is licensed under AGPL-3.0-or-later. Source: <https://github.com/etibiri/poresira>.
