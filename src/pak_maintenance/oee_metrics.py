"""Exact, bounded, count-based line metrics; no I/O or hidden downtime policy."""
from decimal import Decimal
import re

from .common import ApiError, decimal_value, strict_int, text

FORMULA_VERSION = 'pak-oee-count-3'
KINDS = ('technical', 'nontechnical', 'planned')


def minute(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
        raise ApiError(400, 'ساعت باید به شکل 08:30 وارد شود.')
    h, m = map(int, value.split(':'))
    return h*60+m


def rate(value):
    if value in (None, ''):
        return None
    result = decimal_value(value, 'سرعت استاندارد')
    if result <= 0 or result > 1000000 or result.as_tuple().exponent < -4:
        raise ApiError(400, 'سرعت استاندارد باید مثبت و حداکثر چهار رقم اعشار داشته باشد.')
    return result


def ratio(numerator, denominator, scale=1):
    return Decimal(numerator)*scale/Decimal(denominator) if denominator else None


def normalize_shift(body, profiles, components):
    """Profiles are authoritative server values; client cannot override rate snapshots."""
    if not isinstance(body, dict):
        raise ApiError(400, 'فرم کارکرد معتبر نیست.')
    start, finish = minute(body.get('start')), minute(body.get('end'))
    if start == finish:
        raise ApiError(400, 'شروع و پایان برابر است؛ بازه شیفت را مشخص کنید.')
    end = finish + (1440 if finish < start else 0)
    stops, products = body.get('stops', []), body.get('products', [])
    if not isinstance(stops, list) or len(stops) > 100 or not isinstance(products, list) or len(products) > 30:
        raise ApiError(400, 'حداکثر ۱۰۰ توقف و ۳۰ محصول در هر شیفت قابل ثبت است.')
    clean_stops, intervals = [], []
    for i, stop in enumerate(stops, 1):
        if not isinstance(stop, dict) or stop.get('kind') not in KINDS:
            raise ApiError(400, f'نوع توقف ردیف {i} معتبر نیست.')
        a, b = minute(stop.get('start')), minute(stop.get('end'))
        a += 1440 if a < start else 0
        b += 1440 if b < start else 0
        if not start <= a < b <= end:
            raise ApiError(400, f'زمان توقف ردیف {i} باید داخل شیفت و پایان پس از شروع باشد.')
        identifiers = stop.get('componentIds', [])
        if not isinstance(identifiers, list) or len(identifiers) > 20 or any(not isinstance(x, str) or x not in components for x in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ApiError(400, f'جزء انتخابی توقف ردیف {i} متعلق به این خط نیست یا تکراری است.')
        reason = text(stop.get('reason', ''), maximum=300, field='علت توقف')
        if not reason:
            raise ApiError(400, f'علت توقف ردیف {i} را وارد کنید.')
        clean_stops.append(dict(start=stop['start'], end=stop['end'], kind=stop['kind'],
            reason=reason, componentIds=identifiers, minutes=b-a,
            rootCause=text(stop.get('rootCause', ''), maximum=1000, field='علت ریشه‌ای'),
            action=text(stop.get('action', ''), maximum=1000, field='اقدام اصلاحی')))
        intervals.append((a, b, i))
    ordered = sorted(intervals)
    for a, b in zip(ordered, ordered[1:]):
        if a[1] > b[0]:
            raise ApiError(400, f'توقف‌های ردیف {a[2]} و {b[2]} هم‌پوشانی دارند؛ یک رخداد با چند جزء ثبت کنید.')
    clean_products, seen = [], set()
    for i, product in enumerate(products, 1):
        if not isinstance(product, dict) or not isinstance(product.get('profileId'), str) or product['profileId'] not in profiles:
            raise ApiError(400, f'محصول ردیف {i} در تنظیمات خط موجود نیست.')
        identifier = product['profileId']
        if identifier in seen:
            raise ApiError(400, 'هر محصول را یک‌بار با جمع تعداد ثبت کنید.')
        seen.add(identifier)
        total = strict_int(product.get('total'), minimum=0, maximum=1_000_000_000, field='تعداد کل')
        rejected = product.get('rejected')
        if rejected is not None:
            strict_int(rejected, minimum=0, maximum=total, field='تعداد نامنطبق')
        profile = profiles[identifier]
        clean_products.append(dict(profileId=identifier, name=profile['name'], total=total,
            rejected=rejected, good=None if rejected is None else total-rejected, idealRate=rate(profile.get('idealRate'))))
    return dict(start=body['start'], end=body['end'], nextDay=end>=1440,
        shiftMinutes=end-start, stops=clean_stops, products=clean_products,
        note=text(body.get('note', ''), maximum=1000, field='توضیح'))


def facts(shift):
    sums = {kind: sum(s['minutes'] for s in shift['stops'] if s['kind'] == kind) for kind in KINDS}
    failures = sum(s['kind'] == 'technical' for s in shift['stops'])
    # PAK company timebase: CIP/planned and nontechnical intervals remain
    # recorded, but only technical failures reduce operating time/availability.
    base = shift['shiftMinutes']
    run = base-sums['technical']
    total = sum(p['total'] for p in shift['products'])
    if run == 0 and total:
        raise ApiError(400, 'با زمان کارکرد صفر، ثبت تعداد تولید مثبت ممکن نیست.')
    # Unknown quality or standard is never silently zero or 100 percent.
    counted = [p for p in shift['products'] if p['total']]
    quality_known = bool(shift['products']) and all(p['good'] is not None for p in counted or shift['products'])
    speed_known = bool(shift['products']) and all(p['idealRate'] is not None for p in counted)
    ideal = sum((Decimal(p['total'])/p['idealRate'] for p in counted), Decimal(0)) if speed_known else None
    ideal_good = sum((Decimal(p['good'])/p['idealRate'] for p in counted), Decimal(0)) if speed_known and quality_known else None
    return dict(shiftMinutes=shift['shiftMinutes'], plannedMinutes=sums['planned'],
        technicalMinutes=sums['technical'], nontechnicalMinutes=sums['nontechnical'], failureCount=failures,
        productionMinutes=base, runMinutes=run, total=total,
        good=sum(p['good'] for p in counted) if quality_known else None,
        idealMinutes=ideal, idealGoodMinutes=ideal_good)


def aggregate(items):
    """Ratios of summed facts, never an average of displayed daily percentages."""
    rows = list(items)
    keys = ('shiftMinutes','plannedMinutes','technicalMinutes','nontechnicalMinutes','failureCount','total')
    result = {key: sum(r[key] for r in rows) for key in keys}
    # Old persisted facts cache v1/v2 production/run values. Derive both
    # from original durations so reports agree with recalculated sheet views
    # without rewriting historical records or immutable revision evidence.
    result['productionMinutes'] = result['shiftMinutes']
    result['runMinutes'] = result['shiftMinutes']-result['technicalMinutes']
    positive = [r for r in rows if r['total'] or r['idealMinutes'] is not None or r['good'] is not None]
    for key in ('good','idealMinutes','idealGoodMinutes'):
        result[key] = sum((r[key] for r in positive), Decimal(0)) if positive and all(r[key] is not None for r in positive) else None
    run, base, technical, count = (result[k] for k in ('runMinutes','productionMinutes','technicalMinutes','failureCount'))
    ideal, ideal_good = result['idealMinutes'], result['idealGoodMinutes']
    result['rejected'] = result['total']-result['good'] if result['good'] is not None else None
    result.update(formulaVersion=FORMULA_VERSION,
        availability=ratio(run,base,100), technicalAvailability=ratio(run,run+technical,100),
        technicalStopToRunPercent=ratio(technical,run,100),
        mtbfMinutes=ratio(run,count), mttrRestorationMinutes=ratio(technical,count),
        actualRate=ratio(result['total'],run),
        performance=ratio(ideal,run,100) if ideal is not None else None,
        countQuality=ratio(result['good'],result['total'],100) if result['good'] is not None else None,
        quality=ratio(ideal_good,ideal,100) if ideal_good is not None else None,
        oee=ratio(ideal_good,base,100) if ideal_good is not None else None,
        technicalOee=ratio(ideal_good,run+technical,100) if ideal_good is not None else None)
    result['warnings'] = []
    if positive and result['idealMinutes'] is None:
        result['warnings'].append('سرعت استاندارد بعضی محصولات وارد نشده؛ کارایی سرعت و OEE کامل نیست.')
    if positive and result['good'] is None:
        result['warnings'].append('تعداد نامنطبق بعضی محصولات مشخص نشده؛ کیفیت و OEE کامل نیست.')
    if result['performance'] is not None and result['performance'] > 100:
        result['warnings'].append('کارایی سرعت بیش از ۱۰۰٪ است؛ سرعت استاندارد، تعداد و زمان‌ها بررسی شوند.')
    return result


def calculate(shift):
    return aggregate([facts(shift)])
