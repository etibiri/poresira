# SPDX-License-Identifier: AGPL-3.0-or-later
from base64 import b64decode

import pytest

from poresira.deposits.archive import (
    ArchiveError,
    EnaArchive,
    EnaClient,
    WebinCli,
    parse_receipt,
    scrub,
)
from poresira.deposits.models import Environment
from poresira.deposits.tests.fake_archive import PASSWORD, USER, FakeArchive


@pytest.fixture
def fake(tmp_path):
    archive = FakeArchive(tmp_path)
    yield archive
    archive.stop()


@pytest.fixture
def config(fake, tmp_path):
    return {
        "WEBIN_USER": USER,
        "WEBIN_PASSWORD": PASSWORD,
        "TEST_URL": fake.url,
        "PRODUCTION_URL": fake.url,
        "WEBIN_CLI": fake.cli,
        "WORK_DIR": str(tmp_path / "work"),
        "TIMEOUT_SECONDS": 10,
        "CLI_TIMEOUT_SECONDS": 30,
    }


class TestReceipts:
    def test_accessions_and_messages_are_read(self):
        receipt = parse_receipt(
            '<RECEIPT success="true">'
            '<PROJECT accession="ERP1" alias="study_a"><EXT_ID accession="PRJEB1" type="Project"/>'
            "</PROJECT>"
            '<SAMPLE accession="ERS2" alias="sample_a">'
            '<EXT_ID accession="SAMEA2" type="biosample"/></SAMPLE>'
            "<MESSAGES><INFO>Done</INFO><ERROR>Bad</ERROR></MESSAGES></RECEIPT>"
        )

        assert receipt.success
        assert receipt.accession_of("PROJECT", "study_a", "PRJ") == "PRJEB1"
        assert receipt.accession_of("SAMPLE", "sample_a", "SAM") == "SAMEA2"
        assert receipt.accession_of("SAMPLE", "other", "SAM") == ""
        assert (receipt.infos, receipt.errors) == (["Done"], ["Bad"])

    def test_an_object_without_the_expected_accession_gives_nothing(self):
        receipt = parse_receipt(
            '<RECEIPT success="true"><SAMPLE accession="ERS1" alias="a"/></RECEIPT>'
        )

        assert receipt.accession_of("SAMPLE", "a", "SAM") == ""
        assert receipt.first("SAMPLE", "ERS") == "ERS1"

    @pytest.mark.parametrize(
        "text",
        [
            "not xml",
            '<!DOCTYPE r [<!ENTITY a "x">]><RECEIPT success="true">&a;</RECEIPT>',
            "<RECEIPT>" + "x" * 2_100_000 + "</RECEIPT>",
        ],
    )
    def test_an_answer_that_is_not_a_plain_receipt_is_refused(self, text):
        with pytest.raises(ArchiveError):
            parse_receipt(text)

    def test_secrets_are_removed_from_a_text(self):
        assert scrub(f"user x password {PASSWORD} end", PASSWORD, "") == "user x password *** end"


class TestClient:
    def test_study_and_samples_are_posted_with_basic_authentication(self, fake):
        client = EnaClient(fake.url, USER, PASSWORD)

        receipt = client.submit(
            {
                "SUBMISSION": "<SUBMISSION/>",
                "PROJECT": '<PROJECT_SET><PROJECT alias="s1"/></PROJECT_SET>',
                "SAMPLE": '<SAMPLE_SET><SAMPLE alias="a1"/></SAMPLE_SET>',
            }
        )

        headers, body = fake.requests[0]
        assert b64decode(headers["Authorization"].split()[1]).decode() == f"{USER}:{PASSWORD}"
        assert 'name="PROJECT"' in body and 'name="SAMPLE"' in body and 'name="SUBMISSION"' in body
        assert receipt.accession_of("PROJECT", "s1", "PRJ") == "PRJEB1"
        assert receipt.accession_of("SAMPLE", "a1", "SAM") == "SAMEA200"

    def test_an_http_error_does_not_leak_the_password(self, fake):
        fake.behaviour = "unauthorised"

        with pytest.raises(ArchiveError) as error:
            EnaClient(fake.url, USER, PASSWORD).submit({"SUBMISSION": "<x/>"})

        assert "401" in str(error.value) and PASSWORD not in str(error.value)

    def test_an_unreachable_archive_is_an_archive_error(self):
        with pytest.raises(ArchiveError, match="could not be reached"):
            EnaClient("http://127.0.0.1:9/submit", USER, PASSWORD, timeout=2).submit({"S": "x"})

    @pytest.mark.parametrize(
        "url", ["http://www.ebi.ac.uk/submit", "ftp://example.org/x", "http://example.org:8080/x"]
    )
    def test_credentials_never_travel_in_the_clear_to_another_computer(self, url):
        with pytest.raises(ArchiveError, match="https"):
            EnaClient(url, USER, PASSWORD)

    def test_https_and_this_computer_are_accepted(self):
        EnaClient("https://wwwdev.ebi.ac.uk/ena/submit/drop-box/submit", USER, PASSWORD)
        EnaClient("http://localhost:8000/x", USER, PASSWORD)


