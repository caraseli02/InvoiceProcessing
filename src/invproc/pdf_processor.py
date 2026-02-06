"""PDF processing and OCR for invoice extraction."""

import logging
from typing import Dict, List, Tuple, Any
from pathlib import Path

import pdfplumber
import pytesseract

from .config import InvoiceConfig

logger = logging.getLogger(__name__)


class PDFProcessor:
    """Process PDF files with native text extraction and OCR fallback."""

    def __init__(self, config: InvoiceConfig):
        self.config = config

    def extract_content(
        self, file_path: Path, debug: bool = False
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Extract text with spatial layout preserved.

        Args:
            file_path: Path to PDF file
            debug: Whether to save debug output

        Returns:
            (text_grid, metadata) tuple
        """
        metadata: Dict[str, Any] = {
            "pages_processed": 0,
            "pages_ocr": 0,
            "method": "native",
            "ocr_confidence": 0.0,
        }

        try:
            with pdfplumber.open(file_path) as pdf:
                full_text_grid = []

                for i, page in enumerate(pdf.pages):
                    metadata["pages_processed"] += 1

                    words = page.extract_words(
                        keep_blank_chars=False,
                        x_tolerance=3,
                        y_tolerance=3,
                        use_text_flow=False,
                    )

                    if len(words) < 10:
                        logger.info(
                            f"Page {i + 1}: Low word count ({len(words)}), using OCR"
                        )
                        metadata["pages_ocr"] += 1
                        metadata["method"] = "hybrid"
                        page_text = self._perform_ocr(page, debug, i, file_path)
                    else:
                        logger.info(f"Page {i + 1}: Native text ({len(words)} words)")
                        page_text = self._generate_text_grid(words, page.width)

                    full_text_grid.append(
                        f"--- Page {i + 1} ({'OCR' if len(words) < 10 else 'Native'}) ---\n{page_text}"
                    )

                return "\n".join(full_text_grid), metadata

        except Exception as e:
            logger.error(f"PDF processing failed: {e}", exc_info=True)
            raise ValueError(f"Could not process PDF: {str(e)}")

    def _generate_text_grid(
        self, words: List[Dict[str, Any]], page_width: float
    ) -> str:
        """
        Generate visual text grid preserving layout.

        Groups words by vertical position and arranges horizontally
        using character padding to preserve column alignment.

        Args:
            words: List of word dictionaries with coordinates
            page_width: Width of the page in pixels

        Returns:
            Multi-line string preserving column alignment
        """
        if not words:
            return ""

        lines: Dict[float, List[Dict[str, Any]]] = {}
        tolerance = self.config.tolerance
        scale_factor = self.config.scale_factor

        for word in words:
            top = word["top"]

            matched_top = None
            for existing_top in lines.keys():
                if abs(existing_top - top) <= tolerance:
                    matched_top = existing_top
                    break

            if matched_top is None:
                matched_top = top
                lines[matched_top] = []

            lines[matched_top].append(word)

        sorted_tops = sorted(lines.keys())
        grid_output = []

        for top in sorted_tops:
            line_words = sorted(lines[top], key=lambda w: w["x0"])
            line_str = ""
            current_char_pos = 0

            for word in line_words:
                target_pos = int(word["x0"] * scale_factor)
                text = word["text"]

                padding = max(1, target_pos - current_char_pos)
                line_str += " " * padding + text
                current_char_pos = len(line_str)

            grid_output.append(line_str)

        return "\n".join(grid_output)

    def _perform_ocr(
        self, page: Any, debug: bool, page_num: int, file_path: Path
    ) -> str:
        """
        OCR fallback for scanned pages.

        Converts page to image and uses Tesseract with multi-language support.

        Args:
            page: pdfplumber Page object
            debug: Whether to save debug output
            page_num: Page number for debug naming
            file_path: Original PDF path for debug naming

        Returns:
            Plain text extracted from OCR
        """
        try:
            im = page.to_image(resolution=self.config.ocr_dpi)
            lang_str = self.config.ocr_languages

            text: str = pytesseract.image_to_string(
                im.original,
                lang=lang_str,
                config=self.config.ocr_config,
            )

            if debug:
                ocr_dir = self.config.output_dir / "ocr_debug"
                stem = file_path.stem
                im.save(ocr_dir / f"{stem}_page{page_num + 1}.png")
                logger.info(
                    f"Saved OCR debug image to {ocr_dir / f'{stem}_page{page_num + 1}.png'}"
                )

            logger.info(f"OCR completed with languages: {lang_str}")
            return text

        except Exception as e:
            logger.error(f"OCR failed: {e}", exc_info=True)
            return "[OCR FAILED]"
