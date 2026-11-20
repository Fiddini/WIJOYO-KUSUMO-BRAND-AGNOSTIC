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
from ipaddress import IPv6Address
from pathlib import Path
from unicodedata import category, name as nama_unicode, normalize
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


# --- Saringan dan anonimisasi Knowledge (Aturan 1.2 dan 1.3) ---
# Trace ID: RUMAH-20261120-PEMBANGUN1-KNOWLEDGE-001
# Log saringan dan hash tidak cocok: hanya jenis kejadian, kategori, id, trace_id. Tidak pernah isi.
LOG_KNOWLEDGE = DIREKTORI / "knowledge_saringan_log.txt"
POLA_TRACE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
POLA_TANGGAL_JUDUL = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?=\s|[-:–—])")
BATAS_PENULIS = 80
TRACE_ID_SEED = "RUMAH-20261120-KNOWLEDGE-001"
HASH_SEED_FOUNDER = "cc47a4201044015fc09d6a5db1ef81624ba93996835c6518a547ba70d49e7ffb"
TRACE_ID_SARINGAN = "RUMAH-20261120-PEMBANGUN1-KNOWLEDGE-001"

SARINGAN = (
    ("PRIVATE-KEY", re.compile(r"BEGIN[^\n]{0,40}PRIVATE\s+KEY|BEGIN\s+CERTIFICATE|ssh-(?:rsa|ed25519)\s+AAAA", re.I)),
    ("KEY", re.compile(
        r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]|api[\s_-]?key|secret[\s_-]?key|access[\s_-]?key|vault[\s_-]?key|kunci\s+(?:api|vault)"
        r"|AKIA[0-9A-Z]{12,}|gh[pousr]_[A-Za-z0-9]{8,}|xox[abprs]-|AIza[0-9A-Za-z_-]{20,}", re.I)),
    ("PASSWORD", re.compile(r'\b(?:password|passwd|pwd|kata\s+sandi)\b[\s"\x27]*[=:]', re.I)),
    ("TOKEN", re.compile(
        r'\b(?:token|secret)\b[\s"\x27]*[=:]|\bbearer\s+\S+|client[_-]?secret'
        r'|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', re.I)),
    ("DATA-PASIEN", re.compile(
        r"\bno\.?\s*rm\b|nomor\s+rm\b|rekam\s+medi[sk]|medical\s+record|\bNIK\b"
        r"|\b\d{16}\b|tanggal\s+lahir|tgl\.?\s*lahir|\bpasien\b|\bpatient\b|log\s+medi(?:cal|[sk])", re.I)),
    ("KODE-TERLARANG", re.compile(
        r"pemecah\s+batu|kode\s+(?:private|privat)\s+wakaf|(?:production|produksi)\s+medi(?:cal|[sk])", re.I)),
    ("CORE-BELUM-DIPUTUSKAN", re.compile(
        r"\b(?:keputusan|usulan)\s+core\b[^\n]{0,80}\b(?:belum|menunggu)\b", re.I)),
    ("INTERNET-BELUM-DIPERIKSA", re.compile(
        r"https?://(?!127\.0\.0\.1(?=[:/\s?#]|$))|\bwww\.", re.I)),
)
POLA_JEMBATAN = re.compile(r"\b(?:gpt(?:-\w+)?|openai|jembatan)\b", re.I)
POLA_ASAL_JEMBATAN = re.compile(
    r"\b(?:dari|sumber|dibuat oleh|dihasilkan oleh)\s*[:=]?\s*\b(?:gpt|openai|jembatan)\b", re.I)
POLA_ASAL_INTERNET = re.compile(r"\b(?:internet|hasil pencarian web)\b", re.I)
POLA_KUTIPAN_INTERNET = re.compile(
    r"\b(?:disalin|dikutip|diambil|diperoleh)\s+dari\s+(?:internet|web)\b"
    r"|\bhasil\s+pencarian\s+(?:web|google|internet|online)\b", re.I)
