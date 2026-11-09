"""Framework-independent API 7 bindings and explicit route authorization.

Only the adapter in application.py knows FastAPI. Domain calls, session cookies,
strict input parsing and route scopes can therefore be tested without a server.
"""
from dataclasses import dataclass, field
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
import re
import sqlite3
from typing import Any

from . import __version__
from .common import ApiError, dumps, loads
from .security import COOKIE, require
from .permissions import CHIEFS, PM_PROPOSERS, PERSONNEL_ADMINISTRATORS

CAPABILITIES = (
    "em-downtime-drilldown",
    "workflow-inbox", "participant-grading", "technician-registration", "performance-reports",
    "scalable-lists", "cursor-reports", "paged-catalogs", "versioned-pm-sheets", "persian-weekly-pm", "equipment-pm-calendar",
    "pm-form-library", "pm-template-designer", "pm-activation-setup", "pm-technician-queue", "approved-parts-consumption", "internal-network-only",
    "individual-head-grading", "chief-personnel-privacy", "pm-change-approval",
    "postgresql-only", "postgresql-17-minimum", "postgresql17-native-backups",
    "pm-submission-archive",
    "daily-line-oee-count",
    "oee-validated-counts-v2",
    "supervisor-self-work-head-only", "pm-activity-structure", "pm-working-pdf",
    "parts-xlsx-import", "pm-responsibility-transfer", "own-performance", "supervisor-review-correction", "supervisor-correction-approve",
)


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    address: str = ""
    secure: bool = False

    def json(self):
        if len(self.body) > 1024 * 1024:
            raise ApiError(413, "حجم درخواست بیش از حد مجاز است.")
        if self.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ApiError(415, "محتوای درخواست باید JSON باشد.")
        try:
            value = loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise ApiError(400, "ساختار اطلاعات درخواست معتبر نیست.") from None
        if not isinstance(value, dict):
            raise ApiError(400, "اطلاعات درخواست باید یک شیء باشد.")
        return value

    def integer(self, key, default=None):
        raw = self.query.get(key)
        if raw is None:
            return default
        if len(raw) > 12 or not re.fullmatch(r"-?[0-9]+", raw, re.ASCII):
            raise ApiError(400, "فیلتر عددی معتبر نیست.")
        value = int(raw)
        if not -2_147_483_648 <= value <= 2_147_483_647:
            raise ApiError(400, "فیلتر عددی خارج از محدوده است.")
        return value

    def boolean(self, key, default=False):
        raw = self.query.get(key)
        if raw is None:
            return default
        if raw.lower() not in {"true", "false"}:
            raise ApiError(400, "فیلتر بله یا خیر معتبر نیست.")
        return raw.lower() == "true"

    @property
    def token(self):
        try:
            raw = self.headers.get("cookie", "")
            # Duplicate authentication cookies are ambiguous across clients and
            # proxies. Do not silently select the last credential.
            if sum(part.strip().split("=", 1)[0] == COOKIE for part in raw.split(";")) > 1:
                return ""
            cookie = SimpleCookie()
            cookie.load(raw)
            value = cookie.get(COOKIE)
            return value.value if value else ""
        except CookieError:
            return ""


@dataclass(frozen=True, slots=True)
class Result:
    payload: Any = None
    status: int = 200
    headers: tuple[tuple[str, str], ...] = ()
    file: Path | None = None
    filename: str | None = None
    stream: Any = None
    media_type: str = "application/json; charset=utf-8"
    binary: bytes | None = None


@dataclass(frozen=True, slots=True)
class Route:
    method: str
    pattern: re.Pattern
    handler: Any
    roles: tuple[str, ...] = ()
    registration: bool = False
    public: bool = False


