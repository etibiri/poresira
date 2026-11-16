# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small stand-in for the archive: a local HTTP service for study and samples, and a script
that behaves like Webin-CLI. They are what the tests (and nothing else) talk to."""

import itertools
import re
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD = "s3cret-pass"
USER = "Webin-12345"

FAKE_WEBIN_CLI = """#!/bin/bash
dir="{dir}"
manifest=""; out=""
all_arguments=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    -manifest) manifest="$2"; shift;;
    -outputDir) out="$2"; shift;;
  esac
  shift
done
printf '%s\\n' "${{all_arguments[@]}}" > "$dir/last_args"
cp "$manifest" "$dir/last_manifest"
[ -f "$dir/fail_cli" ] && {{ echo "ERROR: invalid FASTQ file" >&2; exit 1; }}
name=$(grep '^NAME' "$manifest" | cut -f2)
n=$(cat "$dir/counter" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$dir/counter"
mkdir -p "$out/reads/$name/submit"
[ -f "$dir/no_receipt" ] && exit 0
cat > "$out/reads/$name/submit/receipt.xml" <<EOF
<RECEIPT success="true">
<EXPERIMENT accession="ERX${{n}}00" alias="${{name}}_exp"/>
<RUN accession="ERR${{n}}00" alias="${{name}}_run"/>
</RECEIPT>
EOF
"""


class FakeArchive:
    """Starts a server on a free local port; ``behaviour`` is ``ok``, ``reject``, ``unauthorised``
    or ``no_accession``."""

    def __init__(self, directory):
        self.directory = directory
        self.behaviour = "ok"
        self.requests = []
        self.counter = itertools.count(1)
        archive = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode()
                archive.requests.append((dict(self.headers), body))
                if archive.behaviour == "unauthorised":
                    self.send_response(401)
                    self.end_headers()
                    return
                answer = archive.receipt(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/submit"
        script = directory / "webin-cli"
        script.write_text(FAKE_WEBIN_CLI.format(dir=directory))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.cli = str(script)

    def receipt(self, body: str) -> str:
        if self.behaviour == "reject":
            return (
                '<RECEIPT success="false"><MESSAGES>'
                "<ERROR>The sample title is missing.</ERROR></MESSAGES></RECEIPT>"
            )
        objects = []
        for tag, alias in re.findall(r'<(PROJECT|SAMPLE) alias="([^"]+)"', body):
            number = next(self.counter)
            if tag == "PROJECT":
                inner = f'<EXT_ID accession="PRJEB{number}" type="Project"/>'
                objects.append(
                    f'<PROJECT accession="ERP{number}" alias="{alias}">{inner}</PROJECT>'
                )
            elif self.behaviour == "no_accession":
                objects.append(f'<SAMPLE accession="ERS{number}" alias="{alias}"/>')
            else:
                inner = f'<EXT_ID accession="SAMEA{number}00" type="biosample"/>'
                objects.append(f'<SAMPLE accession="ERS{number}" alias="{alias}">{inner}</SAMPLE>')
        return f'<RECEIPT success="true">{"".join(objects)}</RECEIPT>'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
