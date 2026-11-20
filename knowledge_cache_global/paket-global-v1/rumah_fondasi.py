"""Rumah lokal. Jalankan dengan python3 rumah_fondasi.py."""

import json
import os
import re
import socket
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unicodedata import normalize
from urllib.parse import parse_qs


ALAMAT = "127.0.0.1"
PORT = 8080
HOST_LOKAL = {f"{ALAMAT}:{PORT}", f"localhost:{PORT}"}
DIREKTORI = Path(__file__).resolve().parent
DB = DIREKTORI / "rumah.db"
UI = DIREKTORI / "rumah_ui.html"
SEED_KNOWLEDGE = Path("knowledge/seed-v1.json")
TERMINAL = socket.gethostname()
BATAS_PROMPT = 2000
BATAS_BODY = 32768
KOLOM_PROMPT = "id, prompt, bangunan, terminal, waktu, hasil"
KATEGORI_KNOWLEDGE = ("alat-alat", "network", "failure", "workflow", "aturan")


def siapkan_db(lokasi):
    if lokasi.is_symlink():
        raise OSError("rumah.db harus berupa file lokal.")
    if not lokasi.exists():
        os.close(os.open(lokasi, os.O_CREAT | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(lokasi)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt TEXT NOT NULL,
                bangunan INTEGER NOT NULL,
                terminal TEXT,
                waktu TEXT NOT NULL,
                hasil TEXT
            )
        """)
        kolom = {baris[1] for baris in db.execute("PRAGMA table_info(prompts)")}
        if "terminal" not in kolom:
            db.execute("ALTER TABLE prompts ADD COLUMN terminal TEXT")
        if "hasil" not in kolom:
            db.execute("ALTER TABLE prompts ADD COLUMN hasil TEXT")
        siapkan_knowledge(db)


def hash_isi(isi):
    return sha256(isi.encode("utf-8")).hexdigest()


def siapkan_knowledge(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            judul TEXT NOT NULL,
            isi TEXT NOT NULL,
            kategori TEXT CHECK(kategori IN ('alat-alat','network','failure','workflow','aturan')),
            sumber TEXT,
            status TEXT CHECK(status IN ('DRAFT','TERVERIFIKASI','DITOLAK')) DEFAULT 'DRAFT',
            verifikator TEXT DEFAULT 'Arga',
            hash TEXT,
            versi INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    objek_fts = db.execute("""
        SELECT COUNT(*) FROM sqlite_schema
        WHERE name IN ('knowledge_fts', 'knowledge_fts_insert', 'knowledge_fts_update', 'knowledge_fts_delete')
    """).fetchone()[0]
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
        USING fts5(judul, isi, content='knowledge', content_rowid='id')
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_insert AFTER INSERT ON knowledge BEGIN
            INSERT INTO knowledge_fts(rowid, judul, isi) VALUES (new.id, new.judul, new.isi);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_update AFTER UPDATE OF id, judul, isi ON knowledge BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, judul, isi)
                VALUES ('delete', old.id, old.judul, old.isi);
            INSERT INTO knowledge_fts(rowid, judul, isi) VALUES (new.id, new.judul, new.isi);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_delete AFTER DELETE ON knowledge BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, judul, isi)
                VALUES ('delete', old.id, old.judul, old.isi);
        END
    """)
    if objek_fts != 4:
        db.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES ('rebuild')")
    seed = json.loads((DIREKTORI / SEED_KNOWLEDGE).read_text(encoding="utf-8"))
    for item in seed:
        judul, isi, kategori = item["judul"], item["isi"], item["kategori"]
        db.execute("""
            INSERT INTO knowledge (judul, isi, kategori, sumber, status, verifikator, hash)
            SELECT ?, ?, ?, ?, 'TERVERIFIKASI', 'Founder', ?
            WHERE NOT EXISTS (SELECT 1 FROM knowledge WHERE judul = ? AND kategori = ?)
        """, (judul, isi, kategori, SEED_KNOWLEDGE.as_posix(), hash_isi(isi), judul, kategori))


def tanda_terima(baris):
    data = dict(baris)
    # Database terdahulu memakai afinitas TEXT untuk nomor bangunan.
    if data["bangunan"] in ("1", "2", "3"):
        data["bangunan"] = int(data["bangunan"])
    return data



