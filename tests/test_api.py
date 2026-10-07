import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402
from server import ApiError, NeedsConfirm, Store  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(os.path.join(self.tmp.name, "t.db"))
        self.ana = self.s.add_member({"name": "Ana", "color": "#f00"})["id"]
        self.ben = self.s.add_member({"name": "Ben", "color": "#00f"})["id"]
        f = self.s.add_freezer({"name": "-80 A"})["id"]
        self.rack = self.s.add_rack({"freezer_id": f, "name": "Rack 1"})["id"]
        self.b9 = self.s.add_box({"rack_id": self.rack, "name": "Box 9", "kind": "grid9"})["id"]
        self.b10 = self.s.add_box({"rack_id": self.rack, "name": "Box 10", "kind": "grid10"})["id"]
        self.bl = self.s.add_box({"rack_id": self.rack, "name": "Bags", "kind": "list"})["id"]

    def tearDown(self):
        self.s.conn.close()
        self.tmp.cleanup()

    def add(self, name="S1", box=None, row=0, col=0, owner=None, force=False, **kw):
        data = {"box_id": box or self.b9, "row": row, "col": col, "name": name,
                "owner_id": owner or self.ana}
        data.update(kw)
        return self.s.add_sample(data, user="Ana", force=force)

    def test_add_edit_remove_with_history(self):
        s = self.add(type="RNA", notes="first")
        self.assertEqual(s["code"], "S-%06d" % s["id"])
        self.assertEqual(s["position"], "A1")
        self.assertEqual(s["location"], "-80 A / Rack 1 / Box 9 / A1")
        self.s.edit_sample(s["id"], {"notes": "second"}, user="Ana")
        self.s.remove_sample(s["id"], user="Ben")
        with self.assertRaises(ApiError):
            self.s.get_sample(s["id"])
        h = self.s.history(entity="sample", entity_id=s["id"])
        self.assertEqual([x["action"] for x in h], ["remove", "edit", "add"])
        self.assertEqual(h[0]["user"], "Ben")
        self.assertEqual(h[0]["before"]["notes"], "second")  # removed sample still readable
        self.assertEqual(h[1]["before"]["notes"], "first")

    def test_spot_taken_and_bounds(self):
        self.add("S1", row=2, col=3)
        with self.assertRaises(ApiError) as e:
            self.add("S2", row=2, col=3)
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(ApiError):
            self.add("J10", box=self.b9, row=9, col=9)  # outside 9x9
        self.assertEqual(self.add("J10", box=self.b10, row=9, col=9)["position"], "J10")
        with self.assertRaises(ApiError):
            self.add("K11", box=self.b10, row=10, col=10)

    def test_list_box_has_no_positions(self):
        a = self.add("Bag 1", box=self.bl, row=4, col=4)
        b = self.add("Bag 2", box=self.bl)
        self.assertIsNone(a["row"])
        self.assertIsNone(b["col"])
        self.assertEqual(len(self.s.get_box(self.bl)["samples"]), 2)

    def test_move(self):
        a = self.add("A", row=0, col=0)
        b = self.add("B", row=0, col=1)
        with self.assertRaises(ApiError):
            self.s.move_sample(a["id"], {"box_id": self.b9, "row": 0, "col": 1})
        moved = self.s.move_sample(a["id"], {"box_id": self.b10, "row": 5, "col": 5}, user="Ana")
        self.assertEqual(moved["position"], "F6")
        to_list = self.s.move_sample(b["id"], {"box_id": self.bl}, user="Ana")
        self.assertIsNone(to_list["row"])
        h = self.s.history(entity="sample", entity_id=a["id"], action="move")[0]
        self.assertEqual(h["before"]["position"], "A1")
        self.assertEqual(h["after"]["position"], "F6")

    def test_claim_warns_others_but_not_owner(self):
        self.s.claim("boxes", self.b9, {"member_id": self.ana, "note": "virus stocks"})
        self.add("Ana's", row=0, col=0, owner=self.ana)  # no warning for the claimer
        with self.assertRaises(NeedsConfirm) as e:
            self.add("Ben's", row=0, col=1, owner=self.ben)
        self.assertIn("claimed by Ana", e.exception.warnings[0])
        self.add("Ben's", row=0, col=1, owner=self.ben, force=True)
        self.s.claim("racks", self.rack, {"member_id": self.ben})
        with self.assertRaises(NeedsConfirm) as e:
            self.add("Ana 2", box=self.b10, row=0, col=0, owner=self.ana)
        self.assertIn("Rack", e.exception.warnings[0])
        self.s.claim("racks", self.rack, {"member_id": None})
        self.assertEqual(self.s.history(entity="rack")[0]["action"], "unclaim")

    def test_reservations(self):
        r = self.s.reserve({"box_id": self.b9, "row": 4, "col": 4, "member_id": self.ana}, user="Ana")
        with self.assertRaises(ApiError):
            self.s.reserve({"box_id": self.b9, "row": 4, "col": 4, "member_id": self.ben})
        with self.assertRaises(ApiError):
            self.s.reserve({"box_id": self.bl, "row": 0, "col": 0, "member_id": self.ben})
        with self.assertRaises(NeedsConfirm):
            self.add("Ben's", row=4, col=4, owner=self.ben)
        self.add("Ana's", row=4, col=4, owner=self.ana)  # reserver fills it: no warning
        self.assertEqual(self.s.get_box(self.b9)["reservations"], [])
        self.assertEqual(self.s.history(entity="reservation", entity_id=r["id"])[0]["action"], "unreserve")

    def test_search(self):
        a = self.add("MNV-1 P3 stock", type="Virus stock", owner=self.ben)
        self.add("pUC19", row=0, col=1, notes="ampR")
        self.assertEqual([x["name"] for x in self.s.search("mnv")], ["MNV-1 P3 stock"])
        self.assertEqual(len(self.s.search("ampr")), 1)
        self.assertEqual(len(self.s.search("Ben")), 1)  # by owner name
        self.assertEqual(self.s.search(a["code"])[0]["id"], a["id"])
        self.assertEqual(self.s.search("100%"), [])

    def test_delete_container_only_when_empty(self):
        self.add("S1")
        with self.assertRaises(ApiError):
            self.s.delete_container("boxes", self.b9)
        self.s.delete_container("boxes", self.b10)
        self.assertEqual(self.s.history(entity="box", action="delete")[0]["label"], "Box 10")

    def test_whos_where(self):
        self.add("a", owner=self.ana)
        self.add("b", row=0, col=1, owner=self.ana)
        self.add("c", row=0, col=2, owner=self.ben)
        self.s.reserve({"box_id": self.b9, "row": 1, "col": 1, "member_id": self.ben})
        row = [r for r in self.s.whos_where()["rows"] if r["box_id"] == self.b9][0]
        self.assertEqual(row["people"], {str(self.ana): 2, str(self.ben): 1})
        self.assertEqual(row["free"], 81 - 3 - 1)
        self.assertEqual(row["reservations"], {str(self.ben): 1})


