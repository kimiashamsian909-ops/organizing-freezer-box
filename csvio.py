"""CSV import and export for the freezer tracker.

Export and import use the same columns, so an exported file is also a valid import template:
    code, freezer, rack, box, box_type, position, name, type, date, owner, notes
Only `name` is required. Freezers, racks, boxes and lab members that don't exist yet are created.
"""
import csv
import io
import re
from datetime import datetime

from server import BOX_KINDS, ROW_LETTERS, ApiError, now, position_label, sample_code

COLUMNS = ["code", "freezer", "rack", "box", "box_type", "position", "name", "type", "date", "owner", "notes"]

# Header spellings people commonly use in their own spreadsheets.
ALIASES = {
    "code": ["code", "sample code", "sample id", "id"],
    "freezer": ["freezer", "freezer name"],
    "rack": ["rack", "rack name", "shelf"],
    "box": ["box", "box name"],
    "box_type": ["box_type", "box type", "box size", "type of box"],
    "position": ["position", "pos", "well", "spot", "location in box", "slot"],
    "name": ["name", "sample", "sample name", "label"],
    "type": ["type", "sample type"],
    "date": ["date", "date frozen", "freeze date", "frozen"],
    "owner": ["owner", "person", "who", "user", "initials"],
    "notes": ["notes", "note", "comments", "comment", "description"],
}
BOX_TYPE_WORDS = {
    "grid9": ["9x9", "9 x 9", "9×9", "81", "grid9"],
    "grid10": ["10x10", "10 x 10", "10×10", "100", "grid10"],
    "list": ["list", "no grid", "nogrid", "none", "bag", "loose"],
}
MEMBER_COLORS = ["#2a78d6", "#d94f70", "#2e9e6a", "#c27c0e", "#8a56c9", "#0f9aa8", "#b5532a", "#a33ea1",
                 "#3b7d23", "#5f6b7a"]
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y", "%b %d, %Y",
                "%B %d, %Y", "%m-%d-%Y"]


class _DryRun(Exception):
    def __init__(self, report):
        super().__init__("dry run")
        self.report = report


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _parse_position(text):
    """'A1', 'a01', 'J10', 'B-3' -> (row, col); '' -> (None, None)."""
    t = (text or "").strip().upper().replace(" ", "").replace("-", "")
    if not t:
        return None, None
    m = re.fullmatch(r"([A-Z])(\d{1,2})", t)
    if not m or m.group(1) not in ROW_LETTERS or not 1 <= int(m.group(2)) <= 10:
        raise ValueError("position \"%s\" isn't like A1 … J10" % text.strip())
    return ROW_LETTERS.index(m.group(1)), int(m.group(2)) - 1


def _parse_date(text):
    t = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    return t  # keep whatever they wrote rather than lose it


def _parse_box_type(text):
    t = _norm(text)
    if not t:
        return None
    for kind, words in BOX_TYPE_WORDS.items():
        if t in words:
            return kind
    raise ValueError("box type \"%s\" should be 9x9, 10x10 or list" % text.strip())


def read_rows(text):
    """Parse CSV text into (header_map, rows). Rows are dicts keyed by our column names plus 'line'."""
    text = text.lstrip("﻿")  # Excel's UTF-8 marker
    if not text.strip():
        raise ApiError(400, "The file is empty")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader)
    mapping = {}
    for i, h in enumerate(header):
        for col, names in ALIASES.items():
            if _norm(h) in names and col not in mapping:
                mapping[col] = i
                break
    if "name" not in mapping:
        raise ApiError(400, "Couldn't find a sample name column. Found columns: %s" % ", ".join(header))
    rows = []
    for line_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        row = {col: (cells[i].strip() if i < len(cells) else "") for col, i in mapping.items()}
        row["line"] = line_no
        rows.append(row)
    return {col: header[i] for col, i in mapping.items()}, rows


