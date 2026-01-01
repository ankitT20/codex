# Library function notes for PDF processing approaches

## Approach 1 – PyMuPDF (`fitz`)
- `fitz.open(path)`: open the source PDF as a `Document`.
- `page.get_text("words")`: returns tuples `(x0, y0, x1, y1, word, block_no, line_no, word_no)` for every word, giving precise bounding boxes in page coordinates.
- `page.new_shape()` / `shape.draw_rect(rect)` / `shape.finish(...)`: fast path to draw rectangle outlines or filled shapes without creating annotation objects.
- `page.draw_rect(rect, color=..., fill=..., fill_opacity=..., width=...)`: convenience API to paint a single rectangle directly on the page (used for alternating highlights).
- `page.get_text("rawdict")`: returns a structured dictionary of blocks/lines/spans used to count words per line and place the `sN_cM` markers.
- `page.insert_textbox(rect, text, ...)`: writes inline text into a rectangle; used to place sentence-id labels to the right of each line.

## Approach 2 – pdfplumber + ReportLab + pypdf
- `pdfplumber.open(path)`: open the PDF for layout inspection.
- `page.extract_words()`: yields word dictionaries with `x0`, `x1`, `top`, `bottom`, and `text` keys; coordinates are converted from pdfplumber’s top-left origin to ReportLab’s bottom-left origin before drawing.
- `reportlab.pdfgen.canvas.Canvas(buffer, pagesize=...)`: creates an in-memory PDF canvas; `rect`, `drawString`, `setFillColor`, and `setStrokeColor` are used to render outlines, highlights (filled rectangles with alpha), and sentence labels.
- `pypdf.PdfReader` / `pypdf.PdfWriter`: read the source PDF and merge each overlay page via `page.merge_page(overlay_page)` before writing outputs.

## Approach 3 – pypdfium2 + ReportLab + pypdf
- `pdfium.PdfDocument(path)` / `pdf[index]`: open and access pages.
- `page.get_textpage()`: obtain a `PdfTextPage` for character-level geometry.
- `textpage.count_chars()`: number of characters on the page.
- `textpage.get_text_range(idx, 1)`: returns the single character string at the given index.
- `textpage.get_charbox(idx)`: returns `(left, bottom, right, top)` for the character at `idx`, enabling manual word grouping.
- The extracted `WordBox` items feed into the same ReportLab overlay pipeline (outlined rectangles, filled highlights, and per-line labels) that is merged back onto the source PDF with `pypdf`.
