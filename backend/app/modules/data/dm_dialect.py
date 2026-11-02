"""Adapt the vendor DM dialect without losing the original DBAPI exception."""
from dmSQLAlchemy.dmpython import DMDialect_dmPython
from sqlalchemy.exc import DBAPIError


class SafeDMDialect(DMDialect_dmPython):
    supports_statement_cache = False

    def connect(self, *args, **kwargs):
        try:
            return super().connect(*args, **kwargs)
        except DBAPIError as error:
            # dmSQLAlchemy wraps the real driver exception with orig=None. Restore it
            # so SQLAlchemy can classify disconnects and preserve its real cause.
            if error.orig is None and error.__context__ is not None:
                raise error.__context__ from None
            raise
