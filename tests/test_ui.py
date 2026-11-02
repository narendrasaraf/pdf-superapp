import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from superapp import App

class ContextualUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=App();cls.app.geometry('980x740');cls.app.update()
    @classmethod
    def tearDownClass(cls):cls.app.destroy()
    def select(self,mode):self.app.select_mode(mode);self.app.update()
    def test_current_password_only_in_remover(self):
        for mode in self.app.TOOLS:
            with self.subTest(mode=mode):
                self.select(mode)
                self.assertEqual(bool(self.app.passrow.winfo_ismapped()),mode=='Remove PDF password')
    def test_no_empty_options_panels(self):
        for mode in self.app.TOOLS:
            with self.subTest(mode=mode):
                self.select(mode)
                self.assertEqual(bool(self.app.options.winfo_ismapped()),mode not in ('Merge PDFs','Word to PDF','Images to PDF'))
                self.assertNotIn('Conversion options',self.app.options_title.cget('text'))
    def test_scroll_reset_and_fixed_action(self):
        self.select('Edit PDF');self.app.canvas.yview_moveto(1);self.app.update()
        self.select('Compress PDF')
        self.assertEqual(self.app.canvas.yview()[0],0)
        bottom=self.app.convert_btn.winfo_rooty()+self.app.convert_btn.winfo_height()
        self.assertLessEqual(bottom,self.app.winfo_rooty()+self.app.winfo_height())
        self.assertFalse(self.app.progress.winfo_ismapped())
    def test_merge_needs_two_inputs(self):
        self.select('Merge PDFs');self.app.files=['one.pdf'];self.app.refresh()
        self.assertEqual(self.app.convert_btn.cget('state'),'disabled')
        self.app.files.append('two.pdf');self.app.refresh()
        self.assertEqual(self.app.convert_btn.cget('state'),'normal')

if __name__=='__main__':unittest.main()
