"""The ONE predefined certificate template (ReportLab, A4 landscape) with a verification QR code."""
from datetime import date
from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1F2A44")
GOLD = colors.HexColor("#B8903A")
GREY = colors.HexColor("#6B7280")


def format_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"  # NOT %-d: that crashes on Windows


def _fit_size(text: str, font: str, start: float, minimum: float, max_width: float) -> float:
    """Largest font size <= start (and >= minimum) at which `text` fits in max_width."""
    size = start
    while size > minimum and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(
    *,
    certificate_id: str,
    recipient_name: str,
    event_name: str,
    issuer: str,
    issue_date: date,
    verify_url: str,
    compress: bool = True,
) -> bytes:
    buf = BytesIO()
    c = Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1 if compress else 0)
    c.setTitle(f"Certificate - {recipient_name}")
    c.setAuthor(issuer or "Certificate Service")
    cx = PAGE_W / 2
    text_w = PAGE_W - 160

    # double border
    c.setStrokeColor(NAVY); c.setLineWidth(4); c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setStrokeColor(GOLD); c.setLineWidth(1.5); c.rect(34, 34, PAGE_W - 68, PAGE_H - 68)

    c.setFillColor(NAVY); c.setFont("Times-Bold", 42)
    c.drawCentredString(cx, PAGE_H - 125, "CERTIFICATE")
    c.setFillColor(GOLD); c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 152, "O F   P A R T I C I P A T I O N")

    c.setFillColor(GREY); c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 205, "This is to certify that")

    size = _fit_size(recipient_name, "Times-BoldItalic", 44, 14, text_w)  # long names shrink to fit
    c.setFillColor(NAVY); c.setFont("Times-BoldItalic", size)
    c.drawCentredString(cx, PAGE_H - 262, recipient_name)
    c.setStrokeColor(GOLD); c.setLineWidth(1)
    line_w = min(text_w, max(220, stringWidth(recipient_name, "Times-BoldItalic", size) + 40))
    c.line(cx - line_w / 2, PAGE_H - 272, cx + line_w / 2, PAGE_H - 272)

    c.setFillColor(GREY); c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 308, "has successfully participated in")

    c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 22)
    y = PAGE_H - 345
    for line in simpleSplit(event_name, "Helvetica-Bold", 22, text_w)[:3]:  # wrap long event names
        c.drawCentredString(cx, y, line)
        y -= 28

    c.setFillColor(GREY); c.setFont("Helvetica", 12)
    c.drawCentredString(cx, 120, f"Issued on {format_date(issue_date)}")
    if issuer:
        c.setStrokeColor(NAVY); c.setLineWidth(0.8)
        c.line(cx - 110, 98, cx + 110, 98)
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(cx, 82, issuer)

    # QR code -> public verification URL; certificate id printed beside it
    qr_size = 78
    widget = QrCodeWidget(verify_url)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(qr_size, qr_size, transform=[qr_size / (x1 - x0), 0, 0, qr_size / (y1 - y0), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, PAGE_W - 60 - qr_size, 56)
    c.setFillColor(GREY); c.setFont("Helvetica", 7)
    c.drawRightString(PAGE_W - 60, 46, f"ID: {certificate_id}")
    c.drawString(60, 46, "Scan the QR code to verify this certificate")

    c.showPage()
    c.save()
    return buf.getvalue()
