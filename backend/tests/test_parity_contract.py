"""Cross-check the shipped legacy API contract against real Python bindings.

This supplements behavioral domain tests; route presence alone does not prove
complete functional equivalence or authorize company data deployment.
"""
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pak_maintenance.api import Api, Request
from pak_maintenance.common import User, dumps


PROJECT = Path(__file__).resolve().parents[1]
LEGACY = PROJECT.parent / "PakMaintenance" / "server" / "Program.cs"


class ParityContractTests(unittest.TestCase):
    def setUp(self):
        self.actor = User("admin", "test-admin", "Synthetic administrator", "admin")
        self.services = SimpleNamespace(auth=SimpleNamespace(authenticate=Mock(return_value=self.actor)),
            **{name: Mock() for name in ("master", "work", "pm", "registry", "setup", "performance", "reporting", "consumption", "backup", "db")})
        self.config = SimpleNamespace(database=SimpleNamespace(provider="sqlite"), seed_directory="unused",
                                      company_name="Synthetic company", local_test_mode=False)
        self.api = Api(self.services, self.config)

    def dispatch(self, method, path, query=None, body=None):
        return self.api.dispatch(Request(method, path, query or {},
            {"content-type": "application/json"}, b"" if body is None else dumps(body).encode()))

    def test_every_legacy_api_path_and_method_has_an_actual_binding(self):
        if not LEGACY.is_file():
            self.skipTest("Legacy source is unavailable in this portable checkout")
        source = LEGACY.read_text(encoding="utf-8-sig")
        expected = {(verb.upper(), path) for verb, path in re.findall(r'app\.Map(Get|Post|Put|Delete|Patch)\("([^"]+)"', source)}
        for path, methods in re.findall(r'app\.MapMethods\("([^"]+)"\s*,\s*new\[\]\{([^}]+)\}', source):
            for verb in re.findall(r'"(GET|HEAD|POST|PUT|DELETE|PATCH)"|HttpMethods\.(Get|Head|Post|Put|Delete|Patch)', methods):
                expected.add((next(value for value in verb if value).upper(), path))
        # The legacy report HEAD routes are emitted by a foreach loop.
        report_loop = re.search(r'foreach\(var reportPath in new\[\]\{([^}]+)\}\)', source)
        self.assertIsNotNone(report_loop)
        expected.update(("HEAD", path) for path in re.findall(r'"([^"]+)"', report_loop[1]))
        self.assertGreaterEqual(len(expected), 65)
        missing = []
        for method, path in sorted(expected):
            actual_path = path.replace("{id}", "synthetic-item")
            if not any(route.pattern.fullmatch(actual_path) and (route.method == method or method == "HEAD" and route.method == "GET") for route in self.api.routes):
                missing.append((method, path))
        self.assertEqual(missing, [])

    def test_health_retains_legacy_capabilities_without_claiming_acceptance(self):
        if not LEGACY.is_file():
            self.skipTest("Legacy source is unavailable in this portable checkout")
        source = LEGACY.read_text(encoding="utf-8-sig")
        capabilities = re.search(r'capabilities=new\[\]\{([^}]+)\}', source)
        self.assertIsNotNone(capabilities)
        health = self.dispatch("GET", "/api/health").payload
        self.assertTrue(set(re.findall(r'"([^"]+)"', capabilities[1])).issubset(health["capabilities"]))
        self.assertTrue({"individual-head-grading", "chief-personnel-privacy", "pm-change-approval"}.issubset(health["capabilities"]))
        self.assertEqual(health["apiVersion"], 5)
        self.assertEqual(health["schemaVersion"], 8)
        self.assertFalse(health["productionAccepted"])

    def test_legacy_registry_and_order_filter_names_reach_services(self):
        query = {"page": "2", "pageSize": "10", "q": "pump", "hall": "Process", "specialty": "mechanical", "family": "pump", "periodClass": "monthly", "year": "1405"}
        self.dispatch("GET", "/api/pm/registry/forms", query)
        self.services.registry.forms.assert_called_once_with(self.actor, page=2, page_size=10, q="pump", hall="Process", specialty="mechanical", family="pump", period_class="monthly", year=1405)
        self.dispatch("GET", "/api/orders", {"page": "2", "pageSize": "10", "q": "pump", "type": "PM", "status": "completed", "assetId": "asset", "view": "all", "hall": "Process", "overdue": "true"})
        self.services.work.list_page.assert_called_once_with(self.actor, page=2, page_size=10, q="pump", type="PM", status="completed", asset_id="asset", view="all", hall="Process", overdue=True)

    def test_master_data_archive_and_work_approval_payloads_keep_legacy_fields(self):
        asset = {"code": "P-1", "name": "Pump", "hall": "Process", "line": "A", "manufacturer": "Maker", "model": "M", "serial": "S", "criticality": "high", "version": 1}
        self.dispatch("PUT", "/api/assets/asset", body=asset)
        self.services.master.save_asset.assert_called_once_with(self.actor, asset, "asset")
        self.dispatch("POST", "/api/imports/legacy", body={"mode": "archive"})
        self.services.master.import_legacy.assert_called_once_with(self.actor, {"mode": "archive"})
        self.actor = User("head", "test-head", "Synthetic chief", "head")
        self.services.auth.authenticate.return_value = self.actor
        approval = {"version": 3, "decision": "approve", "grades": [{"personId": "person", "coefficient": 2}], "comment": "reviewed"}
        self.dispatch("POST", "/api/orders/work/decision", body=approval)
        self.services.work.decision.assert_called_once_with(self.actor, "work", approval)


if __name__ == "__main__":
    unittest.main()
