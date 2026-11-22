"""Business work categories and explicit non-equipment hall targets."""
import hashlib

from .common import ApiError, dumps

WORK_TYPES = frozenset({'PM', 'EM', 'CM', 'PROJECT', 'GENERAL'})
GENERAL_WORK_LABEL = 'امور محوله'
GENERAL_TARGET_NAMES = ('سایر — کارهای عمومی', 'امور عمومی', GENERAL_WORK_LABEL)
# These fixed names are owned by the application and are never user input.
_GENERAL_TARGET_NAMES_SQL = ", ".join("'" + name.replace("'", "''") + "'" for name in GENERAL_TARGET_NAMES)


def is_general_asset(asset):
    """Recognize a current or historical logical target without guessing by hall."""
    return not asset['line'].strip() and (
        asset['name'] in GENERAL_TARGET_NAMES or
        asset['id'].startswith('general-hall-') and asset['code'].startswith('GENERAL-'))


def physical_asset_sql(alias='assets'):
    """Source-controlled SQL matching is_general_asset; never request identifiers."""
    if alias not in {'assets', 'a'}:
        raise ValueError('Unsupported asset alias')
    return (f"NOT (trim({alias}.line)='' AND ({alias}.name IN ({_GENERAL_TARGET_NAMES_SQL})"
            f" OR ({alias}.id LIKE 'general-hall-%' AND {alias}.code LIKE 'GENERAL-%')))")


def general_hall_target(connection, hall):
    """Resolve/create a logical target only during an explicit authorized report save.

    Existing targets and historical orders are never renamed or reclassified.
    A hall must already exist in master data. Creation shares the report's
    transaction, so a rejected report cannot leave an orphan target behind.
    """
    if not hall or len(hall) > 100 or '\x00' in hall or not connection.scalar(
            'SELECT COUNT(*) FROM assets WHERE hall=:hall', {'hall': hall}):
        raise ApiError(400, 'سالن معتبر برای امور محوله انتخاب کنید.')
    digest = hashlib.md5(hall.encode('utf-8'), usedforsecurity=False).hexdigest()
    identifier, code = 'general-hall-' + digest, 'GENERAL-' + digest[:12]
    args = {'hall': hall, 'id': identifier, 'code': code, 'name': GENERAL_WORK_LABEL}
    existing = connection.one("SELECT id,code,name,hall,line FROM assets WHERE hall=:hall AND trim(line)='' "
        f"AND (id=:id OR name IN ({_GENERAL_TARGET_NAMES_SQL})) "
        "ORDER BY CASE WHEN id=:id THEN 0 ELSE 1 END,id LIMIT 1", args)
    if existing:
        if not is_general_asset(existing):
            raise ApiError(409, 'محل ثبت امور محوله با شناسنامهٔ موجود تداخل دارد. اطلاعات پایه را بررسی کنید.')
        return existing['id']
    args['body'] = dumps(dict(id=identifier, code=code, name=args['name'], hall=hall,
        line='', criticality='normal', workTarget='GENERAL', version=1))
    connection.execute('INSERT INTO assets(id,code,name,hall,line,body,version) '
        "VALUES(:id,:code,:name,:hall,'',:body,1) ON CONFLICT DO NOTHING", args)
    target = connection.one('SELECT id,code,name,hall,line FROM assets WHERE id=:id', args)
    if not target or target['hall'] != hall or target['code'] != code or not is_general_asset(target):
        raise ApiError(409, 'محل ثبت امور محوله با شناسنامهٔ موجود تداخل دارد. اطلاعات پایه را بررسی کنید.')
    return identifier
