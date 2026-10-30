"""Remove encryption from a PDF when the user supplies its current password."""

from __future__ import annotations

import os
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = PdfWriter = None


APP_TITLE = "PDF Password Remover"


def unlock_pdf(source: Path, destination: Path, password: str, replace_existing: bool = False) -> None:
    """Write an unencrypted copy of a PDF using a supplied password."""
    if PdfReader is None:
        raise RuntimeError("The pypdf package is not installed. Install it with: python -m pip install pypdf")
    if not source.is_file():
        raise ValueError("Choose an existing PDF file.")
    if source.suffix.lower() != ".pdf":
        raise ValueError("The selected input file must have a .pdf extension.")
    if source.resolve() == destination.resolve():
        raise ValueError("Choose a different output file so the original stays untouched.")
    if destination.exists() and not replace_existing:
        raise FileExistsError(f"The output file already exists:\n{destination}")

    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted:
        result = reader.decrypt(password)
        if not result:
            raise PermissionError("That password did not unlock this PDF. Check it and try again.")

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".pdf-unlock-", suffix=".tmp", dir=destination.parent, delete=False) as output_stream:
            temp_name = output_stream.name
            writer.write(output_stream)
        if destination.exists() and not replace_existing:
            raise FileExistsError(f"The output file already exists:\n{destination}")
        os.replace(temp_name, destination)
    except Exception:
        # Avoid leaving a partial temporary output after a failed write.
        try:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
