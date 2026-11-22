"""Repeatable local verification; never downloads or uses company data.

Real loopback TLS transport and isolated PostgreSQL tests are opt-in through
their explicit QA configuration. No company service or host policy is changed.
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
from importlib import metadata
import json
from pathlib import Path
import platform
import sqlite3
import ssl
import sys
import time
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))


def source_manifest():
    files = {}
    for directory in ("src", "schema", "tools", "tests"):
        for path in sorted((PROJECT / directory).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".json", ".sql"} and not any(p.startswith(".") or p == "__pycache__" for p in path.relative_to(PROJECT).parts):
                files[path.relative_to(PROJECT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("pyproject.toml", "README.md", "requirements.lock"):
        if not (PROJECT / name).is_file():
            continue
        files[name] = hashlib.sha256((PROJECT / name).read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return files, digest


class CountingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.modules = defaultdict(lambda: dict(tests=0, passed=0, failures=0, errors=0, skipped=0))

    def startTest(self, test):
        self.modules[type(test).__module__]["tests"] += 1
        super().startTest(test)

    def addSuccess(self, test):
        self.modules[type(test).__module__]["passed"] += 1
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.modules[type(test).__module__]["failures"] += 1
        super().addFailure(test, err)

    def addError(self, test, err):
        self.modules[type(test).__module__]["errors"] += 1
        super().addError(test, err)

    def addSkip(self, test, reason):
        if isinstance(test, unittest.TestCase):
            self.modules[type(test).__module__]["skipped"] += 1
        super().addSkip(test, reason)


def main():
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests"))
    result = unittest.TextTestRunner(verbosity=2, resultclass=CountingResult).run(suite)
    files, digest = source_manifest()
    report = {
        "timestampUtc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "openssl": ssl.OPENSSL_VERSION,
        "testsRun": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
        "successful": result.wasSuccessful(), "durationSeconds": round(time.monotonic() - started, 3),
        "liveCompanyDataWritten": False, "companyServerModified": False,
        "dependencyAvailability": {name: importlib.util.find_spec(name) is not None for name in ("fastapi", "starlette", "uvicorn", "psycopg")},
        "realPostgreSQLTested": result.modules.get("test_postgresql_integration", {}).get("tests", 0) > 0,
        "realHTTPFrameworkTested": result.modules.get("test_http_framework", {}).get("tests", 0) > 0,
        "realTCPAndTLSTested": result.modules.get("test_uvicorn_transport", {}).get("tests", 0) > 0,
        "moduleResults": dict(result.modules),
        "dependencyVersions": {name: metadata.version(name) for name in ("fastapi", "starlette", "uvicorn", "psycopg", "tzdata") if importlib.util.find_spec(name) is not None},
        "productionAccepted": False, "sourceManifestSha256": digest,
        "sourceFiles": files,
    }
    (PROJECT / "docs" / "verification-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
