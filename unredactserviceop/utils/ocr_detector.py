"""
UnredactServiceOp - OCR Detector
Advanced OCR ghosting detection using Tesseract and pdfminer.six.
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import loguru
import numpy as np

# Try imports with graceful degradation
try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    from pdfminer.high_level import extract_text, extract_pages
    from pdfminer.layout import LTTextContainer, LTChar, LTAnno, LAParams
    PDFMINER_AVAILABLE = True
except ImportError:
    PDFMINER_AVAILABLE = False

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False


@dataclass
class OCRFinding:
    """OCR detection finding."""
    type: str
    page: int
    location: Tuple[int, int, int, int]
    confidence: float
    text: str
    description: str


class OCRDetector:
    """
    OCR Ghosting Detector for identifying text remnants after redaction.
    
    Methods:
    1. Tesseract OCR with confidence scoring
    2. PDFMiner text extraction comparison
    3. Before/after text comparison
    4. Visual text region analysis
    """
    
    def __init__(self, trace_id: str = None):
        self.trace_id = trace_id or 'unknown'
        self.logger = loguru.logger.bind(trace_id=self.trace_id)
        
        # Confidence thresholds
        self.HIGH_CONFIDENCE = 0.8
        self.MEDIUM_CONFIDENCE = 0.5
        self.LOW_CONFIDENCE = 0.3
    
    def detect_ocr_ghosting(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Detect OCR ghosting in PDF - text remnants after redaction.
        
        Uses multiple methods:
        1. PDFMiner text extraction
        2. Tesseract OCR on rendered pages
        3. Comparison analysis
        """
        findings = []
        
        if not PYMUPDF_AVAILABLE:
            self.logger.warning("PyMuPDF not available - PDF OCR detection skipped")
            return findings
        
        if not TESSERACT_AVAILABLE:
            self.logger.warning("Tesseract not available - using PDFMiner only")
        
        try:
            doc = fitz.open(pdf_path)
            page_count = len(doc)
            
            self.logger.info(f"Starting OCR ghosting detection on {page_count} pages")
            
            for page_num in range(1, page_count + 1):
                try:
                    page = doc[page_num - 1]
                    
                    # Method 1: Extract text using PDFMiner (if available)
                    if PDFMINER_AVAILABLE:
                        pdfminer_text = self._extract_pdfminer_text(pdf_path, page_num)
                    else:
                        pdfminer_text = ""
                    
                    # Method 2: Extract text using PyMuPDF
                    pymupdf_text = page.get_text('text')
                    
                    # Method 3: Tesseract OCR (if available)
                    if TESSERACT_AVAILABLE:
                        tesseract_result = self._tesseract_ocr_page(pdf_path, page_num)
                        tesseract_text = tesseract_result.get('text', '')
                        tesseract_findings = tesseract_result.get('findings', [])
                    else:
                        tesseract_text = ""
                        tesseract_findings = []
                    
                    # Compare methods to find discrepancies
                    discrepancies = self._compare_text_extraction(
                        pdfminer_text, pymupdf_text, tesseract_text
                    )
                    
                    if discrepancies:
                        findings.append({
                            'page': page_num,
                            'type': 'text_discrepancy',
                            'description': f'Found {len(discrepancies)} text discrepancies',
                            'confidence': 0.7,
                            'details': discrepancies[:5]  # Limit to 5 findings
                        })
                    
                    # Add Tesseract findings
                    findings.extend(tesseract_findings)
                    
                    # Check for text in redacted regions
                    redacted_text = self._check_redacted_regions(page, tesseract_text)
                    if redacted_text:
                        findings.append({
                            'page': page_num,
                            'type': 'text_in_redacted_region',
                            'description': 'Text detected in redacted region',
                            'confidence': 0.9,
                            'text': redacted_text
                        })
                    
                except Exception as e:
                    self.logger.warning(f"Error analyzing page {page_num}: {e}")
                    continue
            
            doc.close()
            
            self.logger.info(f"OCR detection completed: {len(findings)} findings")
            
        except Exception as e:
            self.logger.error(f"OCR ghosting detection failed: {e}")
        
        return findings
    
    def detect_ocr_ghosting_image(self, image_path: str) -> List[Dict[str, Any]]:
        """
        Detect OCR ghosting in image file.
        
        Uses Tesseract to detect text that might be hidden or faint.
        """
        findings = []
        
        if not TESSERACT_AVAILABLE:
            self.logger.warning("Tesseract not available - image OCR detection skipped")
            return findings
        
        if not CV2_AVAILABLE:
            self.logger.warning("OpenCV not available - limited image analysis")
            return findings
        
        try:
            # Read image
            img = cv2.imread(image_path)
            
            if img is None:
                raise ValueError(f"Failed to load image: {image_path}")
            
            # Preprocessing for better OCR
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Multiple OCR passes with different preprocessing
            results = []
            
            # 1. Direct OCR
            result = self._tesseract_extract(gray)
            results.append(('direct', result))
            
            # 2. OCR on inverted image (for white text on dark)
            inverted = cv2.bitwise_not(gray)
            result = self._tesseract_extract(inverted)
            results.append(('inverted', result))
            
            # 3. OCR on thresholded image
            _, thresh = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
            result = self._tesseract_extract(thresh)
            results.append(('thresholded', result))
            
            # 4. OCR on enhanced contrast
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            result = self._tesseract_extract(enhanced)
            results.append(('enhanced', result))
            
            # Combine and analyze results
            all_text = []
            for method, (text, data) in results:
                if text.strip():
                    all_text.append({
                        'method': method,
                        'text': text,
                        'data': data
                    })
            
            if all_text:
                findings.append({
                    'page': 1,
                    'type': 'detected_text',
                    'description': f'Text detected using {len(all_text)} preprocessing methods',
                    'confidence': 0.8,
                    'text_preview': all_text[0]['text'][:200] if all_text else '',
                    'methods_used': [r['method'] for r in all_text]
                })
                
                # Check for suspicious text patterns
                suspicious = self._check_suspicious_text(all_text)
                if suspicious:
                    findings.extend(suspicious)
            
            self.logger.info(f"Image OCR detection completed: {len(findings)} findings")
            
        except Exception as e:
            self.logger.error(f"Image OCR detection failed: {e}")
        
        return findings
    
    def _extract_pdfminer_text(self, pdf_path: str, page_num: int) -> str:
        """Extract text using pdfminer.six."""
        text = ""
        
        try:
            # Set up LAParams for better text extraction
            laparams = LAParams(
                line_margin=0.5,
                word_margin=0.1,
                char_margin=2.0
            )
            
            # Extract text from specific page
            for page in extract_pages(pdf_path, page_numbers=[page_num - 1]):
                for element in page:
                    if isinstance(element, LTTextContainer):
                        text += element.get_text()
            
        except Exception as e:
            self.logger.warning(f"PDFMiner extraction failed for page {page_num}: {e}")
        
        return text
    
    def _tesseract_ocr_page(self, pdf_path: str, page_num: int) -> Dict[str, Any]:
        """Run Tesseract OCR on specific PDF page."""
        result = {
            'text': '',
            'findings': [],
            'confidence': 0.0
        }
        
        if not TESSERACT_AVAILABLE or not PDF2IMAGE_AVAILABLE:
            return result
        
        try:
            # Convert PDF page to image
            images = convert_from_path(
                pdf_path,
                first_page=page_num,
                last_page=page_num,
                dpi=300
            )
            
            if not images:
                return result
            
            img = images[0]
            
            # Convert to grayscale
            gray = np.array(img.convert('L'))
            
            # Extract text with Tesseract
            text, data = self._tesseract_extract(gray)
            result['text'] = text
            
            # Analyze for ghosting
            if data:
                words = data.get('words', [])
                
                # Check for low confidence words
                low_conf_words = []
                for word in words:
                    conf = word.get('confidence', 100) / 100
                    if conf < self.MEDIUM_CONFIDENCE:
                        low_conf_words.append({
                            'text': word.get('text', ''),
                            'confidence': conf,
                            'bbox': word.get('bbox', ())
                        })
                
                if low_conf_words:
                    result['findings'].append({
                        'page': page_num,
                        'type': 'low_confidence_text',
                        'description': f'Found {len(low_conf_words)} words with low confidence',
                        'confidence': 0.6,
                        'words': low_conf_words[:10]  # Limit
                    })
                
                # Calculate overall confidence
                if words:
                    confidences = [w.get('confidence', 100) / 100 for w in words]
                    result['confidence'] = sum(confidences) / len(confidences)
            
        except Exception as e:
            self.logger.warning(f"Tesseract OCR failed for page {page_num}: {e}")
        
        return result
    
    def _tesseract_extract(self, image: np.ndarray) -> Tuple[str, Dict]:
        """Extract text using Tesseract with detailed data."""
        text = ""
        data = {}
        
        try:
            # Get detailed data
            data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                config='--psm 6'  # Assume uniform block of text
            )
            
            # Get just the text
            text = pytesseract.image_to_string(image)
            
        except Exception as e:
            self.logger.warning(f"Tesseract extraction failed: {e}")
        
        return text, data
    
    def _compare_text_extraction(self, text1: str, text2: str, text3: str) -> List[Dict]:
        """Compare text from different extraction methods."""
        discrepancies = []
        
        # Clean text
        texts = [
            t1.strip() for t1 in [text1, text2, text3] if t1
        ]
        
        if len(texts) < 2:
            return discrepancies
        
        # Find words that appear in some but not all methods
        all_words = set()
        word_sets = []
        
        for text in texts:
            words = set(text.lower().split())
            word_sets.append(words)
            all_words.update(words)
        
        # Find discrepancies
        for word in all_words:
            appearances = sum(1 for words in word_sets if word in words)
            
            if appearances != len(word_sets):
                # Word appears in some but not all extractions
                discrepancies.append({
                    'word': word,
                    'methods_detected': appearances,
                    'total_methods': len(word_sets)
                })
        
        return discrepancies[:20]  # Limit results
    
    def _check_redacted_regions(self, page: fitz.Page, extracted_text: str) -> Optional[str]:
        """Check if there's text in redacted regions."""
        
        try:
            # Get redaction annotations
            annots = page.annots()
            
            if not annots:
                return None
            
            for annot in annots:
                if annot.type[0] == 4:  # Redaction annotation
                    rect = annot.rect
                    
                    # Check if there's text in this region
                    text_in_region = page.get_text('text', clip=rect)
                    
                    if text_in_region.strip():
                        return text_in_region[:100]
        
        except Exception as e:
            self.logger.warning(f"Redacted region check failed: {e}")
        
        return None
    
    def _check_suspicious_text(self, results: List[Dict]) -> List[Dict]:
        """Check for suspicious text patterns that might indicate hidden content."""
        suspicious = []
        
        # Patterns that might indicate redacted but visible text
        suspicious_patterns = [
            'xxx', 'xxx', '█', '▓', '■',
            'redacted', 'confidential', 'secret',
            '****', '////', '===='
        ]
        
        for result in results:
            text = result.get('text', '').lower()
            
            for pattern in suspicious_patterns:
                if pattern.lower() in text:
                    suspicious.append({
                        'page': 1,
                        'type': 'suspicious_pattern',
                        'description': f'Suspicious pattern found: {pattern}',
                        'confidence': 0.7,
                        'text_preview': result.get('text', '')[:100]
                    })
                    break
        
        return suspicious
    
    def verify_redaction(self, image_path: str, region: Tuple[int, int, int, int]) -> Dict[str, Any]:
        """
        Verify if a specific region is properly redacted using OCR.
        
        Returns whether any text is detected in the region.
        """
        result = {
            'is_properly_redacted': True,
            'detected_text': None,
            'confidence': 0.0
        }
        
        if not TESSERACT_AVAILABLE or not CV2_AVAILABLE:
            return result
        
        try:
            # Read image
            img = cv2.imread(image_path)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Extract region
            x1, y1, x2, y2 = region
            roi = gray[y1:y2, x1:x2]
            
            if roi.size == 0:
                return result
            
            # Try OCR on region
            text, data = self._tesseract_extract(roi)
            
            if text and text.strip():
                result['is_properly_redacted'] = False
                result['detected_text'] = text[:200]
                
                if data and 'conf' in data:
                    confidences = [c for c in data['conf'] if c > 0]
                    if confidences:
                        result['confidence'] = sum(confidences) / len(confidences) / 100
            
        except Exception as e:
            self.logger.warning(f"Redaction verification failed: {e}")
        
        return result