def import_csv(store, text, user="", commit=False, default_freezer="Imported", default_rack="Rack 1",
               default_box="Imported box"):
    """Validate and (if commit) import samples. Rows with problems are skipped and reported.

    The whole import runs in one transaction; a preview runs the same code and rolls back.
    """
    columns, rows = read_rows(text)
    report = {"columns": columns, "rows": len(rows), "added": 0, "skipped": [], "errors": [],
              "created": {"freezers": [], "racks": [], "boxes": [], "members": []}}

    def go():
        conn = store.conn
        freezers = {_norm(f["name"]): f["id"] for f in store._q("SELECT id, name FROM freezers")}
        racks = {(r["freezer_id"], _norm(r["name"])): r["id"] for r in store._q("SELECT id, freezer_id, name FROM racks")}
        boxes = {(b["rack_id"], _norm(b["name"])): dict(b) for b in store._q("SELECT * FROM boxes")}
        members = {_norm(m["name"]): m["id"] for m in store._q("SELECT id, name FROM members")}
        taken = {(s["box_id"], s["row"], s["col"]) for s in store._q(
            "SELECT box_id, row, col FROM samples WHERE row IS NOT NULL")}
        existing = {s["id"]: _norm(s["name"]) for s in store._q("SELECT id, name FROM samples")}

        # A new box's type comes from its column, else from the positions used in it.
        wanted_kind = {}
        for r in rows:
            key = (_norm(r.get("freezer") or default_freezer), _norm(r.get("rack") or default_rack),
                   _norm(r.get("box") or default_box))
            try:
                kind = _parse_box_type(r.get("box_type"))
                pos = _parse_position(r.get("position"))
            except ValueError:
                continue
            k = wanted_kind.setdefault(key, {"explicit": None, "max": -1, "any_pos": False})
            k["explicit"] = k["explicit"] or kind
            if pos[0] is not None:
                k["any_pos"] = True
                k["max"] = max(k["max"], pos[0], pos[1])

        def get_freezer(name):
            key = _norm(name)
            if key not in freezers:
                cur = conn.execute("INSERT INTO freezers (name) VALUES (?)", (name,))
                freezers[key] = cur.lastrowid
                store._log(user, "add", "freezer", cur.lastrowid, name, None, {"name": name, "via": "CSV import"})
                report["created"]["freezers"].append(name)
            return freezers[key]

        def get_rack(freezer_id, name):
            key = (freezer_id, _norm(name))
            if key not in racks:
                cur = conn.execute("INSERT INTO racks (freezer_id, name) VALUES (?, ?)", (freezer_id, name))
                racks[key] = cur.lastrowid
                store._log(user, "add", "rack", cur.lastrowid, name, None, {"name": name, "via": "CSV import"})
                report["created"]["racks"].append(name)
            return racks[key]

        def get_box(rack_id, name, kind_info):
            key = (rack_id, _norm(name))
            if key not in boxes:
                kind = kind_info["explicit"] or (
                    "list" if not kind_info["any_pos"] else "grid10" if kind_info["max"] >= 9 else "grid9")
                cur = conn.execute("INSERT INTO boxes (rack_id, name, kind) VALUES (?, ?, ?)", (rack_id, name, kind))
                boxes[key] = {"id": cur.lastrowid, "kind": kind, "name": name}
                store._log(user, "add", "box", cur.lastrowid, name, None, {"name": name, "kind": kind, "via": "CSV import"})
                report["created"]["boxes"].append("%s (%s)" % (name, {"grid9": "9×9", "grid10": "10×10", "list": "no grid"}[kind]))
            return boxes[key]

        def get_member(name):
            key = _norm(name)
            if not key:
                return None
            if key not in members:
                cur = conn.execute("INSERT INTO members (name, color) VALUES (?, ?)",
                                   (name, MEMBER_COLORS[len(members) % len(MEMBER_COLORS)]))
                members[key] = cur.lastrowid
                store._log(user, "add", "member", cur.lastrowid, name, None, {"name": name, "via": "CSV import"})
                report["created"]["members"].append(name)
            return members[key]

        for r in rows:
            line = r["line"]
            name = r.get("name", "")
            if not name:
                report["errors"].append({"line": line, "name": "", "error": "no sample name"})
                continue
            m = re.fullmatch(r"[Ss]-?0*(\d+)", r.get("code") or "")
            if m and existing.get(int(m.group(1))) == _norm(name):
                report["skipped"].append({"line": line, "name": name, "reason": "already in the tracker (%s)" % r["code"]})
                continue
            try:
                row_i, col_i = _parse_position(r.get("position"))
                kind = _parse_box_type(r.get("box_type"))
            except ValueError as e:
                report["errors"].append({"line": line, "name": name, "error": str(e)})
                continue
            fname = r.get("freezer") or default_freezer
            rname = r.get("rack") or default_rack
            bname = r.get("box") or default_box
            kind_info = wanted_kind[(_norm(fname), _norm(rname), _norm(bname))]
            box = get_box(get_rack(get_freezer(fname), rname), bname, kind_info)
            size = BOX_KINDS[box["kind"]]
            if kind and kind != box["kind"]:
                report["errors"].append({"line": line, "name": name, "error": "box \"%s\" already exists as a different type" % bname})
                continue
            if size is None:
                row_i = col_i = None
            elif row_i is None:
                report["errors"].append({"line": line, "name": name, "error": "box \"%s\" is a grid; a position like A1 is needed" % bname})
                continue
            elif row_i >= size or col_i >= size:
                report["errors"].append({"line": line, "name": name, "error": "%s is outside the %dx%d box \"%s\"" % (position_label(row_i, col_i), size, size, bname)})
                continue
            elif (box["id"], row_i, col_i) in taken:
                report["errors"].append({"line": line, "name": name, "error": "%s in \"%s\" is already taken" % (position_label(row_i, col_i), bname)})
                continue
            owner_id = get_member(r.get("owner", ""))
            ts = now()
            cur = conn.execute(
                "INSERT INTO samples (box_id, row, col, name, type, date, owner_id, notes, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (box["id"], row_i, col_i, name, r.get("type", ""), _parse_date(r.get("date")), owner_id,
                 r.get("notes", ""), ts, ts))
            if row_i is not None:
                taken.add((box["id"], row_i, col_i))
                conn.execute("DELETE FROM reservations WHERE box_id = ? AND row = ? AND col = ?", (box["id"], row_i, col_i))
            after = store._decorate(store._one("SELECT * FROM samples WHERE id = ?", (cur.lastrowid,)))
            after["via"] = "CSV import"
            store._log(user, "add", "sample", after["id"], name, None, after)
            report["added"] += 1

        if not commit:
            raise _DryRun(report)
        return report

    try:
        return store._tx(go)
    except _DryRun as d:
        return d.report


def export_csv(store, box_id=None):
    sql = ("SELECT s.*, b.name box, b.kind, r.name rack, f.name freezer, m.name owner"
           " FROM samples s JOIN boxes b ON b.id = s.box_id JOIN racks r ON r.id = b.rack_id"
           " JOIN freezers f ON f.id = r.freezer_id LEFT JOIN members m ON m.id = s.owner_id")
    args = ()
    if box_id:
        sql += " WHERE s.box_id = ?"
        args = (box_id,)
    sql += " ORDER BY f.id, r.id, b.id, s.row, s.col, s.name"
    kind_label = {"grid9": "9x9", "grid10": "10x10", "list": "list"}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLUMNS)
    for s in store._q(sql, args):
        w.writerow([sample_code(s["id"]), s["freezer"], s["rack"], s["box"], kind_label[s["kind"]],
                    position_label(s["row"], s["col"]), s["name"], s["type"], s["date"], s["owner"] or "", s["notes"]])
    return "﻿" + out.getvalue()  # BOM so Excel opens accents (°C) correctly