# Lapisan ke-2: sebutan jembatan/internet di mana pun = ragu. Tetap DRAFT, tersembunyi, ditandai untuk Arga.
POLA_RAGU_ASAL = re.compile(r"\b(?:gpt(?:-\w+)?|openai|chatgpt|jembatan|internet|google|online)\b", re.I)

_OKTET = r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9]{1,2})"
_TINDAKAN_ORANG = r"(?:memeriksa|memakai|mencatat|menghubungi|melaporkan|mengecek|menangani|bertugas)"
POLA_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:.]*(?:%[\w-]+)?(?![\w:])", re.I)
ANONIM = (
    ("EMAIL-ANONIM", "USER-A", re.compile(r"[\w.%+\x27-]+@[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}")),
    ("IP-INTERNAL", "IP-INTERNAL", re.compile(rf"(?<![\d.]){_OKTET}(?:\.{_OKTET}){{3}}(?!\d|\.\d)")),
    ("TELEPON-ANONIM", "[TELEPON-DIHAPUS]", re.compile(
        r"(?<![\w.+])(?:\+\d{1,3}|62|0)[ .-]?\(?\d{2,4}\)?(?:[ .-]?\d{3,4}){2,3}(?!\w|\.\d)")),
    ("HOST-A", "HOST-A", re.compile(
        r"(?i)\b(?:host(?:name)?|nama\s+host)\s*[:=]\s*(?!HOST-A\b)[\w.-]+"
        r"|\b[\w-]+(?:\.[\w-]+)*\.(?:local|lan|internal|corp|com|net|org|id)(?![\w-])")),
    ("ALAT-A", "ALAT-A", re.compile(
        r"(?i)\b(?:nomor\s+seri|serial(?:\s+number)?)\s*[:=]\s*(?!ALAT-A\b)[\w.-]+"
        r"|(?<![\da-f])(?:[\da-f]{2}:){5}[\da-f]{2}(?![\da-f])"
        r"|(?<![\da-f])(?:[\da-f]{2}-){5}[\da-f]{2}(?![\da-f])")),
    ("USER-A", "USER-A", re.compile(r"(?i)\b(?:user(?:name)?|nama\s+pengguna)\s*[:=]\s*(?!USER-A\b)[\w.-]+")),
    ("PETUGAS-A", "Petugas A", re.compile(
        r"\b(?:"
        rf"(?i:nama(?:\s+(?:petugas|dokter|teknisi))?|petugas|dokter|teknisi)[ \t]*[:=][ \t]*"
        rf"(?![A-Z]\b)[^\W\d_][\w'-]*(?: +(?!(?i:{_TINDAKAN_ORANG})\b)[^\W\d_][\w'-]*){{0,2}}"
        r"|(?i:nama\s+(?:petugas|dokter|teknisi)|petugas|dokter|teknisi)\s+[A-Z][A-Za-z'-]+(?: +[A-Z][A-Za-z'-]+)*"
        rf"|(?:[A-Z][a-z'-]+ ){{1,2}}[A-Z][a-z'-]+(?= +(?i:{_TINDAKAN_ORANG})\b)"
        r"|(?m:^|(?<=[.!?][ \t]))"
        r"(?!(?:agar|cara|dokter|langkah|panduan|petugas|saat|sebelum|setelah|teknisi|untuk)\b)"
        rf"[a-z][a-z'-]+ [a-z][a-z'-]+(?= +{_TINDAKAN_ORANG}\b)"
        r")")),
    ("RS-A", "RS A", re.compile(
        r"\b(?i:RS(?:UD|UP)?|rumah\s+sakit|klinik|instansi)\s+[A-Z][A-Za-z'-]+(?: +[A-Z][A-Za-z'-]+)*")),
    ("RUANG-A", "RUANG-A", re.compile(
        r"\b(?i:ruang(?:an)?|lantai|kota|lokasi)[ \t]*[:=]?[ \t]+(?:[\w-]+)(?: +[A-Z][A-Za-z'-]+)*")),
)
POLA_PENULIS_ANONIM = re.compile(
    r"(?:Aldo|Arga|Sufi|Founder|(?:Petugas|Teknisi) [A-Z]|(?:USER|AGEN|PEMBANGUN)[ -][A-Z0-9]{1,3})", re.I)

