import csv
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import csvio  # noqa: E402
from server import ApiError, Store  # noqa: E402


class CsvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.s.conn.close()
        self.tmp.cleanup()

    def count(self, table):
        return self.s._one("SELECT COUNT(*) n FROM %s" % table)["n"]

    def test_preview_changes_nothing_then_commit(self):
        text = ("Freezer,Rack,Box,Position,Sample Name,Sample Type,Date Frozen,Owner,Comments\n"
                "-80 A,Rack 1,Box 1,A1,MNV P3,Virus stock,3/14/2025,Kimia,titer 1e7\n"
                "-80 A,Rack 1,Box 1,b2,pUC19,Plasmid,2025-02-01,Sam,\n")
        report = csvio.import_csv(self.s, text, user="K")
        self.assertEqual(report["added"], 2)
        self.assertEqual(report["created"]["members"], ["Kimia", "Sam"])
        self.assertEqual(report["created"]["boxes"], ["Box 1 (9×9)"])
        self.assertEqual(self.count("samples"), 0)       # preview rolled back
        self.assertEqual(self.count("freezers"), 0)
        self.assertEqual(self.count("history"), 0)
        csvio.import_csv(self.s, text, user="K", commit=True)
        samples = self.s.search("MNV")
        self.assertEqual(samples[0]["location"], "-80 A / Rack 1 / Box 1 / A1")
        self.assertEqual(samples[0]["date"], "2025-03-14")
        self.assertEqual(samples[0]["owner"], "Kimia")
        self.assertEqual(self.s.search("pUC19")[0]["position"], "B2")
        self.assertEqual(self.s.history(entity="sample")[0]["after"]["via"], "CSV import")

    def test_errors_skip_only_bad_rows(self):
        self.s.add_freezer({"name": "F"})
        text = ("freezer,rack,box,box type,position,name\n"
                "F,R,Small,9x9,A1,ok 1\n"
                "F,R,Small,9x9,J10,outside\n"
                "F,R,Small,9x9,A1,duplicate spot\n"
                "F,R,Small,9x9,Z9,bad position\n"
                "F,R,Small,9x9,,\n"
                "F,R,Small,9x9,,needs position\n"
                "F,R,Bags,,,bag 1\n"
                "F,R,Big,,J10,big one\n")
        r = csvio.import_csv(self.s, text, commit=True)
        self.assertEqual(r["added"], 3)
        self.assertEqual([e["line"] for e in r["errors"]], [3, 4, 5, 6, 7])
        kinds = {b["name"]: b["kind"] for b in self.s._q("SELECT name, kind FROM boxes")}
        self.assertEqual(kinds, {"Small": "grid9", "Bags": "list", "Big": "grid10"})
        self.assertEqual(self.count("freezers"), 1)       # reused existing "F"

    def test_missing_name_column(self):
        with self.assertRaises(ApiError):
            csvio.import_csv(self.s, "freezer,box\nA,B\n")

    def test_export_round_trip(self):
        csvio.import_csv(self.s, "box,position,name,owner,notes\nB1,C3,X,Ana,\"has, comma\"\nB1,C4,Y,,\n", commit=True)
        out = csvio.export_csv(self.s)
        rows = list(csv.DictReader(io.StringIO(out.lstrip("﻿"))))
        self.assertEqual([r["name"] for r in rows], ["X", "Y"])
        self.assertEqual(rows[0]["notes"], "has, comma")
        self.assertEqual(rows[0]["box_type"], "9x9")
        # Re-importing an export doesn't duplicate samples.
        again = csvio.import_csv(self.s, out, commit=True)
        self.assertEqual((again["added"], len(again["skipped"])), (0, 2))
        box_id = self.s._one("SELECT id FROM boxes")["id"]
        self.assertEqual(len(csvio.export_csv(self.s, box_id).strip().splitlines()), 3)

    def test_excel_semicolon_and_bom(self):
        r = csvio.import_csv(self.s, "﻿Name;Box;Position\nA;Q;A1\n", commit=True)
        self.assertEqual(r["added"], 1)


if __name__ == "__main__":
    unittest.main()
