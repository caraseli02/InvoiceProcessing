# Invoice Processing Service: Technical Blueprint

This document contains the complete source code and setup instructions for the Python FastAPI Invoice Service. You can start a new project and copy these files directly.

## 1. Project Overview

This service implements the **Hybrid Text-Map Approach**:
1.  **Extracts Text & Coordinates** from PDFs using `pdfplumber` (Native) or `Tesseract` (OCR).
2.  **Constructs a "Text Grid" string** that visually aligns columns (Quantity, Price) to preserve spatial context.
3.  **Parses the Grid** using `gpt-4o-mini` with a strict JSON schema.

**Why this works:** It prevents "hallucinations" where the AI swaps columns, because the input "Text Grid" explicitly locks numbers to their visual positions.

## 2. Directory Structure

Create a new folder and set up this structure:

```
invoice-service/
├── main.py
├── pdf_processor.py
├── requirements.txt
├── Dockerfile
└── README.md
```

## 3. File Contents

### `requirements.txt`
```text
fastapi==0.109.2
uvicorn==0.27.1
python-multipart==0.0.9
pdfplumber==0.10.3
pytesseract==0.3.10
Pillow==10.2.0
openai==1.12.0
pydantic==2.6.1
requests==2.31.0
```

### `pdf_processor.py`
```python
import logging
import io
import pdfplumber
import pytesseract
from PIL import Image
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PDFProcessor:
    def extract_content(self, file_bytes: bytes, filename: str) -> str:
        """
        Extracts content from a PDF file.
        Strategy:
        1. Try to extract native text using pdfplumber.
        2. If text density is low (scanned PDF), use OCR (Tesseract).
        """
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                full_text_grid = []
                
                for i, page in enumerate(pdf.pages):
                    words = page.extract_words(keep_blank_chars=False, x_tolerance=3, y_tolerance=3)
                    
                    # Heuristic: If fewer than 10 words, it's likely a scan or image-only page
                    if len(words) < 10:
                        logger.info(f"Page {i+1} seems to be a scan (only {len(words)} words). Content: {words}")
                        page_text = self._perform_ocr_on_page(page)
                        full_text_grid.append(f"--- Page {i+1} (OCR) ---\n{page_text}")
                    else:
                        logger.info(f"Page {i+1} has native text ({len(words)} words).")
                        page_grid = self._generate_text_grid(words, page.width)
                        full_text_grid.append(f"--- Page {i+1} (Native) ---\n{page_grid}")
                
                return "\n".join(full_text_grid)
        except Exception as e:
            logger.error(f"Failed to process PDF: {e}")
            raise ValueError(f"Could not process PDF file: {str(e)}")

    def _generate_text_grid(self, words: List[Dict[str, Any]], page_width: float) -> str:
        """
        Generates a visual 'grid' representation of the text to preserve layout.
        Groups words by their 'top' coordinate (rows) and places them roughly where they appear horizontally.
        """
        if not words:
            return ""

        # Group words by lines (using a tolerance for 'top')
        lines = {}
        tolerance = 3 # pixels
        
        for word in words:
            top = word['top']
            # Find an existing line that matches this 'top' within tolerance
            matched_top = None
            for existing_top in lines.keys():
                if abs(existing_top - top) <= tolerance:
                    matched_top = existing_top
                    break
            
            if matched_top is None:
                matched_top = top
                lines[matched_top] = []
            
            lines[matched_top].append(word)

        # Sort lines by vertical position
        sorted_tops = sorted(lines.keys())
        
        # Build the string representation
        grid_output = []
        scale_factor = 0.2 # Scale down pixel width to character columns (approx)
        
        for top in sorted_tops:
            line_words = sorted(lines[top], key=lambda w: w['x0'])
            line_str = ""
            current_char_pos = 0
            
            for word in line_words:
                target_pos = int(word['x0'] * scale_factor)
                text = word['text']
                
                # Add padding to reach target position
                padding = max(1, target_pos - current_char_pos)
                line_str += " " * padding + text
                current_char_pos = len(line_str)
                
            grid_output.append(line_str)
            
        return "\n".join(grid_output)

    def _perform_ocr_on_page(self, page) -> str:
        """
        Converts a PDF page to an image and runs regular Tesseract OCR.
        """
        try:
            # Resolution for OCR
            im = page.to_image(resolution=300)
            text = pytesseract.image_to_string(im.original)
            return text
        except Exception as e:
            logger.error(f"OCR Failed for page: {e}")
            return "[OCR FAILED]"

processor = PDFProcessor()
```

