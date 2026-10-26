#!/usr/bin/env python3
"""Parse every .lua under Beam_mp with luaparser. Catches syntax errors before upload.
    python3 Beam_mp/tools/check_lua.py
"""
import pathlib, sys
from luaparser import ast
root = pathlib.Path(__file__).resolve().parent.parent
bad = 0
for f in sorted(root.rglob("*.lua")):
    try:
        ast.parse(f.read_text())
        print("ok  ", f.relative_to(root))
    except Exception as e:
        bad += 1
        print("FAIL", f.relative_to(root), "->", e)
sys.exit(1 if bad else 0)