class RumahHandler(BaseHTTPRequestHandler):
    timeout = 10

    def balas(self, status, tipe, isi):
        data = isi.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{tipe}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def balas_json(self, status, isi):
        self.balas(status, "application/json", json.dumps(isi, ensure_ascii=True))

    def galat(self, status, pesan):
        self.balas_json(status, {"error": pesan})

    def lokal(self):
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        if host not in HOST_LOKAL or (origin is not None and origin != f"http://{host}"):
            self.galat(403, "Akses hanya dari Rumah lokal.")
            return False
        return True

    def do_GET(self):
        if not self.lokal():
            return
        jalur, _, parameter = self.path.partition("?")
        if jalur in ("/", "/rumah_ui.html"):
            try:
                halaman = UI.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                self.galat(503, "Halaman Rumah sedang tidak dapat dibaca.")
                return
            self.balas(200, "text/html", halaman)
        elif jalur == "/cari-knowledge":
            self.cari_knowledge(parameter)
        elif jalur == "/kotak_masuk":
            try:
                with closing(sqlite3.connect(DB)) as db:
                    db.row_factory = sqlite3.Row
                    baris = [tanda_terima(b) for b in db.execute(
                        f"SELECT {KOLOM_PROMPT} FROM prompts ORDER BY id DESC"
                    )]
            except sqlite3.Error:
                self.galat(503, "Kotak masuk SQLite sedang tidak dapat dibaca.")
                return
            self.balas_json(200, baris)
        else:
            self.galat(404, "Halaman tidak ditemukan.")

    def do_POST(self):
        if not self.lokal():
            return
        jalur = self.path.partition("?")[0]
        if jalur == "/kirim":
            self.simpan()
        elif jalur == "/tambah-knowledge":
            self.tambah_knowledge()
        else:
            self.galat(404, "Halaman tidak ditemukan.")

    def do_PUT(self):
        if not self.lokal():
            return
        prefiks, _, nomor = self.path.partition("?")[0].rpartition("/")
        if prefiks != "/kirim" or not nomor.isascii() or not nomor.isdecimal() or len(nomor) > 19:
            self.galat(404, "Tanda terima tidak ditemukan.")
            return
        nomor = int(nomor)
        if not 0 < nomor < 2**63:
            self.galat(404, "Tanda terima tidak ditemukan.")
            return
        self.simpan(nomor)

    def baca_json(self):
        if self.headers.get_content_type() != "application/json":
            self.galat(415, "Gunakan format application/json.")
            return
        try:
            panjang = int(self.headers.get("Content-Length", ""))
        except ValueError:
            panjang = 0
        if panjang <= 0:
            self.galat(400, "Permintaan perlu memuat objek JSON.")
            return
        if panjang > BATAS_BODY:
            self.galat(413, "Permintaan terlalu panjang.")
            return
        try:
            body = json.loads(self.rfile.read(panjang).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, RecursionError):
            self.galat(400, "Isi permintaan harus berupa objek JSON yang sah.")
            return
        return body

    def baca_prompt(self):
        body = self.baca_json()
        if body is None:
            return
        try:
            if not isinstance(body.get("prompt"), str):
                raise ValueError
            prompt = body["prompt"].strip()
            prompt.encode("utf-8")
            if not 0 < len(prompt) <= BATAS_PROMPT:
                raise ValueError
            bangunan = body.get("bangunan", 1)
            if type(bangunan) is not int or bangunan not in (1, 2, 3):
                raise ValueError
        except ValueError:
            self.galat(400, f"Isi prompt 1–{BATAS_PROMPT} karakter dan gunakan bangunan 1, 2, atau 3.")
            return
        return prompt, bangunan

    def cari_knowledge(self, parameter):
        try:
            query = parse_qs(parameter, keep_blank_values=True, max_num_fields=8, errors="strict").get("q", [])
        except ValueError:
            query = []
        if len(query) != 1 or not 0 < len(query[0].strip()) <= 250:
            self.galat(400, "Isi q dengan kata pencarian 1–250 karakter.")
            return
        kata = re.findall(r"[^\W_]+", normalize("NFC", query[0]))
        if not kata:
            self.balas_json(200, [])
            return
        # Kata pencarian diperlakukan sebagai teks, termasuk kata seperti OR dan NOT.
        cocok = " AND ".join(f'"{item}"' for item in kata)
        try:
            with closing(sqlite3.connect(DB, timeout=0.5)) as db:
                db.row_factory = sqlite3.Row
                hasil = [dict(baris) for baris in db.execute("""
                    SELECT k.id, k.judul, k.isi, k.kategori, k.sumber, k.versi
                    FROM knowledge_fts
                    JOIN knowledge AS k ON k.id = knowledge_fts.rowid
                    WHERE knowledge_fts MATCH ? AND k.status = 'TERVERIFIKASI'
                    ORDER BY knowledge_fts.rank, k.id
                """, (cocok,))]
        except sqlite3.Error:
            self.galat(503, "Indeks Knowledge sedang tidak dapat dibaca.")
            return
        self.balas_json(200, hasil)

    def tambah_knowledge(self):
        body = self.baca_json()
        if body is None:
            return
        judul, isi, kategori, sumber = (body.get(kunci) for kunci in ("judul", "isi", "kategori", "sumber"))
        if (
            not isinstance(judul, str) or not judul.strip()
            or not isinstance(isi, str) or not isi.strip()
            or kategori not in KATEGORI_KNOWLEDGE
            or (sumber is not None and not isinstance(sumber, str))
        ):
            self.galat(400, "Isi judul, isi, dan kategori yang sah. Sumber harus berupa teks jika diisi.")
            return
        judul, isi = judul.strip(), isi.strip()
        sumber = (sumber or "").strip() or None
        try:
            for nilai in (judul, sumber or ""):
                nilai.encode("utf-8")
            checksum = hash_isi(isi)
        except UnicodeError:
            self.galat(400, "Judul, isi, dan sumber harus berupa teks UTF-8 yang sah.")
            return
        try:
            with closing(sqlite3.connect(DB)) as db, db:
                nomor = db.execute("""
                    INSERT INTO knowledge (judul, isi, kategori, sumber, status, hash)
                    VALUES (?, ?, ?, ?, 'DRAFT', ?)
                """, (judul, isi, kategori, sumber, checksum)).lastrowid
        except sqlite3.Error:
            self.galat(503, "SQLite sedang tidak dapat menyimpan Knowledge. Coba lagi.")
            return
        self.balas_json(201, {"id": nomor, "status": "DRAFT", "hash": checksum})

    def simpan(self, nomor=None):
        nilai = self.baca_prompt()
        if nilai is None:
            return
        prompt, bangunan = nilai
        baru = nomor is None
        try:
            with closing(sqlite3.connect(DB)) as db, db:
                db.row_factory = sqlite3.Row
                if baru:
                    waktu = datetime.now().astimezone().isoformat(timespec="seconds")
                    nomor = db.execute("""
                        INSERT INTO prompts (prompt, bangunan, terminal, waktu, hasil)
                        VALUES (?, ?, ?, ?, ?)
                    """, (prompt, bangunan, TERMINAL, waktu, "Prompt tersimpan di SQLite.")).lastrowid
                else:
                    db.execute("UPDATE prompts SET prompt = ?, hasil = ? WHERE id = ?",
                               (prompt, "Koreksi tersimpan di SQLite.", nomor))
                baris = db.execute(
                    f"SELECT {KOLOM_PROMPT} FROM prompts WHERE id = ?", (nomor,)
                ).fetchone()
        except sqlite3.Error:
            self.galat(503, "SQLite sedang tidak dapat menyimpan prompt. Coba lagi.")
            return
        if baris is None:
            self.galat(404, "Tanda terima tidak ditemukan.")
            return
        self.balas_json(201 if baru else 200, tanda_terima(baris))


def main():
    try:
        server = ThreadingHTTPServer((ALAMAT, PORT), RumahHandler)
    except OSError as galat:
        print(f"Rumah gagal membuka http://{ALAMAT}:{PORT}: {galat}", file=sys.stderr)
        return 1
    with server:
        try:
            siapkan_db(DB)
        except (OSError, ValueError, sqlite3.Error) as galat:
            print(f"Rumah gagal menyiapkan {DB}: {galat}", file=sys.stderr)
            return 1
        print(f"Rumah siap di http://{ALAMAT}:{PORT} · SQLite {DB}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nRumah berhenti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