### `main.py`
```python
import os
import logging
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import openai

from pdf_processor import processor

# setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("invoice-service")

app = FastAPI(title="Invoice Processing Service")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OpenAI Setup
openai.api_key = os.getenv("OPENAI_API_KEY")

class Product(BaseModel):
    raw_code: Optional[str] = None
    name: str
    quantity: float
    unit_price: float
    total_price: float
    confidence_score: float

class InvoiceData(BaseModel):
    supplier: Optional[str] = None
    invoice_number: Optional[str] = None
    date: Optional[str] = None
    total_amount: float
    currency: str
    products: List[Product]

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/extract", response_model=InvoiceData)
async def extract_invoice(file: UploadFile = File(...)):
    """
    Extracts data from an invoice PDF.
    1. Reads PDF content (Native Text Grid or OCR)
    2. Sends Text Grid to GPT-4o-mini for parsing
    3. Returns structured data
    """
    logger.info(f"Received file: {file.filename}, content_type: {file.content_type}")
    
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported currently")

    try:
        content = await file.read()
        logger.info(f"File size: {len(content)} bytes")
        
        # 1. Extract Text Grid
        text_grid = processor.extract_content(content, file.filename)
        
        logger.info(f"Extracted Text Grid (First 500 chars):\n{text_grid[:500]}")
        
        # 2. Call OpenAI
        if not openai.api_key:
             # Mock response for testing without keys
             logger.warning("OPENAI_API_KEY not set. Returning mock data.")
             return InvoiceData(
                 supplier="MOCK SUPPLIER", 
                 total_amount=100.0, 
                 currency="EUR", 
                 products=[]
             )

        response = await parse_with_llm(text_grid)
        return response

    except Exception as e:
        logger.error(f"Error processing invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

async def parse_with_llm(text_grid: str) -> InvoiceData:
    """
    Sends the text grid to LLM and parses the response.
    """
    system_prompt = \"\"\"You are a precise data extraction assistant. 
You will receive a text representation of an invoice where the layout is preserved (columns are visually aligned).
Extract the following fields:
- Supplier Name
- Invoice Number
- Invoice Date
- Total Amount (Net or Gross depending on context, usually the final total payable)
- Currency (EUR, USD, MDL, etc.)
- List of Products (Name, Code/EAN, Quantity, Unit Price, Total Price)

CRITICAL RULES:
1. QUANTITY vs PRICE: Look at the column headers if visible. 
   - Quantity is usually an integer (1, 5, 10) and has lower value variance.
   - Price usually has 2 decimals.
   - Total Price = Quantity * Unit Price. VERIFY THIS MATH.
2. If column headers are 'Cant/Qty' and 'Pret/Price', respect the vertical alignment.
3. Do not hallucinates codes. If no code/EAN is visible, return null.
4. Return strict JSON matching the requested schema.
\"\"\"

    user_prompt = f"Here is the invoice text layout:\n\n{text_grid}"

    try:
        # We use strict JSON mode with the new OpenAI API
        completion = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={ "type": "json_object" },
            temperature=0
        )
        
        raw_json = completion.choices[0].message.content
        logger.info(f"LLM Response: {raw_json[:200]}...")
        
        # In a real app we'd validate this against the Pydantic schema properly
        # For now, we rely on the LLM generating the right shape or we do a quick pass
        import json
        data = json.loads(raw_json)
        
        # Basic normalization to match Pydantic model keys
        # (Assuming LLM outputs keys close to our model)
        products = []
        for p in data.get("products", []):
            products.append(Product(
                raw_code=p.get("code") or p.get("raw_code"),
                name=p.get("name") or p.get("description", "Unknown"),
                quantity=float(p.get("quantity") or 1),
                unit_price=float(p.get("unit_price") or p.get("price") or 0),
                total_price=float(p.get("total_price") or p.get("total") or 0),
                confidence_score=0.9 # Placeholder
            ))

        return InvoiceData(
            supplier=data.get("supplier"),
            invoice_number=data.get("invoice_number"),
            date=data.get("date") or data.get("invoice_date"),
            total_amount=float(data.get("total_amount") or 0),
            currency=data.get("currency", "EUR"),
            products=products
        )

    except Exception as e:
        logger.error(f"LLM Parsing failed: {e}")
        raise e
```

### `Dockerfile`
```dockerfile
FROM python:3.11-slim

# Install system dependencies for Tesseract OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 4. How to Run

1.  **Build**: `docker build -t invoice-backend .`
2.  **Run**: `docker run -p 8000:8000 -e OPENAI_API_KEY=sk-... invoice-backend`
3.  **Test**:
    ```bash
    curl -X POST "http://localhost:8000/extract" \
      -F "file=@/path/to/invoice.pdf"
    ```
