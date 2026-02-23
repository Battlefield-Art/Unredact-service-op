"""
UnredactServiceOp - Report Generator
Generates professional PDF and JSON audit reports with risk scores.
"""

import os
import sys
import json
import base64
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import loguru

# Try imports with graceful degradation
try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        Image as RLImage, PageBreak, HRFlowable
    )
    from reportlab.pdfgen import canvas
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


class ReportGenerator:
    """
    Report Generator for creating professional audit reports.
    
    Generates:
    - JSON reports with full analysis data
    - PDF reports with formatted results, risk scores, and recommendations
    """
    
    def __init__(self, trace_id: str = None):
        self.trace_id = trace_id or 'unknown'
        self.logger = loguru.logger.bind(trace_id=self.trace_id)
        
        # Colors for risk levels
        self.RISK_COLORS = {
            'none': colors.green,
            'low': colors.lightgreen,
            'medium': colors.orange,
            'high': colors.red,
            'critical': colors.darkred
        }
    
    def generate_json_report(self, analysis_results: Dict[str, Any], output_path: str):
        """Generate JSON report with full analysis results."""
        
        try:
            # Build comprehensive report structure
            report = {
                'report_metadata': {
                    'report_type': 'Redaction Audit Report',
                    'generated_at': datetime.now().isoformat(),
                    'trace_id': self.trace_id,
                    'version': '1.0.0'
                },
                'file_info': {
                    'filename': analysis_results.get('filename', 'unknown'),
                    'file_type': analysis_results.get('file_type', 'unknown'),
                    'file_size': analysis_results.get('file_size', 0),
                    'file_path': analysis_results.get('file_path', '')
                },
                'analysis_summary': {
                    'status': analysis_results.get('status', 'unknown'),
                    'risk_score': analysis_results.get('risk_score', 0),
                    'page_count': analysis_results.get('page_count', 1),
                    'processing_time_seconds': analysis_results.get('processing_time', 0)
                },
                'metadata_analysis': analysis_results.get('metadata', {}),
                'redaction_issues': analysis_results.get('redaction_issues', []),
                'ocr_findings': analysis_results.get('ocr_findings', []),
                'risk_factors': analysis_results.get('risk_factors', []),
                'recommendations': analysis_results.get('recommendations', []),
                'image_analysis': analysis_results.get('image_analysis', {}),
                'raw_data': analysis_results
            }
            
            # Write JSON
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, default=str, ensure_ascii=False)
            
            self.logger.info(f"JSON report generated: {output_path}")
            
        except Exception as e:
            self.logger.error(f"JSON report generation failed: {e}")
            raise
    
    def generate_pdf_report(self, analysis_results: Dict[str, Any], output_path: str):
        """Generate professional PDF report with formatted results."""
        
        if not REPORTLAB_AVAILABLE:
            self.logger.warning("ReportLab not available - PDF generation skipped")
            return
        
        try:
            # Create PDF document
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72
            )
            
            # Build content
            story = []
            styles = self._create_custom_styles()
            
            # Title
            title = Paragraph(
                '<b>UNREDACTSERVICEOP</b><br/>Redaction Audit Report',
                styles['Title']
            )
            story.append(title)
            story.append(Spacer(1, 0.3 * inch))
            
            # Report metadata
            metadata_data = [
                ['Report ID:', self.trace_id],
                ['Generated:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
                ['Filename:', analysis_results.get('filename', 'unknown')],
                ['File Type:', analysis_results.get('file_type', 'unknown').upper()],
            ]
            
            metadata_table = Table(metadata_data, colWidths=[1.5 * inch, 4 * inch])
            metadata_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(metadata_table)
            story.append(Spacer(1, 0.3 * inch))
            
            # Risk Score Section
            risk_score = analysis_results.get('risk_score', 0)
            risk_level = self._get_risk_level(risk_score)
            
            story.append(Paragraph('<b>Risk Assessment</b>', styles['Heading2']))
            story.append(Spacer(1, 0.1 * inch))
            
            # Risk score visualization
            score_data = [
                ['Risk Score:', f"{risk_score}/100"],
                ['Risk Level:', risk_level.upper()],
                ['Status:', analysis_results.get('status', 'unknown').upper()]
            ]
            
            score_table = Table(score_data, colWidths=[1.5 * inch, 4 * inch])
            score_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                ('TEXTCOLOR', (1, 0), (1, 0), self.RISK_COLORS.get(risk_level, colors.black)),
                ('TEXTCOLOR', (1, 1), (1, 1), self.RISK_COLORS.get(risk_level, colors.black)),
            ]))
            story.append(score_table)
            story.append(Spacer(1, 0.3 * inch))
            
            # Horizontal line
            story.append(HRFlowable(width='100%', color=colors.gray, thickness=1))
            story.append(Spacer(1, 0.2 * inch))
            
            # Findings Section
            story.append(Paragraph('<b>Analysis Findings</b>', styles['Heading2']))
            story.append(Spacer(1, 0.1 * inch))
            
            # Redaction Issues
            redaction_issues = analysis_results.get('redaction_issues', [])
            if redaction_issues:
                story.append(Paragraph(f'Redaction Issues Found: {len(redaction_issues)}', styles['Heading3']))
                
                for i, issue in enumerate(redaction_issues[:10], 1):  # Limit to 10
                    if isinstance(issue, dict):
                        issue_text = f"{i}. <b>{issue.get('type', 'Unknown').replace('_', ' ').title()}</b> - "
                        issue_text += f"Page {issue.get('page', 'N/A')}, "
                        issue_text += f"Severity: {issue.get('severity', 'unknown')}"
                        
                        if issue.get('description'):
                            issue_text += f"<br/>&nbsp;&nbsp;&nbsp;{issue.get('description')}"
                        
                        story.append(Paragraph(issue_text, styles['Normal']))
                        story.append(Spacer(1, 0.1 * inch))
            else:
                story.append(Paragraph('No redaction issues detected.', styles['Normal']))
                story.append(Spacer(1, 0.1 * inch))
            
            # OCR Findings
            ocr_findings = analysis_results.get('ocr_findings', [])
            if ocr_findings:
                story.append(Spacer(1, 0.2 * inch))
                story.append(Paragraph(f'OCR Ghosting Findings: {len(ocr_findings)}', styles['Heading3']))
                
                for i, finding in enumerate(ocr_findings[:5], 1):
                    if isinstance(finding, dict):
                        finding_text = f"{i}. <b>{finding.get('type', 'Unknown').replace('_', ' ').title()}</b> - "
                        finding_text += f"Page {finding.get('page', 'N/A')}, "
                        finding_text += f"Confidence: {finding.get('confidence', 0):.1%}"
                        
                        if finding.get('description'):
                            finding_text += f"<br/>&nbsp;&nbsp;&nbsp;{finding.get('description')}"
                        
                        story.append(Paragraph(finding_text, styles['Normal']))
                        story.append(Spacer(1, 0.1 * inch))
            
            # Metadata Analysis
            story.append(Spacer(1, 0.2 * inch))
            story.append(Paragraph('<b>Metadata Analysis</b>', styles['Heading2']))
            story.append(Spacer(1, 0.1 * inch))
            
            metadata = analysis_results.get('metadata', {})
            if metadata:
                risk_level_meta = metadata.get('risk_level', 'none')
                risk_factors = metadata.get('risk_factors', [])
                
                meta_text = f"Metadata Risk Level: <b>{risk_level_meta.upper()}</b>"
                story.append(Paragraph(meta_text, styles['Normal']))
                story.append(Spacer(1, 0.1 * inch))
                
                if risk_factors:
                    story.append(Paragraph('Risk Factors:', styles['Normal']))
                    for factor in risk_factors[:5]:
                        story.append(Paragraph(f"• {factor}", styles['Normal']))
                    story.append(Spacer(1, 0.1 * inch))
            else:
                story.append(Paragraph('No metadata analysis available.', styles['Normal']))
            
            # Recommendations
            recommendations = analysis_results.get('recommendations', [])
            if recommendations:
                story.append(Spacer(1, 0.2 * inch))
                story.append(Paragraph('<b>Recommendations</b>', styles['Heading2']))
                story.append(Spacer(1, 0.1 * inch))
                
                for i, rec in enumerate(recommendations, 1):
                    story.append(Paragraph(f"{i}. {rec}", styles['Normal']))
                    story.append(Spacer(1, 0.1 * inch))
            
            # Processing Info
            story.append(Spacer(1, 0.3 * inch))
            story.append(HRFlowable(width='100%', color=colors.gray, thickness=1))
            story.append(Spacer(1, 0.1 * inch))
            
            processing_time = analysis_results.get('processing_time', 0)
            page_count = analysis_results.get('page_count', 1)
            
            info_text = f"""
            <b>Analysis Information:</b><br/>
            Processing Time: {processing_time:.2f} seconds<br/>
            Pages Analyzed: {page_count}
            """
            story.append(Paragraph(info_text, styles['Normal']))
            
            # Footer disclaimer
            story.append(Spacer(1, 0.3 * inch))
            disclaimer = """
            <font size="8">This report is generated by UnredactServiceOp and is intended for informational purposes only. 
            Always manually verify redaction results before sharing sensitive documents.</font>
            """
            story.append(Paragraph(disclaimer, styles['Normal']))
            
            # Build PDF
            doc.build(story)
            
            self.logger.info(f"PDF report generated: {output_path}")
            
        except Exception as e:
            self.logger.error(f"PDF report generation failed: {e}")
            raise
    
    def _create_custom_styles(self):
        """Create custom paragraph styles for the report."""
        styles = getSampleStyleSheet()
        
        # Title style
        styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=styles['Title'],
            fontSize=24,
            textColor=colors.HexColor('#1a1a2e'),
            spaceAfter=20,
            alignment=TA_CENTER
        ))
        
        # Heading styles
        styles.add(ParagraphStyle(
            name='CustomHeading2',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#16213e'),
            spaceBefore=15,
            spaceAfter=10
        ))
        
        styles.add(ParagraphStyle(
            name='CustomHeading3',
            parent=styles['Heading3'],
            fontSize=12,
            textColor=colors.HexColor('#0f3460'),
            spaceBefore=10,
            spaceAfter=8
        ))
        
        # Normal style
        styles.add(ParagraphStyle(
            name='CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=5
        ))
        
        return {
            'Title': styles['CustomTitle'],
            'Heading2': styles['CustomHeading2'],
            'Heading3': styles['CustomHeading3'],
            'Normal': styles['CustomNormal']
        }
    
    def _get_risk_level(self, score: int) -> str:
        """Convert risk score to risk level."""
        if score == 0:
            return 'none'
        elif score < 30:
            return 'low'
        elif score < 60:
            return 'medium'
        elif score < 85:
            return 'high'
        else:
            return 'critical'
    
    def generate_summary_report(self, results: List[Dict[str, Any]]) -> str:
        """Generate a summary report for multiple files."""
        
        summary = {
            'total_files': len(results),
            'total_risk_score': 0,
            'high_risk_count': 0,
            'medium_risk_count': 0,
            'low_risk_count': 0,
            'clean_count': 0,
            'files': []
        }
        
        for result in results:
            score = result.get('risk_score', 0)
            summary['total_risk_score'] += score
            
            if score >= 85:
                summary['high_risk_count'] += 1
            elif score >= 60:
                summary['medium_risk_count'] += 1
            elif score >= 30:
                summary['low_risk_count'] += 1
            else:
                summary['clean_count'] += 1
            
            summary['files'].append({
                'filename': result.get('filename', 'unknown'),
                'risk_score': score,
                'status': result.get('status', 'unknown')
            })
        
        if results:
            summary['average_risk_score'] = summary['total_risk_score'] / len(results)
        else:
            summary['average_risk_score'] = 0
        
        return json.dumps(summary, indent=2)
