#!/bin/sh
CDIR=$(dirname "$0")
cd "$CDIR"
mkdir -p ~/RUMAH
cp -a rumah_fondasi.py rumah_ui.html knowledge knowledge_cache_global periksa_paket.py LAYANAN-1-CEK-NETWORK-PRINTER.md ~/RUMAH/
cd ~/RUMAH
python3 rumah_fondasi.py
