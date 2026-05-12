"""Generación de PDF para bonos (NewTicket), compartida por descarga y envío por email."""
from __future__ import annotations

from io import BytesIO

import qrcode
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from events.models import GrupoMiembro
from tickets.models import NewTicket


def build_new_ticket_pdf_bytes(ticket: NewTicket) -> bytes:
    """
    Construye el PDF del bono (misma presentación que la descarga desde mi-fuego).
    Requiere reportlab y qrcode instalados.
    """
    buffer = BytesIO()

    def draw_background_pattern(canvas_obj, doc):
        canvas_obj.saveState()

        pattern_color = colors.HexColor('#e0e0e0')
        canvas_obj.setFillColor(pattern_color)
        canvas_obj.setStrokeColor(pattern_color)
        canvas_obj.setFillAlpha(0.2)
        canvas_obj.setStrokeAlpha(0.2)

        pattern_width = 48 * 0.75
        pattern_height = 49 * 0.75

        content_width = doc.width
        content_height = doc.height

        for x in range(0, int(content_width) + int(pattern_width), int(pattern_width)):
            for y in range(0, int(content_height) + int(pattern_height), int(pattern_height)):
                abs_x = doc.leftMargin + x
                abs_y = doc.bottomMargin + y

                center_x = abs_x + pattern_width / 2
                center_y = abs_y + pattern_height / 2
                radius = 6
                canvas_obj.circle(center_x, center_y, radius, fill=1, stroke=0)

                line_length = 10
                canvas_obj.line(
                    center_x - line_length, center_y, center_x + line_length, center_y
                )
                canvas_obj.line(
                    center_x, center_y - line_length, center_x, center_y + line_length
                )

                canvas_obj.circle(abs_x + 8, abs_y + 5, 5, fill=1, stroke=0)
                canvas_obj.circle(abs_x + pattern_width - 8, abs_y + 5, 5, fill=1, stroke=0)

        canvas_obj.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        onFirstPage=draw_background_pattern,
        onLaterPages=draw_background_pattern,
    )
    elements = []

    styles = getSampleStyleSheet()

    header_style = ParagraphStyle(
        'Header',
        parent=styles['Normal'],
        fontSize=16,
        textColor=colors.white,
        fontName='Helvetica-Bold',
        spaceAfter=15,
        alignment=0,
    )

    normal_style = ParagraphStyle(
        'Normal',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor('#1e1e1e'),
        spaceAfter=8,
        alignment=0,
    )

    info_style = ParagraphStyle(
        'Info',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.HexColor('#666666'),
        spaceAfter=5,
        alignment=1,
    )

    header_bg_color = colors.HexColor('#198754')

    ticket_id = str(ticket.key)[-2:].upper()
    header_text = f"#{ticket_id} {ticket.event.name}"
    if ticket.is_used:
        header_text = f"#{ticket_id} {ticket.event.name} - USADO"
        header_bg_color = colors.HexColor('#6c757d')

    header_data = [[Paragraph(header_text, header_style)]]
    header_table = Table(header_data, colWidths=[doc.width])
    header_table.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, -1), header_bg_color),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 14),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, -1), 12),
                ('LEFTPADDING', (0, 0), (-1, -1), 15),
                ('RIGHTPADDING', (0, 0), (-1, -1), 15),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 0))

    grupo_miembro = None
    ticket_user = ticket.holder if ticket.holder else ticket.owner

    if ticket_user:
        grupo_miembro = (
            GrupoMiembro.objects.filter(grupo__event=ticket.event, user=ticket_user)
            .select_related('grupo')
            .first()
        )

    body_elements = []

    if ticket.owner:
        owner_name = f"{ticket.owner.first_name} {ticket.owner.last_name}".strip() or ticket.owner.email
        body_elements.append(Paragraph(f'<b>Nombre:</b> {owner_name}', normal_style))
        if hasattr(ticket.owner, 'profile') and ticket.owner.profile.document_number:
            body_elements.append(
                Paragraph(f'<b>DNI:</b> {ticket.owner.profile.document_number}', normal_style)
            )

    body_elements.append(Paragraph(f'<b>Tipo de Bono:</b> {ticket.ticket_type.name}', normal_style))
    body_elements.append(Paragraph(f'<b>Precio:</b> ${ticket.ticket_type.price:,.0f}', normal_style))

    if ticket.event.location:
        body_elements.append(Paragraph(f'<b>Ubicación:</b> {ticket.event.location}', normal_style))

    if ticket.event.start:
        body_elements.append(
            Paragraph(
                f'<b>Fecha:</b> {ticket.event.start.strftime("%d/%m/%Y %H:%M")}',
                normal_style,
            )
        )

    if grupo_miembro:
        has_ingreso = grupo_miembro.ingreso_anticipado or bool(grupo_miembro.ingreso_anticipado_fecha)
        if has_ingreso:
            body_elements.append(Spacer(1, 0.15 * inch))
            if grupo_miembro.ingreso_anticipado_fecha:
                body_elements.append(
                    Paragraph(
                        f'<b>Ingreso Anticipado:</b> Podés ingresar desde el '
                        f'{grupo_miembro.ingreso_anticipado_fecha.strftime("%d/%m/%Y")}',
                        normal_style,
                    )
                )
            elif grupo_miembro.grupo.ingreso_anticipado_desde:
                body_elements.append(
                    Paragraph(
                        f'<b>Ingreso Anticipado:</b> Podés ingresar desde el '
                        f'{timezone.localtime(grupo_miembro.grupo.ingreso_anticipado_desde).strftime("%d/%m/%Y %H:%M")}',
                        normal_style,
                    )
                )
            else:
                body_elements.append(
                    Paragraph(
                        '<b>Ingreso Anticipado:</b> Tenés ingreso anticipado habilitado',
                        normal_style,
                    )
                )

        if grupo_miembro.late_checkout:
            if grupo_miembro.grupo.late_checkout_hasta:
                body_elements.append(
                    Paragraph(
                        f'<b>Late Checkout:</b> Podés salir hasta el '
                        f'{timezone.localtime(grupo_miembro.grupo.late_checkout_hasta).strftime("%d/%m/%Y %H:%M")}',
                        normal_style,
                    )
                )
            else:
                body_elements.append(
                    Paragraph('<b>Late Checkout:</b> Tenés late checkout habilitado', normal_style)
                )

        if grupo_miembro.restriccion and grupo_miembro.restriccion != 'sin_restricciones':
            restriccion_display = dict(grupo_miembro.RESTRICCION_CHOICES).get(
                grupo_miembro.restriccion, grupo_miembro.restriccion
            )
            body_elements.append(
                Paragraph(f'<b>Restricción Alimentaria:</b> {restriccion_display}', normal_style)
            )

    body_elements.append(Spacer(1, 0.2 * inch))

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(str(ticket.key))
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    qr_buffer = BytesIO()
    qr_img.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)

    qr_image = Image(qr_buffer, width=2.5 * inch, height=2.5 * inch)
    qr_image.hAlign = 'CENTER'
    body_elements.append(qr_image)
    body_elements.append(Spacer(1, 0.15 * inch))

    body_elements.append(Paragraph(f'<b>Código:</b> {str(ticket.key)[-8:].upper()}', info_style))
    body_elements.append(
        Paragraph(f'<i>Bono válido solo para {ticket.event.name}</i>', info_style)
    )

    body_data = [[body_elements]]
    body_table = Table(body_data, colWidths=[doc.width])
    body_table.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, -1), colors.white),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#1e1e1e')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('FONTSIZE', (0, 0), (-1, -1), 11),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 20),
                ('TOPPADDING', (0, 0), (-1, -1), 20),
                ('LEFTPADDING', (0, 0), (-1, -1), 20),
                ('RIGHTPADDING', (0, 0), (-1, -1), 20),
                ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
                ('GRID', (0, 0), (-1, -1), 0, colors.white),
            ]
        )
    )
    elements.append(body_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer.read()


def new_ticket_pdf_filename(ticket: NewTicket) -> str:
    return f'bono_{ticket.event.slug or ticket.event.id}_{str(ticket.key)[-8:]}.pdf'
