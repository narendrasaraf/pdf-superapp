import io, os, tempfile, threading, queue, traceback
from pathlib import Path
from xml.sax.saxutils import escape
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pymupdf as fitz
from PIL import Image, ImageOps, ImageSequence
import pillow_heif
from docx import Document
from docx.shared import Inches
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from unlocker import unlock_pdf
from pdf_tools import MODES, operate, estimate_size, human_size

pillow_heif.register_heif_opener()

def convert(mode, sources, target, password='', appearance=False):
    target=Path(target)
    if any(Path(s).resolve()==target.resolve() for s in sources):
        raise ValueError('Choose a new output path. The original cannot be replaced.')
    with tempfile.TemporaryDirectory(prefix='pdf-superapp-') as temp:
        out=Path(temp)/target.name
        if mode=='Remove PDF password':
            unlock_pdf(Path(sources[0]), out, password)
        elif mode in ('Images to PDF','Merge PDFs'):
            with fitz.open() as pdf:
                for name in sources:
                    with Image.open(name) as img:
                        for frame in ImageSequence.Iterator(img):
                            frame=ImageOps.exif_transpose(frame.copy()).convert('RGBA')
                            white=Image.new('RGB',frame.size,'white'); white.paste(frame,mask=frame.getchannel('A'))
                            buffer=io.BytesIO(); white.save(buffer,format='PNG')
                            w,h=white.size
                            scale=min(540/w,770/h)
                            page=pdf.new_page(width=612,height=842)
                            x=(612-w*scale)/2; y=(842-h*scale)/2
                            page.insert_image(fitz.Rect(x,y,x+w*scale,y+h*scale),stream=buffer.getvalue())
                pdf.save(out,deflate=True)
        elif mode=='PDF to Word':
            doc=Document()
            with fitz.open(sources[0]) as pdf:
                if pdf.needs_pass and not pdf.authenticate(password):
                    raise ValueError('Enter the current PDF password.')
                for index,page in enumerate(pdf):
                    if index: doc.add_page_break()
                    if appearance:
                        doc.add_picture(io.BytesIO(page.get_pixmap(matrix=fitz.Matrix(1.5,1.5)).tobytes('png')),width=Inches(6))
                    else:
                        blocks=page.get_text('dict',sort=True)['blocks']
                        has_text=False
                        for block in blocks:
                            if block['type']==0:
                                text='\n'.join(''.join(s['text'] for s in line['spans']) for line in block['lines'])
                                if text.strip(): doc.add_paragraph(text); has_text=True
                            elif block['type']==1:
                                doc.add_picture(io.BytesIO(block['image']),width=Inches(min(6,block['width']/96)))
                        if not has_text and not any(b['type']==1 for b in blocks):
                            doc.add_picture(io.BytesIO(page.get_pixmap().tobytes('png')),width=Inches(6))
            doc.save(out)
        else:
            styles=getSampleStyleSheet(); story=[]
            source=Path(sources[0])
            if source.suffix.lower()=='.txt':
                for line in source.read_text(encoding='utf-8-sig').splitlines():
                    story.append(Paragraph(escape(line) or ' ',styles['Normal'])); story.append(Spacer(1,5))
            else:
                from docx.text.paragraph import Paragraph as DParagraph
                from docx.table import Table as DTable
                document=Document(source)
                for child in document.element.body:
                    if child.tag.endswith('}p'):
                        p=DParagraph(child,document)
                        name=p.style.name if p.style else ''
                        style=styles['Heading1'] if name.startswith('Heading') else styles['Normal']
                        if p.text: story.extend([Paragraph(escape(p.text).replace('\n','<br/>'),style),Spacer(1,7)])
                        for blip in child.xpath('.//a:blip'):
                            rid=blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                            if rid:
                                data=document.part.related_parts[rid].blob
                                picture=RLImage(io.BytesIO(data)); ratio=min(1,468/picture.imageWidth,650/picture.imageHeight)
                                picture.drawWidth=picture.imageWidth*ratio; picture.drawHeight=picture.imageHeight*ratio
                                story.extend([picture,Spacer(1,8)])
                    elif child.tag.endswith('}tbl'):
                        table=DTable(child,document)
                        rows=[[Paragraph(escape(cell.text),styles['Normal']) for cell in row.cells] for row in table.rows]
                        if rows:
                            rendered=Table(rows,colWidths=[468/len(rows[0])]*len(rows[0]))
                            rendered.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP')]))
                            story.extend([rendered,Spacer(1,10)])
            SimpleDocTemplate(str(out)).build(story or [Paragraph(' ',styles['Normal'])])
        # Stage on the destination volume, then replace only after conversion succeeds.
        import shutil
        fd, staging=tempfile.mkstemp(prefix='.pdf-superapp-',dir=target.parent)
        os.close(fd)
        try:
            shutil.copyfile(out,staging); os.replace(staging,target)
        finally:
            if os.path.exists(staging): os.unlink(staging)

