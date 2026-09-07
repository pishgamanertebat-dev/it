from pathlib import Path


PROJECT_ROOT = Path(r"E:\KomatsoAI")

DB_PATH = (
    PROJECT_ROOT
    / "data"
    / "fleet"
    / "db"
    / "fleet_ops.db"
)

WORK_ORDER_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "work_orders"
)

WORK_ORDER_TEMPLATE_ROOT = (
    PROJECT_ROOT
    / "templates"
    / "work_orders"
)