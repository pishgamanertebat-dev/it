"""Named staff choices sharing the explicitly authorized pilot transport."""
from tools.fleet.work_orders.core.staff_dispatch import create_schema

NAMES = ('محسن غضنفری','حسین محمودی','پوریا آسترکی')


def migrate(con):
    create_schema(con)
    staff = con.execute("SELECT id FROM service_staff WHERE bale_id='85539397' AND active=1").fetchone()
    if not staff:
        raise RuntimeError('Active pilot transport 85539397 was not found.')
    for index,name in enumerate(NAMES,1):
        existing = con.execute('SELECT display_name,staff_id FROM service_staff_roster WHERE id=?',(index,)).fetchone()
        if existing and tuple(existing) != (name,staff[0]):
            raise RuntimeError('Roster entry already exists with different data; inspect before replacing.')
        con.execute('INSERT OR IGNORE INTO service_staff_roster(id,display_name,staff_id) VALUES (?,?,?)',(index,name,staff[0]))
