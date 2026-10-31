"""Parallel downloads, with a fake TSE portal in place of the network."""

import hashlib
import threading
import time

import pytest

from election_tally import download

BASE = "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2022/buweb/"
STATES = ["AC", "AL", "AM", "AP", "BA", "CE"]


class FakePortal:
    """Answers the CKAN listing, the zip files and their .sha512, counting concurrency."""

    def __init__(self, broken=(), bad_hash=()):
        self.files = {f"{BASE}bweb_1t_{uf}_051020221321.zip": f"conteudo {uf}".encode() * 1000
                      for uf in STATES}
        self.broken, self.bad_hash = set(broken), set(bad_hash)
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def get(self, url, params=None, stream=False, timeout=None):
        return FakeResponse(self, url)


class FakeResponse:
    def __init__(self, portal, url):
        self.portal, self.url, self.status_code = portal, url, 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if any(uf in self.url for uf in self.portal.broken):
            raise ConnectionError("connection reset")

    def json(self):
        urls = list(self.portal.files) + [u + ".sha512" for u in self.portal.files]
        return {"result": {"resources": [{"url": u} for u in urls]}}

    @property
    def text(self):
        content = self.portal.files[self.url.removesuffix(".sha512")]
        if any(uf in self.url for uf in self.portal.bad_hash):
            content = b"something else"
        return hashlib.sha512(content).hexdigest() + "  file.zip\n"

    def iter_content(self, size):
        p = self.portal
        with p.lock:
            p.active += 1
            p.peak = max(p.peak, p.active)
        try:
            time.sleep(0.05)  # long enough for the other workers to start
            yield p.files[self.url]
        finally:
            with p.lock:
                p.active -= 1


@pytest.fixture
def portal(monkeypatch):
    def install(**kw):
        fake = FakePortal(**kw)
        monkeypatch.setattr(download.requests, "get", fake.get)
        return fake
    return install


def test_downloads_run_in_parallel_up_to_jobs(portal, tmp_path):
    fake = portal()
    files = download.download(2022, tmp_path, jobs=2)
    assert fake.peak == 2
    assert [f.name for f in files] == [f"bweb_1t_{uf}_051020221321.zip" for uf in STATES]
    assert all(f.read_bytes() == fake.files[BASE + f.name] for f in files)
    assert not list(tmp_path.rglob("*.part"))


def test_default_is_four_parallel_downloads(portal, tmp_path):
    fake = portal()
    download.download(2022, tmp_path)
    assert fake.peak == 4


def test_existing_files_are_skipped(portal, tmp_path):
    fake = portal()
    (tmp_path / "2022").mkdir()
    kept = tmp_path / "2022" / "bweb_1t_AC_051020221321.zip"
    kept.write_bytes(b"already here")
    download.download(2022, tmp_path, jobs=3)
    assert kept.read_bytes() == b"already here"
    assert fake.peak == 3


def test_a_failure_keeps_the_others_and_is_reported(portal, tmp_path):
    portal(broken={"BA"}, bad_hash={"CE"})
    with pytest.raises(SystemExit) as err:
        download.download(2022, tmp_path, jobs=4)
    message = str(err.value)
    assert "bweb_1t_BA_051020221321.zip: connection reset" in message
    assert "bweb_1t_CE_051020221321.zip: SHA-512 does not match" in message
    left = sorted(f.name for f in (tmp_path / "2022").iterdir())
    assert left == [f"bweb_1t_{uf}_051020221321.zip" for uf in ["AC", "AL", "AM", "AP"]]


def test_jobs_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        download.download(2022, tmp_path, jobs=0)


def test_download_municipalities_writes_normalized_table(monkeypatch, tmp_path):
    import io
    import zipfile

    from election_tally import municipalities

    raw = ('"DT_GERACAO";"HH_GERACAO";"CD_UF_TSE";"CD_UF_IBGE";"SG_UF";"NM_UF";'
           '"CD_MUNICIPIO_TSE";"NM_MUNICIPIO_TSE";"CD_MUNICIPIO_IBGE";"NM_MUNICIPIO_IBGE"\n'
           '"25/10/2026";"09:00:09";24;12;"AC";"Acre";"01392";"Rio Branco";1200401;"Rio Branco"\n'
           '"25/10/2026";"09:00:09";24;12;"AC";"Acre";"01007";"Bujari";1200138;"Bujari"\n')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("municipio_tse_ibge.csv", raw.encode("latin-1"))

    class Resp:
        content = buf.getvalue()
        def raise_for_status(self): pass

    monkeypatch.setattr(municipalities.requests, "get", lambda url, timeout: Resp())
    out = municipalities.download(tmp_path / "m.csv")
    assert out.read_text(encoding="utf-8").splitlines() == [
        "state,municipality_code,municipality_name,ibge_code",
        "AC,1007,Bujari,1200138",
        "AC,1392,Rio Branco,1200401",
    ]


def test_packaged_municipality_table_is_complete():
    import csv

    from election_tally.municipalities import TABLE_PATH

    rows = list(csv.DictReader(open(TABLE_PATH, encoding="utf-8")))
    assert len(rows) > 5500
    assert len({r["municipality_code"] for r in rows}) == len(rows)
    assert all(r["ibge_code"].isdigit() and len(r["ibge_code"]) == 7 for r in rows)