class HttpTest(unittest.TestCase):
    """End-to-end through the HTTP layer, including the passcode and confirm flow."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.Handler.store = Store(os.path.join(cls.tmp.name, "h.db"))
        server.Handler.passcode = "ice"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.Handler.store.conn.close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, passcode="ice"):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Passcode": passcode, "X-User": "Tester",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flow(self):
        self.assertEqual(self.call("GET", "/api/tree", passcode="nope")[0], 401)
        _, m1 = self.call("POST", "/api/members", {"name": "Cy", "color": "#0a0"})
        _, m2 = self.call("POST", "/api/members", {"name": "Di", "color": "#a0a"})
        _, f = self.call("POST", "/api/freezers", {"name": "F"})
        _, r = self.call("POST", "/api/racks", {"freezer_id": f["id"], "name": "R"})
        _, b = self.call("POST", "/api/boxes", {"rack_id": r["id"], "name": "B", "kind": "grid10"})
        self.call("PUT", "/api/boxes/%d/claim" % b["id"], {"member_id": m1["id"]})
        sample = {"box_id": b["id"], "row": 9, "col": 9, "name": "X", "owner_id": m2["id"]}
        status, body = self.call("POST", "/api/samples", sample)
        self.assertEqual(status, 409)
        self.assertTrue(body["needs_confirm"])
        status, s = self.call("POST", "/api/samples", dict(sample, force=True))
        self.assertEqual((status, s["position"]), (200, "J10"))
        self.assertEqual(self.call("GET", "/api/search?q=X")[1][0]["id"], s["id"])
        h = self.call("GET", "/api/history?entity=sample")[1]
        self.assertEqual(h[0]["user"], "Tester")
        self.assertEqual(self.call("GET", "/api/boxes/999")[0], 404)

    def test_static_and_traversal(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"<html", r.read())
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/../server.py")


if __name__ == "__main__":
    unittest.main()
