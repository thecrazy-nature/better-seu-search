from __future__ import annotations

import io
import unittest
import zipfile

from backend.app.attachments import (
    attachment_extension,
    attachment_table_row_parts,
    extract_attachment_payload,
    infer_attachment_extension,
)
from backend.app.storage import split_document_chunks


def make_docx(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>"
                f"{text}"
                "</w:t></w:r></w:p></w:body></w:document>"
            ),
        )
    return buffer.getvalue()


def make_xlsx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            (
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="SheetA" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                "</Relationships>"
            ),
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<si><t>college</t></si><si><t>major</t></si>"
                "<si><t>computer science</t></si><si><t>software engineering</t></si>"
                "</sst>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>"
                '<row r="1"><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
                '<row r="2"><c t="s"><v>2</v></c><c t="s"><v>3</v></c></row>'
                "</sheetData>"
                "</worksheet>"
            ),
        )
    return buffer.getvalue()


class AttachmentExtractionTest(unittest.TestCase):
    def test_extension_falls_back_to_name_for_upload_urls(self) -> None:
        url = "https://example.edu/_upload/article/files/id/download"
        self.assertEqual(attachment_extension(url, "notice.docx"), ".docx")

    def test_infer_extension_from_content_when_url_has_no_suffix(self) -> None:
        payload = make_docx("hidden suffix document")
        self.assertEqual(infer_attachment_extension(payload, "https://example.edu/download", ""), ".docx")

    def test_docx_zip_fallback_extracts_text(self) -> None:
        payload = extract_attachment_payload(make_docx("major transfer form"), "", "form.docx")
        self.assertIn("major transfer", payload["text"])

    def test_xlsx_zip_fallback_extracts_sheets(self) -> None:
        payload = extract_attachment_payload(make_xlsx(), "", "sheet.xlsx")
        self.assertEqual(payload["sheets"][0]["sheet"], "SheetA")
        self.assertIn("computer science", payload["sheets"][0]["text"])
        self.assertEqual(payload["sheets"][0]["rows"][0]["row_number"], 2)
        self.assertIn("college：computer science", payload["sheets"][0]["rows"][0]["text"])

    def test_table_rows_become_row_level_chunks(self) -> None:
        payload = extract_attachment_payload(make_xlsx(), "", "sheet.xlsx")
        chunks = split_document_chunks(
            "转专业接收方案",
            "",
            [{"name": "接收条件表.xlsx", "url": "https://example.edu/table.xlsx", "sheets": payload["sheets"]}],
        )
        row_chunks = [chunk for chunk in chunks if chunk.get("chunk_kind") == "attachment_table_row"]

        self.assertEqual(len(row_chunks), 1)
        self.assertIn("附件《接收条件表.xlsx》", str(row_chunks[0]["text"]))
        self.assertIn("第2行", str(row_chunks[0]["text"]))
        self.assertIn("college：computer science", str(row_chunks[0]["text"]))

    def test_plain_pdf_pages_are_not_forced_into_table_rows(self) -> None:
        parts = attachment_table_row_parts(
            {
                "name": "普通通知.pdf",
                "pages": [
                    {
                        "page": 1,
                        "text": "一、加强考务管理\n各院系应认真落实课程考核要求，做好监考通知和培训。",
                    }
                ],
            }
        )

        self.assertEqual(parts, [])

    def test_table_like_pdf_pages_become_row_level_parts(self) -> None:
        parts = attachment_table_row_parts(
            {
                "name": "各学院接收学生转专业信息一览表.pdf",
                "pages": [
                    {
                        "page": 2,
                        "text": "接收学院 专业名称 接收名额 考核课程\n计算机科学与工程学院 计算机科学与技术 3 计算机专业基础",
                    }
                ],
            }
        )

        self.assertTrue(parts)
        self.assertEqual(parts[0]["page"], 2)
        self.assertIn("PDF页：第2页", str(parts[0]["text"]))

    def test_legacy_office_binary_fallback_extracts_readable_text(self) -> None:
        content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "缓考申请表 proof material".encode("utf-16le")
        payload = extract_attachment_payload(content, "", "form.doc")
        self.assertIn("proof material", payload["text"])


if __name__ == "__main__":
    unittest.main()
