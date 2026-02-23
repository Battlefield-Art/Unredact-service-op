"""
UnredactServiceOp - Image Processor
Real-time OpenCV image processing with gamma, contrast, exposure, and CLAHE adjustments.
"""

import os
import sys
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import loguru

# Import constants
from constants import (
    MIN_GAMMA,
    MAX_GAMMA,
    MIN_CONTRAST,
    MAX_CONTRAST,
    MIN_EXPOSURE,
    MAX_EXPOSURE,
    MIN_CLAHE,
    MAX_CLAHE,
    BLACK_REGION_MIN_WIDTH,
    BLACK_REGION_MIN_HEIGHT,
    BLACK_REGION_MIN_AREA,
    BLACK_REGION_LARGE_AREA,
    BLACK_REGION_COLOR_THRESHOLD,
    SOLID_COLOR_STD_THRESHOLD,
    SOLID_REGION_MIN_PERIMETER,
    LSB_VARIANCE_THRESHOLD,
    JPEG_BLOCK_SIZE,
    JPEG_BLOCK_VARIANCE_THRESHOLD,
)


@dataclass
class ImageAnalysisResult:
    """Results from image analysis."""
    width: int
    height: int
    channels: int
    format: str
    has_alpha: bool
    issues: list
    histogram: Dict[str, list]


