"""Read-only admission of the explicit schema-12 work-type constraint."""
import re

from .database import SchemaMismatchError

CONSTRAINT_NAME = 'ck_orders_work_type'
ORDER_TYPES = ('PM', 'EM', 'CM', 'PROJECT', 'GENERAL')
LEGACY_ORDER_TYPES = ('PM', 'EM', 'PROJECT')


def verify_work_category_contract(connection, version=12):
    if version not in (11, 12):
        raise ValueError('Only the explicit category upgrade source or target is supported')
    expected_types = ORDER_TYPES if version == 12 else LEGACY_ORDER_TYPES
    if connection.is_sqlite:
        definition = connection.scalar("SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'") or ''
        match = re.search(r'(?:\bCONSTRAINT\s+(\w+)\s+)?\bCHECK\s*\(\s*type\s+IN\s*\(([^)]*)\)\s*\)',
                          definition, re.IGNORECASE)
        valid = bool(match and (version == 11 or match[1] == CONSTRAINT_NAME) and
                     tuple(value.strip() for value in match[2].split(',')) == tuple(repr(value) for value in expected_types))
    else:
        constraints = connection.query("SELECT conname AS name,pg_get_constraintdef(oid) AS definition,convalidated AS validated "
            "FROM pg_constraint WHERE conrelid='public.orders'::regclass "
            "AND contype='c' AND conkey=ARRAY[(SELECT attnum FROM pg_attribute "
            "WHERE attrelid='public.orders'::regclass AND attname='type')]::smallint[]")
        constraint = constraints[0] if len(constraints) == 1 else None
        normalized = re.sub(r'\s+|::text', '', constraint['definition']).lower() if constraint else ''
        literals = tuple(re.findall(r"'([^']*)'", constraint['definition'])) if constraint else ()
        valid = bool(constraint and constraint['validated'] is True and
            (version == 11 or constraint['name'] == CONSTRAINT_NAME) and literals == expected_types and
            normalized == ("check((type=any(array['pm','em','cm','project','general'])))"
                           if version == 12 else "check((type=any(array['pm','em','project'])))"))
    if not valid:
        raise SchemaMismatchError(f'Schema {version} requires its reviewed validated '
            'work-type constraint. Apply the explicit owner upgrade; startup changed nothing.')
