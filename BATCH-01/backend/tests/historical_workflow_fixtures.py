"""Explicit pre-rc12 workflow evidence; never calls the removed live return action."""
from uuid import uuid4

from pak_maintenance.common import audit, now


def seed_historical_return(database, work, actor, row, reason):
    """Represent an already-persisted legacy return without re-enabling it.

    Tests still exercise recovery/readability of existing company history after
    the current application removes the return-for-correction command.
    """
    identifier, stamp = str(uuid4()), now()
    stage = row['status']
    if stage not in ('supervisor', 'head'):
        raise AssertionError('Historical return fixture needs an old review stage')
    specialty = actor.specialty if stage == 'supervisor' else ''
    with database.transaction() as c:
        c.execute('''INSERT INTO approvals(id,order_id,revision,stage,specialty,decision,
            coefficient,reason,actor_id,created_at)
            VALUES(:approval,:order,:revision,:stage,:specialty,'return',NULL,:reason,:actor,:stamp)''',
            dict(approval=identifier, order=row['id'], revision=row['workflowRevision'], stage=stage,
                 specialty=specialty, reason=reason, actor=actor.id, stamp=stamp))
        changed = c.execute("UPDATE orders SET status='returned',version=version+1,updated_at=:stamp WHERE id=:order AND version=:version",
                            dict(order=row['id'], version=row['version'], stamp=stamp))
        if changed != 1:
            raise AssertionError('Historical fixture cannot overwrite a newer record')
        audit(c, actor, 'return', 'order', row['id'],
              dict(approvalId=identifier, stage=stage, specialty=specialty, coefficient=None,
                   grades=[], reason=reason, **{'from': stage, 'to': 'returned'},
                   revision=row['workflowRevision']), created_at=stamp)
    return work.get(actor, row['id'])
