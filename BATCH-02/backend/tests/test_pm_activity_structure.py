"""Structural edits must preserve source evidence, result identity and history."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from uuid import uuid4

from pak_maintenance.common import ApiError, User
from pak_maintenance.database import Database, DatabaseConfig
from pak_maintenance.pm_activity_structure import apply_activity_structure, describe_activity_structure, _grid
from pak_maintenance.pm_service import PmService, dump, parse
from pak_maintenance.work_service import WorkService

ROOT = Path(__file__).resolve().parents[1]


def cell(identifier, value="", *, activity=False, editable=False, column=None):
    result = dict(id=identifier, text=value, colSpan=1, activity=activity, editable=editable)
    if column is not None:
        result["sourceRef"] = dict(column=column)
    return result


def matrix():
    return dict(id="source-form", version=1, formKind="grouped-inspection",
                source=dict(sha256="a" * 64, path="original.xlsx"),
                bodyBlocks=[dict(id="block", type="table", tableId="matrix")],
                tables=[dict(id="matrix", sourceSheet="Sheet1", originalMergedRanges=["A1:D1"], rows=[
                    dict(id="heading", sourceRow=1, cells=[cell("heading", "بازدید", column=1), cell("covered", column=2), cell("covered2", column=3), cell("covered3", column=4)]),
                    dict(id="activities", sourceRow=2, cells=[cell("name", "نام دستگاه", column=1), cell("code", "کد دستگاه", column=2), cell("a", "نشتی", activity=True, column=3), cell("b", "لرزش", activity=True, column=4)]),
                    dict(id="machine", sourceRow=3, cells=[cell("m", "پمپ", column=1), cell("mc", "39-1", column=2), cell("ra", editable=True, column=3), cell("rb", editable=True, column=4)])])])


class PmActivityGeometryTests(unittest.TestCase):
    def test_column_add_delete_keeps_equipment_and_result_identities_and_spanning_heading(self):
        original = matrix(); before = deepcopy(original)
        changed = apply_activity_structure(original, [dict(id="a", text="نشتی اصلاح‌شده"), dict(text="صدا", cloneFromId="a")])
        self.assertEqual(original, before)
        self.assertEqual(changed["source"], before["source"])
        self.assertEqual(changed["bodyBlocks"], before["bodyBlocks"])
        table = changed["tables"][0]
        self.assertEqual(table["layoutVersion"], 1)
        values = {e["cell"]["id"]: e for e in _grid(table)}
        for identifier in ("m", "mc", "a", "ra"):
            self.assertIn(identifier, values)
        self.assertNotIn("b", values); self.assertNotIn("rb", values)
        self.assertEqual(values["heading"]["w"], 4)
        self.assertEqual(values["a"]["cell"]["sourceRef"], dict(column=3))
        results = [v for v in values.values() if v["cell"].get("editable")]
        self.assertEqual(len(results), 2)
        self.assertEqual(len({v["cell"]["id"] for v in results}), 2)
        self.assertEqual(table["rows"][2]["sourceRow"], 3)
        # A subsequent revision must use active geometry, not old merge ranges.
        changed["version"] = 2
        activities = describe_activity_structure(changed)
        again = apply_activity_structure(changed, [dict(id=a["id"], text=a["text"]) for a in activities] + [dict(text="دما", cloneFromId="a")])
        self.assertEqual(next(e for e in _grid(again["tables"][0]) if e["cell"]["id"] == "heading")["w"], 5)

    def test_invalid_identity_empty_text_order_and_last_activity_are_rejected(self):
        for payload in ([], [dict(id="unknown", text="x")], [dict(text="x", cloneFromId="unknown")],
                        [dict(id="a", text="x"), dict(id="a", text="x")],
                        [dict(id="b", text="x"), dict(id="a", text="x")],
                        [dict(id="a", text=" ")], [dict(text="x", cloneFromId="a")] * 201):
            with self.subTest(payload=payload), self.assertRaises(ApiError):
                apply_activity_structure(matrix(), payload)

    def test_all_available_original_activity_forms_support_add_and_delete_without_source_change(self):
        baseline = ROOT.parents[1] / "pak-data" / "PAK-Data-rc10" / "seed" / "pm-import.json"
        if not baseline.is_file():
            self.skipTest("Company baseline is intentionally absent from a source-only package")
        forms = json.loads(baseline.read_text(encoding="utf-8-sig"))["templates"]
        checked = 0
        for original in forms:
            activities = describe_activity_structure(original)
            if not activities:
                continue
            with self.subTest(template=original["id"]):
                payload = [dict(id=a["id"], text=a["text"]) for a in activities]
                payload.insert(1, dict(text="فعالیت جدید آزمایشی", cloneFromId=activities[0]["id"]))
                changed = apply_activity_structure(original, payload)
                self.assertEqual(len(describe_activity_structure(changed)), len(activities) + 1)
                self.assertEqual(changed["source"], original["source"])
                self.assertEqual(changed.get("bodyBlocks"), original.get("bodyBlocks"))
                # Remove one original activity while retaining the newly cloned unit.
                changed["version"] = 2
                active = describe_activity_structure(changed)
                reduced = apply_activity_structure(changed, [dict(id=a["id"], text=a["text"]) for a in active if a["id"] != activities[0]["id"]])
                self.assertEqual(len(describe_activity_structure(reduced)), len(activities))
                self.assertEqual(reduced["source"], original["source"])
                checked += 1
        self.assertEqual(checked, 268)


class PmActivityPublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "tests" / ".pm-structure-fixtures" / uuid4().hex
        self.directory.mkdir(parents=True)
        self.path = self.directory / "synthetic.db"
        raw = sqlite3.connect(self.path)
        for script in (ROOT / "schema" / "sqlite-v10.sql", *sorted((ROOT / "schema").glob("sqlite-*-guards.sql"))):
            raw.executescript(script.read_text(encoding="utf-8-sig"))
        raw.close()
        self.db = Database(DatabaseConfig(provider="sqlite", database=str(self.path), development=True))
        self.head = User("head", role="head")
        self.supervisor = User("supervisor", role="supervisor", specialty="مکانیک")
        with self.db.transaction() as c:
            for person in (self.head, self.supervisor):
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:id,:role,:specialty,1,'',0)", dict(id=person.id, role=person.role, specialty=person.specialty))
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','39-1','پمپ','فرآیند','','{}')")
        self.work = WorkService(self.db); self.pm = PmService(self.db, self.work); self.work.pm_service = self.pm
        self.template = self.pm.create_template(self.head, dict(requestKey=uuid4().hex, title="بازدید", hall="فرآیند", specialty="مکانیک", period="ماهانه", activities=["نشتی", "لرزش"]))
        self.activities = describe_activity_structure(self.template)

    def tearDown(self):
        shutil.rmtree(self.directory)
        if not any(self.directory.parent.iterdir()):
            self.directory.parent.rmdir()

    def payload(self):
        return dict(version=1, reason="افزودن بازدید صدا و حذف لرزش", activities=[dict(id=self.activities[0]["id"], text="نشتی"), dict(text="صدا", cloneFromId=self.activities[0]["id"])])

    def test_full_sheet_preview_is_read_only_and_matches_publication_geometry(self):
        with self.db.connect() as c:
            audits=c.scalar("SELECT COUNT(*) FROM audit")
        proposed=self.pm.preview_activities(self.supervisor,self.template["id"],self.payload())
        self.assertEqual(proposed["version"],2)
        self.assertEqual(self.pm.template(self.head,self.template["id"])["version"],1)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit"),audits)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_template_revisions"),1)
        published=self.pm.edit_template(self.head,self.template["id"],self.payload())
        self.assertEqual(proposed["tables"],published["tables"])

    def test_preview_rejects_stale_version_and_outside_specialty(self):
        with self.assertRaises(ApiError) as stale:
            self.pm.preview_activities(self.head,self.template["id"],dict(self.payload(),version=0))
        self.assertEqual(stale.exception.status,409)
        outside=User("outside",role="supervisor",specialty="برق")
        with self.assertRaises(ApiError) as denied:
            self.pm.preview_activities(outside,self.template["id"],self.payload())
        self.assertEqual(denied.exception.status,403)

    def test_publication_preserves_issued_revision_and_source_result_ids(self):
        instant = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
        self.pm.edit_plan(self.head, self.template["planId"], dict(version=1, reason="فعال‌سازی آزمایشی", assetId="asset", supervisorId="supervisor", startDate="2026-09-23", slots=[dict(month=7, week=1)], active=True, mappingConfirmed=True), instant)
        self.pm.generate(instant)
        changed = self.pm.edit_template(self.head, self.template["id"], self.payload())
        self.assertEqual(changed["version"], 2)
        with self.db.connect() as c:
            old = parse(c.scalar("SELECT body FROM pm_template_revisions WHERE template_id=:id AND revision=1", dict(id=self.template["id"])))
            self.assertEqual([a["text"] for a in describe_activity_structure(old)], ["نشتی", "لرزش"])
            self.assertEqual(c.scalar("SELECT template_version FROM pm_weekly_occurrences"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_template_revisions"), 2)
        with self.assertRaises(ApiError) as stale:
            self.pm.edit_template(self.head, self.template["id"], self.payload())
        self.assertEqual(stale.exception.status, 409)

    def test_proposal_uses_same_validation_and_only_publishes_on_approval(self):
        body = dict(self.payload(), requestKey=uuid4().hex)
        proposal = self.pm.request_change(self.supervisor, "edit-template", self.template["id"], body)
        self.assertTrue(proposal["pendingApproval"])
        self.assertEqual(self.pm.template(self.head, self.template["id"])["version"], 1)
        request = proposal["changeRequest"]
        self.pm.decide_change(self.head, request["id"], dict(version=request["version"], decision="approve", reason="تأیید تغییر فعالیت"))
        self.assertEqual(self.pm.template(self.head, self.template["id"])["version"], 2)
        self.assertEqual([a["text"] for a in describe_activity_structure(self.pm.template(self.head, self.template["id"]))], ["نشتی", "صدا"])

    def test_revision_failure_rolls_back_template_and_audit(self):
        with self.db.transaction() as c:
            previous_audits = c.scalar("SELECT COUNT(*) FROM audit")
            c.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON pm_template_revisions BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.pm.edit_template(self.head, self.template["id"], self.payload())
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT version FROM pm_templates"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit"), previous_audits)


if __name__ == "__main__":
    unittest.main()
