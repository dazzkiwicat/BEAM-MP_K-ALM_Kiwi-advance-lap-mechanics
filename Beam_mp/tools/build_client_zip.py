#!/usr/bin/env python3
"""Zip Resources/Client/kkLap/ into Resources/Client/kkLap.zip.

Forward slashes inside the zip, no junk files, and the source folder stays unzipped
so you edit source, never the output.
Run from anywhere:   python3 Beam_mp/tools/build_client_zip.py
"""
import os, sys, zipfile, pathlib

root = pathlib.Path(__file__).resolve().parent.parent / "Resources" / "Client"
src  = root / "kkLap"
out  = root / "kkLap.zip"

if not src.is_dir():
    sys.exit(f"source folder missing: {src}")

count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.name not in ("Thumbs.db", ".DS_Store"):
            z.write(p, p.relative_to(src).as_posix())   # as_posix = forward slashes
            count += 1
print(f"wrote {out}  ({count} files)")