class ImageProcessor:
    """
    Image Processor for redaction analysis and real-time adjustments.
    
    Features:
    - Gamma correction
    - Contrast adjustment
    - Exposure correction
    - CLAHE (Contrast Limited Adaptive Histogram Equalization)
    - Side-by-side comparison
    - Histogram analysis
    """
    
    def __init__(self, trace_id: str = None):
        self.trace_id = trace_id or 'unknown'
        self.logger = loguru.logger.bind(trace_id=self.trace_id)
        self.temp_dir = Path(__file__).parent.parent / 'temp'
        self.temp_dir.mkdir(exist_ok=True)
    
    def analyze_image(self, image_path: str) -> Dict[str, Any]:
        """
        Analyze image for potential redaction issues.
        
        Returns:
            Dictionary with analysis results
        """
        result = {
            'width': 0,
            'height': 0,
            'channels': 0,
            'format': '',
            'has_alpha': False,
            'issues': [],
            'histogram': {},
            'statistics': {}
        }
        
        try:
            # Read image
            img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            
            if img is None:
                raise ValueError(f"Failed to load image: {image_path}")
            
            # Basic properties
            result['height'], result['width'] = img.shape[:2]
            result['channels'] = img.shape[2] if len(img.shape) > 2 else 1
            result['format'] = Path(image_path).suffix.lower()
            result['has_alpha'] = result['channels'] == 4
            
            # Analyze for issues
            issues = self._detect_image_issues(img)
            result['issues'] = issues
            
            # Calculate histogram
            result['histogram'] = self._calculate_histogram(img)
            
            # Calculate statistics
            result['statistics'] = self._calculate_statistics(img)
            
            self.logger.info(f"Image analysis completed: {result['width']}x{result['height']}")
            
        except Exception as e:
            self.logger.error(f"Image analysis failed: {e}")
            result['error'] = str(e)
        
        return result
    
    def _detect_image_issues(self, img: np.ndarray) -> List[Dict]:
        """
        Detect potential redaction issues in image.

        Args:
            img: OpenCV image array

        Returns:
            List of detected issues
        """
        issues = []

        try:
            # Convert to different color spaces for analysis
            # Use ndim attribute instead of len() for dimension count
            if img.ndim == 3:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                gray = img

            # 1. Detect large black regions (possible fake redactions)
            # Apply threshold to find black regions
            _, black_thresh = cv2.threshold(gray, BLACK_REGION_COLOR_THRESHOLD, 255, cv2.THRESH_BINARY)

            # Find contours of black regions
            contours, _ = cv2.findContours(black_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = cv2.contourArea(contour)

                # Skip small regions
                if w < BLACK_REGION_MIN_WIDTH or h < BLACK_REGION_MIN_HEIGHT or area < BLACK_REGION_MIN_AREA:
                    continue

                # Check aspect ratio (redactions usually have specific shapes)
                aspect_ratio = float(w) / h if h > 0 else 0

                issues.append({
                    'type': 'large_black_region',
                    'bbox': (int(x), int(y), int(x + w), int(y + h)),
                    'size': (int(w), int(h)),
                    'area': int(area),
                    'aspect_ratio': round(aspect_ratio, 2),
                    'severity': 'high' if area > BLACK_REGION_LARGE_AREA else 'medium',
                    'description': f'Large black region ({w}x{h}) - verify it\'s intentional'
                })

            # 2. Detect suspicious solid color regions
            # Use edge detection to find uniform regions
            edges = cv2.Canny(gray, 50, 150)
            edge_contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in edge_contours:
                perimeter = cv2.arcLength(contour, True)
                if perimeter < SOLID_REGION_MIN_PERIMETER:
                    continue

                # Check if it's a rectangle (common redaction shape)
                approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)

                if len(approx) == 4:
                    x, y, w, h = cv2.boundingRect(contour)
                    # Check if it's mostly solid
                    roi = gray[y:y+h, x:x+w]
                    if roi.size > 0:
                        std_dev = np.std(roi)
                        if std_dev < SOLID_COLOR_STD_THRESHOLD:  # Very uniform region
                            issues.append({
                                'type': 'solid_color_region',
                                'bbox': (int(x), int(y), int(x + w), int(y + h)),
                                'severity': 'medium',
                                'description': f'Uniform region - verify content is not hidden'
                            })

            # 3. Detect embedded images (steganography indicators)
            # Check for unusual pixel patterns in LSB
            if img.ndim == 3 and img.dtype == np.uint8:
                # Check least significant bits for patterns
                blue_channel = img[:, :, 0]
                lsb_pattern = blue_channel & 1
                lsb_variance = np.var(lsb_pattern)

                # Unusually uniform LSB might indicate hidden data
                if abs(lsb_variance - 0.25) < LSB_VARIANCE_THRESHOLD:
                    issues.append({
                        'type': 'unusual_lsb_pattern',
                        'severity': 'low',
                        'description': 'Unusual pixel patterns detected - low priority'
                    })

            # 4. Check for JPEG artifacts (might indicate copied/redacted content)
            if img.ndim == 3:
                # Check for blocking artifacts
                blocks_variance = []

                for i in range(0, gray.shape[0] - JPEG_BLOCK_SIZE, JPEG_BLOCK_SIZE):
                    for j in range(0, gray.shape[1] - JPEG_BLOCK_SIZE, JPEG_BLOCK_SIZE):
                        block = gray[i:i+JPEG_BLOCK_SIZE, j:j+JPEG_BLOCK_SIZE]
                        if block.size > 0:
                            blocks_variance.append(np.var(block))

                if blocks_variance:
                    # High variance between blocks might indicate JPEG compression
                    # which can hide redaction evidence
                    between_block_variance = np.var(blocks_variance)
                    if between_block_variance > JPEG_BLOCK_VARIANCE_THRESHOLD:
                        issues.append({
                            'type': 'jpeg_artifacts',
                            'severity': 'low',
                            'description': 'JPEG compression artifacts detected'
                        })
            
        except Exception as e:
            self.logger.warning(f"Issue detection error: {e}")
        
        return issues
    
    def _calculate_histogram(self, img: np.ndarray) -> Dict[str, list]:
        """Calculate image histogram for analysis."""
        histogram = {}
        
        try:
            if len(img.shape) == 3:
                # BGR histogram
                for i, channel in enumerate(['blue', 'green', 'red']):
                    hist = cv2.calcHist([img], [i], None, [256], [0, 256])
                    histogram[channel] = hist.flatten().tolist()
            else:
                # Grayscale histogram
                hist = cv2.calcHist([img], [0], None, [256], [0, 256])
                histogram['grayscale'] = hist.flatten().tolist()
            
            # Calculate histogram statistics
            if 'grayscale' in histogram:
                gray_hist = np.array(histogram['grayscale'])
                total_pixels = gray_hist.sum()
                if total_pixels > 0:
                    # Calculate mean brightness
                    bins = np.arange(256)
                    mean_brightness = (bins * gray_hist).sum() / total_pixels
                    histogram['mean_brightness'] = float(mean_brightness)
                    
                    # Find dominant histogram range
                    non_zero_bins = np.where(gray_hist > total_pixels * 0.01)[0]
                    if len(non_zero_bins) > 0:
                        histogram['histogram_range'] = [int(non_zero_bins[0]), int(non_zero_bins[-1])]
        
        except Exception as e:
            self.logger.warning(f"Histogram calculation error: {e}")
        
        return histogram
    
    def _calculate_statistics(self, img: np.ndarray) -> Dict[str, float]:
        """
        Calculate image statistics with division by zero protection.

        Args:
            img: OpenCV image array

        Returns:
            Dictionary with image statistics
        """
        stats = {}

        try:
            if img.ndim == 3:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                gray = img

            stats['mean'] = float(np.mean(gray))
            stats['std_dev'] = float(np.std(gray))
            stats['min'] = float(np.min(gray))
            stats['max'] = float(np.max(gray))
            stats['median'] = float(np.median(gray))

            # Contrast ratio - prevent division by zero
            stats['contrast_ratio'] = stats['max'] / max(stats['min'], 1)

            # Dynamic range
            stats['dynamic_range'] = stats['max'] - stats['min']

        except Exception as e:
            self.logger.warning(f"Statistics calculation error: {e}")
        return stats
    
    def apply_adjustments(self, image_path: str,
                         gamma: float = 1.0,
                         contrast: float = 1.0,
                         exposure: float = 1.0,
                         clahe: float = 0) -> Dict[str, Any]:
        """
        Apply real-time image adjustments with input validation.

        Args:
            image_path: Path to input image
            gamma: Gamma correction (0.1 - 3.0, default 1.0)
            contrast: Contrast adjustment (0.5 - 2.0, default 1.0)
            exposure: Exposure adjustment (0.5 - 2.0, default 1.0)
            clahe: CLAHE clip limit (0 - 5, default 0 = disabled)

        Returns:
            Dictionary with output path and applied adjustments

        Raises:
            ValueError: If any parameter is out of valid range
        """
        # Validate input ranges
        if not MIN_GAMMA <= gamma <= MAX_GAMMA:
            raise ValueError(f"Gamma must be between {MIN_GAMMA} and {MAX_GAMMA}, got {gamma}")
        if not MIN_CONTRAST <= contrast <= MAX_CONTRAST:
            raise ValueError(f"Contrast must be between {MIN_CONTRAST} and {MAX_CONTRAST}, got {contrast}")
        if not MIN_EXPOSURE <= exposure <= MAX_EXPOSURE:
            raise ValueError(f"Exposure must be between {MIN_EXPOSURE} and {MAX_EXPOSURE}, got {exposure}")
        if not MIN_CLAHE <= clahe <= MAX_CLAHE:
            raise ValueError(f"CLAHE must be between {MIN_CLAHE} and {MAX_CLAHE}, got {clahe}")

        result = {
            'input_path': image_path,
            'adjustments': {
                'gamma': gamma,
                'contrast': contrast,
                'exposure': exposure,
                'clahe': clahe
            },
            'output_path': None
        }

        try:
            # Read image
            img = cv2.imread(image_path)

            if img is None:
                raise ValueError(f"Failed to load image: {image_path}")

            # Apply adjustments in order

            # 1. Exposure adjustment (brightness)
            if exposure != 1.0:
                img = np.clip(img * exposure, 0, 255).astype(np.uint8)

            # 2. Gamma correction
            if gamma != 1.0:
                # Build gamma lookup table
                inv_gamma = 1.0 / gamma
                table = np.array([((i / 255.0) ** inv_gamma) * 255
                                 for i in range(256)]).astype(np.uint8)
                img = cv2.LUT(img, table)

            # 3. Contrast adjustment
            if contrast != 1.0:
                # Apply contrast using alpha (gain) and beta (bias)
                alpha = contrast
                beta = 0  # No bias
                img = np.clip(img * alpha + beta, 0, 255).astype(np.uint8)

            # 4. CLAHE (Contrast Limited Adaptive Histogram Equalization)
            if clahe > 0:
                # Convert to LAB color space
                lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)

                # Apply CLAHE to L channel
                clahe_obj = cv2.createCLAHE(clipLimit=clahe * 2, tileGridSize=(8, 8))
                l = clahe_obj.apply(l)

                # Merge and convert back
                lab = cv2.merge([l, a, b])
                img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

            # Save output
            output_filename = f"adjusted_{Path(image_path).stem}_{int(gamma*100)}_{int(contrast*100)}.png"
            output_path = self.temp_dir / output_filename

            cv2.imwrite(str(output_path), img)
            result['output_path'] = str(output_path)
            
            self.logger.info(f"Applied adjustments: gamma={gamma}, contrast={contrast}, exposure={exposure}, clahe={clahe}")
            
        except Exception as e:
            self.logger.error(f"Adjustment failed: {e}")
            result['error'] = str(e)
        
        return result
    
    def create_comparison_image(self, original_path: str, 
                               adjusted_path: str) -> str:
        """Create side-by-side comparison image."""
        try:
            # Read both images
            original = cv2.imread(original_path)
            adjusted = cv2.imread(adjusted_path)
            
            if original is None or adjusted is None:
                raise ValueError("Failed to load images for comparison")
            
            # Resize to match if necessary
            if original.shape != adjusted.shape:
                adjusted = cv2.resize(adjusted, (original.shape[1], original.shape[0]))
            
            # Create side-by-side
            comparison = np.hstack([original, adjusted])
            
            # Add labels
            label_original = np.zeros((50, original.shape[1], 3), dtype=np.uint8)
            label_adjusted = np.zeros((50, adjusted.shape[1], 3), dtype=np.uint8)
            
            cv2.putText(label_original, "Original", (20, 35), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.putText(label_adjusted, "Adjusted", (20, 35), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            
            # Combine
            comparison = np.vstack([label_original, comparison, label_adjusted])
            
            # Save
            output_path = self.temp_dir / f"comparison_{Path(original_path).stem}.png"
            cv2.imwrite(str(output_path), comparison)
            
            return str(output_path)
            
        except Exception as e:
            self.logger.error(f"Comparison creation failed: {e}")
            return None
    
    def detect_text_regions(self, image_path: str) -> List[Dict]:
        """Detect regions that might contain text (for OCR ghosting check)."""
        text_regions = []
        
        try:
            img = cv2.imread(image_path)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Apply threshold
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # Find contours
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                
                # Filter by size (text regions are usually small)
                if w > 20 and h > 10 and w < img.shape[1] * 0.8:
                    text_regions.append({
                        'bbox': (int(x), int(y), int(x + w), int(y + h)),
                        'size': (int(w), int(h)),
                        'aspect_ratio': round(w / h, 2) if h > 0 else 0
                    })
            
        except Exception as e:
            self.logger.warning(f"Text region detection failed: {e}")
        
        return text_regions
