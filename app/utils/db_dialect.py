"""Cross-database SQL helpers.

SQLite / PostgreSQL / MySQL use different date functions. Keeping the difference
in one place means switching databases does not require hunting through the code.
"""

from sqlalchemy import func

from app import db


def dialect_name() -> str:
    """Current database dialect name (sqlite / postgresql / mysql ...)."""
    try:
        return db.engine.dialect.name
    except Exception:
        return "sqlite"


def date_bucket(column):
    """Expression that extracts the date part.

    ``date(x)`` works on SQLite, PostgreSQL and MySQL alike.
    """
    return func.date(column)


def month_bucket(column):
    """Expression that renders a year-month string for monthly grouping."""
    name = dialect_name()
    if name == "postgresql":
        return func.to_char(column, "YYYY-MM")
    if name == "mysql":
        return func.date_format(column, "%Y-%m")
    # SQLite and fallback
    return func.strftime("%Y-%m", column)
