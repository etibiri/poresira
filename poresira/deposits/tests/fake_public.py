# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local stand-in for NCBI E-utilities and the ENA Portal API, for tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RUNINFO_HEADER = "Run,ReleaseDate,Experiment,BioProject,BioSample,SampleName"


class FakePublic:
    def __init__(self):
        self.runs = []  # (run, experiment, biosample, sample name)
        self.genbank = {}  # accession -> (length, isolate, title)
        self.biosamples = {}  # accession -> attributes
        self.ncbi_status = 200
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                owner.requests.append((url.path, query))
                status, body = owner.answer(url.path, query)
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def config(self):
        return {
            "NCBI_URL": f"{self.base}/eutils",
            "ENA_URL": f"{self.base}/ena",
            "NCBI_API_KEY": "",
            "MIN_INTERVAL_SECONDS": 0,
            "TIMEOUT_SECONDS": 10,
        }

    def answer(self, path: str, query: dict) -> tuple[int, str]:
        if path.startswith("/eutils") and self.ncbi_status != 200:
            return self.ncbi_status, "busy"
        if path.endswith("esearch.fcgi"):
            ids = [str(index) for index, _ in enumerate(self.runs)]
            return 200, json.dumps({"esearchresult": {"count": str(len(ids)), "idlist": ids}})
        if path.endswith("efetch.fcgi") and query.get("db") == "sra":
            wanted = [int(i) for i in query["id"].split(",")]
            lines = [RUNINFO_HEADER]
            for index in wanted:
                run, experiment, biosample, name = self.runs[index]
                lines.append(f"{run},2026-05-01 20:21:37,{experiment},PRJNA1,{biosample},{name}")
            return 200, "\n".join(lines) + "\n"
        if path.endswith("efetch.fcgi") and query.get("db") == "biosample":
            return 200, self.biosample_xml(query["id"].split(","))
        if path.endswith("esummary.fcgi"):
            return 200, self.genbank_json(query["id"].split(","))
        if path.endswith("/search"):
            rows = [
                "run_accession\texperiment_accession\tsample_accession\tsample_alias\tfirst_public"
            ]
            rows += [f"{r}\t{e}\t{b}\t{n}\t2026-05-05" for r, e, b, n in self.runs]
            return 200, "\n".join(rows) + "\n"
        return 404, "not found"

    def biosample_xml(self, accessions) -> str:
        parts = []
        for accession in accessions:
            attributes = self.biosamples.get(accession)
            if attributes is None:
                continue
            inner = "".join(
                f'<Attribute attribute_name="{k}" harmonized_name="{k}">{v}</Attribute>'
                for k, v in attributes.items()
            )
            parts.append(
                f'<BioSample accession="{accession}"><Attributes>{inner}</Attributes></BioSample>'
            )
        return f"<BioSampleSet>{''.join(parts)}</BioSampleSet>"

    def genbank_json(self, accessions) -> str:
        result = {"uids": []}
        for index, accession in enumerate(accessions):
            if accession not in self.genbank:
                continue
            length, isolate, title = self.genbank[accession]
            uid = str(1000 + index)
            result["uids"].append(uid)
            result[uid] = {
                "caption": accession,
                "title": title,
                "slen": length,
                "subtype": "isolate|host",
                "subname": f"{isolate}|Ipomoea batatas",
            }
        return json.dumps({"result": result})

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
