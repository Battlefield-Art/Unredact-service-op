"""
UnredactServiceOp - PDF Analyzer
Detects fake redactions, black rectangles, hidden text, and hidden objects in PDFs.
"""

import os
import sys
import fitz  # PyMuPDF
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import loguru


@dataclass
class RedactionIssue:
    """Data class for redaction issues."""
    type: str
    page: int
    bbox: Tuple[float, float, float, float]
    severity: str
    description: str
    evidence: Dict[str, Any] = field(default_factory=dict)


class PDFAnalyzer:
    """
    PDF Analyzer for detecting fake redactions and hidden content.
    
    Detection methods:
    1. Black rectangle detection - Identifies solid black rectangles that may mask content
    2. Hidden text detection - Finds text that appears below redaction annotations
    3. Hidden object detection - Identifies embedded objects/images beneath redactions
    4. Coordinate matching - Links redaction annotations to underlying content
    """
    
    def __init__(self, trace_id: str = None):
        self.trace_id = trace_id or 'unknown'
        self.logger = loguru.logger.bind(trace_id=self.trace_id)
    
    def get_page_count(self, pdf_path: str) -> int:
        """Get total number of pages in PDF."""
        try:
            doc = fitz.open(pdf_path)
            count = len(doc)
            doc.close()
            return count
        except Exception as e:
            self.logger.warning(f"Failed to get page count: {e}")
            return 0
    
    def detect_fake_redactions(self, pdf_path: str, 
                              page_numbers: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        Main method to detect fake redactions in PDF.
        
        Args:
            pdf_path: Path to PDF file
            page_numbers: Optional list of specific pages to analyze
            
        Returns:
            List of detected issues with details
        """
        issues = []
        
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            
            # Determine which pages to analyze
            if page_numbers:
                pages_to_analyze = [p for p in page_numbers if 1 <= p <= total_pages]
            else:
                pages_to_analyze = range(1, total_pages + 1)
            
            self.logger.info(f"Analyzing {len(pages_to_analyze)} pages for redaction issues")
            
            for page_num in pages_to_analyze:
                try:
                    page = doc[page_num - 1]  # fitz uses 0-based indexing
                    
                    # 1. Detect black rectangles (possible fake redactions)
                    rect_issues = self._detect_black_rectangles(page, page_num)
                    issues.extend(rect_issues)
                    
                    # 2. Detect hidden text under redactions
                    hidden_text_issues = self._detect_hidden_text(page, page_num)
                    issues.extend(hidden_text_issues)
                    
                    # 3. Detect hidden objects
                    hidden_object_issues = self._detect_hidden_objects(page, page_num)
                    issues.extend(hidden_object_issues)
                    
                    # 4. Check for unredacted metadata in page
                    page_metadata_issues = self._check_page_metadata(page, page_num)
                    issues.extend(page_metadata_issues)
                    
                except Exception as e:
                    self.logger.warning(f"Error analyzing page {page_num}: {e}")
                    continue
            
            doc.close()
            
        except Exception as e:
            self.logger.error(f"PDF analysis failed: {e}")
            raise
        
        return issues
    
    def _detect_black_rectangles(self, page: fitz.Page, page_num: int) -> List[Dict]:
        """
        Detect black rectangles that may indicate fake redactions.
        
        Method: Look for filled rectangles with specific color characteristics
        that match common "redaction" appearance but aren't proper redactions.
        """
        issues = []
        
        try:
            # Get all drawings/rectangles on page
            drawings = page.get_drawings()
            
            for i, drawing in enumerate(drawings):
                # Check if it's a filled rectangle
                if drawing.get('type') == 'f' or 'fill' in drawing:
                    # Get rectangle coordinates
                    rect = drawing.get('rect', fitz.Rect(0, 0, 0, 0))
                    
                    # Skip very small rectangles
                    if rect.width < 5 or rect.height < 5:
                        continue
                    
                    # Get fill color
                    fill = drawing.get('fill', (0, 0, 0))
                    
                    # Check if it's black (common redaction color)
                    if isinstance(fill, (tuple, list)) and len(fill) >= 3:
                        # Check for black or near-black colors
                        if all(c < 50 for c in fill[:3]):
                            issues.append({
                                'type': 'black_rectangle',
                                'page': page_num,
                                'bbox': (rect.x0, rect.y0, rect.x1, rect.y1),
                                'severity': 'high',
                                'description': f'Black rectangle found at position ({rect.x0:.1f}, {rect.y0:.1f})',
                                'evidence': {
                                    'width': rect.width,
                                    'height': rect.height,
                                    'color': fill[:3],
                                    'drawing_index': i
                                }
                            })
                            
                            # Check if there's underlying text
                            text_under_rect = page.get_text('text', clip=rect)
                            if text_under_rect.strip():
                                issues.append({
                                    'type': 'potential_hidden_text',
                                    'page': page_num,
                                    'bbox': (rect.x0, rect.y0, rect.x1, rect.y1),
                                    'severity': 'critical',
                                    'description': 'Text detected under black rectangle - possible fake redaction',
                                    'evidence': {
                                        'underlying_text': text_under_rect[:100],
                                        'rectangle_index': i
                                    }
                                })
            
            # Also check for redaction annotations
            annots = page.annots()
            if annots:
                for annot in annots:
                    if annot.type[0] == 4:  # Redaction annotation
                        rect = annot.rect
                        # Check if the redaction has been "applied" (content removed)
                        # vs just marked for redaction
                        contents = annot.contents
                        if not contents or len(contents.strip()) == 0:
                            # Check for underlying content
                            text_under = page.get_text('text', clip=rect)
                            images_under = page.get_images(clip=rect)
                            
                            if text_under.strip():
                                issues.append({
                                    'type': 'unapplied_redaction',
                                    'page': page_num,
                                    'bbox': (rect.x0, rect.y0, rect.x1, rect.y1),
                                    'severity': 'critical',
                                    'description': 'Redaction annotation with underlying text not removed',
                                    'evidence': {
                                        'underlying_text': text_under[:100],
                                        'has_images': len(images_under) > 0
                                    }
                                })
            
        except Exception as e:
            self.logger.warning(f"Black rectangle detection error: {e}")
        
        return issues
    
    def _detect_hidden_text(self, page: fitz.Page, page_num: int) -> List[Dict]:
        """
        Detect text that may be hidden or masked by objects.
        
        Method: Check for text with low opacity, unusual colors, or
        positioned behind other objects.
        """
        issues = []
        
        try:
            # Get text blocks
            blocks = page.get_text('blocks')
            
            for block in blocks:
                if len(block) >= 6:
                    bbox = block[:4]
                    text = block[4]
                    
                    # Check for empty or whitespace-only blocks that shouldn't be
                    if not text.strip() and bbox[2] - bbox[0] > 50:
                        issues.append({
                            'type': 'empty_text_block',
                            'page': page_num,
                            'bbox': bbox,
                            'severity': 'low',
                            'description': 'Empty text block detected - possible removed content',
                            'evidence': {'size': (bbox[2] - bbox[0], bbox[3] - bbox[1])}
                        })
            
            # Check for text with transparency
            for obj in page.get_objects():
                if obj.get('type') == 1:  # Text object
                    opacity = obj.get('opacity', 1.0)
                    if opacity < 0.3:
                        bbox = fitz.Rect(obj['bbox'])
                        issues.append({
                            'type': 'low_opacity_text',
                            'page': page_num,
                            'bbox': (bbox.x0, bbox.y0, bbox.x1, bbox.y1),
                            'severity': 'medium',
                            'description': f'Text with low opacity ({opacity:.2f}) detected',
                            'evidence': {'opacity': opacity}
                        })
            
        except Exception as e:
            self.logger.warning(f"Hidden text detection error: {e}")
        
        return issues
    
    def _detect_hidden_objects(self, page: fitz.Page, page_num: int) -> List[Dict]:
        """
        Detect hidden images or objects that may contain masked content.
        """
        issues = []
        
        try:
            # Get all images on page
            images = page.get_images(full=True)
            
            self.logger.debug(f"Page {page_num}: Found {len(images)} images")
            
            for img_index, img in enumerate(images):
                try:
                    xref = img[0]
                    
                    # Get image metadata
                    img_info = page.get_image_info(xref=xref)
                    
                    # Check for images that might be redaction overlays
                    if img_info:
                        width = img_info.get('width', 0)
                        height = img_info.get('height', 0)
                        
                        # Large black images might be fake redactions
                        if width > 100 and height > 100:
                            # Check image colorspace
                            base_img = doc = fitz.Pixmap(page.parent, xref)
                            if base_img.n > 1:  # Not grayscale
                                # Check if mostly black
                                samples = base_img.samples
                                if len(samples) > 1000:
                                    black_pixels = sum(1 for i in range(0, len(samples), base_img.n) 
                                                     if all(samples[i:i+3] < [50, 50, 50]))
                                    if black_pixels / (len(samples) / base_img.n) > 0.8:
                                        issues.append({
                                            'type': 'large_black_image',
                                            'page': page_num,
                                            'bbox': (0, 0, width, height),
                                            'severity': 'high',
                                            'description': f'Large black image ({width}x{height}) - possible fake redaction',
                                            'evidence': {
                                                'width': width,
                                                'height': height,
                                                'image_xref': xref
                                            }
                                        })
                            
                            base_img = None  # Free memory
                
                except Exception as e:
                    self.logger.warning(f"Image analysis error for img {img_index}: {e}")
                    continue
            
            # Check for overlapping objects
            objects = page.get_objects()
            for i, obj1 in enumerate(objects):
                bbox1 = fitz.Rect(obj1.get('bbox', (0, 0, 0, 0)))
                for j, obj2 in enumerate(objects[i+1:], i+1):
                    bbox2 = fitz.Rect(obj2.get('bbox', (0, 0, 0, 0)))
                    
                    # Check for significant overlap
                    overlap = bbox1.intersect(bbox2)
                    if overlap and overlap.width > 10 and overlap.height > 10:
                        # Check if one is a rectangle (possible redaction)
                        if (obj1.get('type') == 5 and obj2.get('type') == 1) or \
                           (obj2.get('type') == 5 and obj1.get('type') == 1):
                            issues.append({
                                'type': 'overlapping_object',
                                'page': page_num,
                                'bbox': (overlap.x0, overlap.y0, overlap.x1, overlap.y1),
                                'severity': 'medium',
                                'description': 'Text overlapped by shape - verify content is fully redacted',
                                'evidence': {
                                    'overlap_area': overlap.width * overlap.height
                                }
                            })
            
        except Exception as e:
            self.logger.warning(f"Hidden object detection error: {e}")
        
        return issues
    
    def _check_page_metadata(self, page: fitz.Page, page_num: int) -> List[Dict]:
        """Check page-level metadata for potential leaks."""
        issues = []
        
        try:
            # Get page metadata
            meta = page.metadata
            
            if meta:
                # Check for suspicious metadata
                for key, value in meta.items():
                    if value and 'author' in key.lower():
                        issues.append({
                            'type': 'author_metadata',
                            'page': page_num,
                            'bbox': None,
                            'severity': 'medium',
                            'description': f'Author metadata found in page: {value}',
                            'evidence': {key: value}
                        })
        
        except Exception as e:
            self.logger.warning(f"Page metadata check error: {e}")
        
        return issues
    
    def extract_text_for_comparison(self, pdf_path: str) -> Dict[int, str]:
        """Extract all text from PDF for OCR comparison."""
        text_by_page = {}
        
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                text_by_page[page_num + 1] = page.get_text('text')
            doc.close()
            
        except Exception as e:
            self.logger.error(f"Text extraction failed: {e}")
        
        return text_by_page
    
    def verify_redaction(self, pdf_path: str, page_num: int, 
                        bbox: Tuple[float, float, float, float]) -> Dict[str, Any]:
        """Verify if a specific region is properly redacted."""
        
        result = {
            'is_properly_redacted': False,
            'has_underlying_text': False,
            'has_underlying_images': False,
            'confidence': 0.0
        }
        
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_num - 1]
            
            # Create rectangle from bbox
            rect = fitz.Rect(bbox)
            
            # Check for underlying text
            text = page.get_text('text', clip=rect)
            if text.strip():
                result['has_underlying_text'] = True
                result['underlying_text'] = text[:100]
            
            # Check for underlying images
            images = page.get_images(clip=rect)
            if images:
                result['has_underlying_images'] = True
                result['image_count'] = len(images)
            
            # Determine if properly redacted
            if not result['has_underlying_text'] and not result['has_underlying_images']:
                result['is_properly_redacted'] = True
                result['confidence'] = 1.0
            else:
                result['confidence'] = 0.0
            
            doc.close()
            
        except Exception as e:
            self.logger.error(f"Redaction verification failed: {e}")
        
        return result
