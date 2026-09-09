"""Export the reviewed workbook using native Excel print rendering."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading

_export_lock = threading.Lock()


def export_staff_pdf(excel_path: Path) -> Path:
    source = Path(excel_path).resolve(strict=True)
    destination = source.with_suffix('.pdf')
    # Always export afresh: an older PDF must never replace a reviewed workbook.
    with _export_lock:
        with tempfile.TemporaryDirectory(prefix='pdf_export_', dir=source.parent) as tmp:
            output = Path(tmp) / destination.name
            powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            result = subprocess.run(
                [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                 'Bypass', '-File', str(Path(__file__).with_name('export_pdf.ps1')),
                 '-SourcePath', str(source), '-OutputPath', str(output)],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode or not output.is_file():
                raise RuntimeError('Excel PDF export failed; work order was not sent.')
            with output.open('rb') as document:
                if document.read(5) != b'%PDF-':
                    raise RuntimeError('Excel did not produce a valid PDF; work order was not sent.')
            output.replace(destination)
    return destination