# --- Lapisan ke-2 anonimisasi nama (Aturan 1.3) ---
# Pola huruf besar saja tidak cukup: "budi periksa printer" juga nama. Ragu = anonimkan.
_KATA = r"[^\W\d_][^\W_]*(?:['’‐‑-][^\W\d_][^\W_]*)*"
KATA_TINDAKAN = (
    "periksa|memeriksa|cek|mengecek|ganti|mengganti|catat|mencatat|lapor|melapor|melaporkan"
    "|pasang|memasang|perbaiki|memperbaiki|restart|matikan|mematikan|nyalakan|menyalakan"
    "|hubungi|menghubungi|tangani|menangani|pakai|memakai|bertugas|cabut|mencabut"
    "|bersihkan|membersihkan|setel|menyetel|servis|menservis"
)
POLA_NAMA_SEBELUM_TINDAKAN = re.compile(
    rf"\b((?:{_KATA}[ \t]+){{0,2}}{_KATA})(?=[\"'”’]?[ \t]+(?:{KATA_TINDAKAN})\b)", re.I)
POLA_SAPAAN = re.compile(
    rf"\b(?i:pak|bapak|bu|ibu|mas|mbak|kak|sdr|sdri|saudara|saudari|dr|drg|tuan|nyonya|nona)\.?[ \t]+"
    rf"({_KATA}(?:[ \t]+{_KATA}){{0,2}})")
POLA_NAMA_KUTIP = re.compile(
    rf"\b(?:nama(?:[ \t]+(?:petugas|dokter|teknisi))?|petugas|dokter|teknisi)[ \t]*[:=][ \t]*"
    rf"[\"'“‘]{_KATA}(?:[ \t]+{_KATA}){{0,2}}[\"'”’]", re.I)
BUKAN_NAMA = frozenset("""
    a lalu langsung jangan harus wajib bisa dapat perlu tolong coba segera kemudian setelah sebelum sesudah
    untuk agar supaya cara saat ketika waktu petugas teknisi dokter perawat bidan admin operator staf tim
    bagian pengguna user kita saya aku kami anda kamu dia ia mereka tidak tak belum sudah telah akan sedang
    masih boleh mohon silakan dan atau juga yang ini itu sering selalu rutin hanya cukup pertama kedua
    terakhir lagi ulang sendiri semua setiap tiap tetap cepat dulu baru sekali yaitu yakni bila jika kalau
    hari besok pagi siang sore malam
    server sistem alat printer komputer laptop jaringan aplikasi kabel data log file berkas router switch
    modem mesin perangkat program skrip script layanan otomatis rumah sakit klinik rs ruang lantai
""".split()) | frozenset(KATA_TINDAKAN.split("|"))
POLA_LABEL = re.compile(r"(?:[A-Z]|[A-Z]+(?:-[A-Z0-9]+)+|\[[A-Z-]+\])")


def _bukan_nama(kata):
    return kata.lower() in BUKAN_NAMA or POLA_LABEL.fullmatch(kata) is not None


