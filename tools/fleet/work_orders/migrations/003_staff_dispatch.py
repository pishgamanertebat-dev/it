"""Apply additive dispatch tracking and the explicitly authorized pilot staff."""
from tools.fleet.work_orders.core.staff_dispatch import create_schema


def migrate(con):
    create_schema(con)
    existing = con.execute("SELECT id,notes FROM service_staff WHERE bale_id='85539397'").fetchone()
    if existing and existing[1] != 'BALE_DISPATCH_PILOT':
        raise RuntimeError('Pilot ID already belongs to another staff record; inspect before changing.')
    con.execute("""INSERT OR IGNORE INTO service_staff(display_name,bale_id,service_role,active,notes)
        VALUES ('سرویسکار هواکش','85539397','GENERAL',1,'BALE_DISPATCH_PILOT')""")
