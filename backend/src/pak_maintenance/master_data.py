"""Role-scoped catalogs and versioned company master data.

The behavior comes from the version-1.9 Program.cs catalog/master-data routes.
There are no stock balances, hidden accounts, automatic seeds, or migrations.
Existing company files are only read when the caller explicitly imports the
legacy archive through the authorized service method.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from .common import ApiError, User, audit, decimal_value, dumps, loads, now, page_bounds, parse_date, strict_int, text
from .network_policy import validate_file_path
from .quantity import value as quantity_value
from .security import ROLES, hash_password, require
from .permissions import CHIEFS, PERSONNEL_ADMINISTRATORS


_EDIT_ROLES = ("admin", "head", "manager", "planner")
_ADMIN_VIEW = ("admin", "head")
_MAX_VERSION = 2_147_483_647
_PART_COLUMNS = """SELECT id,code,name,specification,unit,version,unit_cost,
COALESCE((SELECT quantity_units FROM part_consumption_totals ct WHERE ct.part_id=parts.id),0) AS consumed_units,
COALESCE((SELECT report_count FROM part_consumption_totals ct WHERE ct.part_id=parts.id),0) AS consumed_reports"""
_USER_COLUMNS = "SELECT id,code,name,role,specialty,active,session_epoch FROM users"


def _body(body):
    if not isinstance(body, dict):
        raise ApiError(400, "اطلاعات درخواست باید یک فرم معتبر باشد.")
    return body


def _field(body, key, maximum, minimum=0):
    raw = body.get(key)
    result = text("" if raw is None else raw, maximum=maximum, field=key)
    if len(result) < minimum:
        raise ApiError(400, f"مقدار {key} باید بین {minimum} و {maximum} نویسه باشد.")
    return result


def _flag(raw, default=True):
    if raw is None:
        return default
    if type(raw) is not bool:
        raise ApiError(400, "مقدار بله یا خیر معتبر نیست.")
    return raw


def _version(body, *, minimum=1):
    version = strict_int(body.get("version"), minimum=minimum, maximum=_MAX_VERSION, field="نسخه رکورد")
    if version == _MAX_VERSION:
        raise ApiError(409, "ظرفیت نسخهٔ رکورد تکمیل شده است؛ مدیر سامانه باید آن را بررسی کند.")
    return version


def _search(query):
    if query is None:
        return ""
    value = text(query, maximum=200, field="عبارت جست‌وجو").lower()
    return "%" + value.replace("~", "~~").replace("%", "~%").replace("_", "~_") + "%" if value else ""


def _page(items, page, size, total):
    return {"items": items, "page": page, "pageSize": size, "total": total, "totalPages": (total + size - 1) // size}


def _legacy(items):
    if len(items) > 1000:
        raise ApiError(413, "فهرست بزرگ است؛ نسخه جدید صفحه‌بندی‌شده سامانه را باز کنید.")
    return items


def _asset(row, technician=False):
    result = {} if technician else loads(row["body"])
    if not isinstance(result, dict):
        raise ApiError(409, "اطلاعات شناسنامهٔ تجهیز باید توسط مدیر سامانه بررسی شود.")
    result.update({key: row[key] for key in ("id", "code", "name", "hall", "line")})
    if not technician:
        result["version"] = row["version"]
    return result


def _part(row, technician=False):
    return {"id": row["id"], "code": row["code"], "name": row["name"], "specification": row["specification"],
            "unit": row["unit"] or None, "version": row["version"],
            "consumedQuantity": None if technician else quantity_value(row["consumed_units"]),
            "consumedReports": None if technician else row["consumed_reports"],
            "unitCost": None if technician or row["unit_cost"] is None else decimal_value(row["unit_cost"])}


def _person(row, technician=False):
    return {key: row[key] for key in ("id", "code", "name", "specialty")} if technician else User.from_row(row).public()


def _actor(connection, user, roles):
    """Recheck mutable account authority inside the same mutation transaction."""
    row = connection.one("SELECT role,specialty,active,session_epoch FROM users WHERE id=:id" +
                         ("" if connection.is_sqlite else " FOR UPDATE"), {"id": user.id})
    if (row is None or row["active"] != 1 or row["role"] not in roles or row["role"] != user.role
            or row["session_epoch"] != user.session_epoch or row["specialty"] != user.specialty):
        raise ApiError(403, "دسترسی حساب کاربری تغییر کرده است؛ دوباره وارد شوید.")


class MasterDataService:
    def __init__(self, db, seed_directory=None):
        self.db = db
        self.seed_directory = Path(validate_file_path(seed_directory)) if seed_directory is not None else None

    def _run(self, operation):
        try:
            return self.db.run_transaction(operation)
        except sqlite3.IntegrityError as error:
            if getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY):
                raise ApiError(409, "کد یا شناسهٔ رکورد تکراری است؛ اطلاعات را تازه‌سازی کنید.") from None
            raise
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise ApiError(409, "کد یا شناسهٔ رکورد تکراری است؛ اطلاعات را تازه‌سازی کنید.") from None
            raise

    def bootstrap(self, user, catalog_mode=None):
        require(user, registration=True)
        paged = catalog_mode == "paged"
        technician = user.role == "technician"
        reference_only = user.role not in PERSONNEL_ADMINISTRATORS
        people_scope = " AND specialty<>''" if technician else " AND role IN ('technician','supervisor')" if reference_only else ""
        with self.db.transaction(write=False) as c:
            counts = {"assets": c.scalar("SELECT COUNT(*) FROM assets"), "parts": c.scalar("SELECT COUNT(*) FROM parts"),
                      "people": c.scalar("SELECT COUNT(*) FROM users WHERE active=1" + people_scope)}
            if not paged and max(counts.values()) > 10000:
                raise ApiError(413, "مرجع اطلاعات بزرگ است؛ نسخه جدید صفحه‌بندی‌شده سامانه را باز کنید.")
            assets = [] if paged else [_asset(row, technician) for row in c.query("SELECT id,code,name,hall,line,body,version FROM assets ORDER BY code,id")]
            people = [] if paged else [_person(row, reference_only) for row in c.query(_USER_COLUMNS + " WHERE active=1" + people_scope + " ORDER BY name,id")]
            parts = [] if paged else [_part(row, technician) for row in c.query(_PART_COLUMNS + " FROM parts ORDER BY code,id")]
            lines = c.query("SELECT DISTINCT hall,line FROM assets ORDER BY hall,line") if paged else []
            locations = [row["hall"] for row in c.query("SELECT DISTINCT hall FROM assets ORDER BY hall")]
            specialties = [row["specialty"] for row in c.query("SELECT DISTINCT specialty FROM users WHERE specialty<>'' ORDER BY specialty")]
        return {"assets": assets, "people": people, "parts": parts, "catalogPaging": paged,
                "catalogCounts": counts, "assetLines": lines, "locations": locations, "specialties": specialties}

    def catalog(self, user, kind, query=None):
        require(user, registration=True)
        if kind not in ("assets", "parts", "people"):
            raise ApiError(404, "فهرست اطلاعات پیدا نشد.")
        query = {} if query is None else query
        if not isinstance(query, Mapping):
            raise ApiError(400, "فیلتر فهرست معتبر نیست.")

        def number(key):
            raw = query.get(key)
            if raw is None or raw == "":
                return None
            if isinstance(raw, str) and raw.isascii() and raw.isdecimal() and len(raw) <= 7:
                return int(raw)
            if type(raw) is int:
                return raw
            raise ApiError(400, "شماره صفحه معتبر نیست.")

        def flag(key):
            if key not in query:
                return None
            raw = query[key]
            if type(raw) is bool:
                return raw
            if isinstance(raw, str) and raw.lower() in ("true", "false"):
                return raw.lower() == "true"
            raise ApiError(400, "فیلتر پرسنل معتبر نیست.")

        page, size = page_bounds(number("page"), number("pageSize"))
        raw_ids = query.get("ids", "")
        if not isinstance(raw_ids, str) or len(raw_ids) > 20000:
            raise ApiError(400, "شناسه‌های فهرست معتبر نیستند.")
        ids = list(dict.fromkeys(identifier.strip() for identifier in raw_ids.split(",") if identifier.strip()))
        if len(ids) > 100 or any(len(identifier) > 100 or "\x00" in identifier for identifier in ids):
            raise ApiError(400, "حداکثر ۱۰۰ شناسه معتبر برای دریافت مشخصات مجاز است.")
        search = _search(query.get("q"))
        args = {"search": search, "size": size, "offset": (page - 1) * size}
        filters = ["1=1"]
        if ids:
            names = [f"selection_{index}" for index in range(len(ids))]
            filters.append("id IN (" + ",".join(":" + name for name in names) + ")")
            args.update(zip(names, ids, strict=True))
        technician = user.role == "technician"
        reference_only = user.role not in PERSONNEL_ADMINISTRATORS
        if kind == "people":
            active = flag("active")
            has_specialty = flag("hasSpecialty")
            if reference_only:
                filters += ["active=1", "specialty<>''"] if technician else ["active=1", "role IN ('technician','supervisor')"]
            else:
                active = (True if not ids else None) if active is None else active
                if active is not None:
                    filters.append("active=:active")
                    args["active"] = int(active)
                if has_specialty is not None:
                    filters.append("specialty<>''" if has_specialty else "specialty=''")
            if search:
                filters.append("(LOWER(code) LIKE :search ESCAPE '~' OR LOWER(name) LIKE :search ESCAPE '~' OR LOWER(specialty) LIKE :search ESCAPE '~')")
            for field, expression in (("specialty", "specialty=:specialty"), ("excludeId", "id<>:excludeId")):
                if field in query:
                    args[field] = text(query[field], maximum=200, field="فیلتر پرسنل", trim=False)
                    filters.append(expression)
            if "role" in query:
                if query["role"] not in ROLES:
                    raise ApiError(400, "نقش پرسنل معتبر نیست.")
                filters.append("role=:role")
                args["role"] = query["role"]
            source, columns, order = "users", _USER_COLUMNS, "name,id"
            projector = lambda row: _person(row, reference_only)
        else:
            if search:
                columns_to_search = ("code", "name", "specification") if kind == "parts" else ("code", "name", "hall", "line")
                filters.append("(" + " OR ".join(f"LOWER({column}) LIKE :search ESCAPE '~'" for column in columns_to_search) + ")")
            if kind == "assets":
                for field in ("hall", "line"):
                    if field in query:
                        args[field] = text(query[field], maximum=100, field="فیلتر محل", trim=False)
                        filters.append(f"{field}=:{field}")
                columns, projector = "SELECT id,code,name,hall,line,body,version FROM assets", lambda row: _asset(row, technician)
            else:
                columns, projector = _PART_COLUMNS + " FROM parts", lambda row: _part(row, technician)
            source, order = kind, "code,id"
        where = " WHERE " + " AND ".join(filters)
        with self.db.transaction(write=False) as c:
            total = c.scalar("SELECT COUNT(*) FROM " + source + where, args)
            items = [projector(row) for row in c.query(columns + where + " ORDER BY " + order + " LIMIT :size OFFSET :offset", args)]
        return _page(items, page, size, total)

    def users(self, user, paged=False, page=None, page_size=None, q=None):
        require(user, *PERSONNEL_ADMINISTRATORS)
        page, size = page_bounds(page, page_size)
        search = _search(q)
        where = " WHERE LOWER(code) LIKE :search ESCAPE '~' OR LOWER(name) LIKE :search ESCAPE '~' OR LOWER(specialty) LIKE :search ESCAPE '~'" if search else ""
        args = {"search": search, "size": size if paged else 1001, "offset": (page - 1) * size if paged else 0}
        with self.db.transaction(write=False) as c:
            items = [_person(row) for row in c.query(_USER_COLUMNS + where + " ORDER BY name,id LIMIT :size OFFSET :offset", args)]
            return _page(items, page, size, c.scalar("SELECT COUNT(*) FROM users" + where, args)) if paged else _legacy(items)

    def audit_log(self, user, paged=False, page=None, page_size=None, q=None):
        require(user, *_ADMIN_VIEW)
        page, size = page_bounds(page, page_size)
        search = _search(q)
        where = " WHERE LOWER(actor_name) LIKE :search ESCAPE '~' OR LOWER(action) LIKE :search ESCAPE '~' OR LOWER(entity) LIKE :search ESCAPE '~'" if search else ""
        args = {"search": search, "size": size if paged else 300, "offset": (page - 1) * size if paged else 0}
        with self.db.transaction(write=False) as c:
            items = [{"id": row["id"], "actorName": row["actor_name"], "action": row["action"], "entity": row["entity"],
                      "entityId": row["entity_id"], "details": loads(row["details"]) if user.role == "head" else None, "createdAt": row["created_at"]}
                     for row in c.query("SELECT * FROM audit" + where + " ORDER BY created_at DESC,id DESC LIMIT :size OFFSET :offset", args)]
            return _page(items, page, size, c.scalar("SELECT COUNT(*) FROM audit" + where, args)) if paged else items

    def save_asset(self, user, body, asset_id=None):
        require(user, *_EDIT_ROLES)
        body = _body(body)
        creating = asset_id is None
        identifier = str(uuid4()) if creating else text(asset_id, maximum=100, field="شناسه تجهیز")
        criticality = _field(body, "criticality", 30) or "normal"
        if criticality not in ("normal", "low", "medium", "high", "critical"):
            raise ApiError(400, "درجه اهمیت تجهیز معتبر نیست.")
        previous = 0 if creating else _version(body)
        result = {"id": identifier, "code": _field(body, "code", 100, 1), "name": _field(body, "name", 500, 1),
                  "hall": _field(body, "hall", 100, 1), "line": _field(body, "line", 100), "criticality": criticality,
                  "manufacturer": _field(body, "manufacturer", 200), "model": _field(body, "model", 200), "serial": _field(body, "serial", 200), "version": previous + 1}
        def operation(c):
            _actor(c, user, _EDIT_ROLES)
            args = dict(result, previous=previous, body=dumps(result))
            count = c.execute("INSERT INTO assets(id,code,name,hall,line,version,body) VALUES(:id,:code,:name,:hall,:line,:version,:body)" if creating else
                              "UPDATE assets SET code=:code,name=:name,hall=:hall,line=:line,version=:version,body=:body WHERE id=:id AND version=:previous", args)
            if count != 1:
                raise ApiError(409, "تجهیز پیدا نشد یا هم‌زمان تغییر کرده است؛ اطلاعات را تازه‌سازی کنید.")
            audit(c, user, "create" if creating else "edit", "asset", identifier, result)
            return result
        return self._run(operation)

    def save_part(self, user, body, part_id=None):
        require(user, *_EDIT_ROLES)
        body = _body(body)
        creating = part_id is None
        identifier = str(uuid4()) if creating else text(part_id, maximum=100, field="شناسه قطعه")
        previous = 0 if creating else _version(body)
        cost = decimal_value(body["unitCost"], "بهای واحد") if body.get("unitCost") is not None else None
        if cost is not None and (cost < 0 or cost > 1_000_000_000_000 or cost.as_tuple().exponent < -2 and cost != cost.quantize(decimal_value("0.01"))):
            raise ApiError(400, "بهای واحد باید نامنفی، حداکثر یک تریلیون و با حداکثر دو رقم اعشار باشد.")
        result = {"id": identifier, "code": _field(body, "code", 100, 1), "name": _field(body, "name", 500, 1),
                  "specification": _field(body, "specification", 2000), "unit": _field(body, "unit", 100), "unitCost": cost, "version": previous + 1}
        def operation(c):
            _actor(c, user, _EDIT_ROLES)
            if not creating:
                old = c.one("SELECT code,unit,version FROM parts WHERE id=:id" + ("" if c.is_sqlite else " FOR UPDATE"), {"id": identifier})
                if old is None or old["version"] != previous:
                    raise ApiError(409, "قطعه پیدا نشد یا هم‌زمان تغییر کرده است؛ اطلاعات را تازه‌سازی کنید.")
                used = c.scalar("SELECT EXISTS(SELECT 1 FROM order_parts WHERE part_id=:id) OR EXISTS(SELECT 1 FROM part_consumptions WHERE part_id=:id)", {"id": identifier})
                historic = c.scalar("SELECT EXISTS(SELECT 1 FROM stock_movements WHERE part_id=:id)", {"id": identifier})
                if old["unit"] != result["unit"] and (used or historic):
                    raise ApiError(409, "واحد قطعهٔ دارای سابقه را نمی‌توان تغییر داد؛ قطعهٔ جدید تعریف کنید.")
                if old["code"] != result["code"] and (used or historic):
                    raise ApiError(409, "کد انبار قطعهٔ دارای سابقه ثابت است؛ برای کد دیگری، قطعهٔ جدید تعریف کنید.")
            args = dict(result, cost=cost, previous=previous)
            count = c.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost,version) VALUES(:id,:code,:name,:specification,:unit,:cost,:version)" if creating else
                              "UPDATE parts SET code=:code,name=:name,specification=:specification,unit=:unit,unit_cost=:cost,version=:version WHERE id=:id AND version=:previous", args)
            if count != 1:
                raise ApiError(409, "قطعه هم‌زمان تغییر کرده است؛ اطلاعات را تازه‌سازی کنید.")
            audit(c, user, "create" if creating else "edit", "part", identifier, result)
            row = c.one(_PART_COLUMNS + " FROM parts WHERE id=:id", {"id": identifier})
            return _part(row)
        return self._run(operation)

    def save_user(self, user, body, user_id=None):
        require(user, *PERSONNEL_ADMINISTRATORS)
        body = _body(body)
        creating = user_id is None
        identifier = str(uuid4()) if creating else text(user_id, maximum=100, field="شناسه کاربر")
        role = _field(body, "role", 30, 1)
        if role not in ROLES:
            raise ApiError(400, "نقش نامعتبر است.")
        specialty = _field(body, "specialty", 200)
        if role in ("technician", "supervisor") and not specialty:
            raise ApiError(400, "تخصص نقش فنی باید مشخص شود.")
        active = _flag(body.get("active"))
        password = "" if body.get("password") is None else body["password"]
        if not isinstance(password, str):
            raise ApiError(400, "رمز معتبر نیست.")
        password_hash = hash_password(password) if password else None
        result = {"code": _field(body, "code", 100, 1), "name": _field(body, "name", 200, 1), "role": role, "specialty": specialty, "active": active}
        if user.id == identifier and (role != user.role or not active):
            raise ApiError(400, "نمی‌توانید دسترسی فعال حساب خودتان را حذف کنید.")
        previous = None if creating else _version(body, minimum=0)
        def operation(c):
            if not c.is_sqlite:
                c.query("SELECT id FROM users WHERE role IN ('admin','head') AND active=1 ORDER BY id FOR UPDATE")
            _actor(c, user, PERSONNEL_ADMINISTRATORS)
            existing = None if creating else c.one("SELECT * FROM users WHERE id=:id", {"id": identifier})
            if not creating and existing is None:
                raise ApiError(404, "کاربر پیدا نشد.")
            if existing and existing["session_epoch"] != previous:
                raise ApiError(409, "کاربر هم‌زمان تغییر کرده است؛ اطلاعات را تازه‌سازی کنید.")
            if existing and existing["role"] == "admin" and existing["active"] == 1 and (role != "admin" or not active):
                if c.scalar("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1") <= 1:
                    raise ApiError(409, "حداقل یک مدیر سامانه فعال باید باقی بماند.")
            if existing and existing["role"] == "head" and existing["active"] == 1 and (role != "head" or not active):
                if c.scalar("SELECT COUNT(*) FROM users WHERE role='head' AND active=1") <= 1:
                    raise ApiError(409, "حداقل یک رئیس فعال باید باقی بماند.")
            args = dict(result, active=int(active), id=identifier, hash=password_hash if password_hash is not None else existing["password_hash"] if existing else "", previous=previous)
            changed = c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,:role,:specialty,:active,:hash,0)" if creating else
                                "UPDATE users SET code=:code,name=:name,role=:role,specialty=:specialty,active=:active,password_hash=:hash,session_epoch=session_epoch+1 WHERE id=:id AND session_epoch=:previous", args)
            if changed != 1:
                raise ApiError(409, "کاربر هم‌زمان تغییر کرده است؛ اطلاعات را تازه‌سازی کنید.")
            if not creating:
                c.execute("DELETE FROM sessions WHERE user_id=:id", {"id": identifier})
            audit(c, user, "create-user" if creating else "edit-user", "user", identifier, result)
            return _person(c.one(_USER_COLUMNS + " WHERE id=:id", {"id": identifier}))
        return self._run(operation)

    def plans(self, user, paged=False, page=None, page_size=None, q=None):
        require(user)
        page, size = page_bounds(page, page_size)
        search = _search(q)
        expression = "json_extract(p.body,'$.assigneeId')" if self.db.is_sqlite else "p.body::jsonb->>'assigneeId'"
        where = " WHERE 1=1"
        if user.role == "supervisor":
            where += " AND EXISTS(SELECT 1 FROM users person WHERE person.id=" + expression + " AND person.specialty=:specialty)"
        if search:
            where += " AND (LOWER(p.body) LIKE :search ESCAPE '~' OR LOWER(a.name) LIKE :search ESCAPE '~')"
        source = " FROM plans p JOIN assets a ON a.id=p.asset_id LEFT JOIN users assigned ON assigned.id=" + expression
        args = {"specialty": user.specialty, "search": search, "size": size if paged else 1001, "offset": (page - 1) * size if paged else 0}
        with self.db.transaction(write=False) as c:
            rows = c.query("SELECT p.body,a.name,assigned.name AS assignee_name" + source + where + " ORDER BY p.next_due,p.id LIMIT :size OFFSET :offset", args)
            items = [dict(loads(row["body"]), assetName=row["name"], assigneeName=row["assignee_name"]) for row in rows]
            return _page(items, page, size, c.scalar("SELECT COUNT(*)" + source + where, args)) if paged else _legacy(items)

    def save_plan(self, user, body, plan_id=None):
        # Old interval-day plans have no proposal workflow. Only the head may
        # publish them; all other PM editing uses the reviewed form workspace.
        require(user, *CHIEFS)
        body = _body(body)
        creating = plan_id is None
        identifier = str(uuid4()) if creating else text(plan_id, maximum=100, field="شناسه برنامه")
        due = parse_date(body.get("nextDueDate")).isoformat()
        checklist = body.get("checklist")
        if not isinstance(checklist, list) or not 1 <= len(checklist) <= 200 or any(not isinstance(item, dict) for item in checklist):
            raise ApiError(400, "برنامه PM باید بین ۱ تا ۲۰۰ فعالیت معتبر داشته باشد.")
        previous = 0 if creating else _version(body)
        result = {"id": identifier, "version": previous + 1, "assetId": _field(body, "assetId", 60, 1), "title": _field(body, "title", 300, 3),
                  "description": _field(body, "description", 10000), "intervalDays": strict_int(body.get("intervalDays"), minimum=1, maximum=3660, field="فاصله PM"),
                  "nextDueDate": due, "assigneeId": _field(body, "assigneeId", 60, 1), "checklist": [{"text": _field(item, "text", 500, 1), "done": False} for item in checklist], "active": _flag(body.get("active"))}
        def operation(c):
            _actor(c, user, CHIEFS)
            existing = None if creating else c.one("SELECT body,version FROM plans WHERE id=:id", {"id": identifier})
            if not creating and existing is None:
                raise ApiError(404, "برنامه پیدا نشد.")
            if existing and existing["version"] != previous:
                raise ApiError(409, "برنامه هم‌زمان تغییر کرده است؛ تازه‌سازی کنید.")
            if existing and user.role == "supervisor":
                old_assignee = loads(existing["body"]).get("assigneeId", "")
                if not c.scalar("SELECT COUNT(*) FROM users WHERE id=:id AND specialty=:specialty", {"id": old_assignee, "specialty": user.specialty}):
                    raise ApiError(403, "این برنامه در حوزه سرپرستی شما نیست.")
            if not c.scalar("SELECT COUNT(*) FROM assets WHERE id=:id", {"id": result["assetId"]}):
                raise ApiError(400, "دستگاه معتبر انتخاب کنید.")
            assigned = c.one("SELECT active,specialty FROM users WHERE id=:id", {"id": result["assigneeId"]})
            if assigned is None or assigned["active"] != 1:
                raise ApiError(400, "مجری فعال برنامه PM انتخاب کنید.")
            if user.role == "supervisor" and assigned["specialty"] != user.specialty:
                raise ApiError(403, "برنامه‌ریزی فقط برای مجریان تخصص شما مجاز است.")
            args = dict(result, active=int(result["active"]), previous=previous, body=dumps(result))
            changed = c.execute("INSERT INTO plans(id,asset_id,next_due,interval_days,active,version,body) VALUES(:id,:assetId,:nextDueDate,:intervalDays,:active,:version,:body)" if creating else
                                "UPDATE plans SET asset_id=:assetId,next_due=:nextDueDate,interval_days=:intervalDays,active=:active,version=:version,body=:body WHERE id=:id AND version=:previous", args)
            if changed != 1:
                raise ApiError(409, "برنامه هم‌زمان تغییر کرده است.")
            audit(c, user, "create-plan" if creating else "edit-plan", "plan", identifier, result)
            return result
        return self._run(operation)

    def _legacy_input(self):
        if self.seed_directory is None:
            raise ApiError(409, "مسیر بستهٔ آرشیو برای این نسخه مشخص نشده است.")
        candidate = Path(validate_file_path(self.seed_directory / "legacy-data.json"))
        if candidate.stat().st_size > 64 * 1024 * 1024:
            raise ApiError(413, "بستهٔ آرشیو از ظرفیت ورود یک‌مرحله‌ای بزرگ‌تر است.")
        data = loads(candidate.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ApiError(409, "بستهٔ آرشیو معتبر نیست.")
        return data

    def import_preview(self, user):
        require(user, *_ADMIN_VIEW)
        data = self._legacy_input()
        return {"summary": data.get("summary"), "manifest": data.get("manifest")}

    def legacy(self, user, paged=False, page=None, page_size=None, q=None):
        require(user, *CHIEFS)
        page, size = page_bounds(page, page_size)
        search = _search(q)
        where = " WHERE LOWER(body) LIKE :search ESCAPE '~'" if search else ""
        args = {"search": search, "size": size if paged else 1001, "offset": (page - 1) * size if paged else 0}
        with self.db.transaction(write=False) as c:
            items = [loads(row["body"]) for row in c.query("SELECT body FROM legacy_records" + where + " ORDER BY id LIMIT :size OFFSET :offset", args)]
            return _page(items, page, size, c.scalar("SELECT COUNT(*) FROM legacy_records" + where, args)) if paged else _legacy(items)

    def import_legacy(self, user, body):
        require(user, "admin")
        if _body(body).get("mode") != "archive":
            raise ApiError(400, "تنها حالت آرشیو مجاز است.")
        data = self._legacy_input()
        records = []
        for record in data["records"]:
            identifier = _field(_body(record), "id", 100, 1)
            records.append({"id": identifier, "body": dumps(record)})
        def operation(c):
            _actor(c, user, ("admin",))
            inserted = 0
            for record in records:
                inserted += c.execute("INSERT INTO legacy_records(id,body,imported_at) VALUES(:id,:body,:now) ON CONFLICT(id) DO NOTHING", dict(record, now=now()))
            audit(c, user, "import-archive", "legacy", "archive", {"inserted": inserted, "manifest": data.get("manifest")})
            return {"inserted": inserted, "activationPerformed": False}
        return self._run(operation)


CatalogService = MasterDataService
