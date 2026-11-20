# Rollback paket-global-v1

Paket ini masih calon lokal. Jalankan `python3 -B uji_knowledge.py` dari direktori paket untuk membaca dan memeriksa berkas tanpa jaringan. Hasil uji dan hash manifest dicatat di `LOG-RUMAH-20261120-KNOWLEDGE-001.txt` di Rumah.

Jika Founder memutuskan rollback pada Rumah ini, hentikan server dari terminal yang menjalankannya. Dari `~/RUMAH`, gunakan `cp -a backup/RUMAH-20261120-KNOWLEDGE-001/rumah_fondasi.py rumah_fondasi.py`, lalu jalankan kembali server. Versi sebelumnya tetap bisa membaca tabel `prompts`. Pertahankan `rumah.db` yang aktif agar semua tanda terima dan knowledge yang sudah tersimpan tetap tersedia. Snapshot SQLite di folder cadangan dapat diperiksa terpisah dengan mode baca saja.
