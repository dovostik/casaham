"""Gabungkan jurnal lokal (argumen 1) ke jurnal/jurnal.json versi repo terbaru, per id.
Field yang terisi di salah satu sisi dipertahankan (nilai lokal menang bila keduanya terisi)."""
import json
import sys

import jurnal as J

local = json.load(open(sys.argv[1]))
remote = J.load()
by = {r["id"]: r for r in remote}
for r in local:
    if r["id"] in by:
        merged = dict(by[r["id"]])
        merged.update({k: v for k, v in r.items() if v is not None})
        by[r["id"]] = merged
    else:
        by[r["id"]] = r
rows = sorted(by.values(), key=lambda r: (r["date"], r.get("time_in", ""), r["id"]))
J.save(rows)
print("jurnal digabung:", len(remote), "+", len(local), "->", len(rows))
