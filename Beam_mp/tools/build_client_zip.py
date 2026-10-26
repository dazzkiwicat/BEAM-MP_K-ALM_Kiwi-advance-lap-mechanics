#!/usr/bin/env python3
"""Zip Resources/Client/kkLap/ into Resources/Client/kkLap_v<VERSION>.zip.
VERSION is read from M.VERSION in the client extension, so the zip name can never
disagree with what the log line says.

Forward slashes inside the zip, no junk files, and the source folder stays unzipped
so you edit source, never the output.
Run from anywhere:   python3 Beam_mp/tools/build_client_zip.py
"""
import os, re, sys, zipfile, pathlib

root = pathlib.Path(__file__).resolve().parent.parent / "Resources" / "Client"
src  = root / "kkLap"
ext  = src / "lua/ge/extensions/kkLap.lua"
m    = re.search(r"M\.VERSION\s*=\s*'([^']+)'", ext.read_text())
if not m:
    sys.exit("M.VERSION not found in kkLap.lua")
out  = root / f"kkLap_v{m.group(1)}.zip"

if not src.is_dir():
    sys.exit(f"source folder missing: {src}")

count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.name not in ("Thumbs.db", ".DS_Store"):
            z.write(p, p.relative_to(src).as_posix())   # as_posix = forward slashes
            count += 1
print(f"wrote {out}  ({count} files)")
