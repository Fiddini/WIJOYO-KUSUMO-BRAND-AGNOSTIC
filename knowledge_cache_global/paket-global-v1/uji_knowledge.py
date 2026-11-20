"""Uji Knowledge dengan SQLite sintetis dan jaringan diblokir."""

import argparse
import importlib.util
import io
import json
import socket
import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlencode


PAKET = Path(__file__).resolve().parent
FILE_PAKET = {
    "rumah_fondasi.py", "rumah_ui.html", "knowledge/seed-v1.json", "uji_knowledge.py", "ROLLBACK.md",
}


class KoneksiUji:
    def __init__(self, payload):
        self.masuk = io.BytesIO(payload)
        self.keluar = io.BytesIO()

    def makefile(self, mode, *_):
        return self.masuk if mode == "rb" else self.keluar

    def sendall(self, payload):
        self.keluar.write(payload)

    def settimeout(self, _):
        pass


def permintaan(app, metode, jalur, body=None):
    data = b"" if body is None else json.dumps(body).encode()
    header = (
        f"{metode} {jalur} HTTP/1.1\r\nHost: 127.0.0.1:8080\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n"
    ).encode()
    koneksi = KoneksiUji(header + data)
    app.RumahHandler(koneksi, ("127.0.0.1", 8080), None)
    header, isi = koneksi.keluar.getvalue().split(b"\r\n\r\n", 1)
    status = int(header.split(b" ")[1])
    return status, json.loads(isi) if b"application/json" in header else isi.decode()


def cari(app, q):
    return permintaan(app, "GET", "/cari-knowledge?" + urlencode({"q": q}))


def uji_k1(app, _db):
    mulai = perf_counter()
    status, baris = cari(app, "failure network")
    milidetik = (perf_counter() - mulai) * 1000
    assert status == 200 and milidetik < 1000
    assert len(baris) == 1 and baris[0]["kategori"] == "network"
    assert set(baris[0]) == {"id", "judul", "isi", "kategori", "sumber", "versi"}
    assert baris[0]["isi"] == "Cek kabel, cek ping 127.0.0.1, cek log, catat di Log Audit"
    assert (app.DIREKTORI / baris[0]["sumber"]).is_file()
    assert cari(app, '"*') == (200, [])
    status, halaman = permintaan(app, "GET", "/")
    assert status == 200 and "Cari knowledge terverifikasi" in halaman and "Sumber:" in halaman
    return f"{milidetik:.2f} ms, jawaban dan sumber lokal tersedia"


def uji_k2(app, db):
    dokumen = {
        "judul": "Pemeriksaan TandaK2",
        "isi": "Konten sintetis",
        "kategori": "workflow",
        "sumber": "Fixture K2",
        "status": "TERVERIFIKASI",
    }
    status, draf = permintaan(app, "POST", "/tambah-knowledge", dokumen)
    assert status == 201 and draf["status"] == "DRAFT" and draf["id"] == 4
    assert draf["hash"] == sha256(dokumen["isi"].encode()).hexdigest()
    assert db.execute("SELECT status, verifikator, hash FROM knowledge WHERE id = ?",
                      (draf["id"],)).fetchone() == ("DRAFT", "Arga", draf["hash"])
    assert cari(app, "TandaK2") == (200, [])
    with db:
        db.execute("UPDATE knowledge SET status = 'TERVERIFIKASI' WHERE id = ?", (draf["id"],))
    status, baris = cari(app, "TandaK2")
    assert status == 200 and len(baris) == 1 and baris[0]["id"] == draf["id"]
    assert baris[0]["sumber"] == dokumen["sumber"]
    with db:
        db.execute("UPDATE knowledge SET judul = 'Pemeriksaan GantiK2' WHERE id = ?", (draf["id"],))
    assert cari(app, "TandaK2") == (200, [])
    assert cari(app, "GantiK2")[1][0]["id"] == draf["id"]
    with db:
        db.execute("UPDATE knowledge SET status = 'DITOLAK' WHERE id = ?", (draf["id"],))
    assert cari(app, "GantiK2") == (200, [])
    status, tanda_terima = permintaan(app, "POST", "/kirim", {"prompt": "Prompt sintetis"})
    assert status == 201 and tanda_terima["id"] == 8
    assert permintaan(app, "GET", "/kotak_masuk")[1][0]["id"] == 8
    assert db.execute("SELECT id, prompt FROM prompts ORDER BY id").fetchall() == [
        (7, "Prompt lama"), (8, "Prompt sintetis"),
    ]
    return "DRAFT tersembunyi, verifikasi tampil, penolakan tersembunyi, /kirim melanjutkan #7 ke #8"


def uji_k3(_app, _db):
    data = (PAKET / "manifest.json").read_bytes()
    manifest = json.loads(data)
    assert manifest["paket"] == "paket-global-v1"
    assert manifest["status"] == "CALON"
    assert set(manifest["berkas"]) == FILE_PAKET
    for nama, hash_diharapkan in manifest["berkas"].items():
        path = PAKET / nama
        assert path.resolve().is_relative_to(PAKET)
        assert sha256(path.read_bytes()).hexdigest() == hash_diharapkan, nama
    return f"{len(FILE_PAKET)} berkas cocok, SHA256 manifest {sha256(data).hexdigest()}"


def uji(lokasi_kode):
    spec = importlib.util.spec_from_file_location("rumah_fondasi_uji", lokasi_kode / "rumah_fondasi.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    connect = sqlite3.connect
    uri = "file:knowledge-uji?mode=memory&cache=shared"

    def koneksi_fixture(lokasi, **opsi):
        assert lokasi == app.DB
        return connect(uri, uri=True, **opsi)

    with closing(connect(uri, uri=True)) as db:
        with db:
            db.execute("CREATE TABLE prompts (id INTEGER PRIMARY KEY, bangunan TEXT, prompt TEXT, waktu TEXT)")
            db.execute("INSERT INTO prompts VALUES (7, '1', 'Prompt lama', '2026-11-19')")
        with patch.object(socket, "socket", side_effect=AssertionError("Socket diblokir")), \
             patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS diblokir")), \
             patch.object(app.sqlite3, "connect", koneksi_fixture), \
             patch.object(app, "DB", Path(__file__)), \
             patch.object(app.RumahHandler, "log_message"):
            app.siapkan_db(app.DB)
            app.siapkan_db(app.DB)
            assert db.execute("SELECT count(*) FROM knowledge WHERE status = 'TERVERIFIKASI'").fetchone() == (3,)
            gagal = False
            for nama, jalankan in (("K1", uji_k1), ("K2", uji_k2), ("K3", uji_k3)):
                try:
                    print(f"{nama} PASS · {jalankan(app, db)}")
                except (AssertionError, OSError, ValueError, KeyError, sqlite3.Error) as error:
                    print(f"{nama} STOP · {error!r}")
                    gagal = True
            return int(gagal)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lokal", action="store_true", help="Uji kode aktif di ~/RUMAH.")
    args = parser.parse_args()
    raise SystemExit(uji(PAKET.parents[1] if args.lokal else PAKET))
