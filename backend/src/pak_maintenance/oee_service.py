"""Optional PostgreSQL OEE module: bounded pages, immutable revisions, live roles."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from .common import ApiError, audit, dumps, loads, now, page_bounds, parse_date, strict_int, text, today
from .oee_metrics import aggregate, calculate, facts, minute, normalize_shift, rate
from .security import assert_live_actor, require

READ_ROLES = ('planner', 'head')
WRITE_ROLES = ('planner',)


def _field(value, label, maximum=100):
    result = text(value, maximum=maximum, field=label)
    if not result:
        raise ApiError(400, f'{label} را وارد کنید.')
    return result


def _list(value, maximum, field):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(x, dict) for x in value):
        raise ApiError(400, f'فهرست {field} معتبر نیست یا بیش از حد بزرگ است.')
    return value


def _version(body):
    return strict_int(body.get('version', 0), minimum=0, maximum=2_000_000_000, field='نسخه')


class OeeService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _sheet(encoded):
        document=loads(encoded)
        # Recompute from the recorded count/time/rate snapshot, never today's catalog.
        # Historical database evidence itself is left unchanged.
        document['metrics']=calculate(document)
        return document

    def ready(self, c):
        if c.is_sqlite or not c.scalar("SELECT to_regclass('public.oee_module') IS NOT NULL"):
            raise ApiError(503, 'بخش کارکرد خطوط هنوز در پایگاه داده فعال نشده است؛ نصب افزونه OEE لازم است.')
        if c.scalar('SELECT version FROM oee_module') != 1:
            raise ApiError(503, 'نسخه بخش کارکرد خطوط سازگار نیست.')

    def _write(self, operation):
        try:
            return self.db.run_transaction(operation)
        except Exception as error:
            if getattr(error, 'sqlstate', None) == '23505':
                raise ApiError(409, 'خط یا شیفت تکراری است؛ اطلاعات را تازه‌سازی کنید.') from None
            raise

    def lines(self, user, page=None, page_size=None, q=None):
        require(user, *READ_ROLES)
        page, size = page_bounds(page, page_size)
        query = text(q or '', maximum=100)
        search = '%'+query.replace('~','~~').replace('%','~%').replace('_','~_')+'%'
        with self.db.transaction(write=False) as c:
            self.ready(c)
            params = dict(q=search, limit=size, offset=(page-1)*size)
            where = "(code ILIKE :q ESCAPE '~' OR name ILIKE :q ESCAPE '~')"
            total = c.scalar('SELECT COUNT(*) FROM oee_lines WHERE '+where, params)
            rows = c.query('SELECT * FROM oee_lines WHERE '+where+' ORDER BY code,id LIMIT :limit OFFSET :offset', params)
        return dict(items=[dict(loads(r['body']), id=r['id'], version=r['version'], active=bool(r['active'])) for r in rows],
            page=page, pageSize=size, total=total, totalPages=(total+size-1)//size)

    def save_line(self, user, body, identifier=None):
        require(user, *WRITE_ROLES)
        if not isinstance(body, dict):
            raise ApiError(400, 'تعریف خط معتبر نیست.')
        version = _version(body)
        profiles, components = [], []
        for key, maximum, destination in (('profiles',100,profiles), ('components',100,components)):
            identifiers = set()
            for item in _list(body.get(key, []), maximum, key):
                item_id = _field(item.get('id'), 'شناسه جزء یا محصول', 80)
                if item_id in identifiers:
                    raise ApiError(400, 'شناسه جزء یا محصول تکراری است.')
                identifiers.add(item_id)
                entry = dict(id=item_id, name=_field(item.get('name'), 'نام جزء یا محصول'))
                if key == 'profiles':
                    entry['idealRate'] = rate(item.get('idealRate'))
                else:
                    entry['assetId'] = text(item.get('assetId') or '', maximum=100) or None
                destination.append(entry)
        active = body.get('active', True)
        if type(active) is not bool:
            raise ApiError(400, 'وضعیت خط معتبر نیست.')
        clean = dict(code=_field(body.get('code'), 'کد خط', 60), name=_field(body.get('name'), 'نام خط'),
            assetId=text(body.get('assetId') or '',maximum=100) or None,
            hall=text(body.get('hall',''),maximum=100), profiles=profiles,components=components,active=active)
        def operation(c):
            assert_live_actor(c, user); self.ready(c)
            previous = c.one('SELECT * FROM oee_lines WHERE id=:id FOR UPDATE',dict(id=identifier)) if identifier else None
            if identifier and previous is None:
                raise ApiError(404, 'خط پیدا نشد.')
            if version != (previous['version'] if previous else 0):
                raise ApiError(409, 'تعریف خط تغییر کرده است؛ تازه‌سازی کنید.')
            for asset in [clean['assetId'], *(x['assetId'] for x in components)]:
                if asset and not c.scalar('SELECT COUNT(*) FROM assets WHERE id=:id',dict(id=asset)):
                    raise ApiError(400, 'تجهیز انتخابی پیدا نشد.')
            key = identifier or str(uuid4())
            values = dict(id=key, code=clean['code'], name=clean['name'], asset=clean['assetId'],
                active=int(active), version=version+1, body=dumps(clean))
            if previous:
                c.execute('UPDATE oee_lines SET code=:code,name=:name,asset_id=:asset,active=:active,version=:version,body=:body WHERE id=:id', values)
            else:
                c.execute('INSERT INTO oee_lines(id,code,name,asset_id,active,version,body) VALUES(:id,:code,:name,:asset,:active,:version,:body)',values)
            audit(c,user,'oee.line.saved','oee_line',key,dict(version=version+1,before=loads(previous['body']) if previous else None,after=clean))
            return dict(clean,id=key,version=version+1)
        return self._write(operation)

    def day(self, user, line_id, work_date):
        require(user, *READ_ROLES)
        parse_date(work_date)
        with self.db.transaction(write=False) as c:
            self.ready(c)
            line = c.one('SELECT * FROM oee_lines WHERE id=:id',dict(id=line_id))
            if line is None:
                raise ApiError(404, 'خط پیدا نشد.')
            rows = c.query('SELECT body FROM oee_sheets WHERE line_id=:line AND work_date=:day ORDER BY shift',dict(line=line_id,day=work_date))
        return dict(line=dict(loads(line['body']),id=line_id,version=line['version']),sheets=[self._sheet(r['body']) for r in rows])

    def preview(self,user,body):
        require(user,*READ_ROLES)
        if not isinstance(body,dict):
            raise ApiError(400,'فرم معتبر نیست.')
        with self.db.transaction(write=False) as c:
            self.ready(c)
            line = c.one('SELECT * FROM oee_lines WHERE id=:id',dict(id=body.get('lineId')))
            if line is None:
                raise ApiError(404,'خط پیدا نشد.')
            old = c.one('SELECT body FROM oee_sheets WHERE line_id=:line AND work_date=:day AND shift=:shift',
                dict(line=line['id'],day=body.get('workDate'),shift=body.get('shift')))
            snapshot = loads(old['body'])['lineSnapshot'] if old else loads(line['body'])
            clean = normalize_shift(body,{p['id']:p for p in snapshot['profiles']},{p['id']:p for p in snapshot['components']})
            return dict(normalized=clean,metrics=calculate(clean))

    def save_sheet(self, user, body):
        require(user,*WRITE_ROLES)
        if not isinstance(body,dict):
            raise ApiError(400,'فرم معتبر نیست.')
        version = _version(body)
        work_date = parse_date(body.get('workDate'))
        shift = body.get('shift')
        if shift not in ('morning','night') or body.get('state') not in ('draft','final'):
            raise ApiError(400,'شیفت یا وضعیت معتبر نیست.')
        if work_date.isoformat() > today():
            raise ApiError(400,'گزارش کارکرد روز آینده قابل ثبت نیست.')
        reason = text(body.get('revisionReason',''),maximum=500)
        def operation(c):
            assert_live_actor(c,user); self.ready(c)
            # One line lock serializes adjacent day/shift overlap checks and setup edits.
            line = c.one('SELECT * FROM oee_lines WHERE id=:id FOR UPDATE',dict(id=body.get('lineId')))
            if line is None:
                raise ApiError(404,'خط پیدا نشد.')
            previous = c.one('SELECT * FROM oee_sheets WHERE line_id=:line AND work_date=:day AND shift=:shift FOR UPDATE',
                dict(line=line['id'],day=work_date.isoformat(),shift=shift))
            snapshot = loads(previous['body'])['lineSnapshot'] if previous else loads(line['body'])
            if not previous and (not line['active'] or body.get('lineVersion') != line['version']):
                raise ApiError(409,'تنظیمات خط تغییر کرده یا غیرفعال است؛ تازه‌سازی کنید.')
            clean = normalize_shift(body,{p['id']:p for p in snapshot['profiles']},{p['id']:p for p in snapshot['components']})
            metrics = calculate(clean)
            if body['state']=='final' and (not clean['products'] or any(p['rejected'] is None for p in clean['products'])):
                raise ApiError(400,'برای ثبت نهایی، محصول، کل تعداد و تعداد نامنطبق را مشخص کنید؛ اگر تولید یا ضایعات ندارید صفر وارد کنید.')
            payload_hash = sha256(dumps(dict(clean,state=body['state'])).encode()).hexdigest()
            if previous and previous['payload_hash'] == payload_hash:
                return self._sheet(previous['body'])
            if version != (previous['version'] if previous else 0):
                raise ApiError(409,'این شیفت قبلاً ثبت یا ویرایش شده است؛ تازه‌سازی کنید.')
            if previous and previous['state']=='final' and not reason:
                raise ApiError(400,'برای اصلاح گزارش نهایی، علت اصلاح را وارد کنید.')
            start = minute(clean['start']); end = start+clean['shiftMinutes']
            if body['state']=='final':
                from .pm_calendar import local_execution_utc
                try:
                    finished=local_execution_utc(datetime.combine(work_date,datetime.min.time())+timedelta(minutes=end))
                except ValueError:
                    raise ApiError(400,'زمان پایان شیفت مبهم یا نامعتبر است.') from None
                if finished>datetime.now(timezone.utc):
                    raise ApiError(400,'شیفت هنوز تمام نشده است؛ فعلاً پیش‌نویس ذخیره کنید.')
            neighbours = c.query('SELECT id,work_date,start_minute,end_minute FROM oee_sheets WHERE line_id=:line AND work_date>=:before AND work_date<=:after',
                dict(line=line['id'],before=(work_date-timedelta(days=1)).isoformat(),after=(work_date+timedelta(days=1)).isoformat()))
            for other in neighbours:
                if previous and other['id']==previous['id']:
                    continue
                delta=(parse_date(other['work_date'])-work_date).days*1440
                if start < other['end_minute']+delta and end > other['start_minute']+delta:
                    raise ApiError(409,'بازه این شیفت با شیفت ثبت‌شده همین خط هم‌پوشانی دارد.')
            key = previous['id'] if previous else str(uuid4())
            document=dict(clean,id=key,lineId=line['id'],workDate=work_date.isoformat(),shift=shift,
                version=version+1,state=body['state'],lineSnapshot=snapshot,metrics=metrics,
                revisionReason=reason,updatedAt=now(),updatedBy=user.name)
            values=dict(id=key,line=line['id'],day=work_date.isoformat(),shift=shift,version=version+1,state=body['state'],
                start=start,end=end,body=dumps(document),facts=dumps(facts(clean)),hash=payload_hash,actor=user.id,stamp=document['updatedAt'])
            if previous:
                c.execute('UPDATE oee_sheets SET version=:version,state=:state,start_minute=:start,end_minute=:end,body=:body,facts=:facts,payload_hash=:hash,actor_id=:actor,updated_at=:stamp WHERE id=:id',values)
            else:
                c.execute('INSERT INTO oee_sheets(id,line_id,work_date,shift,version,state,start_minute,end_minute,body,facts,payload_hash,actor_id,updated_at) VALUES(:id,:line,:day,:shift,:version,:state,:start,:end,:body,:facts,:hash,:actor,:stamp)',values)
            c.execute('INSERT INTO oee_sheet_revisions(sheet_id,version,body,actor_id,created_at) VALUES(:id,:version,:body,:actor,:stamp)',values)
            audit(c,user,'oee.sheet.saved','oee_sheet',key,dict(version=version+1,state=body['state'],reason=reason))
            return document
        return self._write(operation)

    def report(self,user,from_date,to_date,line_id=None,page=None,page_size=None):
        require(user,*READ_ROLES)
        first,last = parse_date(from_date),parse_date(to_date)
        if not 0 <= (last-first).days <= 366:
            raise ApiError(400,'بازه گزارش باید حداکثر ۳۶۷ روز باشد.')
        page,size = page_bounds(page,page_size)
        params=dict(first=first.isoformat(),last=last.isoformat(),line=line_id,limit=size,offset=(page-1)*size)
        where='work_date>=:first AND work_date<=:last'+(' AND line_id=:line' if line_id else '')
        with self.db.transaction(write=False) as c:
            self.ready(c)
            total=c.scalar('SELECT COUNT(*) FROM oee_sheets WHERE '+where,params)
            rows=c.query('SELECT body FROM oee_sheets WHERE '+where+' ORDER BY work_date DESC,line_id,shift LIMIT :limit OFFSET :offset',params)
            # Bound aggregation independently of bounded detail pages. Request a line/narrower period if needed.
            count=c.scalar("SELECT COUNT(*) FROM oee_sheets WHERE state='final' AND "+where,params)
            if count>20000:
                raise ApiError(413,'برای گزارش تجمیعی یک خط یا بازه کوتاه‌تر انتخاب کنید.')
            totals=c.query("""SELECT facts,CASE WHEN (facts::jsonb->>'total')::bigint=0
                AND facts::jsonb->'idealMinutes'='null'::jsonb THEN body::jsonb->'products'
                ELSE NULL END AS zero_products FROM oee_sheets WHERE state='final' AND """+where,params)
        values=[]
        for row in totals:
            value=loads(row['facts']);products=row['zero_products']
            if products and all(p.get('total')==0 and p.get('rejected')==0 for p in products):
                value.update(good=0,idealMinutes=0,idealGoodMinutes=0)
            values.append(value)
        return dict(items=[self._sheet(r['body']) for r in rows],total=total,page=page,pageSize=size,totalPages=(total+size-1)//size,
            finalCount=count,metrics=aggregate(values))

    def history(self,user,identifier,page=None,page_size=None):
        require(user,*READ_ROLES)
        page,size=page_bounds(page,page_size)
        with self.db.transaction(write=False) as c:
            self.ready(c)
            rows=c.query('SELECT version,body,created_at FROM oee_sheet_revisions WHERE sheet_id=:id ORDER BY version DESC LIMIT :limit OFFSET :offset',dict(id=identifier,limit=size,offset=(page-1)*size))
        return dict(items=[dict(version=r['version'],createdAt=r['created_at'],sheet=loads(r['body'])) for r in rows],page=page,pageSize=size)
