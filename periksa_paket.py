#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
periksa_paket.py — Pemeriksa Paket RUMAH Local First (Aturan 2 + Aturan 4)
Trace ID : RUMAH-20261120-PEMBANGUN2-PAKET-001
Pembuat  : Pembangun 2 (eksekutor)
Versi pondasi: v1.0.0-2026-11-19

Pemakaian:
    python3 periksa_paket.py <folder_paket>

Sifat:
- HANYA MEMBACA. Tidak menulis apa pun ke paket, tidak mengubah status paket,
  tidak memasang apa pun, tidak memakai jaringan. Pustaka bawaan Python saja.
- Symlink tidak pernah diikuti. Symlink apa pun di dalam paket = BELUM LULUS
  (yang menunjuk ke luar folder dicatat khusus).
- Hasil hanya LULUS atau BELUM LULUS. Baris terakhir keluaran:
  "HASIL: LULUS" (exit 0) atau "HASIL: BELUM LULUS" (exit 1).
- LULUS dari skrip ini BUKAN TERVERIFIKASI. TERVERIFIKASI hanya setelah
  Arga LULUS + Founder SETUJU (Aturan 2.2).

Format manifest.json yang diterima:
{
  "paket": "nama-paket",            (alias: "nama")
  "versi": "...", "trace_id": "...", "pembuat": "...",
  "tanggal": "...", "status": "...",
  "berkas": { "path/relatif": "<sha256 hex 64>", ... }
}
Bagian hash boleh bernama "berkas", "files", atau "sha256"; boleh dict
{path: hash} atau list [{"path": ..., "sha256": ...}].
"""

import hashlib
import json
import os
import posixpath
import re
import stat
import sys

TRACE_ID = "RUMAH-20261120-PEMBANGUN2-PAKET-001"

KODE_EXT = (".py", ".html", ".htm", ".js", ".sh")
FIELD_WAJIB = ("paket", "versi", "trace_id", "pembuat", "tanggal", "status")
KUNCI_HASH = ("berkas", "files", "sha256")
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

# --- (d) perintah terlarang di ROLLBACK.md ---------------------------------
# Dicek sebagai kata/perintah utuh: tidak didahului/diikuti huruf, angka,
# '_', '-', '.', '/'. Jadi "form", "arm", "rmdir_lama", "remove" tidak kena.
B_KIRI = r"(?<![A-Za-z0-9_./-])"
B_KANAN = r"(?![A-Za-z0-9_-])"
ROLLBACK_TERLARANG = [
    ("rm", re.compile(B_KIRI + r"rm" + B_KANAN)),
    ("rmdir", re.compile(B_KIRI + r"rmdir" + B_KANAN)),
    ("unlink", re.compile(B_KIRI + r"unlink" + B_KANAN)),
    ("mv", re.compile(B_KIRI + r"mv" + B_KANAN)),
    ("--delete", re.compile(r"--delete")),
    ("find -delete", re.compile(r"(?<![A-Za-z0-9_-])-delete" + B_KANAN)),
]
# cp / rsync harus memakai -a (atau --archive)
RE_SALIN = re.compile(B_KIRI + r"(cp|rsync)" + r"((?:[ \t]+-{1,2}[A-Za-z0-9=_-]*)*)")

# --- (e) perintah install dari internet -----------------------------------
INSTALL_TERLARANG = [
    ("pip install", re.compile(r"(?<![A-Za-z0-9_-])pip[0-9.]*\s+install\b", re.I)),
    ("python -m pip install", re.compile(r"-m\s+pip\s+install\b", re.I)),
    ("npm install", re.compile(r"(?<![A-Za-z0-9_-])npm\s+(install|i|ci|add)\b", re.I)),
    ("yarn/pnpm add", re.compile(r"(?<![A-Za-z0-9_-])(yarn|pnpm)\s+(add|install)\b", re.I)),
    ("npx", re.compile(r"(?<![A-Za-z0-9_-])npx\s+\S", re.I)),
    ("brew install", re.compile(r"(?<![A-Za-z0-9_-])brew\s+(install|reinstall|upgrade|tap)\b", re.I)),
    ("apt/apt-get install", re.compile(r"(?<![A-Za-z0-9_-])apt(-get)?\s+install\b", re.I)),
    ("curl | sh", re.compile(r"(?<![A-Za-z0-9_-])curl\b[^\n]*\|\s*(sudo\s+)?(ba|z|k)?sh\b", re.I)),
    ("wget", re.compile(r"(?<![A-Za-z0-9_-])wget" + B_KANAN, re.I)),
    ("git clone", re.compile(r"(?<![A-Za-z0-9_-])git\s+clone\b", re.I)),
]

# --- (f) pola key / password / data pasien --------------------------------
RAHASIA_TERLARANG = [
    ("key sk-...", re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{6,}")),
    ("PRIVATE KEY", re.compile(r"BEGIN[A-Z ]*PRIVATE KEY")),
    ("password=", re.compile(r"\b(password|passwd|pwd)\s*[=:]", re.I)),
    ("api_key", re.compile(r"api[_-]?key", re.I)),
    ("token=", re.compile(r"\b[A-Za-z_]*token\s*[=:]", re.I)),
    ("rekam medis", re.compile(r"rekam\s+medis", re.I)),
    ("no. RM", re.compile(r"\bno\.?\s*RM\b", re.I)),
    ("NIK 16 digit", re.compile(r"(?<![0-9A-Za-z])\d{16}(?![0-9A-Za-z])")),
    ("NIK", re.compile(r"\bNIK\b\s*[:=]?\s*\d")),
]


def sha256_berkas(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(65536), b""):
            h.update(blok)
    return h.hexdigest()


def baca_teks(path):
    """Kembalikan isi teks (str) atau None kalau biner."""
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def no_baris(teks, posisi):
    return teks.count("\n", 0, posisi) + 1


def daftar_isi(root, alasan):
    """Jalan di folder paket tanpa mengikuti symlink.
    Kembalikan dict {path_relatif_posix: path_absolut} untuk file biasa."""
    hasil = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for nama in sorted(dirnames + filenames):
            ab = os.path.join(dirpath, nama)
            rel = os.path.relpath(ab, root).replace(os.sep, "/")
            st = os.lstat(ab)
            if stat.S_ISLNK(st.st_mode):
                tujuan = os.path.realpath(ab)
                if os.path.commonpath([tujuan, root]) != root:
                    alasan.append("symlink keluar folder paket (tidak diikuti): %s" % rel)
                else:
                    alasan.append("symlink tidak diizinkan di paket (tidak diikuti): %s" % rel)
                if nama in dirnames:
                    dirnames.remove(nama)
                continue
            if nama in filenames:
                if stat.S_ISREG(st.st_mode):
                    hasil[rel] = ab
                else:
                    alasan.append("bukan file biasa: %s" % rel)
    return hasil


def path_manifest_aman(p):
    if not isinstance(p, str) or not p.strip():
        return False, "path kosong / bukan teks"
    if "\x00" in p or "\\" in p:
        return False, "path berisi karakter terlarang"
    if p.startswith("/") or p.startswith("~") or re.match(r"^[A-Za-z]:", p):
        return False, "path absolut"
    if ".." in p.split("/"):
        return False, "path keluar folder (..)"
    norm = posixpath.normpath(p)
    if norm.startswith("..") or norm.startswith("/"):
        return False, "path keluar folder"
    return True, norm


def ambil_hash_manifest(m, alasan):
    bagian = None
    for k in KUNCI_HASH:
        if k in m:
            bagian = m[k]
            break
    if bagian is None:
        alasan.append("manifest.json tidak punya daftar hash (kunci: %s)" % "/".join(KUNCI_HASH))
        return {}
    pasangan = []
    if isinstance(bagian, dict):
        pasangan = list(bagian.items())
    elif isinstance(bagian, list):
        for item in bagian:
            if isinstance(item, dict):
                pasangan.append((item.get("path"), item.get("sha256")))
            else:
                alasan.append("manifest: entri daftar hash tidak sah: %r" % (item,))
    else:
        alasan.append("manifest: daftar hash harus dict atau list")
        return {}
    if not pasangan:
        alasan.append("manifest: daftar hash kosong")
    hasil = {}
    for p, h in pasangan:
        ok, norm = path_manifest_aman(p)
        if not ok:
            alasan.append("manifest: path ditolak (%s): %r" % (norm, p))
            continue
        if not isinstance(h, str) or not HEX64.match(h):
            alasan.append("manifest: hash bukan SHA256 hex 64 untuk %s" % norm)
            continue
        if norm in hasil:
            alasan.append("manifest: path tercatat ganda: %s" % norm)
        hasil[norm] = h.lower()
    return hasil


def periksa(root_arg):
    alasan = []
    info = []
    root = os.path.realpath(root_arg)
    if not os.path.isdir(root):
        return ["folder paket tidak ada / bukan folder: %s" % root_arg], info

    isi = daftar_isi(root, alasan)
    info.append("folder paket: %s" % root)
    info.append("jumlah file biasa: %d" % len(isi))

    # ---- (a) 5 bagian ----------------------------------------------------
    def tes_file(rel):
        b = posixpath.basename(rel)
        return b.endswith(".py") and (b.startswith("uji_") or b.startswith("test_"))

    kode = [r for r in isi if r.lower().endswith(KODE_EXT) and not tes_file(r)]
    tes = [r for r in isi if tes_file(r)]
    knowledge = [r for r in isi if r.startswith("knowledge/")]
    if not knowledge:
        knowledge = [r for r in isi
                     if r.lower().endswith((".json", ".md"))
                     and "knowledge" in posixpath.basename(r).lower()]
    ada_rollback = "ROLLBACK.md" in isi
    ada_manifest = "manifest.json" in isi

    info.append("kode: %s" % (", ".join(sorted(kode)) or "-"))
    info.append("knowledge: %s" % (", ".join(sorted(knowledge)) or "-"))
    info.append("tes: %s" % (", ".join(sorted(tes)) or "-"))
    if not kode:
        alasan.append("bagian KODE tidak ada (.py/.html/.js/.sh selain tes)")
    if not knowledge:
        alasan.append("bagian KNOWLEDGE tidak ada (folder knowledge/ atau file *knowledge*.json/.md)")
    if not tes:
        alasan.append("bagian TES tidak ada (uji_*.py / test_*.py)")
    if not ada_rollback:
        alasan.append("bagian ROLLBACK.md tidak ada")
    if not ada_manifest:
        alasan.append("bagian manifest.json tidak ada")

    # ---- (b) + (c) manifest ---------------------------------------------
    if ada_manifest:
        m = None
        teks_m = baca_teks(isi["manifest.json"])
        try:
            m = json.loads(teks_m) if teks_m is not None else None
        except ValueError as e:
            alasan.append("manifest.json bukan JSON sah: %s" % e)
        if teks_m is None:
            alasan.append("manifest.json bukan teks UTF-8")
        if m is not None and not isinstance(m, dict):
            alasan.append("manifest.json harus objek JSON")
            m = None
        if m is not None:
            # (c) field wajib
            for f in FIELD_WAJIB:
                nilai = m.get(f)
                if f == "paket" and nilai in (None, ""):
                    nilai = m.get("nama")
                if nilai is None or (isinstance(nilai, str) and not nilai.strip()):
                    alasan.append("manifest: field wajib tidak ada / kosong: %s" % f)
            info.append("manifest: paket=%r versi=%r trace_id=%r pembuat=%r tanggal=%r status=%r" % (
                m.get("paket", m.get("nama")), m.get("versi"), m.get("trace_id"),
                m.get("pembuat"), m.get("tanggal"), m.get("status")))
            # (b) hash
            daftar = ambil_hash_manifest(m, alasan)
            for rel, h in sorted(daftar.items()):
                if rel == "manifest.json":
                    alasan.append("manifest: manifest.json tidak boleh mencatat dirinya sendiri")
                    continue
                if rel not in isi:
                    alasan.append("file tercatat di manifest tapi hilang / bukan file biasa: %s" % rel)
                    continue
                nyata = sha256_berkas(isi[rel])
                if nyata != h:
                    alasan.append("hash TIDAK COCOK: %s (manifest %s.., nyata %s..)" % (rel, h[:12], nyata[:12]))
                else:
                    info.append("hash cocok: %s" % rel)
            for rel in sorted(isi):
                if rel != "manifest.json" and rel not in daftar:
                    alasan.append("file tidak tercatat di manifest: %s" % rel)

    # ---- (d) ROLLBACK.md --------------------------------------------------
    if ada_rollback:
        t = baca_teks(isi["ROLLBACK.md"])
        if t is None:
            alasan.append("ROLLBACK.md bukan teks UTF-8")
        else:
            for label, rx in ROLLBACK_TERLARANG:
                for mt in rx.finditer(t):
                    alasan.append("ROLLBACK.md memakai '%s' (baris %d)" % (label, no_baris(t, mt.start())))
            jumlah_salin = 0
            for mt in RE_SALIN.finditer(t):
                jumlah_salin += 1
                flag = mt.group(2).split()
                pakai_a = any(
                    (x.startswith("-") and not x.startswith("--") and "a" in x[1:])
                    or x == "--archive" for x in flag)
                if not pakai_a:
                    alasan.append("ROLLBACK.md: '%s' tanpa -a (baris %d)" % (mt.group(1), no_baris(t, mt.start())))
            if jumlah_salin == 0:
                alasan.append("ROLLBACK.md tidak berisi langkah cp -a / rsync -a")

    # ---- (e) + (f) pindai semua file teks --------------------------------
    for rel in sorted(isi):
        t = baca_teks(isi[rel])
        if t is None:
            info.append("biner (tidak dipindai teks): %s" % rel)
            continue
        for label, rx in INSTALL_TERLARANG:
            for mt in rx.finditer(t):
                alasan.append("perintah install dari internet '%s': %s baris %d" % (label, rel, no_baris(t, mt.start())))
        for label, rx in RAHASIA_TERLARANG:
            for mt in rx.finditer(t):
                # isi yang cocok TIDAK disalin ke keluaran (Aturan 1.2)
                alasan.append("pola terlarang '%s': %s baris %d" % (label, rel, no_baris(t, mt.start())))

    return alasan, info


def main(argv):
    print("periksa_paket.py — trace_id %s — hanya membaca" % TRACE_ID)
    if len(argv) != 2:
        print("Pemakaian: python3 periksa_paket.py <folder_paket>")
        print("HASIL: BELUM LULUS")
        return 1
    try:
        alasan, info = periksa(argv[1])
    except Exception as e:  # jangan pernah LULUS kalau pemeriksaan gagal
        alasan, info = ["pemeriksaan gagal: %s: %s" % (type(e).__name__, e)], []
    for baris in info:
        print("  - " + baris)
    if alasan:
        print("ALASAN BELUM LULUS (%d):" % len(alasan))
        for a in alasan:
            print("  * " + a)
        print("HASIL: BELUM LULUS")
        return 1
    print("(LULUS skrip ini bukan TERVERIFIKASI; wajib Arga LULUS + Founder SETUJU)")
    print("HASIL: LULUS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