class TestWebinCli:
    MANIFEST = "STUDY\tPRJEB1\nSAMPLE\tSAMEA1\nNAME\treads_1\nFASTQ\t/d/a.fastq.gz\n"

    def cli(self, fake, tmp_path, test=True):
        return WebinCli(fake.cli, USER, PASSWORD, timeout=30, test=test)

    def test_the_manifest_is_written_the_command_run_and_the_receipt_read(self, fake, tmp_path):
        receipt = self.cli(fake, tmp_path).submit_reads(self.MANIFEST, "reads_1", tmp_path / "w")

        assert receipt.first("RUN", "ERR") == "ERR100" and receipt.first("EXPERIMENT", "ERX")
        arguments = (tmp_path / "last_args").read_text().split("\n")
        assert arguments[:6] == ["-context", "reads", "-userName", USER, "-password", PASSWORD]
        assert "-submit" in arguments and "-test" in arguments
        assert (tmp_path / "w" / "manifest.txt").read_text() == self.MANIFEST

    def test_the_test_flag_is_absent_in_production(self, fake, tmp_path):
        self.cli(fake, tmp_path, test=False).submit_reads(self.MANIFEST, "reads_1", tmp_path / "w")

        assert "-test" not in (tmp_path / "last_args").read_text().split("\n")

    def test_a_failure_is_reported_without_the_password(self, fake, tmp_path):
        (tmp_path / "fail_cli").write_text("")

        with pytest.raises(ArchiveError, match="invalid FASTQ") as error:
            self.cli(fake, tmp_path).submit_reads(self.MANIFEST, "reads_1", tmp_path / "w")

        assert PASSWORD not in str(error.value)

    def test_no_receipt_means_no_success(self, fake, tmp_path):
        (tmp_path / "no_receipt").write_text("")

        with pytest.raises(ArchiveError, match="did not submit"):
            self.cli(fake, tmp_path).submit_reads(self.MANIFEST, "reads_1", tmp_path / "w")

    def test_a_missing_program_is_an_archive_error(self, tmp_path):
        cli = WebinCli("/nowhere/webin-cli", USER, PASSWORD, timeout=5, test=True)

        with pytest.raises(ArchiveError, match="could not be run"):
            cli.submit_reads("NAME\tx\n", "x", tmp_path / "w")


class TestArchive:
    def test_the_environment_chooses_the_address_and_the_test_flag(self, config):
        test = EnaArchive(Environment.TEST, config)
        production = EnaArchive(Environment.PRODUCTION, config)

        assert test.webin.test and not production.webin.test

    def test_without_a_webin_account_nothing_is_sent(self, config):
        for name in ("WEBIN_USER", "WEBIN_PASSWORD"):
            with pytest.raises(ArchiveError, match="not configured"):
                EnaArchive(Environment.TEST, {**config, name: ""})

    def test_only_the_given_parts_are_registered(self, config, fake):
        archive = EnaArchive(Environment.TEST, config)

        archive.register(None, '<SAMPLE_SET><SAMPLE alias="a1"/></SAMPLE_SET>', "<SUBMISSION/>")

        body = fake.requests[0][1]
        assert 'name="SAMPLE"' in body and 'name="PROJECT"' not in body

    def test_text_is_scrubbed_of_the_password(self, config):
        assert EnaArchive(Environment.TEST, config).scrub(f"x {PASSWORD}") == "x ***"