def anonimkan_nama(teks, jenis):
    """Nama sebelum kata tindakan (huruf kecil/Unicode/petik/strip) dan nama setelah sapaan jadi Petugas A."""
    if not teks:
        return teks

    def ganti_tindakan(cocok):
        potongan = cocok.group(0)
        kata = list(re.finditer(r"\S+", potongan))
        if _bukan_nama(kata[-1].group()):
            return potongan
        mulai = len(kata) - 1
        while mulai > 0 and not _bukan_nama(kata[mulai - 1].group()):
            mulai -= 1
        jenis.add("PETUGAS-A")
        return potongan[: kata[mulai].start()] + "Petugas A"

    def ganti_sapaan(cocok):
        potongan = cocok.group(0)
        geser = cocok.start(1) - cocok.start(0)
        kata = list(re.finditer(r"\S+", cocok.group(1)))
        akhir = None
        for item in kata:
            if _bukan_nama(item.group()):
                break
            akhir = geser + item.end()
        if akhir is None:
            return potongan
        jenis.add("PETUGAS-A")
        return "Petugas A" + potongan[akhir:]

    def ganti_kutip(_):
        jenis.add("PETUGAS-A")
        return "Petugas A"

    teks = POLA_NAMA_KUTIP.sub(ganti_kutip, teks)
    teks = POLA_SAPAAN.sub(ganti_sapaan, teks)
    return POLA_NAMA_SEBELUM_TINDAKAN.sub(ganti_tindakan, teks)


def bersihkan_teks(teks):
    """Buang karakter tak terlihat (Cf: zero-width, bidi) lalu NFKC. Dipakai sebelum saringan dan hash."""
    if not teks:
        return teks
    return normalize("NFKC", "".join(ch for ch in teks if category(ch) != "Cf"))


def teks_meragukan(teks):
    """Satu kata memakai lebih dari satu aksara (misal Latin + Sirilik) = penyamaran, tolak."""
    for kata in re.findall(r"[^\W\d_]+", teks or ""):
        aksara = set()
        for ch in kata:
            try:
                aksara.add(nama_unicode(ch).split()[0])
            except ValueError:
                return True
        if len(aksara) > 1:
            return True
    return False


class KnowledgeDitolak(Exception):
    def __init__(self, kategori):
        super().__init__(",".join(kategori))
        self.kategori = kategori


def siapkan_entri(judul, isi, sumber, penulis):
    """Satu jalur untuk SEMUA knowledge yang masuk (tambah, seed, impor nanti).
    Urutan: bersihkan -> saring (Aturan 1.2) -> anonimkan (Aturan 1.3) -> hash.
    Kembalikan (judul, isi, sumber, penulis, hash, jenis_dianonimkan, ragu). Gagal = KnowledgeDitolak."""
    judul, isi, sumber, penulis = (bersihkan_teks(t) for t in (judul, isi, sumber, penulis))
    kategori_tolak = saring_knowledge(judul, isi, sumber, penulis)
    if any(teks_meragukan(t) for t in (judul, isi, sumber, penulis)):
        kategori_tolak.append("TEKS-MERAGUKAN")
    if (
        POLA_JEMBATAN.search(penulis or "") or POLA_JEMBATAN.search(sumber or "")
        or any(POLA_ASAL_JEMBATAN.search(t) for t in (judul, isi))
    ):
        kategori_tolak.append("JEMBATAN-TANPA-ARGA")
    asal_internet = POLA_ASAL_INTERNET.search(sumber or "") or any(
        POLA_KUTIPAN_INTERNET.search(t) for t in (judul, isi))
    if asal_internet and "INTERNET-BELUM-DIPERIKSA" not in kategori_tolak:
        kategori_tolak.append("INTERNET-BELUM-DIPERIKSA")
    if kategori_tolak:
        raise KnowledgeDitolak(kategori_tolak)
    jenis = set()
    judul, isi, sumber = (anonimkan_nama(anonimkan(t, jenis), jenis) for t in (judul, isi, sumber))
    if penulis and not POLA_PENULIS_ANONIM.fullmatch(penulis):
        jenis.add("USER-A")
        penulis = "USER-A"
    ragu = sorted({"ASAL-JEMBATAN-ATAU-INTERNET"} if any(
        POLA_RAGU_ASAL.search(t or "") for t in (judul, isi, sumber)) else set())
    return judul, isi, sumber, penulis, hash_isi(isi), jenis, ragu


