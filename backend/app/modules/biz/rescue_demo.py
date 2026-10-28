"""Local synthetic data binding for workspace 4."""
from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table
from app.common import sql_guard

table = Table(
    'rescue_incident_w4_demo', MetaData(),
    Column('id', BigInteger, primary_key=True),
    Column('ontology_id', BigInteger),
    Column('incident_id', String(64), nullable=False),
    Column('code', String(64), nullable=False),
    Column('status', String(32), nullable=False),
    Column('distress_position', String(128)),
    Column('team_id', String(64)),
    Column('lock_version', Integer, nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False),
)
sql_guard.register_governed(sql_guard.GovernedTable(
    table, 'RescueIncident', {key: table.c[key] for key in ('incident_id', 'status', 'distress_position', 'team_id', 'updated_at')}, ontology_id=4
))
teams = Table('rescue_team_w4_demo', MetaData(),
              Column('id', BigInteger, primary_key=True), Column('ontology_id', BigInteger),
              Column('team_id', String(64), nullable=False), Column('code', String(64), nullable=False),
              Column('status', String(32), nullable=False))
sql_guard.register_governed(sql_guard.GovernedTable(
    teams, 'RescueTeam', {key: teams.c[key] for key in ('team_id', 'status')}, ontology_id=4
))
