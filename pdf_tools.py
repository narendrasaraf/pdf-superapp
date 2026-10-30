"""Local PDF operations. Never modify the source files."""
import io
import os
import secrets
import shutil
import tempfile
import zipfile
from pathlib import Path

import pymupdf as fitz
from pypdf import PdfReader, PdfWriter

MODES = {'Compress PDF', 'Merge PDFs', 'Add PDF password', 'Split PDF', 'Edit PDF'}


def pages_from_text(text, count):
    if not text.strip():
        return list(range(count))
    pages = []
    for token in text.split(','):
        parts = token.strip().split('-')
        if len(parts) not in (1, 2) or not all(p.strip().isdigit() for p in parts):
            raise ValueError('Use page numbers like 1-3,5,7. Blank means all pages.')
        first, last = int(parts[0]), int(parts[-1])
        if first < 1 or last < first or last > count:
            raise ValueError(f'Page range must be between 1 and {count}, in ascending order.')
        for number in range(first - 1, last):
            if number not in pages:
                pages.append(number)
    return pages


def open_pdf(path, password):
    doc = fitz.open(path)
    if not doc.is_pdf:
        doc.close()
        raise ValueError('Choose a PDF document.')
    if doc.needs_pass and not doc.authenticate(password):
        doc.close()
        raise ValueError(f'Current password is required or incorrect for {Path(path).name}.')
    if not len(doc):
        doc.close()
        raise ValueError('This PDF has no pages.')
    return doc


def save_pdf(doc, target):
    doc.save(str(target), garbage=4, deflate=True, use_objstms=1,
             encryption=fitz.PDF_ENCRYPT_NONE)


def compress_pdf(source, output, strength, password):
    strength = int(strength)
    if not 1 <= strength <= 100:
        raise ValueError('Compression strength must be between 1 and 100.')
    with open_pdf(source, password) as doc:
        encrypted = bool(doc.metadata.get('encryption'))
        # Optimize first; keep this candidate if image recompression makes it bigger.
        save_pdf(doc, output)
        quality = round(95 - (strength - 1) * 65 / 99)
        dpi = round(300 - (strength - 1) * 228 / 99)
        doc.rewrite_images(dpi_threshold=dpi + 20, dpi_target=dpi,
                           quality=quality, bitonal=False)
        image_candidate = Path(output).with_suffix('.candidate.pdf')
        save_pdf(doc, image_candidate)
        if image_candidate.stat().st_size < Path(output).stat().st_size:
            os.replace(image_candidate, output)
        else:
            image_candidate.unlink()
        # Already optimized unencrypted PDFs should not grow.
        if not encrypted and Path(source).stat().st_size < Path(output).stat().st_size:
            shutil.copyfile(source, output)


def operate(mode, sources, target, password='', **options):
    target = Path(target)
    if any(Path(s).resolve() == target.resolve() for s in sources):
        raise ValueError('Choose a new output path; source files cannot be overwritten.')
    if not sources:
        raise ValueError('Choose at least one PDF.')
    with tempfile.TemporaryDirectory(prefix='pdf-superapp-') as folder:
        output = Path(folder) / ('output.zip' if options.get('separate') else 'output.pdf')
        if mode == 'Compress PDF':
            compress_pdf(sources[0], output, options.get('strength', 50), password)
        elif mode == 'Merge PDFs':
            if len(sources) < 2:
                raise ValueError('Choose at least two PDFs to merge.')
            with fitz.open() as merged:
                for source in sources:
                    with open_pdf(source, password) as part:
                        merged.insert_pdf(part)
                save_pdf(merged, output)
        elif mode == 'Add PDF password':
            new = options.get('new_password', '')
            if not new:
                raise ValueError('Enter a new password.')
            if new != options.get('confirm_password'):
                raise ValueError('The new passwords do not match.')
            if len(new.encode('utf-8')) > 127:
                raise ValueError('Use a password of at most 127 UTF-8 bytes.')
            reader = PdfReader(sources[0])
            if reader.is_encrypted and not reader.decrypt(password):
                raise ValueError('The current PDF password is incorrect.')
            writer = PdfWriter()
            writer.clone_document_from_reader(reader)
            writer.encrypt(new, owner_password=secrets.token_urlsafe(32), algorithm='AES-256')
            writer.write(output)
        elif mode in ('Split PDF', 'Edit PDF'):
            with open_pdf(sources[0], password) as doc:
                selected = pages_from_text(options.get('pages', ''), len(doc))
                if mode == 'Split PDF':
                    if options.get('separate'):
                        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                            for index in selected:
                                with fitz.open() as part:
                                    part.insert_pdf(doc, from_page=index, to_page=index)
                                    archive.writestr(f'page-{index + 1:04}.pdf', part.tobytes(garbage=4, deflate=True))
                    else:
                        with fitz.open() as part:
                            for index in selected:
                                part.insert_pdf(doc, from_page=index, to_page=index)
                            save_pdf(part, output)
                else:
                    action = options.get('action', 'Add text')
                    if action == 'Delete pages':
                        if len(selected) == len(doc):
                            raise ValueError('At least one page must remain after deleting pages.')
                        for index in sorted(selected, reverse=True):
                            doc.delete_page(index)
                    elif action.startswith('Rotate '):
                        angle = int(action.split()[1])
                        for index in selected:
                            doc[index].set_rotation((doc[index].rotation + angle) % 360)
                    elif action == 'Add text':
                        text = options.get('text', '')
                        if not text.strip():
                            raise ValueError('Enter the text to add.')
                        try:
                            text.encode('latin-1')
                        except UnicodeEncodeError:
                            raise ValueError('Basic text editing currently supports Latin-1 characters only.')
                        x, y, size = (float(options.get(k, v)) for k, v in [('x', 36), ('y', 36), ('font_size', 14)])
                        if not 4 <= size <= 144 or x < 0 or y < 0:
                            raise ValueError('Use non-negative positions and a font size between 4 and 144 points.')
                        for index in selected:
                            page = doc[index]
                            rect = page.rect * page.derotation_matrix
                            box = fitz.Rect(x, y, rect.width - 18, rect.height - 18)
                            if box.is_empty or page.insert_textbox(box, text, fontsize=size, fontname='helv') < 0:
                                raise ValueError(f'Text does not fit on page {index + 1}. Change its position or size.')
                    else:
                        raise ValueError('Unknown editing action.')
                    save_pdf(doc, output)
        else:
            raise ValueError('Unknown PDF operation.')
        fd, staging = tempfile.mkstemp(prefix='.pdf-superapp-', dir=target.parent)
        os.close(fd)
        try:
            shutil.copyfile(output, staging)
            os.replace(staging, target)
        finally:
            if os.path.exists(staging):
                os.unlink(staging)


def estimate_size(source, strength, password=''):
    """Measure a disposable trial conversion, rather than promise a reduction ratio."""
    with tempfile.TemporaryDirectory(prefix='pdf-superapp-estimate-') as folder:
        output = Path(folder) / 'estimate.pdf'
        compress_pdf(source, output, strength, password)
        return Path(source).stat().st_size, output.stat().st_size


def human_size(size):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:,.1f} {unit}'
        size /= 1024
