#!/usr/bin/env python3
"""Freezer sample tracker: SQLite store + JSON API + static web UI.

Standard library only. Run:  python3 server.py  [--config config.json] [--db freezer.db]
"""
import argparse
import json
import mimetypes
import os
import re
import sqlite3
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
BOX_KINDS = {"grid9": 9, "grid10": 10, "list": None}
ROW_LETTERS = "ABCDEFGHIJ"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class NeedsConfirm(Exception):
    """Raised when an action would step on someone else's claim or reservation."""

    def __init__(self, warnings):
        super().__init__("; ".join(warnings))
        self.warnings = warnings


def now():
    return datetime.now().isoformat(timespec="seconds")


def sample_code(sample_id):
    return "S-%06d" % sample_id


def position_label(row, col):
    if row is None or col is None:
        return ""
    return "%s%d" % (ROW_LETTERS[row], col + 1)


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        with open(os.path.join(HERE, "schema.sql")) as f:
            self.conn.executescript(f.read())

    # ---- plumbing -------------------------------------------------------

    def _q(self, sql, args=()):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def _tx(self, fn):
        """Run fn() inside one write transaction, holding the global write lock."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn()
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")
            return result

    def _log(self, user, action, entity, entity_id, label, before=None, after=None):
        self.conn.execute(
            "INSERT INTO history (ts, user, action, entity, entity_id, label, before_json, after_json)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (now(), user or "", action, entity, entity_id, label,
             json.dumps(before) if before is not None else None,
             json.dumps(after) if after is not None else None),
        )

    def _get(self, table, id_, what):
        row = self._one("SELECT * FROM %s WHERE id = ?" % table, (id_,))
        if not row:
            raise ApiError(404, "%s %s not found" % (what, id_))
        return row

    def _member_name(self, member_id):
        if member_id is None:
            return None
        m = self._one("SELECT name FROM members WHERE id = ?", (member_id,))
        return m["name"] if m else None

    def _check_member(self, member_id):
        if member_id is not None:
            self._get("members", member_id, "Lab member")

    # ---- members --------------------------------------------------------

    def list_members(self):
        return self._q("SELECT * FROM members ORDER BY active DESC, name")

    def add_member(self, data, user=""):
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError(400, "Name is required")
        color = data.get("color") or "#888888"

        def go():
            if self._one("SELECT id FROM members WHERE name = ?", (name,)):
                raise ApiError(409, "%s is already a lab member" % name)
            cur = self.conn.execute("INSERT INTO members (name, color) VALUES (?, ?)", (name, color))
            m = self._get("members", cur.lastrowid, "Lab member")
            self._log(user, "add", "member", m["id"], name, None, m)
            return m
        return self._tx(go)

    def update_member(self, member_id, data, user=""):
        def go():
            before = self._get("members", member_id, "Lab member")
            after = dict(before)
            for k in ("name", "color", "active"):
                if k in data:
                    after[k] = data[k]
            after["name"] = (after["name"] or "").strip()
            if not after["name"]:
                raise ApiError(400, "Name is required")
            self.conn.execute("UPDATE members SET name=?, color=?, active=? WHERE id=?",
                              (after["name"], after["color"], int(bool(after["active"])), member_id))
            after = self._get("members", member_id, "Lab member")
            self._log(user, "edit", "member", member_id, after["name"], before, after)
            return after
        return self._tx(go)

    # ---- containers -----------------------------------------------------

    def add_freezer(self, data, user=""):
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError(400, "Freezer name is required")

        def go():
            cur = self.conn.execute("INSERT INTO freezers (name, location, temperature) VALUES (?,?,?)",
                                    (name, data.get("location", ""), data.get("temperature", "")))
            f = self._get("freezers", cur.lastrowid, "Freezer")
            self._log(user, "add", "freezer", f["id"], name, None, f)
            return f
        return self._tx(go)

    def add_rack(self, data, user=""):
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError(400, "Rack name is required")

        def go():
            self._get("freezers", data.get("freezer_id"), "Freezer")
            cur = self.conn.execute("INSERT INTO racks (freezer_id, name) VALUES (?,?)",
                                    (data["freezer_id"], name))
            r = self._get("racks", cur.lastrowid, "Rack")
            self._log(user, "add", "rack", r["id"], name, None, r)
            return r
        return self._tx(go)

    def add_box(self, data, user=""):
        name = (data.get("name") or "").strip()
        kind = data.get("kind")
        if not name:
            raise ApiError(400, "Box name is required")
        if kind not in BOX_KINDS:
            raise ApiError(400, "Box type must be 9x9, 10x10 or no grid")

        def go():
            self._get("racks", data.get("rack_id"), "Rack")
            cur = self.conn.execute("INSERT INTO boxes (rack_id, name, kind) VALUES (?,?,?)",
                                    (data["rack_id"], name, kind))
            b = self._get("boxes", cur.lastrowid, "Box")
            self._log(user, "add", "box", b["id"], name, None, b)
            return b
        return self._tx(go)

    def rename(self, table, id_, data, user=""):
        entity, fields = {"freezers": ("freezer", ("name", "location", "temperature")),
                          "racks": ("rack", ("name",)),
                          "boxes": ("box", ("name",))}[table]

        def go():
            before = self._get(table, id_, entity.capitalize())
            after = dict(before)
            for k in fields:
                if k in data:
                    after[k] = (data[k] or "").strip()
            if not after["name"]:
                raise ApiError(400, "Name is required")
            sets = ", ".join("%s = ?" % k for k in fields)
            self.conn.execute("UPDATE %s SET %s WHERE id = ?" % (table, sets),
                              [after[k] for k in fields] + [id_])
            after = self._get(table, id_, entity.capitalize())
            self._log(user, "rename", entity, id_, after["name"], before, after)
            return after
        return self._tx(go)

    def delete_container(self, table, id_, user=""):
        entity, child_sql = {
            "freezers": ("freezer", "SELECT COUNT(*) n FROM racks WHERE freezer_id = ?"),
            "racks": ("rack", "SELECT COUNT(*) n FROM boxes WHERE rack_id = ?"),
            "boxes": ("box", "SELECT COUNT(*) n FROM samples WHERE box_id = ?"),
        }[table]

        def go():
            before = self._get(table, id_, entity.capitalize())
            if self._one(child_sql, (id_,))["n"]:
                raise ApiError(409, "%s \"%s\" is not empty" % (entity.capitalize(), before["name"]))
            if table == "boxes":
                self.conn.execute("DELETE FROM reservations WHERE box_id = ?", (id_,))
            self.conn.execute("DELETE FROM %s WHERE id = ?" % table, (id_,))
            self._log(user, "delete", entity, id_, before["name"], before, None)
        return self._tx(go)

    def claim(self, table, id_, data, user=""):
        entity = {"racks": "rack", "boxes": "box"}[table]
        member_id = data.get("member_id")

        def go():
            before = self._get(table, id_, entity.capitalize())
            self._check_member(member_id)
            note = (data.get("note") or "").strip() if member_id else ""
            self.conn.execute("UPDATE %s SET claimed_by = ?, claim_note = ? WHERE id = ?" % table,
                              (member_id, note, id_))
            after = self._get(table, id_, entity.capitalize())
            who = self._member_name(member_id)
            label = "%s → %s" % (before["name"], who) if who else before["name"]
            self._log(user, "claim" if member_id else "unclaim", entity, id_, label, before, after)
            return after
        return self._tx(go)

    # ---- reading --------------------------------------------------------

    def _location(self, box_id):
        return self._one(
            "SELECT b.id box_id, b.name box, b.kind, r.id rack_id, r.name rack, f.id freezer_id, f.name freezer"
            " FROM boxes b JOIN racks r ON r.id = b.rack_id JOIN freezers f ON f.id = r.freezer_id"
            " WHERE b.id = ?", (box_id,))

    def _decorate(self, s):
        """Add code, position label, owner name and location to a sample row."""
        s["code"] = sample_code(s["id"])
        s["position"] = position_label(s["row"], s["col"])
        s["owner"] = self._member_name(s["owner_id"])
        loc = self._location(s["box_id"])
        if loc:
            s["location"] = " / ".join(x for x in (loc["freezer"], loc["rack"], loc["box"], s["position"]) if x)
        return s

    def tree(self):
        freezers = self._q("SELECT * FROM freezers ORDER BY id")
        racks = self._q("SELECT * FROM racks ORDER BY id")
        boxes = self._q("SELECT * FROM boxes ORDER BY id")
        counts = {(r["box_id"], r["owner_id"]): r["n"] for r in self._q(
            "SELECT box_id, owner_id, COUNT(*) n FROM samples GROUP BY box_id, owner_id")}
        reserved = {r["box_id"]: r["n"] for r in self._q(
            "SELECT box_id, COUNT(*) n FROM reservations GROUP BY box_id")}
        for b in boxes:
            size = BOX_KINDS[b["kind"]]
            b["capacity"] = size * size if size else None
            people = {}
            for (box_id, owner_id), n in counts.items():
                if box_id == b["id"]:
                    people[owner_id] = n
            b["people"] = [{"member_id": k, "count": v} for k, v in sorted(people.items(), key=lambda kv: -kv[1])]
            b["used"] = sum(people.values())
            b["reserved"] = reserved.get(b["id"], 0)
        for r in racks:
            r["boxes"] = [b for b in boxes if b["rack_id"] == r["id"]]
        for f in freezers:
            f["racks"] = [r for r in racks if r["freezer_id"] == f["id"]]
        return {"freezers": freezers, "members": self.list_members()}

    def get_box(self, box_id):
        box = self._get("boxes", box_id, "Box")
        box.update(self._location(box_id))
        box["size"] = BOX_KINDS[box["kind"]]
        rack = self._get("racks", box["rack_id"], "Rack")
        box["rack_claimed_by"] = rack["claimed_by"]
        box["rack_claim_note"] = rack["claim_note"]
        box["samples"] = [self._decorate(s) for s in self._q(
            "SELECT * FROM samples WHERE box_id = ? ORDER BY row, col, name", (box_id,))]
        box["reservations"] = self._q("SELECT * FROM reservations WHERE box_id = ?", (box_id,))
        return box

    def get_sample(self, sample_id):
        return self._decorate(self._get("samples", sample_id, "Sample"))

    # ---- samples --------------------------------------------------------

    def _check_spot(self, box, row, col, ignore_sample=None):
        """Validate a target spot; return (row, col) normalised for the box kind."""
        size = BOX_KINDS[box["kind"]]
        if size is None:
            return None, None
        try:
            row, col = int(row), int(col)
        except (TypeError, ValueError):
            raise ApiError(400, "Pick a position in the box grid")
        if not (0 <= row < size and 0 <= col < size):
            label = position_label(row, col) if 0 <= row < 10 and 0 <= col < 10 else "row %d, col %d" % (row, col)
            raise ApiError(400, "Position %s is outside this %dx%d box" % (label, size, size))
        taken = self._one("SELECT id, name FROM samples WHERE box_id = ? AND row = ? AND col = ?",
                          (box["id"], row, col))
        if taken and taken["id"] != ignore_sample:
            raise ApiError(409, "%s is already taken by %s" % (position_label(row, col), taken["name"]))
        return row, col

    def _conflicts(self, box, row, col, owner_id):
        """Warnings when placing owner_id's sample into someone else's claim or reservation."""
        warnings = []
        rack = self._get("racks", box["rack_id"], "Rack")
        for what, thing in (("Rack", rack), ("Box", box)):
            if thing["claimed_by"] and thing["claimed_by"] != owner_id:
                note = " (%s)" % thing["claim_note"] if thing["claim_note"] else ""
                warnings.append("%s \"%s\" is claimed by %s%s"
                                % (what, thing["name"], self._member_name(thing["claimed_by"]), note))
        if row is not None:
            res = self._one("SELECT * FROM reservations WHERE box_id = ? AND row = ? AND col = ?",
                            (box["id"], row, col))
            if res and res["member_id"] != owner_id:
                warnings.append("%s is reserved by %s" % (position_label(row, col),
                                                          self._member_name(res["member_id"])))
        return warnings

    def _fill_reservation(self, box_id, row, col, user):
        """A spot that gets filled is no longer reserved."""
        if row is None:
            return
        res = self._one("SELECT * FROM reservations WHERE box_id = ? AND row = ? AND col = ?", (box_id, row, col))
        if res:
            self.conn.execute("DELETE FROM reservations WHERE id = ?", (res["id"],))
            self._log(user, "unreserve", "reservation", res["id"],
                      "%s filled" % position_label(row, col), res, None)

    def _sample_fields(self, data, base=None):
        s = dict(base or {})
        for k in ("name", "type", "date", "notes"):
            if k in data:
                s[k] = (data[k] or "").strip()
        if "owner_id" in data:
            s["owner_id"] = data["owner_id"] or None
        if not s.get("name"):
            raise ApiError(400, "Sample name is required")
        self._check_member(s.get("owner_id"))
        return s

    def add_sample(self, data, user="", force=False):
        def go():
            box = self._get("boxes", data.get("box_id"), "Box")
            row, col = self._check_spot(box, data.get("row"), data.get("col"))
            s = self._sample_fields(data)
            warnings = self._conflicts(box, row, col, s.get("owner_id"))
            if warnings and not force:
                raise NeedsConfirm(warnings)
            self._fill_reservation(box["id"], row, col, user)
            ts = now()
            cur = self.conn.execute(
                "INSERT INTO samples (box_id, row, col, name, type, date, owner_id, notes, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (box["id"], row, col, s["name"], s.get("type", ""), s.get("date", ""), s.get("owner_id"),
                 s.get("notes", ""), ts, ts))
            after = self.get_sample(cur.lastrowid)
            self._log(user, "add", "sample", after["id"], after["name"], None, after)
            return after
        return self._tx(go)

    def edit_sample(self, sample_id, data, user="", force=False):
        def go():
            before = self.get_sample(sample_id)
            s = self._sample_fields(data, before)
            if s.get("owner_id") != before["owner_id"]:
                box = self._get("boxes", before["box_id"], "Box")
                warnings = self._conflicts(box, None, None, s.get("owner_id"))
                if warnings and not force:
                    raise NeedsConfirm(warnings)
            self.conn.execute(
                "UPDATE samples SET name=?, type=?, date=?, owner_id=?, notes=?, updated_at=? WHERE id=?",
                (s["name"], s["type"], s["date"], s.get("owner_id"), s["notes"], now(), sample_id))
            after = self.get_sample(sample_id)
            self._log(user, "edit", "sample", sample_id, after["name"], before, after)
            return after
        return self._tx(go)

    def move_sample(self, sample_id, data, user="", force=False):
        def go():
            before = self.get_sample(sample_id)
            box = self._get("boxes", data.get("box_id"), "Box")
            row, col = self._check_spot(box, data.get("row"), data.get("col"), ignore_sample=sample_id)
            if box["id"] == before["box_id"] and row == before["row"] and col == before["col"]:
                raise ApiError(400, "The sample is already there")
            warnings = self._conflicts(box, row, col, before["owner_id"])
            if warnings and not force:
                raise NeedsConfirm(warnings)
            self._fill_reservation(box["id"], row, col, user)
            self.conn.execute("UPDATE samples SET box_id=?, row=?, col=?, updated_at=? WHERE id=?",
                              (box["id"], row, col, now(), sample_id))
            after = self.get_sample(sample_id)
            self._log(user, "move", "sample", sample_id, after["name"], before, after)
            return after
        return self._tx(go)

    def remove_sample(self, sample_id, user=""):
        def go():
            before = self.get_sample(sample_id)
            self.conn.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
            self._log(user, "remove", "sample", sample_id, before["name"], before, None)
        return self._tx(go)

    # ---- reservations ---------------------------------------------------

    def reserve(self, data, user=""):
        def go():
            box = self._get("boxes", data.get("box_id"), "Box")
            if box["kind"] == "list":
                raise ApiError(400, "Spots can only be reserved in grid boxes")
            row, col = self._check_spot(box, data.get("row"), data.get("col"))
            self._get("members", data.get("member_id"), "Lab member")
            if self._one("SELECT id FROM reservations WHERE box_id=? AND row=? AND col=?", (box["id"], row, col)):
                raise ApiError(409, "%s is already reserved" % position_label(row, col))
            cur = self.conn.execute(
                "INSERT INTO reservations (box_id, row, col, member_id, note, created_at) VALUES (?,?,?,?,?,?)",
                (box["id"], row, col, data["member_id"], (data.get("note") or "").strip(), now()))
            res = self._get("reservations", cur.lastrowid, "Reservation")
            self._log(user, "reserve", "reservation", res["id"],
                      "%s %s for %s" % (box["name"], position_label(row, col), self._member_name(res["member_id"])),
                      None, res)
            return res
        return self._tx(go)

    def unreserve(self, res_id, user=""):
        def go():
            res = self._get("reservations", res_id, "Reservation")
            box = self._get("boxes", res["box_id"], "Box")
            self.conn.execute("DELETE FROM reservations WHERE id = ?", (res_id,))
            self._log(user, "unreserve", "reservation", res_id,
                      "%s %s" % (box["name"], position_label(res["row"], res["col"])), res, None)
        return self._tx(go)

    # ---- search, history, overview -------------------------------------

    def search(self, q, limit=200):
        q = (q or "").strip()
        if not q:
            return []
        m = re.fullmatch(r"[Ss]-?0*(\d+)", q)
        if m:
            rows = self._q("SELECT * FROM samples WHERE id = ?", (int(m.group(1)),))
            if rows:
                return [self._decorate(s) for s in rows]
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self._q(
            "SELECT s.* FROM samples s LEFT JOIN members m ON m.id = s.owner_id"
            " WHERE s.name LIKE ?1 ESCAPE '\\' OR s.type LIKE ?1 ESCAPE '\\' OR s.notes LIKE ?1 ESCAPE '\\'"
            " OR s.date LIKE ?1 ESCAPE '\\' OR m.name LIKE ?1 ESCAPE '\\'"
            " ORDER BY s.name LIMIT ?2", (like, limit))
        return [self._decorate(s) for s in rows]

    def history(self, entity=None, entity_id=None, user=None, action=None, q=None, limit=200, offset=0):
        where, args = [], []
        for col, val in (("entity", entity), ("entity_id", entity_id), ("user", user), ("action", action)):
            if val not in (None, ""):
                where.append("%s = ?" % col)
                args.append(val)
        if q:
            where.append("(label LIKE ? OR before_json LIKE ? OR after_json LIKE ?)")
            args += ["%" + q + "%"] * 3
        sql = "SELECT * FROM history"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        rows = self._q(sql, args + [int(limit), int(offset)])
        for r in rows:
            r["before"] = json.loads(r.pop("before_json")) if r["before_json"] else None
            r["after"] = json.loads(r.pop("after_json")) if r["after_json"] else None
        return rows

    def whos_where(self):
        out = []
        tree = self.tree()
        res = self._q("SELECT box_id, member_id, COUNT(*) n FROM reservations GROUP BY box_id, member_id")
        for f in tree["freezers"]:
            for r in f["racks"]:
                for b in r["boxes"]:
                    out.append({
                        "freezer": f["name"], "rack": r["name"], "rack_id": r["id"],
                        "rack_claimed_by": r["claimed_by"], "rack_claim_note": r["claim_note"],
                        "box": b["name"], "box_id": b["id"], "kind": b["kind"],
                        "box_claimed_by": b["claimed_by"], "box_claim_note": b["claim_note"],
                        "capacity": b["capacity"], "used": b["used"], "reserved": b["reserved"],
                        "free": (b["capacity"] - b["used"] - b["reserved"]) if b["capacity"] else None,
                        "people": {("null" if p["member_id"] is None else str(p["member_id"])): p["count"]
                                   for p in b["people"]},
                        "reservations": {str(x["member_id"]): x["n"] for x in res if x["box_id"] == b["id"]},
                    })
        return {"rows": out, "members": tree["members"]}


# ---- HTTP ---------------------------------------------------------------

ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def _int(v):
    return int(v) if v not in (None, "") else None


@route("GET", r"/api/tree")
def r_tree(store, m, body, qs, user):
    return store.tree()


@route("GET", r"/api/members")
def r_members(store, m, body, qs, user):
    return store.list_members()


@route("POST", r"/api/members")
def r_add_member(store, m, body, qs, user):
    return store.add_member(body, user)


@route("PUT", r"/api/members/(\d+)")
def r_edit_member(store, m, body, qs, user):
    return store.update_member(int(m.group(1)), body, user)


@route("POST", r"/api/freezers")
def r_add_freezer(store, m, body, qs, user):
    return store.add_freezer(body, user)


@route("POST", r"/api/racks")
def r_add_rack(store, m, body, qs, user):
    return store.add_rack(body, user)


@route("POST", r"/api/boxes")
def r_add_box(store, m, body, qs, user):
    return store.add_box(body, user)


@route("PUT", r"/api/(freezers|racks|boxes)/(\d+)")
def r_rename(store, m, body, qs, user):
    return store.rename(m.group(1), int(m.group(2)), body, user)


@route("DELETE", r"/api/(freezers|racks|boxes)/(\d+)")
def r_delete(store, m, body, qs, user):
    store.delete_container(m.group(1), int(m.group(2)), user)
    return {"ok": True}


@route("PUT", r"/api/(racks|boxes)/(\d+)/claim")
def r_claim(store, m, body, qs, user):
    return store.claim(m.group(1), int(m.group(2)), body, user)


@route("GET", r"/api/boxes/(\d+)")
def r_box(store, m, body, qs, user):
    return store.get_box(int(m.group(1)))


@route("GET", r"/api/samples/(\d+)")
def r_sample(store, m, body, qs, user):
    return store.get_sample(int(m.group(1)))


@route("POST", r"/api/samples")
def r_add_sample(store, m, body, qs, user):
    return store.add_sample(body, user, force=bool(body.get("force")))


@route("PUT", r"/api/samples/(\d+)")
def r_edit_sample(store, m, body, qs, user):
    return store.edit_sample(int(m.group(1)), body, user, force=bool(body.get("force")))


@route("POST", r"/api/samples/(\d+)/move")
def r_move_sample(store, m, body, qs, user):
    return store.move_sample(int(m.group(1)), body, user, force=bool(body.get("force")))


@route("DELETE", r"/api/samples/(\d+)")
def r_remove_sample(store, m, body, qs, user):
    store.remove_sample(int(m.group(1)), user)
    return {"ok": True}


@route("POST", r"/api/reservations")
def r_reserve(store, m, body, qs, user):
    return store.reserve(body, user)


@route("DELETE", r"/api/reservations/(\d+)")
def r_unreserve(store, m, body, qs, user):
    store.unreserve(int(m.group(1)), user)
    return {"ok": True}


@route("GET", r"/api/search")
def r_search(store, m, body, qs, user):
    return store.search(qs.get("q", ""))


@route("GET", r"/api/history")
def r_history(store, m, body, qs, user):
    return store.history(entity=qs.get("entity"), entity_id=_int(qs.get("entity_id")), user=qs.get("user"),
                         action=qs.get("action"), q=qs.get("q"),
                         limit=_int(qs.get("limit")) or 200, offset=_int(qs.get("offset")) or 0)


@route("GET", r"/api/whos-where")
def r_whos_where(store, m, body, qs, user):
    return store.whos_where()


class Handler(BaseHTTPRequestHandler):
    store = None
    passcode = ""

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "Not found"})
        with open(full, "rb") as f:
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            self._send(200, f.read(), ctype)

    def _api(self, method):
        url = urlparse(self.path)
        if url.path == "/api/config":
            return self._send(200, {"needs_passcode": bool(self.passcode)})
        if self.passcode and self.headers.get("X-Passcode", "") != self.passcode:
            return self._send(401, {"error": "Wrong or missing lab passcode"})
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}
        user = unquote(self.headers.get("X-User", ""))[:80]
        body = {}
        if method in ("POST", "PUT"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "Invalid JSON"})
        for meth, pattern, fn in ROUTES:
            m = pattern.match(url.path)
            if m and meth == method:
                try:
                    with self.store.lock:  # one shared connection: serialise all access
                        result = fn(self.store, m, body, qs, user)
                    return self._send(200, result)
                except ApiError as e:
                    return self._send(e.status, {"error": e.message})
                except NeedsConfirm as e:
                    return self._send(409, {"error": "Please confirm", "needs_confirm": True,
                                            "warnings": e.warnings})
                except sqlite3.IntegrityError as e:
                    return self._send(409, {"error": "Database rule broken: %s" % e})
        self._send(404, {"error": "Unknown API route"})

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        self._static(urlparse(self.path).path)

    def do_POST(self):
        self._api("POST")

    def do_PUT(self):
        self._api("PUT")

    def do_DELETE(self):
        self._api("DELETE")


def load_config(path):
    cfg = {"host": "127.0.0.1", "port": 8000, "db": "freezer.db", "passcode": ""}
    if path and os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Freezer sample tracker")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--db")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    for k in ("db", "host", "port"):
        if getattr(args, k):
            cfg[k] = getattr(args, k)
    db = cfg["db"] if os.path.isabs(cfg["db"]) else os.path.join(HERE, cfg["db"])

    Handler.store = Store(db)
    Handler.passcode = cfg.get("passcode") or ""
    server = ThreadingHTTPServer((cfg["host"], int(cfg["port"])), Handler)
    url = "http://%s:%d/" % ("localhost" if cfg["host"] in ("0.0.0.0", "127.0.0.1") else cfg["host"], cfg["port"])
    print("Freezer tracker running at %s  (database: %s)" % (url, db))
    if cfg["host"] == "0.0.0.0":
        print("Lab mates can connect at http://%s:%d/" % (os.uname().nodename, cfg["port"]))
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
