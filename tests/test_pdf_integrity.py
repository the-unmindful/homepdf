import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import fitz
from pdf_ultimate.core.pdf_tools import PdfToolkit, ProtectOptions


class PdfIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.pdf'
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page()
            page.insert_text((50, 50), f'Original page {i+1}')
        doc.set_metadata({'title': 'Original title', 'author': 'Author'})
        doc.set_toc([[1, 'First', 1], [1, 'Last', 3]])
        page = doc[0]
        page.add_text_annot((100, 100), 'Keep annotation')
        page.insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(50, 60, 150, 80), 'page': 2})
        field = fitz.Widget()
        field.field_name = 'name'
        field.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        field.rect = fitz.Rect(50, 120, 180, 150)
        field.field_value = 'Alice'
        page.add_widget(field)
        doc.save(self.source)
        doc.close()
        self.original_bytes = self.source.read_bytes()
        self.toolkit = PdfToolkit(self.root / 'outputs')

    def assert_properties(self, path, expected_toc):
        with fitz.open(path) as doc:
            self.assertEqual(doc.metadata['title'], 'Original title')
            self.assertEqual(doc.metadata['author'], 'Author')
            self.assertEqual(doc.get_toc(), expected_toc)
        self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_rotate_preserves_document_and_interactive_content(self):
        path = self.toolkit.rotate_pages(self.source, '2', 90, self.root / 'rotated.pdf')
        self.assert_properties(path, [[1, 'First', 1], [1, 'Last', 3]])
        with fitz.open(path) as doc:
            self.assertEqual(doc[1].rotation, 90)
            self.assertEqual(next(doc[0].widgets()).field_value, 'Alice')
            self.assertEqual(next(doc[0].annots()).info['content'], 'Keep annotation')
            self.assertEqual(doc[0].get_links()[0]['page'], 2)

    def test_delete_remaps_bookmarks_and_internal_link(self):
        path = self.toolkit.delete_pages(self.source, '2', self.root / 'deleted.pdf')
        self.assert_properties(path, [[1, 'First', 1], [1, 'Last', 2]])
        with fitz.open(path) as doc:
            self.assertEqual(doc[0].get_links()[0]['page'], 1)
            self.assertEqual(next(doc[0].widgets()).field_value, 'Alice')

    def test_reorder_duplicates_preserves_metadata_and_remaps_outline(self):
        path = self.toolkit.reorder_pages(self.source, '3,1,1', self.root / 'reordered.pdf')
        self.assert_properties(path, [[1, 'First', 2], [1, 'Last', 1]])
        with fitz.open(path) as doc:
            self.assertIn('Original page 3', doc[0].get_text())
            self.assertIn('Original page 1', doc[1].get_text())
            self.assertIn('Original page 1', doc[2].get_text())

    def test_extract_preserves_selected_outline(self):
        path = self.toolkit.extract_pages(self.source, '1,3', self.root / 'extract.pdf')
        self.assert_properties(path, [[1, 'First', 1], [1, 'Last', 2]])

    def test_repeated_split_does_not_replace_existing_output(self):
        first = self.toolkit.split_every(self.source, 1, self.root / 'split')
        first[0].write_bytes(b'Keep prior result')
        second = self.toolkit.split_every(self.source, 1, self.root / 'split')
        self.assertEqual(first[0].read_bytes(), b'Keep prior result')
        self.assertNotEqual(first[0], second[0])
        with fitz.open(second[0]) as doc:
            self.assertEqual(doc.page_count, 1)

    def test_existing_output_and_source_are_never_overwritten(self):
        output = self.root / 'extract.pdf'
        output.write_bytes(b'Keep me')
        actual = self.toolkit.extract_pages(self.source, '1', output)
        self.assertEqual(output.read_bytes(), b'Keep me')
        self.assertNotEqual(actual, output)
        actual2 = self.toolkit.rotate_pages(self.source, '1', 90, self.source)
        self.assertNotEqual(actual2, self.source)
        self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_password_roundtrip_preserves_forms_and_outline(self):
        encrypted = self.toolkit.protect(self.source, 'secret', None, ProtectOptions(), self.root / 'protected.pdf')
        unlocked = self.toolkit.unlock(encrypted, 'secret', self.root / 'unlocked.pdf')
        self.assert_properties(unlocked, [[1, 'First', 1], [1, 'Last', 3]])
        with fitz.open(unlocked) as doc:
            self.assertEqual(next(doc[0].widgets()).field_value, 'Alice')

    def test_rtf_import_rejects_raw_syntax(self):
        rtf = self.source.with_suffix('.rtf')
        rtf.write_text(r'{\rtf1 Hello}')
        with self.assertRaisesRegex(Exception, "RTF"):
            self.toolkit.convert_to_pdf([rtf], self.source.parent / 'rtf.pdf')

    def test_default_names_are_distinct_even_in_same_second(self):
        self.assertNotEqual(self.toolkit.default_output_path(self.source, 'rotate'),
                            self.toolkit.default_output_path(self.source, 'rotate'))


if __name__ == '__main__':
    unittest.main()