class App(tk.Tk):
    TOOLS = {
        'Compress PDF': ('', 'Smaller files, adjustable quality', 'PDF documents', 'Strength 1–100% controls image compression, not a guaranteed size reduction. Higher values can reduce image quality. Estimate size runs a local trial. Output is unencrypted.'),
        'Merge PDFs': ('', 'Combine PDFs in your chosen order', 'Two or more PDF documents', 'Add PDFs and reorder them before merging. Encrypted inputs must share the current password, or be unlocked first. Output is unencrypted.'),
        'Add PDF password': ('', 'Protect your PDF with AES-256', 'PDF documents', 'Set a new password required to open the output. Keep it somewhere safe; the app cannot recover it.'),
        'Split PDF': ('', 'Extract pages or save each one separately', 'PDF documents', 'Enter ranges such as 1-3,5. Blank selects every page. Save a single extracted PDF or a ZIP containing one PDF per selected page. Output is unencrypted.'),
        'Edit PDF': ('', 'Add text, rotate or delete pages', 'PDF documents', 'Basic editing creates a new copy. Add text overlays; it does not replace or redact existing text. Positions use points from the unrotated page’s top-left. Output is unencrypted.'),
        'Word to PDF': ('W → P', 'Turn documents into PDFs', 'DOCX and TXT', 'Basic text, tables and inline images. Complex layouts, headers and fonts may change. Legacy .doc is not supported.'),
        'PDF to Word': ('P → W', 'Make your PDF editable', 'PDF documents', 'Extract text and images into DOCX. Complex layouts may change. Scanned text remains an image; OCR is not included.'),
        'Images to PDF': ('▧ → P', 'Bring your images together', 'JPG, PNG, HEIC, WebP, TIFF and more', 'Images become pages in the order shown. All animation and TIFF frames are included. RAW, SVG and proprietary formats may not be supported.'),
        'Remove PDF password': ('◇', 'Unlock a copy of your PDF', 'Password-protected PDFs', 'Use the current password to save an unlocked copy. This tool does not recover forgotten passwords.')}
    def __init__(self):
        super().__init__()
        self.title('PDF Superapp'); self.geometry('1080x780'); self.minsize(980,740)
        self.configure(bg='#F5F6FA'); self.files=[]; self.events=queue.Queue(); self.busy=False; self.last_output=None
        self.mode=tk.StringVar(value='Word to PDF'); self.password=tk.StringVar(); self.appearance=tk.BooleanVar(); self.show_password=tk.BooleanVar()
        assets=Path(__file__).resolve().parent
        self.logo=tk.PhotoImage(file=str(assets/'logo.png')); self.iconphoto(True,self.logo)
        self.small_logo=self.logo.subsample(5,5)
        style=ttk.Style(self); style.theme_use('clam')
        style.configure('Accent.Horizontal.TProgressbar',background='#6554E8',troughcolor='#EBE8FF',borderwidth=0)
        style.configure('Card.TCheckbutton',background='#FFFFFF',foreground='#334155',font=('Segoe UI',10))
        style.map('Card.TCheckbutton',background=[('active','#FFFFFF')])
        self.columnconfigure(1,weight=1); self.rowconfigure(0,weight=1)
        side=tk.Frame(self,bg='#151A30',width=226);side.grid(row=0,column=0,sticky='ns');side.grid_propagate(False)
        tk.Label(side,image=self.small_logo,bg='#151A30').pack(anchor='w',padx=24,pady=(28,12))
        self.label(side,'PDF Superapp',18,'#FFFFFF','#151A30',True).pack(anchor='w',padx=24)
        self.label(side,'A little less paperwork.',10,'#9DA7C1','#151A30').pack(anchor='w',padx=24,pady=(5,18))
        self.label(side,'YOUR TOOLS',9,'#8390B0','#151A30',True).pack(anchor='w',padx=24,pady=(0,10))
        self.nav={}
        for mode in self.TOOLS:
            b=tk.Button(side,text=mode,anchor='w',font=('Segoe UI',11),relief='flat',bd=0,padx=16,pady=7,cursor='hand2',command=lambda m=mode:self.select_mode(m))
            b.pack(fill='x',padx=12,pady=1);self.nav[mode]=b
        foot=tk.Frame(side,bg='#151A30');foot.pack(side='bottom',fill='x',padx=24,pady=28)
        self.label(foot,'●  Entirely on your computer',9,'#91DFBB','#151A30').pack(anchor='w')
        self.label(foot,'No uploads. No sign-in.',9,'#9DA7C1','#151A30').pack(anchor='w',pady=(6,0))
        holder=tk.Frame(self,bg='#F5F6FA');holder.grid(row=0,column=1,sticky='nsew')
        canvas=tk.Canvas(holder,bg='#F5F6FA',highlightthickness=0)
        scrollbar=ttk.Scrollbar(holder,orient='vertical',command=canvas.yview);scrollbar.pack(side='right',fill='y')
        canvas.pack(side='left',fill='both',expand=True);canvas.configure(yscrollcommand=scrollbar.set)
        main=tk.Frame(canvas,bg='#F5F6FA',padx=26,pady=24);window=canvas.create_window((0,0),window=main,anchor='nw')
        main.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        self.bind_all('<MouseWheel>',lambda e:canvas.yview_scroll(int(-e.delta/120),'units') if e.widget.winfo_class() not in ('Scale','TCombobox') else None)
        main.columnconfigure(0,weight=1);main.rowconfigure(2,weight=1)
        header=tk.Frame(main,bg='#F5F6FA');header.grid(row=0,column=0,sticky='ew',pady=(0,18))
        self.label(header,'WORKSPACE',9,'#7C8296','#F5F6FA',True).pack(anchor='w')
        self.title_label=self.label(header,'',25,'#20243D','#F5F6FA',True);self.title_label.pack(anchor='w',pady=(7,5))
        self.subtitle=self.label(header,'',11,'#737B91','#F5F6FA');self.subtitle.pack(anchor='w')
        self.hint=self.label(main,'',10,'#686B84','#F5F6FA');self.hint.configure(wraplength=720,justify='left');self.hint.grid(row=1,column=0,sticky='ew',pady=(0,16))
        card=tk.Frame(main,bg='white',highlightbackground='#E3E6EE',highlightthickness=1,padx=22,pady=18);card.grid(row=2,column=0,sticky='nsew')
        card.columnconfigure(0,weight=1);card.rowconfigure(1,weight=1)
        top=tk.Frame(card,bg='white');top.grid(row=0,column=0,sticky='ew',pady=(0,16))
        self.label(top,'01   Add your files',12,'#262B43','white',True).pack(side='left')
        self.count=self.label(top,'',10,'#7C8296','white');self.count.pack(side='right')
        self.well=tk.Frame(card,bg='#F8F9FD',highlightbackground='#E3E6F0',highlightthickness=1);self.well.grid(row=1,column=0,sticky='nsew')
        self.empty=tk.Frame(self.well,bg='#F8F9FD');self.empty.pack(expand=True,fill='both',padx=12,pady=16)
        self.label(self.empty,'＋',28,'#6554E8','#F8F9FD').pack(pady=(4,0))
        self.label(self.empty,'Start with a file',14,'#343A54','#F8F9FD',True).pack(pady=(2,6))
        self.format_label=self.label(self.empty,'',10,'#737B91','#F8F9FD');self.format_label.pack()
        self.browse_empty=self.button(self.empty,'Browse files',self.choose,primary=True);self.browse_empty.pack(pady=(16,4))
        self.listbox=tk.Listbox(self.well,bg='#F8F9FD',fg='#31384F',selectbackground='#E5DFFF',selectforeground='#493BA6',font=('Segoe UI',11),bd=0,highlightthickness=0,activestyle='none',height=4,exportselection=False)
        self.filebar=tk.Frame(card,bg='white');self.filebar.grid(row=2,column=0,sticky='ew',pady=(12,0))
        self.add_btn=self.button(self.filebar,'+ Add files',self.choose);self.add_btn.pack(side='left')
        self.up_btn=self.button(self.filebar,'Move up',lambda:self.move(-1));self.up_btn.pack(side='left',padx=(8,0))
        self.down_btn=self.button(self.filebar,'Move down',lambda:self.move(1));self.down_btn.pack(side='left',padx=(8,0))
        self.remove_btn=self.button(self.filebar,'Remove',self.remove);self.remove_btn.pack(side='right')
        self.options=tk.Frame(main,bg='white',highlightbackground='#E3E6EE',highlightthickness=1,padx=22,pady=16);self.options.grid(row=3,column=0,sticky='ew',pady=(16,0))
        self.label(self.options,'02   Conversion options',12,'#262B43','white',True).pack(anchor='w',pady=(0,10))
        self.passrow=tk.Frame(self.options,bg='white')
        self.label(self.passrow,'Current PDF password',10,'#666E85','white').pack(anchor='w',pady=(0,6))
        r=tk.Frame(self.passrow,bg='white');r.pack(fill='x')
        self.entry=tk.Entry(r,textvariable=self.password,show='•',font=('Segoe UI',11),relief='solid',bd=1,bg='#F8F9FD',fg='#20243D');self.entry.pack(side='left',fill='x',expand=True,ipady=7)
        ttk.Checkbutton(r,text='Show',variable=self.show_password,command=lambda:self.entry.configure(show='' if self.show_password.get() else '•'),style='Card.TCheckbutton').pack(side='left',padx=(14,0))
        self.appearance_check=ttk.Checkbutton(self.options,text='Preserve page appearance as images (text will not be editable)',variable=self.appearance,style='Card.TCheckbutton')
        self.default_option=self.label(self.options,'Ready to convert. You’ll choose where to save the result.',10,'#737B91','white')
        self.extra=tk.Frame(self.options,bg='white')
        self.strength=tk.IntVar(value=50);self.estimate_text=tk.StringVar(value='Select a PDF, then estimate its compressed size.')
        self.new_password=tk.StringVar();self.confirm_password=tk.StringVar();self.pages=tk.StringVar()
        self.separate=tk.BooleanVar();self.action=tk.StringVar(value='Add text');self.edit_text=tk.StringVar()
        self.x=tk.StringVar(value='36');self.y=tk.StringVar(value='36');self.font_size=tk.StringVar(value='14')
        bottom=tk.Frame(main,bg='#F5F6FA');bottom.grid(row=4,column=0,sticky='ew',pady=(18,0))
        self.progress=ttk.Progressbar(bottom,mode='indeterminate',style='Accent.Horizontal.TProgressbar');self.progress.pack(fill='x',pady=(0,12))
        self.convert_btn=self.button(bottom,'Convert and save  →',self.run,primary=True);self.convert_btn.pack(side='right')
        self.open_btn=self.button(bottom,'Open output folder',self.open_folder)
        self.status=self.label(main,'Ready when you are.',10,'#737B91','#F5F6FA');self.status.configure(wraplength=720,justify='left');self.status.grid(row=5,column=0,sticky='ew',pady=(12,0))
        self.protocol('WM_DELETE_WINDOW',self.close);self.select_mode('Word to PDF');self.after(100,self.poll)
    def field(self,parent,label,variable,secret=False,width=None):
        self.label(parent,label,10,'#666E85','white').pack(anchor='w',pady=(7,4))
        entry=ttk.Entry(parent,textvariable=variable,show='•' if secret else '',width=width)
        entry.pack(fill='x',ipady=4)
        return entry
    def build_options(self,mode):
        for widget in self.extra.winfo_children():widget.destroy()
        if mode not in MODES:return
        self.extra.pack(fill='x',pady=(8,0))
        if mode=='Compress PDF':
            self.label(self.extra,'Compression strength: 1% gentle  →  100% strongest',10,'#666E85','white').pack(anchor='w')
            tk.Scale(self.extra,from_=1,to=100,orient='horizontal',variable=self.strength,bg='white',fg='#6554E8',highlightthickness=0,command=lambda v:self.estimate_text.set('Settings changed. Estimate again.')).pack(fill='x')
            self.button(self.extra,'Estimate output size',self.estimate).pack(anchor='w',pady=6)
            ttk.Label(self.extra,textvariable=self.estimate_text,wraplength=650).pack(anchor='w')
        elif mode=='Add PDF password':
            self.field(self.extra,'New password',self.new_password,True)
            self.field(self.extra,'Confirm new password',self.confirm_password,True)
        elif mode in ('Split PDF','Edit PDF'):
            self.field(self.extra,'Pages: e.g. 1-3,5  (blank = all pages)',self.pages)
            if mode=='Split PDF':
                ttk.Checkbutton(self.extra,text='Save each selected page separately in a ZIP',variable=self.separate,style='Card.TCheckbutton').pack(anchor='w',pady=10)
            else:
                self.label(self.extra,'Editing action',10,'#666E85','white').pack(anchor='w',pady=(8,4))
                combo=ttk.Combobox(self.extra,textvariable=self.action,state='readonly',values=['Add text','Rotate 90','Rotate 180','Rotate 270','Delete pages']);combo.pack(fill='x')
                text_options=tk.Frame(self.extra,bg='white')
                self.field(text_options,'Text to add (Latin-1 characters)',self.edit_text)
                row=tk.Frame(text_options,bg='white');row.pack(fill='x')
                for label,var in [('X position',self.x),('Y position',self.y),('Font size',self.font_size)]:
                    part=tk.Frame(row,bg='white');part.pack(side='left',fill='x',expand=True,padx=(0,8));self.field(part,label+' (pt)',var,width=12)
                def update(event=None):
                    text_options.pack_forget()
                    if self.action.get()=='Add text':text_options.pack(fill='x')
                combo.bind('<<ComboboxSelected>>',update);update()
        elif mode=='Merge PDFs':
            self.label(self.extra,'All pages are merged in the file order shown above.',10,'#666E85','white').pack(anchor='w')
    def operation_options(self):
        return dict(strength=self.strength.get(),new_password=self.new_password.get(),confirm_password=self.confirm_password.get(),pages=self.pages.get(),separate=self.separate.get() if self.mode.get()=='Split PDF' else False,action=self.action.get(),text=self.edit_text.get(),x=self.x.get(),y=self.y.get(),font_size=self.font_size.get())
    def estimate(self):
        if self.busy or not self.files:return
        source=self.files[0];strength=self.strength.get();password=self.password.get()
        self.busy=True;self.refresh();self.progress.start(12);self.estimate_text.set('Estimating locally…');self.status.configure(text='Running a temporary compression trial…')
        def worker():
            try:
                before,after=estimate_size(source,strength,password)
                result=f'{strength}% strength: {human_size(before)} → about {human_size(after)} ({max(0,100*(1-after/before)):.1f}% smaller)'
            except Exception as exc:result='Estimate failed: '+str(exc)
            self.events.put(('estimate',(strength,result)))
        threading.Thread(target=worker,daemon=True).start()
    def label(self,parent,text,size,fg,bg,bold=False):
        return tk.Label(parent,text=text,font=('Segoe UI Semibold' if bold else 'Segoe UI',size),fg=fg,bg=bg,anchor='w',bd=0)
    def button(self,parent,text,command,primary=False):
        bg='#6554E8' if primary else '#F0EFF7';fg='white' if primary else '#57516F'
        return tk.Button(parent,text=text,command=command,bg=bg,fg=fg,activebackground='#5040C8' if primary else '#E3DFF2',activeforeground='white' if primary else '#403756',disabledforeground='#A9A3C4',font=('Segoe UI Semibold',10),relief='flat',bd=0,padx=17,pady=10,cursor='hand2')
    def select_mode(self,mode):
        if self.busy:return
        self.new_password.set('');self.confirm_password.set('');self.pages.set('');self.estimate_text.set('Select a PDF, then estimate its compressed size.');self.mode.set(mode);self.files=[];self.password.set('');self.appearance.set(False);self.show_password.set(False);self.entry.configure(show='•')
        _,title,formats,hint=self.TOOLS[mode]
        self.title_label.configure(text=mode);self.subtitle.configure(text=title);self.hint.configure(text=hint);self.format_label.configure(text=formats)
        for name,b in self.nav.items():b.configure(bg='#343054' if name==mode else '#151A30',fg='#FFFFFF' if name==mode else '#ABB5CF',activebackground='#343054',activeforeground='white')
        for widget in [self.passrow,self.appearance_check,self.default_option,self.extra]:widget.pack_forget()
        if mode in MODES or mode in ('PDF to Word','Remove PDF password'):self.passrow.pack(fill='x',pady=(0,4))
        else:self.default_option.pack(anchor='w')
        self.build_options(mode)
        if mode=='PDF to Word':self.appearance_check.pack(anchor='w',pady=(10,0))
        for b in (self.up_btn,self.down_btn):b.pack_forget()
        if mode in ('Images to PDF','Merge PDFs'):
            self.up_btn.pack(side='left',padx=(8,0));self.down_btn.pack(side='left',padx=(8,0))
        self.refresh();self.status.configure(text='Ready when you are.',fg='#737B91')
    def refresh(self):
        self.listbox.delete(0,'end')
        for i,f in enumerate(self.files):self.listbox.insert('end',f'  {i+1:02d}   {Path(f).name}')
        self.count.configure(text=f'{len(self.files)} file'+('s' if len(self.files)!=1 else ''))
        self.listbox.pack_forget();self.empty.pack_forget()
        if self.files:self.listbox.pack(fill='both',expand=True,padx=12,pady=12)
        else:self.empty.pack(expand=True,fill='both',padx=12,pady=16)
        self.convert_btn.configure(state='normal' if self.files and not self.busy else 'disabled')
    def choose(self):
        if self.busy:return
        mode=self.mode.get()
        types=[('Documents','*.docx *.txt')] if mode=='Word to PDF' else [('PDF','*.pdf')] if mode!='Images to PDF' else [('Images','*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.gif *.webp *.heic *.heif *.avif *.ico *.ppm *.pgm *.pcx *.tga'),('All files','*.*')]
        files=filedialog.askopenfilenames(filetypes=types) if mode in ('Images to PDF','Merge PDFs') else [filedialog.askopenfilename(filetypes=types)]
        files=[f for f in files if f]
        if files:self.files.extend(files) if mode in ('Images to PDF','Merge PDFs') else self.files.__setitem__(slice(None),files);self.refresh()
    def move(self,direction):
        if self.busy:return
        selection=self.listbox.curselection()
        if selection:
            i=selection[0];j=i+direction
            if 0<=j<len(self.files):self.files[i],self.files[j]=self.files[j],self.files[i];self.refresh();self.listbox.selection_set(j)
    def remove(self):
        if self.busy:return
        for i in reversed(self.listbox.curselection()):del self.files[i]
        self.refresh()
    def run(self):
        if self.busy or not self.files:return
        if self.mode.get()=='Edit PDF' and self.action.get()=='Delete pages' and not messagebox.askyesno('Delete selected pages?', 'Remove the selected pages from the new output copy? The source stays unchanged.'):return
        ext='.zip' if self.mode.get()=='Split PDF' and self.separate.get() else '.docx' if self.mode.get()=='PDF to Word' else '.pdf'
        target=filedialog.asksaveasfilename(defaultextension=ext,initialfile=Path(self.files[0]).stem+'_converted'+ext,filetypes=[('Output','*'+ext)])
        if not target:return
        args=(self.mode.get(),list(self.files),target,self.password.get(),self.appearance.get())
        options=self.operation_options()
        self.busy=True;self.refresh();self.progress.start(12);self.status.configure(text='Converting on your computer…',fg='#6554E8');self.open_btn.pack_forget()
        def worker():
            try:
                if args[0] in MODES:operate(args[0],args[1],args[2],args[3],**options)
                else:convert(*args)
                self.events.put((True,target))
            except Exception as exc:self.events.put((False,str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    def poll(self):
        try:
            ok,text=self.events.get_nowait();self.busy=False;self.progress.stop();self.refresh()
            if ok=='estimate':
                strength, result=text
                self.estimate_text.set(result if strength==self.strength.get() else 'Strength changed. Estimate again.')
                self.status.configure(text='Estimate finished. Choose Convert and save to create the PDF.',fg='#737B91')
                self.after(100,self.poll);return
            self.password.set('');self.new_password.set('');self.confirm_password.set('')
            if ok:
                self.last_output=text;self.status.configure(text='Saved successfully: '+Path(text).name+' ('+human_size(Path(text).stat().st_size)+')',fg='#21805B');self.open_btn.pack(side='left')
            else:self.status.configure(text='Conversion failed. Check the file and try again.',fg='#B24355');messagebox.showerror('Conversion failed',text)
        except queue.Empty:pass
        self.after(100,self.poll)
    def open_folder(self):
        if self.last_output:os.startfile(str(Path(self.last_output).parent))
    def close(self):
        if self.busy:messagebox.showinfo('Conversion in progress','Please let the conversion finish before closing.');return
        self.destroy()

if __name__=='__main__':App().mainloop()
