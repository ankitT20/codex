"""Utilities to build the three requested PDF outputs from ``files/pi.pdf``.

Approach 1 uses PyMuPDF directly for reading and annotating. Approach 2 relies on
pdfplumber for text geometry plus ReportLab + pypdf to draw overlays. Approach 3
uses pypdfium2 to read character boxes and applies the same ReportLab overlay
pattern.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, List, Sequence, Tuple

import fitz  # PyMuPDF
import pdfplumber
import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.pdfgen import canvas


@dataclass
class WordBox:
    text: str
    rect: Tuple[float, float, float, float]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def draw_overlay(
    page_sizes: Sequence[Tuple[float, float]],
    draw_page: Callable[[canvas.Canvas, int, float, float], None],
) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=page_sizes[0])
    for page_index, (width, height) in enumerate(page_sizes):
        if page_index:
            c.setPageSize((width, height))
        draw_page(c, page_index, width, height)
        c.showPage()
    c.save()
    return buffer.getvalue()


def merge_overlay(base_pdf: Path, overlay_bytes: bytes, output_path: Path) -> None:
    reader = PdfReader(str(base_pdf))
    overlay_reader = PdfReader(BytesIO(overlay_bytes))
    writer = PdfWriter()
    for page_index, page in enumerate(reader.pages):
        page.merge_page(overlay_reader.pages[page_index])
        writer.add_page(page)
    with output_path.open("wb") as output_file:
        writer.write(output_file)


# ---------------------------------------------------------------------------
# Approach 1: PyMuPDF end-to-end
# ---------------------------------------------------------------------------


def approach1_bounding_boxes(source_pdf: Path, output_path: Path) -> None:
    doc = fitz.open(source_pdf)
    for page in doc:
        shape = page.new_shape()
        for x0, y0, x1, y1, *_ in page.get_text("words"):
            shape.draw_rect(fitz.Rect(x0, y0, x1, y1))
        shape.finish(color=(1, 0.85, 0.1), width=0.9)
        shape.commit()
    doc.save(output_path)


def approach1_highlights(source_pdf: Path, output_path: Path) -> None:
    doc = fitz.open(source_pdf)
    colors_cycle = [(0, 1, 0), (1, 0, 0)]
    for page in doc:
        for idx, (x0, y0, x1, y1, *_rest) in enumerate(page.get_text("words")):
            color = colors_cycle[idx % len(colors_cycle)]
            page.draw_rect(
                fitz.Rect(x0, y0, x1, y1),
                color=color,
                fill=color,
                fill_opacity=0.35,
                width=0,
            )
    doc.save(output_path)


def approach1_annotations(source_pdf: Path, output_path: Path) -> None:
    doc = fitz.open(source_pdf)
    line_number = 1
    for page in doc:
        raw = page.get_text("rawdict")
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                words = []
                for span in spans:
                    words.extend([w for w in span.get("text", "").split() if w])
                if not words:
                    continue
                label = f"s{line_number}_c{len(words)}"
                max_x = max(span["bbox"][2] for span in spans)
                min_y = min(span["bbox"][1] for span in spans)
                max_y = max(span["bbox"][3] for span in spans)
                target_rect = fitz.Rect(max_x + 4, min_y, max_x + 80, max_y)
                page.insert_textbox(
                    target_rect,
                    label,
                    fontname="helv",
                    fontsize=8,
                    color=(0.2, 0.2, 0.2),
                )
                line_number += 1
    doc.save(output_path)


def run_approach1(source_pdf: Path, output_dir: Path) -> None:
    ensure_dir(output_dir)
    approach1_bounding_boxes(source_pdf, output_dir / "bbox_pi.pdf")
    approach1_highlights(source_pdf, output_dir / "highlight_pi.pdf")
    approach1_annotations(source_pdf, output_dir / "annotation_pi.pdf")


# ---------------------------------------------------------------------------
# Approach 2: pdfplumber + ReportLab overlays merged via pypdf
# ---------------------------------------------------------------------------


def plumber_word_boxes(page: pdfplumber.page.Page) -> List[WordBox]:
    words = page.extract_words()
    converted: List[WordBox] = []
    for word in words:
        y0 = page.height - word["bottom"]
        y1 = page.height - word["top"]
        converted.append(WordBox(word["text"], (word["x0"], y0, word["x1"], y1)))
    return converted


def group_lines_from_tops(words: Iterable[dict], tolerance: float = 2.0) -> List[List[dict]]:
    lines: List[List[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        placed = False
        for line in lines:
            if abs(line[0]["top"] - word["top"]) <= tolerance:
                line.append(word)
                placed = True
                break
        if not placed:
            lines.append([word])
    return lines


def approach2_draw_rectangles(c: canvas.Canvas, word_boxes: Sequence[WordBox], stroke_color: colors.Color):
    c.setStrokeColor(stroke_color)
    c.setLineWidth(1)
    for box in word_boxes:
        x0, y0, x1, y1 = box.rect
        c.rect(x0, y0, x1 - x0, y1 - y0, fill=0)


def approach2_draw_highlights(c: canvas.Canvas, word_boxes: Sequence[WordBox]):
    palette = [colors.Color(0, 1, 0, alpha=0.35), colors.Color(1, 0, 0, alpha=0.35)]
    for idx, box in enumerate(word_boxes):
        color = palette[idx % len(palette)]
        x0, y0, x1, y1 = box.rect
        c.setFillColor(color)
        c.setStrokeColor(color)
        c.rect(x0, y0, x1 - x0, y1 - y0, fill=1, stroke=0)


def approach2_draw_annotations(c: canvas.Canvas, page: pdfplumber.page.Page, line_start: int) -> int:
    lines = group_lines_from_tops(page.extract_words())
    current_line = line_start
    for line in lines:
        label = f"s{current_line}_c{len(line)}"
        max_x = max(item["x1"] for item in line)
        top = min(item["top"] for item in line)
        bottom = max(item["bottom"] for item in line)
        y0 = page.height - bottom
        y1 = page.height - top
        c.setFillColor(colors.darkgray)
        c.setFont("Helvetica", 8)
        c.drawString(max_x + 4, y0, label)
        c.setLineWidth(0.5)
        c.line(max_x, y0, max_x + 2, y0)
        current_line += 1
    return current_line


def run_approach2(source_pdf: Path, output_dir: Path) -> None:
    ensure_dir(output_dir)
    with pdfplumber.open(source_pdf) as pdf:
        page_sizes = [(page.width, page.height) for page in pdf.pages]
        plumber_boxes = [plumber_word_boxes(page) for page in pdf.pages]

        bbox_overlay = draw_overlay(
            page_sizes,
            lambda c, idx, _w, _h: approach2_draw_rectangles(c, plumber_boxes[idx], colors.yellow),
        )
        merge_overlay(source_pdf, bbox_overlay, output_dir / "bbox_pi.pdf")

        highlight_overlay = draw_overlay(
            page_sizes,
            lambda c, idx, _w, _h: approach2_draw_highlights(c, plumber_boxes[idx]),
        )
        merge_overlay(source_pdf, highlight_overlay, output_dir / "highlight_pi.pdf")

        line_start = 1
        def draw_annotations_page(c: canvas.Canvas, idx: int, _w: float, _h: float) -> None:
            nonlocal line_start
            line_start = approach2_draw_annotations(c, pdf.pages[idx], line_start)

        annotation_overlay = draw_overlay(page_sizes, draw_annotations_page)
        merge_overlay(source_pdf, annotation_overlay, output_dir / "annotation_pi.pdf")


# ---------------------------------------------------------------------------
# Approach 3: pypdfium2 extraction + ReportLab overlays
# ---------------------------------------------------------------------------


def extract_words_pdfium(page: pdfium.PdfPage) -> List[WordBox]:
    textpage = page.get_textpage()
    total = textpage.count_chars()
    words: List[WordBox] = []
    current_chars: List[Tuple[str, Tuple[float, float, float, float]]] = []

    def flush() -> None:
        nonlocal current_chars
        if not current_chars:
            return
        text = "".join(ch for ch, _ in current_chars)
        if text.strip():
            xs = [bbox[0] for _, bbox in current_chars] + [bbox[2] for _, bbox in current_chars]
            ys = [bbox[1] for _, bbox in current_chars] + [bbox[3] for _, bbox in current_chars]
            words.append(WordBox(text, (min(xs), min(ys), max(xs), max(ys))))
        current_chars = []

    for idx in range(total):
        ch = textpage.get_text_range(idx, 1)
        bbox = textpage.get_charbox(idx)
        if ch.isspace():
            flush()
        else:
            current_chars.append((ch, bbox))
    flush()
    return words


def group_pdfium_lines(words: Sequence[WordBox], tolerance: float = 2.5) -> List[List[WordBox]]:
    lines: List[List[WordBox]] = []
    sorted_words = sorted(words, key=lambda w: (-((w.rect[1] + w.rect[3]) / 2), w.rect[0]))
    for word in sorted_words:
        y_center = (word.rect[1] + word.rect[3]) / 2
        placed = False
        for line in lines:
            ref_center = (line[0].rect[1] + line[0].rect[3]) / 2
            if abs(ref_center - y_center) <= tolerance:
                line.append(word)
                placed = True
                break
        if not placed:
            lines.append([word])
    return lines


def run_approach3(source_pdf: Path, output_dir: Path) -> None:
    ensure_dir(output_dir)
    pdf = pdfium.PdfDocument(str(source_pdf))
    page_sizes = [(page.get_width(), page.get_height()) for page in pdf]
    all_words = [extract_words_pdfium(page) for page in pdf]

    bbox_overlay = draw_overlay(
        page_sizes,
        lambda c, idx, _w, _h: approach2_draw_rectangles(c, all_words[idx], colors.lightblue),
    )
    merge_overlay(source_pdf, bbox_overlay, output_dir / "bbox_pi.pdf")

    highlight_overlay = draw_overlay(
        page_sizes,
        lambda c, idx, _w, _h: approach2_draw_highlights(c, all_words[idx]),
    )
    merge_overlay(source_pdf, highlight_overlay, output_dir / "highlight_pi.pdf")

    line_start = 1
    def draw_pdfium_annotations(c: canvas.Canvas, idx: int, _w: float, _h: float) -> None:
        nonlocal line_start
        lines = group_pdfium_lines(all_words[idx])
        for line in lines:
            label = f"s{line_start}_c{len(line)}"
            max_x = max(word.rect[2] for word in line)
            y0 = min(word.rect[1] for word in line)
            c.setFillColor(colors.darkgray)
            c.setFont("Helvetica", 8)
            c.drawString(max_x + 4, y0, label)
            line_start += 1

    annotation_overlay = draw_overlay(page_sizes, draw_pdfium_annotations)
    merge_overlay(source_pdf, annotation_overlay, output_dir / "annotation_pi.pdf")


def main() -> None:
    source_pdf = Path("files/pi.pdf")
    run_approach1(source_pdf, Path("approach1"))
    run_approach2(source_pdf, Path("approach2"))
    run_approach3(source_pdf, Path("approach3"))


if __name__ == "__main__":
    main()
