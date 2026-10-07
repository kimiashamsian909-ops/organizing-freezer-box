#!/usr/bin/env python3
"""Fill the database with example freezers, boxes and samples for testing.

    python3 seed.py --reset          # wipe freezer.db and rebuild it
    python3 seed.py --db other.db --reset
"""
import argparse
import os
import random
from datetime import date, timedelta

from server import HERE, Store

MEMBERS = [("Lab mate A", "#2a78d6"), ("Lab mate B", "#d94f70"), ("Lab mate C", "#2e9e6a"),
           ("Lab mate D", "#c27c0e"), ("Lab mate E", "#8a56c9")]

TYPES = {
    "Virus stock": ["MNV-1 P{n} stock", "MNV CW3 P{n}", "MNV barcode lib {n}", "HuNoV GII.4 isolate {n}"],
    "Plasmid": ["pUC19-BC{n}", "pCMV-VP1 #{n}", "pMNV-CW3 clone {n}", "pLenti-GFP #{n}"],
    "RNA": ["Infected RAW RNA {n}h", "Mock RNA rep{n}", "Ileum RNA mouse {n}", "Viral RNA extract {n}"],
    "cDNA": ["cDNA RAW {n}h", "cDNA ileum M{n}", "RT-qPCR plate {n} cDNA"],
    "gDNA": ["Mouse tail gDNA #{n}", "BV2 gDNA {n}"],
    "Cell pellet": ["RAW 264.7 pellet P{n}", "BV2 pellet P{n}", "HEK293T pellet {n}"],
    "Serum": ["Mouse serum M{n}", "Pre-bleed {n}"],
    "Primer": ["MNV ORF1 F{n}", "MNV ORF1 R{n}", "BC amp primer {n}"],
    "Antibody": ["anti-VP1 aliquot {n}", "anti-CD300lf lot {n}"],
}
NOTES = ["", "", "", "", "Thaw on ice", "Titer 1e7 PFU/mL", "Sequence verified", "Do not refreeze",
         "From KS06 run", "Aliquot 2 of 5", "Low volume", "Ask before using"]


def build(store, rng):
    members = [store.add_member({"name": n, "color": c}, user="seed")["id"] for n, c in MEMBERS]
    layout = [
        ({"name": "-80 °C Freezer A", "location": "Room 4120", "temperature": "-80 °C"},
         [("Rack 1", ["grid9", "grid9", "grid10"]),
          ("Rack 2", ["grid10", "grid9", "list"]),
          ("Rack 3", ["grid9", "grid10"])]),
        ({"name": "-20 °C Freezer B", "location": "Room 4118", "temperature": "-20 °C"},
         [("Rack 1", ["grid9", "grid9", "list"]),
          ("Rack 2", ["grid10", "grid9"])]),
    ]
    list_names = iter(["Large tubes", "Serum bags"])
    boxes = []
    box_no = 1
    for fdata, racks in layout:
        f = store.add_freezer(fdata, user="seed")
        for rname, kinds in racks:
            r = store.add_rack({"freezer_id": f["id"], "name": rname}, user="seed")
            for kind in kinds:
                if kind == "list":
                    name = next(list_names)
                else:
                    name = "Box %02d" % box_no
                    box_no += 1
                boxes.append(store.add_box({"rack_id": r["id"], "name": name, "kind": kind}, user="seed"))

    # Claims: some racks/boxes belong to one person, the samples in them mostly theirs.
    box_owner = {}
    store.claim("racks", 1, {"member_id": members[0], "note": "Virus stocks"}, user="Lab mate A")
    for b in boxes:
        if b["rack_id"] == 1:
            box_owner[b["id"]] = members[0]
    claims = [(boxes[4]["id"], members[1], "Plasmid preps"), (boxes[6]["id"], members[2], "RNA timecourse"),
              (boxes[9]["id"], members[3], "")]
    for box_id, m, note in claims:
        store.claim("boxes", box_id, {"member_id": m, "note": note}, user=MEMBERS[members.index(m)][0])
        box_owner[box_id] = m

    fills = [0.9, 0.55, 0.3, 0.75, 1.0, 0.6, 0.6, 0.0, 0.45, 0.8, 0.5, 0.5, 0.25]
    start = date(2025, 1, 6)
    counter = {}
    samples = []
    for b, fill in zip(boxes, fills):
        size = {"grid9": 9, "grid10": 10}.get(b["kind"])
        spots = [(r, c) for r in range(size) for c in range(size)] if size else [(None, None)] * 20
        n = int(len(spots) * fill)
        favourite = rng.choice(list(TYPES))
        for row, col in spots[:n]:
            stype = favourite if rng.random() < 0.6 else rng.choice(list(TYPES))
            counter[stype] = counter.get(stype, 0) + 1
            owner = box_owner.get(b["id"]) if rng.random() < 0.85 and b["id"] in box_owner else rng.choice(members)
            s = store.add_sample({
                "box_id": b["id"], "row": row, "col": col,
                "name": rng.choice(TYPES[stype]).format(n=counter[stype]),
                "type": stype, "owner_id": owner, "notes": rng.choice(NOTES),
                "date": (start + timedelta(days=rng.randrange(0, 640))).isoformat(),
            }, user=MEMBERS[members.index(owner)][0], force=True)
            samples.append(s)

    # Reservations in partly empty boxes.
    for b, m in ((boxes[2], members[1]), (boxes[8], members[4]), (boxes[11], members[2])):
        size = {"grid9": 9, "grid10": 10}[b["kind"]]
        taken = {(s["row"], s["col"]) for s in store.get_box(b["id"])["samples"]}
        free = [(r, c) for r in range(size) for c in range(size) if (r, c) not in taken]
        for row, col in free[:rng.randint(3, 6)]:
            store.reserve({"box_id": b["id"], "row": row, "col": col, "member_id": m,
                           "note": "For upcoming preps"}, user=MEMBERS[members.index(m)][0])

    # Some everyday edits, moves and removals so the history log has variety.
    for s in rng.sample(samples, 12):
        store.edit_sample(s["id"], {"notes": "Checked in inventory"}, user=s["owner"] or "seed", force=True)
    empty_box = boxes[7]  # created with no samples
    for i, s in enumerate(rng.sample(samples, 4)):
        store.move_sample(s["id"], {"box_id": empty_box["id"], "row": 0, "col": i},
                          user=s["owner"] or "seed", force=True)
    for s in rng.sample([s for s in samples if s["box_id"] != empty_box["id"]], 5):
        store.remove_sample(s["id"], user=s["owner"] or "seed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(HERE, "freezer.db"))
    ap.add_argument("--reset", action="store_true", help="delete the database first")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if os.path.exists(args.db):
        if not args.reset:
            raise SystemExit("%s already exists; use --reset to replace it with example data" % args.db)
        for suffix in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(args.db + suffix):
                os.remove(args.db + suffix)
    store = Store(args.db)
    build(store, random.Random(args.seed))
    n = store._one("SELECT COUNT(*) n FROM samples")["n"]
    nb = store._one("SELECT COUNT(*) n FROM boxes")["n"]
    nh = store._one("SELECT COUNT(*) n FROM history")["n"]
    print("Example data written to %s: %d boxes, %d samples, %d history entries" % (args.db, nb, n, nh))


if __name__ == "__main__":
    main()
