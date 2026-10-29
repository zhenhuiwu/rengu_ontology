param(
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'exports\database'),
    [string]$PgDump = 'D:\datadb\16\bin\pg_dump.exe'
)

$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Backend Python environment not found.' }
if (-not (Test-Path -LiteralPath $PgDump -PathType Leaf)) { throw 'pg_dump not found; specify -PgDump.' }

Push-Location $backendRoot
try {
    @'
from datetime import datetime
from pathlib import Path
import hashlib
import os
import re
import subprocess
import sys

import psycopg
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

out_dir = Path(sys.argv[1]).expanduser().resolve()
pg_dump = Path(sys.argv[2]).resolve()
url_value = dotenv_values('.env').get('RENGU_DATABASE_URL')
if not url_value:
    sys.exit('RENGU_DATABASE_URL is missing from backend/.env.')
try:
    url = make_url(url_value)
except Exception:
    sys.exit('Invalid database configuration.')
if url.database != 'rengu_ontology' or url.host not in ('localhost', '127.0.0.1', '::1') or url.port != 5433:
    sys.exit('Refusing export: expected local rengu_ontology on port 5433.')

version = subprocess.run([str(pg_dump), '--version'], capture_output=True, text=True, check=True).stdout
match = re.search(r'\b(\d+)\.', version)
if not match:
    sys.exit('Unable to determine pg_dump version.')
try:
    with psycopg.connect(host='127.0.0.1', port=5433, dbname=url.database,
                         user=url.username, password=url.password, connect_timeout=10) as connection:
        server_major = connection.info.server_version // 10000
except Exception:
    sys.exit('Unable to connect to the configured local database.')
if int(match.group(1)) < server_major:
    sys.exit('pg_dump major version is older than the database server.')

out_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
for suffix in range(1000):
    name = f'rengu_ontology_{stamp}' + (f'_{suffix}' if suffix else '') + '.sql'
    output = out_dir / name
    partial = out_dir / (name + '.partial')
    if output.exists():
        continue
    try:
        with partial.open('xb'):
            pass
        break
    except FileExistsError:
        continue
else:
    sys.exit('Unable to allocate a unique output filename.')

try:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
    env['PGPASSWORD'] = url.password or ''
    command = [str(pg_dump), '--host', '127.0.0.1', '--port', '5433', '--username', url.username,
               '--dbname', 'rengu_ontology', '--format=plain', '--encoding=UTF8', '--no-owner',
               '--no-privileges', '--file', str(partial)]
    result = subprocess.run(command, env=env, capture_output=True)
    if result.returncode:
        sys.exit(f'pg_dump failed (exit {result.returncode}); no SQL file was published.')
    digest = hashlib.sha256()
    found = [False, False, False]
    with partial.open('rb') as stream:
        for line in stream:
            digest.update(line)
            found[0] |= line.startswith(b'CREATE TABLE ')
            found[1] |= line.startswith(b'COPY ')
            found[2] |= b'PostgreSQL database dump complete' in line
    if not all(found) or not partial.stat().st_size:
        sys.exit('Export is incomplete; no SQL file was published.')
    if output.parent.resolve() != out_dir or partial.parent.resolve() != out_dir:
        sys.exit('Output path is outside the requested directory.')
    partial.rename(output)
    print(f'SQL: {output}')
    print(f'Bytes: {output.stat().st_size}')
    print(f'SHA256: {digest.hexdigest()}')
    print(f'PostgreSQL server: {server_major}; pg_dump: {match.group(1)}')
    print('Verified: schema, data section, and completion marker present.')
finally:
    if partial.exists():
        partial.unlink()
'@ | & $python - $OutputDirectory $PgDump
    if ($LASTEXITCODE -ne 0) { throw 'Database export failed.' }
} finally {
    Pop-Location
}