def knowledge_pemakai(db, cocok):
    """SATU-SATUNYA jalan knowledge keluar ke pemakai (Aturan 3).
    Saringan di server/DB: status = 'TERVERIFIKASI' di SQL, lalu hash isi dicek ulang. Jalur baru wajib memakai ini."""
    db.row_factory = sqlite3.Row
    baris_db = [dict(baris) for baris in db.execute("""
        SELECT k.id, k.judul, k.isi, k.kategori, k.sumber, k.versi, k.hash
        FROM knowledge_fts
        JOIN knowledge AS k ON k.id = knowledge_fts.rowid
        WHERE knowledge_fts MATCH ? AND k.status = 'TERVERIFIKASI'
        ORDER BY knowledge_fts.rank, k.id
    """, (cocok,))]
    hasil = []
    for item in baris_db:
        tersimpan = item.pop("hash")
        try:
            sah = isinstance(item["isi"], str) and tersimpan == hash_isi(item["isi"])
        except UnicodeError:
            sah = False
        if sah:
            hasil.append(item)
        else:
            catat_knowledge("HASH-TIDAK-COCOK", id=item["id"], trace_id=TRACE_ID_SARINGAN, laporkan="Arga")
    return hasil


def catat_knowledge(kejadian, **rincian):
    """Tulis satu baris log lokal. Rincian hanya kategori/id/trace_id, tanpa isi."""
    waktu = datetime.now().astimezone().isoformat(timespec="seconds")
    baris = " ".join([waktu, kejadian] + [f"{k}={v}" for k, v in rincian.items()]) + "\n"
    try:
        fd = os.open(LOG_KNOWLEDGE, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as berkas:
            berkas.write(baris)
    except OSError:
        print(baris.strip(), file=sys.stderr)


def saring_knowledge(*teks):
    """Kembalikan daftar kategori terlarang yang ditemukan (kosong = lolos)."""
    gabung = bersihkan_teks("\n".join(t for t in teks if t)).translate(str.maketrans("“”‘’", "\"\"''"))
    return [kategori for kategori, pola in SARINGAN if pola.search(gabung)]


def anonimkan(teks, jenis):
    """Ganti pengenal yang dikenali dengan label umum dan catat jenisnya."""
    if not teks:
        return teks

    def ganti_ipv6(cocok):
        calon = cocok.group()
        alamat = calon.rstrip(".")
        try:
            IPv6Address(alamat)
        except ValueError:
            return calon
        jenis.add("IP-INTERNAL")
        return "IP-INTERNAL" + calon[len(alamat):]

    teks = POLA_IPV6.sub(ganti_ipv6, teks)
    for label, pengganti, pola in ANONIM:
        def ganti(cocok):
            if label == "IP-INTERNAL" and cocok.group(0) == "127.0.0.1":
                return cocok.group(0)
            jenis.add(label)
            return pengganti
        teks = pola.sub(ganti, teks)
    return teks


def siapkan_knowledge(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            judul TEXT NOT NULL,
            isi TEXT NOT NULL,
            kategori TEXT CHECK(kategori IN ('alat-alat','network','failure','workflow','aturan')),
            sumber TEXT,
            status TEXT CHECK(status IN ('DRAFT','TERVERIFIKASI','DITOLAK')) DEFAULT 'DRAFT',
            verifikator TEXT DEFAULT NULL,
            hash TEXT,
            versi INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrasi tambah kolom saja. Data lama tidak diubah.
    kolom = {baris[1] for baris in db.execute("PRAGMA table_info(knowledge)")}
    if "trace_id" not in kolom:
        db.execute("ALTER TABLE knowledge ADD COLUMN trace_id TEXT")
    if "penulis" not in kolom:
        db.execute("ALTER TABLE knowledge ADD COLUMN penulis TEXT")
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
    data_seed = (DIREKTORI / SEED_KNOWLEDGE).read_bytes()
    if sha256(data_seed).hexdigest() != HASH_SEED_FOUNDER:
        catat_knowledge("SEED-KNOWLEDGE-TIDAK-SAH", trace_id=TRACE_ID_SEED, laporkan="Arga")
        return
    seed = json.loads(data_seed)
    for urutan, item in enumerate(seed):
        judul, isi, kategori = item["judul"], item["isi"], item["kategori"]
        sumber = SEED_KNOWLEDGE.as_posix()
        # Seed lewat jalur yang sama. Seed Founder harus sudah bersih: kalau saringan menolak
        # atau anonimisasi mengubah isi, entri tidak dijadikan TERVERIFIKASI, dilaporkan ke Arga.
        try:
            judul_aman, isi_aman, sumber_aman, _, checksum, _, _ = siapkan_entri(judul, isi, sumber, "Founder")
        except KnowledgeDitolak as tolak:
            catat_knowledge("SEED-DITOLAK-SARINGAN", urutan=urutan, kategori=",".join(tolak.kategori),
                            trace_id=TRACE_ID_SEED, laporkan="Arga")
            continue
        if (judul_aman, isi_aman, sumber_aman) != (judul, isi, sumber) or kategori not in KATEGORI_KNOWLEDGE:
            catat_knowledge("SEED-BELUM-ANONIM", urutan=urutan, trace_id=TRACE_ID_SEED, laporkan="Arga")
            continue
        db.execute("""
            INSERT INTO knowledge (judul, isi, kategori, sumber, status, verifikator, hash, trace_id, penulis)
            SELECT ?, ?, ?, ?, 'TERVERIFIKASI', 'Founder', ?, ?, 'Founder'
            WHERE NOT EXISTS (SELECT 1 FROM knowledge WHERE judul = ? AND kategori = ?)
        """, (judul, isi, kategori, sumber, checksum, TRACE_ID_SEED, judul, kategori))


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

    def balas_knowledge(self, hasil):
        # Format tetap array (dibaca rumah_ui.html). Hasil kosong diberi tanda di header.
        if hasil:
            self.balas_json(200, hasil)
            return
        data = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Rumah-Knowledge", "belum ada ilmu terverifikasi")
        self.end_headers()
        self.wfile.write(data)

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
            self.balas_knowledge([])
            return
        # Kata pencarian diperlakukan sebagai teks, termasuk kata seperti OR dan NOT.
        cocok = " AND ".join(f'"{item}"' for item in kata)
        try:
            with closing(sqlite3.connect(DB, timeout=0.5)) as db:
                # Aturan 3: TERVERIFIKASI di SQL + hash dicek ulang, lewat satu fungsi bersama.
                hasil = knowledge_pemakai(db, cocok)
        except sqlite3.Error:
            self.galat(503, "Indeks Knowledge sedang tidak dapat dibaca.")
            return
        self.balas_knowledge(hasil)

    def tambah_knowledge(self):
        body = self.baca_json()
        if body is None:
            return
        judul, isi, kategori, sumber, trace_id, penulis = (
            body.get(kunci) for kunci in ("judul", "isi", "kategori", "sumber", "trace_id", "penulis"))
        # Keputusan Founder: hash DIHITUNG SERVER setelah anonimisasi, bukan dari pemakai.
        # Status, verifikator, hash, id, versi dari pemakai diabaikan. Hasil selalu DRAFT.
        diabaikan = sorted(k for k in ("status", "verifikator", "hash", "id", "versi") if k in body)
        if diabaikan:
            catat_knowledge("METADATA-PEMAKAI-DIABAIKAN", kunci=",".join(diabaikan), trace_id=TRACE_ID_SARINGAN)

        def tolak_metadata(pesan):
            # Lapisan ke-2 (a): metadata wajib tidak lengkap = 422, nol INSERT.
            catat_knowledge("DITOLAK-METADATA", trace_id=TRACE_ID_SARINGAN)
            self.balas_json(422, {"error": pesan, "kategori": ["METADATA-TIDAK-LENGKAP"]})

        if (
            not isinstance(judul, str) or not judul.strip()
            or not isinstance(isi, str) or not isi.strip()
            or kategori not in KATEGORI_KNOWLEDGE
            or (sumber is not None and not isinstance(sumber, str))
        ):
            tolak_metadata("Isi judul, isi, dan kategori yang sah. Sumber harus berupa teks jika diisi.")
            return
        if (
            not isinstance(trace_id, str) or not POLA_TRACE_ID.fullmatch(trace_id.strip())
            or not isinstance(penulis, str) or not 0 < len(penulis.strip()) <= BATAS_PENULIS
        ):
            tolak_metadata(f"Isi trace_id (huruf, angka, . _ : -) dan penulis (1–{BATAS_PENULIS} karakter).")
            return
        judul, isi = judul.strip(), isi.strip()
        sumber = (sumber or "").strip() or None
        trace_id, penulis = trace_id.strip(), penulis.strip()
        try:
            for nilai in (judul, isi, sumber or "", penulis):
                nilai.encode("utf-8")
        except UnicodeError:
            tolak_metadata("Judul, isi, sumber, dan penulis harus berupa teks UTF-8 yang sah.")
            return
        trace_sensitif = bool(saring_knowledge(trace_id)) or anonimkan(trace_id, set()) != trace_id
        # Aturan 1.2 + 1.3 lewat jalur bersama: bersihkan -> saring -> anonimkan -> hash.
        try:
            if trace_sensitif:
                raise KnowledgeDitolak(["TRACE-ID-SENSITIF"])
            judul, isi, sumber, penulis, checksum, jenis, ragu = siapkan_entri(judul, isi, sumber, penulis)
        except KnowledgeDitolak as tolak:
            catat_knowledge(
                "DITOLAK-SARINGAN", kategori=",".join(tolak.kategori),
                trace_id=TRACE_ID_SARINGAN if trace_sensitif else trace_id,
            )
            self.balas_json(422, {
                "error": "Knowledge ditolak saringan dan tidak disimpan.",
                "kategori": tolak.kategori,
            })
            return
        if not isi.strip() or not penulis or not checksum:
            tolak_metadata("Isi dan penulis wajib ada setelah anonimisasi; hash SHA256 dihitung server.")
            return
        tanggal = POLA_TANGGAL_JUDUL.match(judul)
        try:
            if tanggal is None or not judul[tanggal.end():].strip(" :-–—"):
                raise ValueError
            datetime.strptime(tanggal.group(), "%Y-%m-%d")
        except ValueError:
            tolak_metadata("Awali judul dengan tanggal yang sah (YYYY-MM-DD), lalu tulis judul.")
            return
        if jenis or ragu:
            # Lapisan ke-2 (b, d): ragu = tetap DRAFT (tersembunyi dari pemakai), ditandai untuk Arga.
            catat_knowledge("DRAFT-RAGU", dianonimkan=",".join(sorted(jenis)) or "-",
                            ragu=",".join(ragu) or "-", trace_id=trace_id, laporkan="Arga")
        try:
            with closing(sqlite3.connect(DB)) as db, db:
                nomor = db.execute("""
                    INSERT INTO knowledge (judul, isi, kategori, sumber, status, verifikator, hash, trace_id, penulis)
                    VALUES (?, ?, ?, ?, 'DRAFT', NULL, ?, ?, ?)
                """, (judul, isi, kategori, sumber, checksum, trace_id, penulis)).lastrowid
        except sqlite3.Error:
            self.galat(503, "SQLite sedang tidak dapat menyimpan Knowledge. Coba lagi.")
            return
        self.balas_json(201, {
            "id": nomor, "status": "DRAFT", "hash": checksum,
            "trace_id": trace_id, "penulis": penulis,
            "dianonimkan": sorted(jenis), "ragu": ragu,
        })

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
