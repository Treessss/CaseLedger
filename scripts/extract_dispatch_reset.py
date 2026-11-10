"""Read the user's dispatch workbook without changing its invalid legacy styles."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def extract(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        strings = ["".join(node.itertext()) for node in
                   ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("m:sheets/m:sheet", ns)
        rows = {}
        for index, sheet in enumerate(sheets, 1):
            if sheet.get("name") not in ("工作表2", "重发"):
                continue
            result = []
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            for row in root.findall("m:sheetData/m:row", ns):
                values = {}
                for cell in row:
                    value = cell.find("m:v", ns)
                    if value is None or value.text is None:
                        continue
                    text = strings[int(value.text)] if cell.get("t") == "s" else value.text
                    column = "".join(char for char in cell.get("r") if char.isalpha())
                    values[column] = text.strip()
                result.append(values)
            rows[sheet.get("name")] = result
    summary = rows["工作表2"]
    shortages = [row["A"] for row in summary if row.get("A", "").isdigit()]
    resends = [row["B"] for row in summary if row.get("B", "").isdigit()]
    assert len(shortages) == len(set(shortages)) == 95
    assert len(resends) == len(set(resends)) == 9
    assert not set(shortages) & set(resends)
    detail = {row["B"]: row for row in rows["重发"] if row.get("B") in resends}
    assert len(detail) == 9
    return {"shop_id": 1, "shop_name": "CASEEASE", "cutoff_inclusive": 12542,
            "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "shortage_numbers": shortages, "resend_numbers": resends,
            "resend_notes": {number: "；".join(f"{label}：{detail[number][column]}"
                for column, label in (("D", "问题"), ("F", "表格商品说明"),
                                      ("G", "表格采购记录"), ("H", "表格状态"), ("I", "原表单号"))
                if detail[number].get(column)) for number in resends}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook")
    parser.add_argument("output")
    args = parser.parse_args()
    data = extract(args.workbook)
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(json.dumps({"shortages": len(data["shortage_numbers"]),
                      "resends": data["resend_numbers"], "sha256": data["source_sha256"]}))
