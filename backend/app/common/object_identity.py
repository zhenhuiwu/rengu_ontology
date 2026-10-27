"""Published, governed object identities plus unambiguous legacy business codes."""

from sqlalchemy import Integer, String, and_, case, cast, func, or_, select, union

from app.core.errors import AppError
from app.modules.static.models import ObjectStatus, ObjectType, Property


def property_column(prop, governed):
    declared = {"id", "code"} | {getattr(column, "name", None) for column in governed.columns.values()}
    binding = (prop.backing_column or prop.source_column) if prop is not None else None
    if (prop is None or prop.is_sensitive or prop.visibility == "hidden" or getattr(prop, "transform", "")
            or binding not in declared or binding not in governed.table.c):
        return None
    return governed.table.c[binding]


def primary_column(obj, governed):
    from app.modules.qa.object_source import primary_property

    column = property_column(primary_property(obj), governed)
    if column is None:
        raise AppError("对象主键没有公开的受控映射", code="OBJECT_IDENTIFIER_UNAVAILABLE", status_code=503)
    if not isinstance(column.type, String | Integer):
        raise AppError("对象主键类型暂不可用于动作", code="OBJECT_IDENTIFIER_UNAVAILABLE", status_code=503)
    return column


def identity_rows(governed):
    """Identity → physical row; collisions across primary keys and old codes are rejected together."""
    table = governed.table
    declared = {"id", "code"} | {getattr(column, "name", None) for column in governed.columns.values()}
    columns = [column for column in table.c if column.name in declared
               and isinstance(column.type, String | Integer)]
    explicit = select(Property.id).where(Property.object_type_id == ObjectType.id,
                                        Property.api_name == ObjectType.pk_property).correlate(ObjectType).exists()
    flagged = select(func.count()).where(Property.object_type_id == ObjectType.id,
                                        Property.is_pk.is_(True)).correlate(ObjectType).scalar_subquery()
    primary = Property.__table__.alias("identity_primary")
    binding = func.coalesce(func.nullif(primary.c.backing_column, ""), primary.c.source_column)
    value = case(*[(binding == column.name, cast(column, String)) for column in columns])
    canonical = select(value.label("identity"), table.c.id.label("row_id")).select_from(table).join(
        ObjectType, ObjectType.code == governed.object_type).join(primary, primary.c.object_type_id == ObjectType.id).where(
        ObjectType.status == ObjectStatus.published, primary.c.is_sensitive.is_(False), primary.c.visibility != "hidden",
        primary.c.transform == "",
        or_(primary.c.api_name == ObjectType.pk_property,
            and_(~explicit, flagged == 1, primary.c.is_pk.is_(True),
                 or_(ObjectType.pk_property.is_(None), ObjectType.pk_property == "",
                     ObjectType.pk_property == primary.c.source_column))))
    aliases = union(canonical, select(cast(table.c.code, String).label("identity"), table.c.id.label("row_id"))).subquery()
    return select(aliases.c.identity, func.min(aliases.c.row_id).label("row_id")).where(
        aliases.c.identity.is_not(None)).group_by(aliases.c.identity).having(func.count() == 1)
