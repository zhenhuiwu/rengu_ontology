"""Offline pg_dump COPY sanitizer. Never connects to a database or prints data values."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

REDACTED = "[REDACTED]"
DROP_DATA = {
    "auth_credential",
    "auth_session",
    "auth_login_throttle",
    "api_key",
    "api_key_usage",
    "api_key_call",
}
CLEAR_NULL = {("datasource", "password_enc"), ("platform_setting", "secret_enc"), ("qa_page", "link_token")}
PRIVATE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "passwordenc",
    "passwordhash",
    "secret",
    "secretenc",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "token",
    "tokenhash",
    "linktoken",
    "clientsecret",
    "signingsecret",
    "hmacsecret",
    "hmackey",
    "privatekey",
    "authorization",
    "proxyauthorization",
    "cookie",
    "setcookie",
}
COPY = re.compile(r"COPY\s+(.+?)\s*\((.*)\)\s+FROM\s+stdin;", re.I)
ENV_PRIVATE = re.compile(r"PASSWORD|SECRET|TOKEN|API_?KEY|DATABASE_URL|(?:^|_)DSN(?:$|_)", re.I)
URL_USER = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^\s/\"'<>]+@)", re.I)
BEARER = re.compile(r"\b(?:Bearer|Basic|HMAC(?:-SHA\d+)?)[ \t]+[A-Za-z0-9._~+/=-]{8,}", re.I)
HEADER = re.compile(
    r"\b(?:Authorization|Proxy-Authorization|Cookie|Set-Cookie)[ \t]*[:=][ \t]*[^\r\n\"']+", re.I
)
ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|signing[_-]?secret|"
    r"hmac[_-]?(?:key|secret|signature)|password|passwd|secret[_-]?enc|password[_-]?enc|private[_-]?key|token[_-]?hash)"
    r"[ \t]*[=:][ \t]*[A-Za-z0-9._~+/$=-]{8,}",
    re.I,
)
TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:rg_(?:test|live)_[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_.*…-])|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
    r"fernet\$[A-Za-z0-9_=-]{60,}|pbkdf2_sha256\$[^\s\"']{20,})"
)
PEM = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----", re.S)
JSON_STRING = re.compile(r'("(?:[^"\\]|\\.)*"\s*:\s*)("(?:[^"\\]|\\.)*")')
SCHEMA_KEYS = {
    "type",
    "$ref",
    "$id",
    "$schema",
    "$defs",
    "definitions",
    "properties",
    "items",
    "required",
    "additionalProperties",
    "description",
    "title",
    "default",
    "examples",
    "example",
    "enum",
    "const",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "anyOf",
    "allOf",
    "oneOf",
    "not",
    "nullable",
    "readOnly",
    "writeOnly",
}


class SanitizeError(Exception):
    """Only static, safe diagnostics may be exposed by the command entry point."""


def reference(value):
    if isinstance(value, dict):
        return (
            len(value) == 1
            and set(value) <= {"param", "context", "valueFrom", "env", "secretRef"}
            and all(isinstance(v, str) for v in value.values())
        )
    return isinstance(value, str) and bool(
        re.fullmatch(
            r"(?:(?:Bearer|Basic|HMAC(?:-SHA\d+)?)\s+)?(?:\{\{[^{}\r\n]{1,300}\}\}|\$\{[^{}\r\n]{1,300}\})|(?:env|secret):[A-Za-z_][A-Za-z0-9_]*",
            value,
        )
    )


def schema_descriptor(value):
    return isinstance(value, bool) or (
        isinstance(value, dict)
        and (
            not value
            or (
                bool(set(value) & {"type", "$ref", "anyOf", "oneOf", "allOf", "properties"})
                and set(value) <= SCHEMA_KEYS
            )
        )
    )


def private_key(key):
    return re.sub(r"[^a-z0-9]", "", key.lower()) in PRIVATE_KEYS


def identifiers(value, separator):
    """Split quoted PostgreSQL identifiers, including doubled quotes and quoted commas/dots."""
    parts, chars, quoted, index = [], [], False, 0
    while index < len(value):
        char = value[index]
        if char == '"':
            if quoted and index + 1 < len(value) and value[index + 1] == '"':
                chars.append('"')
                index += 2
                continue
            quoted = not quoted
        elif char == separator and not quoted:
            parts.append("".join(chars).strip())
            chars = []
        else:
            chars.append(char)
        index += 1
    if quoted:
        raise ValueError("Unterminated COPY identifier")
    parts.append("".join(chars).strip())
    if not all(parts):
        raise ValueError("Invalid or duplicate COPY identifiers")
    return parts


def decode_cell(raw):
    if raw == r"\N":
        return None

    def escape(match):
        value = match[1]
        if value.startswith("x") and len(value) > 1:
            return chr(int(value[1:], 16))
        if value[0] in "01234567":
            return chr(int(value, 8))
        return {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}.get(value, value)

    return re.sub(r"\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3}|.)", escape, raw)


def encode_cell(value):
    if value is None:
        return r"\N"
    for source, target in (
        ("\\", "\\\\"),
        ("\b", r"\b"),
        ("\f", r"\f"),
        ("\n", r"\n"),
        ("\r", r"\r"),
        ("\t", r"\t"),
        ("\v", r"\v"),
    ):
        value = value.replace(source, target)
    return value


def known_secrets(env_file):
    """Only retain private values; never disclose environment names or values in output."""
    from dotenv import dotenv_values

    result = set()

    def add(value):
        if isinstance(value, str) and len(value) >= 8 and value != REDACTED and not reference(value):
            result.add(value)

    def leaves(value, all_private=False):
        if isinstance(value, dict):
            for key, item in value.items():
                leaves(item, all_private or private_key(key) or key.lower().endswith("secretref"))
        elif isinstance(value, list):
            for item in value:
                leaves(item, all_private)
        elif all_private:
            add(value)

    values = dotenv_values(env_file, interpolate=False) if env_file and env_file.is_file() else {}
    for name, value in {**values, **os.environ}.items():
        if not isinstance(value, str):
            continue
        if ENV_PRIVATE.search(name):
            try:
                leaves(json.loads(value), True)
            except ValueError:
                add(value)
                for segment in value.split(","):
                    add(segment.strip())
            with contextlib.suppress(ValueError):
                add(unquote(urlsplit(value).password or ""))
        elif name in {"RENGU_INTEGRATION_ADAPTERS", "RENGU_INTEGRATION_LOCAL_DEMOS"}:
            try:
                leaves(json.loads(value))
            except ValueError:
                raise ValueError("Private integration configuration cannot be parsed") from None
    return sorted(result, key=len, reverse=True)


class Sanitizer:
    def __init__(self, known=()):
        self.known = tuple(known)

    def text(self, value):
        for secret in self.known:
            if secret in value:
                if secret.isdecimal() and value.strip().isdecimal():
                    raise SanitizeError(
                        "Known private value collides with a numeric scalar; manual review required"
                    )
                value = value.replace(secret, REDACTED)
        value = URL_USER.sub(lambda m: m[1], value)
        for pattern in (PEM, TOKEN, HEADER, BEARER, ASSIGNMENT):
            value = pattern.sub(REDACTED, value)

        def json_string(match):
            try:
                key = json.loads(match[1].rsplit(":", 1)[0].strip())
                cell = json.loads(match[2])
            except ValueError:
                return match[0]
            return (
                match[1] + json.dumps(REDACTED)
                if private_key(key) and cell not in ("", REDACTED) and not reference(cell)
                else match[0]
            )

        return JSON_STRING.sub(json_string, value)

    def cell(self, value):
        def walk(item, schema_fields=False):
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if (
                        not schema_fields
                        and private_key(key)
                        and child not in (None, "", REDACTED, [], {})
                        and not reference(child)
                    ):
                        if isinstance(child, (dict, list)):
                            if not schema_descriptor(child):
                                raise SanitizeError(
                                    "Ambiguous structured private configuration; manual review required"
                                )
                        elif isinstance(child, str):
                            result[key] = REDACTED
                            continue
                        else:
                            raise SanitizeError(
                                "Ambiguous non-text private configuration; manual review required"
                            )
                    fields = (
                        key == "properties"
                        and isinstance(child, dict)
                        and all(schema_descriptor(v) for v in child.values())
                    )
                    result[key] = walk(child, schema_fields=fields)
                return result
            if isinstance(item, list):
                return [walk(child) for child in item]
            return self.text(item) if isinstance(item, str) else item

        if value.lstrip().startswith(("{", "[")):
            try:
                original = json.loads(value)
            except ValueError:
                return self.text(value)
            cleaned = walk(original)
            return (
                json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
                if cleaned != original
                else value
            )
        return self.text(value)


def convert(source, target, sanitizer, *, verify=False):
    tables, revisions, changed = {}, set(), 0
    current = None
    for line in source:
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if current is None:
            match = COPY.fullmatch(body)
            if match:
                qualified = identifiers(match[1], ".")
                table, columns = qualified[-1], identifiers(match[2], ",")
                name = ".".join(qualified)
                if name in tables:
                    raise ValueError("Duplicate COPY section")
                if len(columns) != len(set(columns)):
                    raise SanitizeError("Duplicate COPY columns")
                tables[name] = {"sourceRows": 0, "outputRows": 0, "cleanedCells": 0, "affectedColumns": {}}
                current = table, columns, tables[name]
                target.write(line)
            elif body.lstrip().upper().startswith("COPY "):
                raise ValueError("Unsupported COPY header")
            else:
                cleaned = sanitizer.text(body)
                if verify and cleaned != body:
                    raise ValueError("Residual credential pattern outside COPY")
                changed += int(cleaned != body)
                target.write(cleaned + ending)
            continue
        if body == r"\.":
            current = None
            target.write(line)
            continue
        table, columns, stats = current
        cells = body.split("\t")
        if len(cells) != len(columns):
            raise ValueError("COPY column count mismatch")
        stats["sourceRows"] += 1
        if table in DROP_DATA:
            if verify:
                raise ValueError("Authentication data remains")
            continue
        for index, (column, raw) in enumerate(zip(columns, cells, strict=True)):
            value = decode_cell(raw)
            if table == "alembic_version" and column == "version_num" and value is not None:
                if not re.fullmatch(r"[a-zA-Z0-9_]+", value):
                    raise ValueError("Invalid migration revision")
                revisions.add(value)
            if (table, column) in CLEAR_NULL:
                cleaned = None
            elif (table, column) == ("ext_system", "auth_detail"):
                cleaned = ""
            else:
                cleaned = sanitizer.cell(value) if value is not None else None
            if cleaned != value:
                if verify:
                    raise ValueError("Residual credential in COPY cell")
                cells[index] = encode_cell(cleaned)
                stats["cleanedCells"] += 1
                stats["affectedColumns"][column] = stats["affectedColumns"].get(column, 0) + 1
        stats["outputRows"] += 1
        target.write("\t".join(cells) + ending)
    if current is not None:
        raise ValueError("Unterminated COPY section")
    return {
        "tables": tables,
        "cleanedCells": sum(t["cleanedCells"] for t in tables.values()),
        "removedAuthRows": sum(t["sourceRows"] - t["outputRows"] for t in tables.values()),
        "cleanedSqlLines": changed,
        "alembicRevision": sorted(revisions),
    }


def self_check():
    raw = (
        "CREATE TABLE public.auth_session (token_hash text);\n"
        'COPY public."auth_session" ("token_hash", user_id) FROM stdin;\nprivate-token\t1\n\\.\n'
        "COPY public.datasource (id, password_enc, name) FROM stdin;\n1\tfernet$private\tkeep\\tname\n\\.\n"
        'COPY public."business.table" ("i,d", "j""son", token_count, key, id) FROM stdin;\n'
        "1\t"
        + encode_cell(
            json.dumps(
                {
                    "password": "private",
                    "nested": {"token": "other-private"},
                    "token_count": 7,
                    "key": "business",
                    "id": "one",
                    "url": "https://user:pass@host/path",
                    "note": "known-private",
                    "ref": "office\\device\nline",
                }
            )
        )
        + "\t7\tordinary\t2\n\\.\n"
        "COPY public.ext_system (id, auth_detail) FROM stdin;\n1\tBearer abcdefghijklmnop\n\\.\n"
        "COPY public.alembic_version (version_num) FROM stdin;\ndata_0049_external_cdc\n\\.\n"
        "SELECT pg_catalog.setval('public.api_key_id_seq', 22, true);\n"
    )
    output = io.StringIO()
    sanitizer = Sanitizer(["known-private"])
    assert (
        all(sanitizer.text("rg_" + mode + "_" + "A_b-C" * 9) == REDACTED for mode in ("test", "live"))
        and sanitizer.text("rg_test_Abcd…wxyz") == "rg_test_Abcd…wxyz"
    )
    manifest = convert(io.StringIO(raw), output, sanitizer)
    value = output.getvalue()
    assert "CREATE TABLE public.auth_session" in value and "private-token" not in value
    assert "setval('public.api_key_id_seq', 22, true)" in value and "keep\\tname" in value
    business_row = next(line for line in value.splitlines() if line.startswith("1\t{"))
    assert '"token_count":7,"key":"business","id":"one"' in decode_cell(business_row.split("\t")[1])
    assert "https://host/path" in value and "known-private" not in value
    assert manifest["tables"]["public.auth_session"]["outputRows"] == 0
    assert manifest["tables"]["public.business.table"]["outputRows"] == 1
    assert manifest["alembicRevision"] == ["data_0049_external_cdc"]
    schema = {"type": "object", "properties": {"secret": {"type": "string"}, "token": {"type": "string"}}}
    assert sanitizer.cell(json.dumps(schema)) == json.dumps(schema)
    template = {
        "secret": "{{ config.API_KEY }}",
        "token": "${TOKEN_NAME}",
        "authorization": "Bearer ${TOKEN_NAME}",
        "password": {"param": "password"},
        "key_id": 4,
        "idempotency_key": "one",
    }
    assert sanitizer.cell(json.dumps(template)) == json.dumps(template)
    try:
        sanitizer.cell('{"secret":{"value":"ambiguous"}}')
    except SanitizeError:
        pass
    else:
        raise AssertionError("Ambiguous private configuration was silently retained")
    convert(io.StringIO(value), io.StringIO(), sanitizer, verify=True)
    print("SQL sanitizer self-check passed (COPY/JSON/quoted identifiers/escapes/schema/sequences)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--acl-file", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if not args.source or not args.output:
        parser.error("source and output are required")
    source, output = args.source.resolve(), args.output.resolve()
    report = output.with_suffix(".manifest.json")
    if source == output or output.exists() or report.exists():
        raise ValueError("Output already exists or equals source; refusing overwrite")
    if not output.parent.is_dir():
        raise ValueError("Output directory must already exist")
    sanitizer = Sanitizer(known_secrets(args.env_file))
    partial = output.with_name(output.name + "." + uuid.uuid4().hex + ".partial")
    report_partial = report.with_name(report.name + "." + uuid.uuid4().hex + ".partial")
    published = False
    try:
        with (
            source.open(encoding="utf-8", newline="") as src,
            partial.open("x", encoding="utf-8", newline="") as dst,
        ):
            manifest = convert(src, dst, sanitizer)
            if args.acl_file:
                acl = args.acl_file.read_text(encoding="utf-8")
                if sanitizer.text(acl) != acl:
                    raise ValueError("ACL input contains a credential pattern")
                dst.write("\nSET search_path = public, pg_catalog;\n")
                dst.write(acl)
                dst.write("\nSELECT pg_catalog.set_config('search_path', '', false);\n")
        with partial.open(encoding="utf-8", newline="") as check:
            verified = convert(check, io.StringIO(), sanitizer, verify=True)
        if not manifest["tables"] or verified["alembicRevision"] != manifest["alembicRevision"]:
            raise ValueError("Missing COPY data or inconsistent revision")
        if set(verified["tables"]) != set(manifest["tables"]) or any(
            verified["tables"][name]["sourceRows"] != stats["outputRows"]
            for name, stats in manifest["tables"].items()
        ):
            raise SanitizeError("Output table counts differ from sanitized input")
        manifest["bytes"] = partial.stat().st_size
        with partial.open("rb") as digest_source:
            manifest["sha256"] = hashlib.file_digest(digest_source, "sha256").hexdigest()
        manifest.update(offlineOnly=True, sourceModified=False, restoreVerified=False)
        with report_partial.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
        os.link(partial, output)  # Atomic no-replace publication, including concurrent creators.
        published = True
        os.link(report_partial, report)
        print(json.dumps(manifest, ensure_ascii=True, sort_keys=True))
    except Exception:
        if published and output.exists() and os.path.samefile(partial, output):
            output.unlink()
        raise
    finally:
        partial.unlink(missing_ok=True)
        report_partial.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never echo exception payloads, input lines, credential values or URL details.
        reason = str(error) if isinstance(error, SanitizeError) else type(error).__name__
        print(f"SQL sanitizer stopped: {reason}; no replacement output published.", file=sys.stderr)
        sys.exit(1)
