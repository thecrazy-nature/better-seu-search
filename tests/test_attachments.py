from __future__ import annotations

import io
import unittest
import zipfile

from backend.app.attachments import attachment_extension, extract_attachment_payload, infer_attachment_extension


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
                "<si><t>college</t></si><si><t>computer science</t></si>"
                "</sst>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row></sheetData>'
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

    def test_legacy_office_binary_fallback_extracts_readable_text(self) -> None:
        content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "缓考申请表 proof material".encode("utf-16le")
        payload = extract_attachment_payload(content, "", "form.doc")
        self.assertIn("proof material", payload["text"])


if __name__ == "__main__":
    unittest.main()