class Api:
    def __init__(self, services, config):
        self.services, self.config = services, config
        self.routes = []
        self._bind()

    def add(self, method, path, handler, *, roles=(), registration=False, public=False):
        pattern = re.escape(path).replace(re.escape("{id}"), r"(?P<id>[^/]{1,100})")
        self.routes.append(Route(method, re.compile(pattern + r"\Z"), handler, tuple(roles), registration, public))

    def dispatch(self, request):
        if len(request.path) > 2048 or len(request.query) > 100 or any(len(k) > 100 or len(v) > 2000 for k, v in request.query.items()):
            raise ApiError(400, "نشانی یا فیلتر درخواست بیش از حد مجاز است.")
        candidates = [r for r in self.routes if (m := r.pattern.fullmatch(request.path))]
        route = next((r for r in candidates if r.method == request.method or request.method == "HEAD" and r.method == "GET"), None)
        if route is None:
            # Unknown future routes never implicitly grant technicians access.
            user = self.services.auth.authenticate(request.token)
            require(user)
            raise ApiError(405 if candidates else 404, "روش درخواست مجاز نیست." if candidates else "مسیر API پیدا نشد.")
        user = None if route.public else self.services.auth.authenticate(request.token)
        if not route.public:
            user = require(user, *route.roles, registration=route.registration)
        values = route.pattern.fullmatch(request.path).groupdict()
        result = route.handler(request, user, **values)
        return result if isinstance(result, Result) else Result(result)

    @staticmethod
    def page(request):
        return dict(page=request.integer("page"), page_size=request.integer("pageSize"), q=request.query.get("q"))

    @staticmethod
    def cookie(token, secure, delete=False):
        value = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={'0' if delete else '28800'}"
        if secure:
            value += "; Secure"
        if delete:
            value += "; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
        return (("set-cookie", value),)

    def login(self, request, user):
        body = request.json()
        user, token = self.services.auth.login(request.address, body.get("code"), body.get("password"))
        return Result({"user": user.public(), "csrfToken": "same-origin-header"}, headers=self.cookie(token, request.secure))

    def logout(self, request, user):
        self.services.auth.logout(user, request.token)
        return Result({"ok": True}, headers=self.cookie("", request.secure, True))

    def password(self, request, user):
        body = request.json()
        self.services.auth.change_password(user, body.get("currentPassword"), body.get("newPassword"))
        return Result({"ok": True}, headers=self.cookie("", request.secure, True))

    def orders(self, request, user):
        q = request.query
        if "page" not in q and "pageSize" not in q:
            return self.services.work.list(user, q.get("type"), q.get("status"), q.get("q"))
        return self.services.work.list_page(user, **self.page(request), type=q.get("type"), status=q.get("status"),
                    asset_id=q.get("assetId"), view=q.get("view"), hall=q.get("hall"), overdue=request.boolean("overdue"))

    def inbox(self, request, user):
        if "page" not in request.query and "pageSize" not in request.query:
            return self.services.work.inbox(user)
        return self.services.work.inbox_page(user, **self.page(request), type=request.query.get("type"),
                    hall=request.query.get("hall"), overdue=request.boolean("overdue"))

    def original(self, request, user, id, task=False):
        path, name = self.services.pm.original(user, "" if task else id, self.config.seed_directory, id if task else None)
        return Result(file=Path(path), filename=Path(name.replace("\\", "/")).name, media_type="application/octet-stream")

    def backup(self, request, user):
        if self.config.database.provider != "postgresql":
            raise ApiError(503, "برنامه باید به PostgreSQL نسخهٔ ۱۷ یا بالاتر متصل باشد.")
        if request.method == "HEAD":
            return Result(status=204)
        snapshot = self.services.backup.create_snapshot()
        from .common import audit
        from .security import assert_live_actor
        def record(c):
            assert_live_actor(c, user)
            audit(c, user, "backup", "database", "postgresql",
                  {"bytes": snapshot.size, "sha256": snapshot.sha256, "archiveDecodeChecked": True,
                   "format": "postgresql-custom", "minimumServerVersion": 17})
        self.services.db.run_transaction(record)
        return Result(file=snapshot.path, filename=snapshot.path.name, media_type="application/octet-stream")

    def pm_change(self, request, user, operation, identifier=None):
        """Preserve the published state until the head reviews a proposed change."""
        body = request.json()
        if user.role not in CHIEFS:
            return Result(self.services.pm.request_change(user, operation, identifier, body), 202)
        methods = {"create-template": self.services.pm.create_template,
                   "edit-template": self.services.pm.edit_template,
                   "design-template": self.services.pm.design_template,
                   "retire-template": self.services.pm.retire_template,
                   "restore-template": self.services.pm.restore_template,
                   "edit-plan": self.services.pm.edit_plan}
        method = methods[operation]
        return method(user, body) if identifier is None else method(user, identifier, body)

    def parts_import(self, request, user, apply=False):
        from .parts_import import MAX_UPLOAD_BYTES
        if len(request.body) > MAX_UPLOAD_BYTES:
            raise ApiError(413, "حجم فایل اکسل نباید بیشتر از ۸ مگابایت باشد.")
        media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media not in {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"}:
            raise ApiError(415, "فایل را با قالب Excel .xlsx انتخاب کنید.")
        sheet = request.query.get("sheet")
        if apply:
            return self.services.parts_import.apply(user, request.body,
                request.headers.get("x-import-fingerprint", ""), sheet)
        return self.services.parts_import.preview(user, request.body, sheet)

    def pm_pdf(self, request, user, id):
        from .pm_pdf import render_template_pdf
        template = self.services.pm.template(user, id)
        expected = request.integer("version")
        if expected is None or expected != template["version"]:
            raise ApiError(409, "نسخهٔ برگه تغییر کرده است؛ صفحه را تازه کنید و دوباره PDF بگیرید.")
        content = render_template_pdf(template)
        # The download name contains only a digest of the template identity.
        import hashlib
        name = "PM-" + hashlib.sha256(id.encode()).hexdigest()[:16] + "-v" + str(expected) + ".pdf"
        return Result(binary=content, media_type="application/pdf", headers=(
            ("content-disposition", 'attachment; filename="' + name + '"'),))

    def _bind(self):
        self.add("POST", "/api/pm/templates/{id}/preview-activities", lambda r, u, id: self.services.pm.preview_activities(u, id, r.json()), roles=(*CHIEFS, *PM_PROPOSERS))
        self.add("GET", "/api/pm/tasks/{id}/responsibility", lambda r, u, id: self.services.pm.responsibility(u, id), roles=("head", "manager"))
        self.add("POST", "/api/pm/tasks/{id}/responsibility", lambda r, u, id: self.services.pm.transfer_responsibility(u, id, r.json()), roles=("head", "manager"))
        self.add("GET", "/api/pm/templates/{id}/pdf", self.pm_pdf, roles=("head", "manager", "planner", "admin", "supervisor"))
        self.add("POST", "/api/parts/import/preview", self.parts_import, roles=("admin", "head", "manager", "planner"))
        self.add("POST", "/api/parts/import/apply", lambda r, u: self.parts_import(r, u, True), roles=("admin", "head", "manager", "planner"))
        s, add = self.services, self.add
        chiefs = CHIEFS
        management = ("admin", "head", "manager", "planner")
        inbox_roles = ("supervisor", "planner", "head", "manager", "admin")
        add("GET", "/api/health", lambda r, u: {"status": "ok", "database": self.config.database.provider,
            "version": __version__, "apiVersion": 7, "schemaVersion": 10,
            "databaseProvider": self.config.database.provider,
            "databaseVersionNum": s.db.server_version_num,
            "databaseVersion": s.db.server_version,
            "releaseStage": "candidate", "productionAccepted": False,
            "localTestMode": self.config.local_test_mode,
            # API compatibility describes supported operations. Deployment
            # approval is separate and is also shown by the candidate frontend.
            "capabilities": list(CAPABILITIES),
            "acceptancePending": ["production-data-reconciliation", "representative-deployment-load"],
            "implementedCapabilities": list(CAPABILITIES)}, public=True)
        add("POST", "/api/auth/login", self.login, public=True)
        add("GET", "/api/auth/me", lambda r, u: {"user": u.public(), "csrfToken": "same-origin-header"}, registration=True)
        add("POST", "/api/auth/logout", self.logout, registration=True)
        add("POST", "/api/auth/password", self.password, registration=True)
        add("GET", "/api/bootstrap", lambda r, u: s.master.bootstrap(u, r.query.get("catalogMode")), registration=True)
        for kind in ("assets", "parts", "people"):
            add("GET", "/api/catalog/" + kind, lambda r, u, kind=kind: s.master.catalog(u, kind, r.query), registration=True)
        add("GET", "/api/orders", self.orders, registration=True)
        add("GET", "/api/orders/{id}", lambda r, u, id: s.work.get(u, id, r.integer("historyPage", 1), r.integer("historyPageSize", 200)), registration=True)
        add("POST", "/api/orders", lambda r, u: s.work.save(u, r.json(), request_key=r.headers.get("x-idempotency-key")), registration=True)
        add("PUT", "/api/orders/{id}", lambda r, u, id: s.work.save(u, r.json(), order_id=id), registration=True)
        add("POST", "/api/orders/{id}/submit", lambda r, u, id: s.work.submit(u, id, r.json().get("version", -1)), registration=True)
        add("POST", "/api/orders/{id}/decision", lambda r, u, id: s.work.decision(u, id, r.json()), roles=("head", "supervisor"))
        add("POST", "/api/orders/{id}/review-correction", lambda r, u, id: s.work.review_correct(u, id, r.json()), roles=("supervisor",))
        add("POST", "/api/orders/{id}/cancel", lambda r, u, id: s.work.cancel(u, id, r.json()))
        add("GET", "/api/inbox", self.inbox, roles=inbox_roles)
        add("GET", "/api/performance", lambda r, u: s.performance.report(u, r.query.get("period"), r.integer("year"),
            r.integer("month"), r.query.get("personId"), r.integer("detailPage"), r.integer("detailPageSize"),
            scope=r.query.get("scope")), registration=True)
        add("GET", "/api/dashboard", lambda r, u: s.reporting.dashboard(u))
        add("GET", "/api/reports/em-downtime", lambda r, u: s.reporting.em_downtime(u,
            r.query.get("from"), r.query.get("to"), r.integer("page"), r.integer("pageSize")),
            roles=("head", "manager", "planner", "supervisor", "technician"), registration=True)
        add("GET", "/api/reports/em-downtime/orders", lambda r, u: s.reporting.em_downtime_orders(u,
            r.query.get("from"), r.query.get("to"), r.query.get("hall"), r.query.get("line"),
            r.integer("page"), r.integer("pageSize")), roles=("head", "manager", "planner", "supervisor", "technician"), registration=True)
        add("GET", "/api/oee/lines", lambda r,u: s.oee.lines(u,**self.page(r)), roles=("planner","head"))
        add("POST", "/api/oee/lines", lambda r,u: s.oee.save_line(u,r.json()), roles=("planner",))
        add("PUT", "/api/oee/lines/{id}", lambda r,u,id: s.oee.save_line(u,r.json(),id), roles=("planner",))
        add("GET", "/api/oee/day", lambda r,u: s.oee.day(u,r.query.get("lineId"),r.query.get("date")), roles=("planner","head"))
        add("POST", "/api/oee/preview", lambda r,u: s.oee.preview(u,r.json()), roles=("planner","head"))
        add("POST", "/api/oee/sheets", lambda r,u: s.oee.save_sheet(u,r.json()), roles=("planner",))
        add("GET", "/api/oee/sheets/{id}/history", lambda r,u,id: s.oee.history(u,id,r.integer("page"),r.integer("pageSize")), roles=("planner","head"))
        add("GET", "/api/oee/report", lambda r,u: s.oee.report(u,r.query.get("from"),r.query.get("to"),r.query.get("lineId"),r.integer("page"),r.integer("pageSize")), roles=("planner","head"))
        for kind, method in (("assets", s.master.save_asset), ("parts", s.master.save_part)):
            add("POST", "/api/" + kind, lambda r, u, method=method: method(u, r.json()), roles=management)
            add("PUT", "/api/" + kind + "/{id}", lambda r, u, id, method=method: method(u, r.json(), id), roles=management)
        add("GET", "/api/assets/{id}/history", lambda r, u, id:
            s.work.list_page(u, page=r.integer("page"), page_size=r.integer("pageSize"), asset_id=id)
            if "page" in r.query or "pageSize" in r.query else s.work.list(u, asset_id=id))
        def removed(r, u):
            raise ApiError(410, "موجودی و گردش انبار حذف شده است؛ مصرف تأییدشدهٔ قطعات را ببینید.")
        add("GET", "/api/stock/movements", removed)
        add("POST", "/api/stock/movements", removed, roles=management)
        add("GET", "/api/consumption", lambda r, u: s.consumption.history(u, **self.page(r), part_id=r.query.get("partId"),
            from_date=r.query.get("from"), to_date=r.query.get("to")))
        add("GET", "/api/plans", lambda r, u: s.master.plans(u, paged=r.boolean("paged"), **self.page(r)))
        add("POST", "/api/plans", lambda r, u: s.master.save_plan(u, r.json()), roles=chiefs)
        add("PUT", "/api/plans/{id}", lambda r, u, id: s.master.save_plan(u, r.json(), id), roles=chiefs)
        add("POST", "/api/plans/generate", lambda r, u: {"generated": s.work.generate_pm(u)}, roles=(*management, "supervisor"))
        add("GET", "/api/pm/templates", lambda r, u: s.pm.templates(u, r.integer("page"), r.integer("pageSize"), r.query.get("q"), r.boolean("retired")))
        add("POST", "/api/pm/templates", lambda r, u: self.pm_change(r, u, "create-template"), roles=(*chiefs, *PM_PROPOSERS))
        add("GET", "/api/pm/templates/{id}", lambda r, u, id: s.pm.template(u, id))
        add("GET", "/api/pm/templates/{id}/history", lambda r, u, id: s.pm.history(u, id, True))
        add("PUT", "/api/pm/templates/{id}", lambda r, u, id: self.pm_change(r, u, "edit-template", id), roles=(*chiefs, *PM_PROPOSERS))
        add("PUT", "/api/pm/templates/{id}/design", lambda r, u, id: self.pm_change(r, u, "design-template", id), roles=(*chiefs, *PM_PROPOSERS))
        add("POST", "/api/pm/templates/{id}/retire", lambda r, u, id: self.pm_change(r, u, "retire-template", id), roles=(*chiefs, *PM_PROPOSERS))
        add("POST", "/api/pm/templates/{id}/restore", lambda r, u, id: self.pm_change(r, u, "restore-template", id), roles=(*chiefs, *PM_PROPOSERS))
        add("GET", "/api/pm/setup", lambda r, u: s.setup.get(u, summary=r.boolean("summary")), roles=(*chiefs, "planner", "manager", "admin"))
        add("POST", "/api/pm/setup/activate", lambda r, u: s.setup.activate(u, r.json()), roles=chiefs)
        add("GET", "/api/pm/plans", lambda r, u: s.pm.plans(u, r.integer("page"), r.integer("pageSize"), r.query.get("q")))
        add("GET", "/api/pm/plans/{id}", lambda r, u, id: s.pm.plan(u, id))
        add("GET", "/api/pm/plans/{id}/history", lambda r, u, id: s.pm.history(u, id, False))
        add("PUT", "/api/pm/plans/{id}", lambda r, u, id: self.pm_change(r, u, "edit-plan", id), roles=(*chiefs, *PM_PROPOSERS))
        add("GET", "/api/pm/changes", lambda r, u: s.pm.change_requests(u, page=r.integer("page"), page_size=r.integer("pageSize"), status=r.query.get("status", "pending")), roles=(*chiefs, *PM_PROPOSERS))
        add("GET", "/api/pm/changes/{id}", lambda r, u, id: s.pm.change_request(u, id), roles=(*chiefs, *PM_PROPOSERS))
        add("POST", "/api/pm/changes/{id}/decision", lambda r, u, id: s.pm.decide_change(u, id, r.json()), roles=chiefs)
        for name, method in (("", s.registry.get), ("/unlinked", s.registry.unlinked), ("/forms", s.registry.forms)):
            def registry(r, u, method=method, name=name):
                extra = {"status": r.query.get("status")} if name == "" else {"family": r.query.get("family")} if name == "/forms" else {}
                return method(u, **self.page(r), hall=r.query.get("hall"), specialty=r.query.get("specialty"),
                              period_class=r.query.get("periodClass"), year=r.integer("year"), **extra)
            add("GET", "/api/pm/registry" + name, registry)
        add("POST", "/api/pm/stage-import", lambda r, u: s.pm.stage_import(u, self.config.seed_directory), roles=chiefs)
        add("POST", "/api/pm/generate", lambda r, u: {"generated": s.pm.generate(actor=u)}, roles=("admin", *chiefs, "planner", "manager", "supervisor"))
        add("GET", "/api/pm/tasks", lambda r, u: s.pm.tasks(u, r.integer("page"), r.integer("pageSize"), r.boolean("overdue"), r.query.get("view")), registration=True)
        add("GET", "/api/pm/archive", lambda r, u: s.archive.list_page(u, **self.page(r),
            hall=r.query.get("hall"), asset_id=r.query.get("assetId"), template_id=r.query.get("templateId"),
            person_id=r.query.get("personId"), status=r.query.get("status"), from_date=r.query.get("from"),
            to_date=r.query.get("to"), order_id=r.query.get("orderId")), roles=("planner",))
        add("GET", "/api/pm/archive/{id}", lambda r, u, id: s.archive.detail(u, id), roles=("planner",))
        def archive_original(r, u, id):
            path, name = s.archive.original(u, id, self.config.seed_directory)
            return Result(file=Path(path), filename=Path(name.replace("\\", "/")).name, media_type="application/octet-stream")
        add("GET", "/api/pm/archive/{id}/original", archive_original, roles=("planner",))
        add("POST", "/api/pm/tasks/{id}/assign", lambda r, u, id: s.pm.assign(u, id, r.json()), roles=("supervisor",))
        add("GET", "/api/pm/templates/{id}/original", self.original)
        add("GET", "/api/pm/tasks/{id}/original", lambda r, u, id: self.original(r, u, id, True), registration=True)
        add("GET", "/api/users", lambda r, u: s.master.users(u, paged=r.boolean("paged"), **self.page(r)), roles=PERSONNEL_ADMINISTRATORS)
        add("POST", "/api/users", lambda r, u: s.master.save_user(u, r.json()), roles=PERSONNEL_ADMINISTRATORS)
        add("PUT", "/api/users/{id}", lambda r, u, id: s.master.save_user(u, r.json(), id), roles=PERSONNEL_ADMINISTRATORS)
        def reset_password(r, u, id):
            s.auth.reset_password(u, id, r.json().get("password"))
            return {"ok": True}
        add("POST", "/api/users/{id}/reset-password", reset_password, roles=PERSONNEL_ADMINISTRATORS)
        add("GET", "/api/audit", lambda r, u: s.master.audit_log(u, paged=r.boolean("paged"), **self.page(r)), roles=("admin", *chiefs))
        add("GET", "/api/imports/preview", lambda r, u: s.master.import_preview(u), roles=("admin", *chiefs))
        add("GET", "/api/imports/legacy", lambda r, u: s.master.legacy(u, paged=r.boolean("paged"), **self.page(r)), roles=chiefs)
        add("POST", "/api/imports/legacy", lambda r, u: s.master.import_legacy(u, r.json()), roles=("admin",))
        add("GET", "/api/settings", lambda r, u: {"companyName": self.config.company_name, "databaseMode": self.config.database.provider,
            "version": __version__, "timezone": "Asia/Tehran", "automaticBackup": s.backup.status() if u.role == "admin" else None,
            "backupPolicy": "پشتیبان PostgreSQL با ابزار بومی تهیه و ساختار کامل آن بررسی می‌شود؛ فقط پشتیبان‌های خودکار ثبت‌شده و بدون تغییر مشمول نگهداری دوره‌ای هستند. آزمون بازیابی در پایگاه جداگانه انجام می‌شود."})
        add("GET", "/api/backup", self.backup, roles=("admin",))
        from .exports import report
        for name in ("works", "people", "stock", "consumption", "consumption-details"):
            add("GET", f"/api/reports/{name}.csv", lambda r, u, name=name: report(s, r, u, name), roles=chiefs if name == "people" else ())


def safe_error(error):
    """Map only known input/constraint failures; never leak SQL or secrets."""
    if isinstance(error, ApiError):
        return Result({"error": error.message}, error.status)
    if isinstance(error, sqlite3.Error) and getattr(error, "sqlite_errorcode", 0) & 0xFF in (5, 6, 19):
        return Result({"error": "اطلاعات تکراری، وابسته یا هم‌زمان تغییرکرده است؛ صفحه را تازه کنید."}, 409)
    if getattr(error, "sqlstate", None) in {"23505", "23503", "23514", "40001", "40P01", "22003"}:
        return Result({"error": "اطلاعات تکراری، وابسته، خارج از محدوده یا هم‌زمان تغییرکرده است."}, 409)
    if getattr(error, 'sqlstate', None) in {'53300', '55P03', '57014'}:
        return Result({'error': 'سامانه موقتاً شلوغ است یا گزارش طول کشیده؛ کمی بعد دوباره تلاش کنید یا فیلتر را محدودتر کنید.'},
                      503, headers=(('retry-after', '2'),))
    if isinstance(error, (ValueError, TypeError, OverflowError)):
        return Result({"error": "نوع یا مقدار اطلاعات درخواست معتبر نیست."}, 400)
    return Result({"error": "خطای داخلی؛ زمان رخداد را به مدیر سامانه گزارش دهید."}, 500)
