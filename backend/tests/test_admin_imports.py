"""用户 Excel / CSV 导入边界，复用真实预览和最终提交校验。"""

import base64
import io
import json
import re
import struct
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import DEFUSEDXML, Workbook
from sqlalchemy import func, select

from app.core.errors import AppError
from app.modules.admin import imports
from app.modules.admin.models import SysUser
from app.modules.admin.schemas import IMPORT_XLSX_MAX_BYTES
from app.modules.admin.service import ADMIN_DEPTS
from tests.function_access_support import grant_fixture_roles

URL = "/api/admin/v1/users"
HEADER = ["姓名", "账号", "部门", "角色"]
ROW = ["新用户", "new.import@example.com", ADMIN_DEPTS[0], "业务查看"]


def workbook(rows, *, second=None):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    if second:
        sheet = book.create_sheet("不导入")
        for row in second:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()


def encoded(data):
    return base64.b64encode(data).decode("ascii")


def rewrite(data, replace=None, add=None):
    out = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as source, ZipFile(out, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            raw = source.read(name)
            if replace:
                raw = replace(name, raw)
            target.writestr(name, raw)
        for name, raw in (add or {}).items():
            target.writestr(name, raw)
    return out.getvalue()


def rejected(data):
    with pytest.raises(AppError) as error:
        imports.parse_import_xlsx(encoded(data))
    assert error.value.status_code == 422 and error.value.code == "VALIDATION_FAILED"


def test_xlsx_library_reorders_blank_rows_first_sheet_and_empty():
    assert DEFUSEDXML is True
    data = workbook(
        [[], ["角色", "账号", "姓名", "部门", "备注"], [], [ROW[3], ROW[1], ROW[0], ROW[2], "ignored"]],
        second=[HEADER, ["不读", *ROW[1:]]],
    )
    rows = imports.parse_import_xlsx(encoded(data))
    assert [r.model_dump() for r in rows] == [
        dict(zip(("name", "account", "dept", "role"), ROW, strict=True))
    ]
    assert imports.parse_import_xlsx(encoded(workbook([]))) == []
    assert imports.parse_import_xlsx(encoded(workbook([HEADER]))) == []


def test_csv_compatibility_and_duplicate_known_header():
    rows = imports.parse_import_csv("\ufeff角色,未知,姓名,账号\n业务查看,x, 张三 ,a@b.cn\n\n")
    assert rows[0].model_dump() == {"name": "张三", "account": "a@b.cn", "dept": "", "role": "业务查看"}
    assert imports.parse_import_csv("") == []
    with pytest.raises(AppError, match="重复"):
        imports.parse_import_csv("姓名,姓名,账号\na,b,c@d.cn")


@pytest.mark.parametrize(
    "rows",
    [
        [["姓名", "账号", "部门", "角色", "姓名"], ROW],
        [["姓名", "账号"], ROW],
        [HEADER, ["=1+1", *ROW[1:]]],
    ],
)
def test_xlsx_rejects_duplicate_missing_header_or_formula(rows):
    rejected(workbook(rows))


def test_all_file_routes_reject_501_without_truncation_and_hidden_dimension():
    rows = [HEADER] + [[f"n{i}", f"n{i}@x.cn", ROW[2], ROW[3]] for i in range(501)]
    data = workbook(rows)
    assert len(imports.parse_import_xlsx(encoded(workbook(rows[:-1])))) == 500
    rejected(data)
    forged = rewrite(
        data,
        lambda name, raw: (
            re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:A1"', raw)
            if name == "xl/worksheets/sheet1.xml"
            else raw
        ),
    )
    rejected(forged)
    with pytest.raises(AppError, match="500"):
        imports.parse_import_csv("姓名,账号,部门,角色\n" + "n,n@x.cn,d,r\n" * 501)


def test_xlsx_zip_size_entry_macro_encryption_and_xml_bombs(monkeypatch):
    data = workbook([HEADER, ROW])
    rejected(b"not a zip")
    with pytest.raises(AppError, match="2 MiB"):
        imports.parse_import_xlsx(encoded(b"x" * (IMPORT_XLSX_MAX_BYTES + 1)))
    for payload in ("bad base64!", "\ud800"):
        with pytest.raises(AppError):
            imports.parse_import_xlsx(payload)
    rejected(rewrite(data, add={"xl/vbaProject.bin": b"macro"}))
    rejected(
        rewrite(
            data,
            lambda name, raw: (
                raw.replace(b"spreadsheetml.sheet.main+xml", b"ms-excel.sheet.macroEnabled.main+xml")
                if name == "[Content_Types].xml"
                else raw
            ),
        )
    )
    rejected(rewrite(data, add={f"extra/{i}": b"x" for i in range(129)}))
    with monkeypatch.context() as patch:
        patch.setattr(imports, "XLSX_MAX_EXPANDED", 100)
        rejected(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        rejected(rewrite(data, add={"[Content_Types].xml": b"duplicate"}))
    # ZIP 通用标志的 encrypted 位；不需要真实加密器即可验证在读取前拒绝。
    encrypted = bytearray(data)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = encrypted.find(signature)
        flags = struct.unpack_from("<H", encrypted, index + offset)[0]
        struct.pack_into("<H", encrypted, index + offset, flags | 1)
    rejected(bytes(encrypted))
    corrupt = bytearray(data)
    name_size, extra_size = struct.unpack_from("<HH", corrupt, 26)
    corrupt[30 + name_size + extra_size] = 255  # 无效 DEFLATE 块也必须转为 422。
    rejected(bytes(corrupt))
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY bad "EXPANDED">]><Types>&bad;</Types>'
    rejected(rewrite(data, lambda name, raw: bomb if name == "[Content_Types].xml" else raw))


def test_xlsx_sparse_row_and_wide_sheet_are_bounded():
    data = workbook([HEADER, ROW])
    sparse = rewrite(
        data,
        lambda name, raw: (
            raw.replace(b'r="2"', b'r="999999999"') if name == "xl/worksheets/sheet1.xml" else raw
        ),
    )
    rejected(sparse)
    rejected(workbook([HEADER + ["extra"] * 30, ROW]))


@pytest.mark.parametrize("dimension", ["missing", "forged"])
@pytest.mark.parametrize("tail", ["真实第34列", "=1+1"])
def test_xlsx_true_width_rejects_hidden_34th_column(dimension, tail):
    data = workbook([HEADER, ROW + [None] * 29 + [tail]])  # 第33列空，第34列才有内容。
    data = rewrite(
        data,
        lambda name, raw: (
            (
                re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:A1"', raw)
                if dimension == "forged"
                else re.sub(rb"<dimension\b[^>]*/>", b"", raw)
            )
            if name == "xl/worksheets/sheet1.xml"
            else raw
        ),
    )
    with pytest.raises(AppError, match="32 列"):
        imports.parse_import_xlsx(encoded(data))


@pytest.mark.parametrize("dimension", ["missing", "forged"])
def test_xlsx_true_width_accepts_32_columns(dimension):
    data = workbook([HEADER, ROW + [None] * 27 + ["第32列"]])
    data = rewrite(
        data,
        lambda name, raw: (
            (
                re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:A1"', raw)
                if dimension == "forged"
                else re.sub(rb"<dimension\b[^>]*/>", b"", raw)
            )
            if name == "xl/worksheets/sheet1.xml"
            else raw
        ),
    )
    assert imports.parse_import_xlsx(encoded(data))[0].name == ROW[0]


@pytest.fixture
async def authorized(users):
    await grant_fixture_roles(users)


async def test_xlsx_preview_duplicates_existing_and_final_import(client, users, authorized):
    rows = [HEADER, ROW, ROW, ["另一个", "second.import@example.com", ROW[2], ROW[3]]]
    response = await client.post(URL + ":import-preview", json={"xlsxBase64": encoded(workbook(rows))})
    assert response.status_code == 200, response.text
    preview = response.json()["rows"]
    assert [r["error"] for r in preview] == [None, "文件内账号重复", None]
    assert await users.scalar(select(SysUser).where(SysUser.account == ROW[1])) is None  # 预览不写库。
    response = await client.post(URL + ":import", json={"rows": preview})
    assert response.status_code == 200 and response.json() == {"imported": 2}
    # 重新提交必须重做校验，而非信任客户端预览的 error。
    response = await client.post(URL + ":import", json={"rows": preview})
    assert response.json() == {"imported": 0}
    assert (await users.scalar(select(SysUser).where(SysUser.account == ROW[1]))).status.value == "待激活"


async def test_preview_input_contract_limit_readonly_and_sources(client, users, authorized):
    source = {"xlsxBase64": encoded(workbook([HEADER, ROW]))}
    for invalid in ({}, source | {"csv": ""}, {"rows": [], "csv": ""}, {"xlsxBase64": "!"}):
        response = await client.post(URL + ":import-preview", json=invalid)
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_FAILED"
    for method in (":import-preview", ":import"):
        response = await client.post(
            URL + method,
            json={"rows": [dict(zip(("name", "account", "dept", "role"), ROW, strict=True))] * 501},
        )
        assert response.status_code == 422
    for context in ({"ver": "v1"}, {"previewAs": "U010006"}):
        response = await client.post(URL + ":import-preview", params=context, json=source)
        assert response.status_code == 403 and response.json()["code"] == "READONLY"


async def test_row_lengths_shared_by_preview_and_submit_no_database_500(client, users, authorized):
    valid = dict(zip(("name", "account", "dept", "role"), ROW, strict=True))
    rows = [
        valid | {field: "x" * (limit + 1)}
        for field, limit in (("name", 64), ("account", 128), ("dept", 128), ("role", 64))
    ]
    response = await client.post(URL + ":import-preview", json={"rows": rows})
    assert response.status_code == 200
    assert all("不能超过" in row["error"] for row in response.json()["rows"])
    response = await client.post(URL + ":import", json={"rows": rows})
    assert response.status_code == 200 and response.json() == {"imported": 0}


@pytest.mark.parametrize("bad", ["a\x00b", "a\ud800b"])
async def test_invalid_text_is_422_for_csv_rows_and_final_submit(client, users, authorized, bad):
    count = await users.scalar(select(func.count()).select_from(SysUser))
    row = dict(zip(("name", "account", "dept", "role"), ROW, strict=True)) | {"account": bad}
    for suffix, body in (
        (":import-preview", {"rows": [row]}),
        (":import", {"rows": [row]}),
        (":import-preview", {"csv": "姓名,账号\nx," + bad}),
    ):
        response = await client.post(
            URL + suffix, content=json.dumps(body), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_FAILED"
    assert await users.scalar(select(func.count()).select_from(SysUser)) == count
