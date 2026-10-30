import sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf as fitz
from pypdf import PdfReader
from PIL import Image
from pdf_tools import operate, pages_from_text, estimate_size

class PDFToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'source.pdf'
        with fitz.open() as doc:
            for i in range(3):doc.new_page().insert_text((40,70),f'Original page {i+1}')
            doc.save(self.source)
        self.original=self.source.read_bytes();self.out=self.root/'output.pdf'
    def tearDown(self):
        self.assertEqual(self.source.read_bytes(),self.original);self.temp.cleanup()
    def run_op(self,mode,**kw):operate(mode,[self.source],self.out,**kw)
    def test_ranges(self):
        self.assertEqual(pages_from_text('3,1-2,1',3),[2,0,1])
        for value in ['0','4','3-1','x','1,,2']:
            with self.assertRaises(ValueError):pages_from_text(value,3)
    def test_merge_order(self):
        operate('Merge PDFs',[self.source,self.source],self.out)
        with fitz.open(self.out) as doc:
            self.assertEqual(len(doc),6);self.assertIn('page 1',doc[3].get_text())
    def test_split_ranges(self):
        self.run_op('Split PDF',pages='3,1')
        with fitz.open(self.out) as doc:
            self.assertEqual(len(doc),2);self.assertIn('page 3',doc[0].get_text())
    def test_split_zip(self):
        self.run_op('Split PDF',pages='1,3',separate=True)
        with zipfile.ZipFile(self.out) as archive:
            self.assertEqual(archive.namelist(),['page-0001.pdf','page-0003.pdf'])
            with fitz.open(stream=archive.read('page-0003.pdf'),filetype='pdf') as doc:self.assertIn('page 3',doc[0].get_text())
    def test_password_and_encrypted_input(self):
        self.run_op('Add PDF password',new_password='secret123',confirm_password='secret123')
        reader=PdfReader(self.out);self.assertTrue(reader.is_encrypted);self.assertFalse(reader.decrypt('wrong'));self.assertTrue(reader.decrypt('secret123'));self.assertEqual(len(reader.pages),3)
        encrypted=self.out;target=self.root/'compressed.pdf'
        operate('Compress PDF',[encrypted],target,password='secret123',strength=50)
        self.assertFalse(PdfReader(target).is_encrypted)
    def test_wrong_password_preserves_output(self):
        self.run_op('Add PDF password',new_password='secret',confirm_password='secret')
        target=self.root/'existing.pdf';target.write_bytes(b'existing-output')
        with self.assertRaises(ValueError):operate('Split PDF',[self.out],target,password='wrong')
        self.assertEqual(target.read_bytes(),b'existing-output')
    def test_password_confirmation(self):
        with self.assertRaises(ValueError):self.run_op('Add PDF password',new_password='a',confirm_password='b')
        self.assertFalse(self.out.exists())
    def test_delete_and_rotate(self):
        self.run_op('Edit PDF',pages='2',action='Delete pages')
        with fitz.open(self.out) as doc:self.assertEqual(len(doc),2);self.assertIn('page 3',doc[1].get_text())
        self.run_op('Edit PDF',pages='1',action='Rotate 90')
        with fitz.open(self.out) as doc:self.assertEqual(doc[0].rotation,90);self.assertEqual(doc[1].rotation,0)
    def test_text_overlay(self):
        self.run_op('Edit PDF',pages='2',action='Add text',text='Added note',x=40,y=100,font_size=14)
        with fitz.open(self.out) as doc:self.assertIn('Added note',doc[1].get_text());self.assertNotIn('Added note',doc[0].get_text());self.assertIn('Original page 2',doc[1].get_text())
    def test_invalid_edits_preserve_output(self):
        self.out.write_bytes(b'keep')
        for options in [dict(action='Delete pages'),dict(action='Add text',text='Test',x=9999),dict(action='Add text',text='Test',font_size=-1)]:
            with self.assertRaises(ValueError):self.run_op('Edit PDF',**options)
            self.assertEqual(self.out.read_bytes(),b'keep')
    def test_no_source_overwrite(self):
        with self.assertRaises(ValueError):operate('Compress PDF',[self.source],self.source)
    def test_compression_and_estimate(self):
        image=Image.effect_noise((900,900),50).convert('RGB');data=self.root/'noise.png';image.save(data)
        with fitz.open(self.source) as doc:doc[0].insert_image(fitz.Rect(20,120,500,600),filename=str(data));doc.save(self.root/'image.pdf')
        photo=self.root/'image.pdf';operate('Compress PDF',[photo],self.out,strength=100)
        self.assertLessEqual(self.out.stat().st_size,photo.stat().st_size)
        with fitz.open(self.out) as doc:self.assertIn('Original page 1',doc[0].get_text());self.assertEqual(len(doc),3)
        before,after=estimate_size(photo,100);self.assertEqual(before,photo.stat().st_size);self.assertAlmostEqual(after,self.out.stat().st_size,delta=100)

if __name__=='__main__':unittest.main()
