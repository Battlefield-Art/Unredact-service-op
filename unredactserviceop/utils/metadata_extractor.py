"""
UnredactServiceOp - Metadata Extractor
Extracts and analyzes EXIF, PDF, and image metadata with risk flagging.
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import loguru

# Try imports with graceful degradation
try:
    import exifread
    EXIFREAD_AVAILABLE = True
except ImportError:
    EXIFREAD_AVAILABLE = False

try:
    import piexif
    PIEXIF_AVAILABLE = True
except ImportError:
    PIEXIF_AVAILABLE = False

try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


class MetadataExtractor:
    """
    Metadata Extractor with risk flagging for sensitive information.
    
    Extracts:
    - EXIF data from images (GPS, camera info, timestamps)
    - PDF metadata (author, creator, timestamps)
    - File system metadata
    
    Risk flags:
    - GPS coordinates (location leak)
    - Author/Creator names
    - Timestamps (creation/modification dates)
    - Software versions
    - Custom fields
    """
    
    def __init__(self, trace_id: str = None):
        self.trace_id = trace_id or 'unknown'
        self.logger = loguru.logger.bind(trace_id=self.trace_id)
        
        # Risk thresholds
        self.HIGH_RISK_FIELDS = ['gps', 'latitude', 'longitude', 'location', 'coordinates']
        self.MEDIUM_RISK_FIELDS = ['author', 'creator', 'producer', 'artist', 'copyright']
        self.LOW_RISK_FIELDS = ['datetime', 'timestamp', 'date', 'software', 'tool']
    
    def extract_pdf_metadata(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extract metadata from PDF file with risk assessment.
        
        Returns:
            Dictionary with metadata and risk flags
        """
        metadata = {
            'file_path': pdf_path,
            'extraction_time': datetime.now().isoformat(),
            'fields': {},
            'risk_level': 'none',
            'risk_factors': [],
            'cleaned_fields': {}
        }
        
        if not PYPDF_AVAILABLE:
            self.logger.warning("pypdf not available - PDF metadata extraction skipped")
            metadata['error'] = 'pypdf library not available'
            return metadata
        
        try:
            reader = PdfReader(pdf_path)
            
            # Get document info
            if reader.metadata:
                for key, value in reader.metadata.items():
                    if value:
                        # Clean key name
                        clean_key = key.strip('/').lower()
                        metadata['fields'][clean_key] = str(value)
            
            # Get page count
            metadata['page_count'] = len(reader.pages)
            
            # Get PDF version
            metadata['pdf_version'] = reader.pdf_version if hasattr(reader, 'pdf_version') else 'unknown'
            
            # Check for encryption
            metadata['is_encrypted'] = reader.is_encrypted
            
            # Analyze risks
            risk_analysis = self._analyze_metadata_risks(metadata['fields'])
            metadata['risk_level'] = risk_analysis['level']
            metadata['risk_factors'] = risk_analysis['factors']
            metadata['cleaned_fields'] = risk_analysis['cleaned']
            
            self.logger.info(f"Extracted PDF metadata: {len(metadata['fields'])} fields, risk: {metadata['risk_level']}")
            
        except Exception as e:
            self.logger.error(f"PDF metadata extraction failed: {e}")
            metadata['error'] = str(e)
        
        return metadata
    
    def extract_image_metadata(self, image_path: str) -> Dict[str, Any]:
        """
        Extract metadata from image file with risk assessment.
        
        Handles:
        - EXIF data (via exifread)
        - EXIF binary (via piexif)
        - IPTC metadata
        - XMP metadata
        """
        metadata = {
            'file_path': image_path,
            'extraction_time': datetime.now().isoformat(),
            'fields': {},
            'risk_level': 'none',
            'risk_factors': [],
            'cleaned_fields': {},
            'has_gps': False,
            'gps_coordinates': None
        }
        
        try:
            # Determine file type
            file_ext = Path(image_path).suffix.lower()
            
            # Extract based on file type
            if file_ext in ['.jpg', '.jpeg']:
                if EXIFREAD_AVAILABLE:
                    metadata.update(self._extract_exif_read(image_path))
                if PIEXIF_AVAILABLE:
                    metadata.update(self._extract_piexif(image_path))
            elif file_ext == '.png':
                metadata.update(self._extract_png_metadata(image_path))
            
            # Add file system metadata
            file_stat = Path(image_path).stat()
            metadata['file_size'] = file_stat.st_size
            metadata['file_created'] = datetime.fromtimestamp(file_stat.st_ctime).isoformat()
            metadata['file_modified'] = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            # Analyze risks
            risk_analysis = self._analyze_metadata_risks(metadata['fields'])
            metadata['risk_level'] = risk_analysis['level']
            metadata['risk_factors'] = risk_analysis['factors']
            metadata['cleaned_fields'] = risk_analysis['cleaned']
            
            # Check for GPS
            if 'gps_coordinates' in metadata and metadata['gps_coordinates']:
                metadata['has_gps'] = True
                metadata['risk_factors'].append('GPS coordinates found - location exposure risk')
                metadata['risk_level'] = 'high'
            
            self.logger.info(f"Extracted image metadata: {len(metadata['fields'])} fields, risk: {metadata['risk_level']}")
            
        except Exception as e:
            self.logger.error(f"Image metadata extraction failed: {e}")
            metadata['error'] = str(e)
        
        return metadata
    
    def _extract_exif_read(self, image_path: str) -> Dict[str, Any]:
        """Extract EXIF using exifread library."""
        result = {}
        
        try:
            with open(image_path, 'rb') as f:
                tags = exifread.process_file(f, details=False)
            
            for tag, value in tags.items():
                # Skip thumbnail
                if 'thumbnail' in tag.lower():
                    continue
                    
                # Convert to string
                try:
                    str_value = str(value)
                except:
                    str_value = repr(value)
                
                # Clean tag name
                clean_tag = tag.replace(' ', '_').replace('/', '_').lower()
                result[clean_tag] = str_value
                
                # Check for GPS
                if 'gps' in tag.lower():
                    result[f'{clean_tag}_value'] = str_value
        
        except Exception as e:
            self.logger.warning(f"EXIF read extraction failed: {e}")
        
        return {'fields': result} if result else {}
    
    def _extract_piexif(self, image_path: str) -> Dict[str, Any]:
        """Extract EXIF binary using piexif library."""
        result = {}
        
        try:
            exif_dict = piexif.load(image_path)
            
            # Process each IFD (Image File Directory)
            ifd_names = ['0th', 'Exif', 'GPS', '1st', 'Interop']
            
            for ifd_name in ifd_names:
                if ifd_name in exif_dict and exif_dict[ifd_name]:
                    for tag_id, tag_value in exif_dict[ifd_name].items():
                        try:
                            tag_name = piexif.TAGS[ifd_name].get(tag_id, {}).get('name', f'tag_{tag_id}')
                            
                            # Handle different value types
                            if isinstance(tag_value, bytes):
                                if tag_name in ['MakerNote', 'UserComment']:
                                    continue  # Skip large binary
                                try:
                                    tag_value = tag_value.decode('utf-8', errors='ignore')
                                except:
                                    tag_value = f'<binary: {len(tag_value)} bytes>'
                            
                            result[f'{ifd_name}_{tag_name}'] = str(tag_value)
                            
                        except Exception:
                            continue
            
            # Extract GPS coordinates if available
            if 'GPS' in exif_dict and exif_dict['GPS']:
                gps = exif_dict['GPS']
                
                def convert_gps(coords, ref):
                    """Convert GPS coordinates to decimal degrees."""
                    if len(coords) >= 3:
                        degrees = coords[0].num / coords[0].den
                        minutes = coords[1].num / coords[1].den
                        seconds = coords[2].num / coords[2].den
                        
                        result = degrees + (minutes / 60) + (seconds / 3600)
                        if ref in ['S', 'W']:
                            result = -result
                        return result
                    return None
                
                lat = convert_gps(gps.get(piexif.GPSIFD.GPSLatitude, []), 
                                 gps.get(piexif.GPSIFD.GPSLatitudeRef, 'N'))
                lon = convert_gps(gps.get(piexif.GPSIFD.GPSLongitude, []), 
                                 gps.get(piexif.GPSIFD.GPSLongitudeRef, 'E'))
                
                if lat and lon:
                    result['gps_coordinates'] = f"{lat:.6f}, {lon:.6f}"
        
        except Exception as e:
            self.logger.warning(f"Piexif extraction failed: {e}")
        
        return result if result else {}
    
    def _extract_png_metadata(self, image_path: str) -> Dict[str, Any]:
        """Extract PNG metadata (tEXt, iTXt, zTXt chunks)."""
        result = {}
        
        try:
            from PIL import Image
            img = Image.open(image_path)
            
            # Get basic info
            result['image_width'] = img.width
            result['image_height'] = img.height
            result['image_format'] = img.format
            result['image_mode'] = img.mode
            
            # Get EXIF if available
            if hasattr(img, '_getexif') and img._getexif():
                exif = img._getexif()
                for tag_id, value in exif.items():
                    result[f'exif_tag_{tag_id}'] = str(value)
            
            # Get info dictionary
            info = img.info
            for key, value in info.items():
                if not key.startswith('_'):
                    result[f'png_{key}'] = str(value)
        
        except Exception as e:
            self.logger.warning(f"PNG metadata extraction failed: {e}")
        
        return result
    
    def _analyze_metadata_risks(self, fields: Dict[str, str]) -> Dict[str, Any]:
        """
        Analyze metadata fields for sensitive information.
        
        Returns risk level and specific factors.
        """
        risk_factors = []
        cleaned = {}
        high_risk_count = 0
        medium_risk_count = 0
        low_risk_count = 0
        
        for key, value in fields.items():
            key_lower = key.lower()
            
            # Clean the value for storage (truncate long values)
            clean_value = str(value)[:200] if value else ''
            
            # Check risk level
            is_high_risk = any(risk in key_lower for risk in self.HIGH_RISK_FIELDS)
            is_medium_risk = any(risk in key_lower for risk in self.MEDIUM_RISK_FIELDS)
            is_low_risk = any(risk in key_lower for risk in self.LOW_RISK_FIELDS)
            
            if is_high_risk:
                high_risk_count += 1
                risk_factors.append(f"HIGH: {key} = {clean_value[:50]}")
                cleaned[key] = '[REDACTED]'
            elif is_medium_risk:
                medium_risk_count += 1
                risk_factors.append(f"MEDIUM: {key}")
                cleaned[key] = '[REDACTED]'
            elif is_low_risk:
                low_risk_count += 1
                cleaned[key] = clean_value
            else:
                cleaned[key] = clean_value
        
        # Determine overall level
        if high_risk_count > 0:
            level = 'high'
        elif medium_risk_count > 0:
            level = 'medium'
        elif low_risk_count > 0:
            level = 'low'
        else:
            level = 'none'
        
        return {
            'level': level,
            'factors': risk_factors,
            'cleaned': cleaned,
            'counts': {
                'high': high_risk_count,
                'medium': medium_risk_count,
                'low': low_risk_count
            }
        }
    
    def strip_metadata(self, file_path: str, output_path: str = None) -> str:
        """
        Strip metadata from file and save to new location.
        
        Returns path to cleaned file.
        """
        file_ext = Path(file_path).suffix.lower()
        
        if not output_path:
            temp_dir = Path(__file__).parent.parent / 'temp'
            temp_dir.mkdir(exist_ok=True)
            output_path = temp_dir / f"cleaned_{Path(file_path).name}"
        
        try:
            if file_ext in ['.jpg', '.jpeg', '.png']:
                # Use PIL to strip metadata
                from PIL import Image
                
                img = Image.open(file_path)
                
                # Create new image without metadata
                data = list(img.getdata())
                clean_img = Image.new(img.mode, img.size)
                clean_img.putdata(data)
                
                clean_img.save(output_path)
                
                self.logger.info(f"Metadata stripped: {file_path} -> {output_path}")
                
            elif file_ext == '.pdf':
                # For PDFs, we need to create a new version without metadata
                if PYPDF_AVAILABLE:
                    from pypdf import PdfReader, PdfWriter
                    
                    reader = PdfReader(file_path)
                    writer = PdfWriter()
                    
                    for page in reader.pages:
                        writer.add_page(page)
                    
                    # Remove metadata
                    writer.add_metadata({})
                    
                    with open(output_path, 'wb') as f:
                        writer.write(f)
                    
                    self.logger.info(f"PDF metadata stripped: {file_path} -> {output_path}")
            
            return str(output_path)
            
        except Exception as e:
            self.logger.error(f"Metadata stripping failed: {e}")
            raise
    
    def generate_metadata_report(self, metadata: Dict[str, Any]) -> str:
        """Generate human-readable metadata report."""
        report = []
        
        report.append("=" * 50)
        report.append("METADATA EXTRACTION REPORT")
        report.append("=" * 50)
        report.append(f"File: {metadata.get('file_path', 'unknown')}")
        report.append(f"Extraction Time: {metadata.get('extraction_time', 'unknown')}")
        report.append(f"Risk Level: {metadata.get('risk_level', 'unknown').upper()}")
        report.append("")
        
        # Risk factors
        if metadata.get('risk_factors'):
            report.append("RISK FACTORS:")
            for factor in metadata['risk_factors']:
                report.append(f"  - {factor}")
            report.append("")
        
        # Fields
        fields = metadata.get('cleaned_fields', metadata.get('fields', {}))
        if fields:
            report.append("METADATA FIELDS:")
            for key, value in fields.items():
                if value and value != '[REDACTED]':
                    report.append(f"  {key}: {value[:100]}")
        
        report.append("=" * 50)
        
        return "\n".join(report)
