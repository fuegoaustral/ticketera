from django.contrib import admin
from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse
from django.db import connection
from decimal import Decimal
from django.urls import path
from django.shortcuts import render
from django.forms import ModelForm
import csv
from .models import (
    Event,
    EventTermsAndConditions,
    EventTermsAndConditionsAcceptance,
    EventRequest,
    EventRequestTicketType,
    GrupoTipo,
    Grupo,
    GrupoMiembro,
    ArtProgram,
    Artwork,
    ArtworkGrantItem,
    ArtworkGrantItemPhoto,
    ArtworkInvitation,
    ArtworkLogisticsPerson,
    ArtworkPhoto,
    ArtworkCheckoutPhoto,
    ArtworkProvider,
    ArtworkProviderVehicle,
)
from .forms import LocalizedDecimalField


class EventAdminForm(ModelForm):
    class Meta:
        model = Event
        fields = '__all__'
        widgets = {
            'admins': FilteredSelectMultiple(
                verbose_name='Administradores',
                is_stacked=False
            ),
            'access_scanner': FilteredSelectMultiple(
                verbose_name='Usuarios con Acceso al Scanner',
                is_stacked=False
            ),
            'access_caja': FilteredSelectMultiple(
                verbose_name='Usuarios con Acceso a la Caja',
                is_stacked=False
            ),
        }


class EventAdmin(admin.ModelAdmin):
    form = EventAdminForm
    filter_horizontal = ('admins', 'access_scanner', 'access_caja')  # Esto también ayuda con la interfaz
    list_display = (
        "name",
        "slug",
        "active",
        "is_main",
        "start",
        "end",
        "max_tickets",
        "donations_art",
        "donations_venue",
        "donations_grant",
    )
    list_filter = ("active", "is_main")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    change_list_template = 'admin/events/event/change_list.html'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('tickets-report/', self.custom_query_view, name='event_tickets_report'),
            path('pending-transfers/', self.pending_transfers_view, name='event_pending_transfers'),
            path('export-csv/', self.export_csv, name='export_csv'),
            path('export-pending-transfers/', self.export_pending_transfers_csv, name='export_pending_transfers'),
            path('orders-report/', self.orders_report_view, name='event_orders_report'),
            path('export-orders-csv/', self.export_orders_csv, name='export_orders_csv'),
            path('tickets-sold-report/', self.tickets_sold_report_view, name='event_tickets_sold_report'),
            path('ingreso-anticipado-report/', self.ingreso_anticipado_report_view, name='event_ingreso_anticipado_report'),
            path('export-ingreso-anticipado-csv/', self.export_ingreso_anticipado_csv, name='export_ingreso_anticipado_csv'),
            path('export-ingreso-anticipado-pdf/', self.export_ingreso_anticipado_pdf, name='export_ingreso_anticipado_pdf'),
            path('late-checkout-report/', self.late_checkout_report_view, name='event_late_checkout_report'),
            path('export-late-checkout-csv/', self.export_late_checkout_csv, name='export_late_checkout_csv'),
            path('export-late-checkout-pdf/', self.export_late_checkout_pdf, name='export_late_checkout_pdf'),
        ]
        return custom_urls + urls

    def custom_query_view(self, request):
        # Check if user has permission to view tickets report
        if not request.user.is_superuser and not request.user.groups.filter(name='tickets_report_viewers').exists():
            return HttpResponse('Permission Denied', status=403)

        event_id = request.GET.get('event_id')
        search_term = request.GET.get('search', '')
        page = int(request.GET.get('page', 1))
        per_page = 50

        events = Event.objects.all()
        
        if event_id:
            with connection.cursor() as cursor:
                query = """
                    SELECT 
                        au.first_name,
                        au.last_name,
                        au.email,
                        upp.phone,
                        upp.document_type,
                        upp.document_number,
                        tt.name,
                        COALESCE(tn.volunteer_umpalumpa, false) as CAOS,
                        COALESCE(tn.volunteer_transmutator, false) as TRANSMUTADOR,
                        COALESCE(tn.volunteer_ranger, false) as RANGER,
                        COALESCE(tn.volunteer_mad, false) as MAD,
                        (SELECT COUNT(*)
                         FROM tickets_newticket tnh
                         WHERE tnh.holder_id = au.id
                           AND tnh.owner_id is null
                           AND tnh.event_id = %s) AS bonos_sin_compartir
                    FROM auth_user au
                    INNER JOIN user_profile_profile upp ON au.id = upp.user_id
                    INNER JOIN tickets_newticket tn ON au.id = tn.owner_id
                    INNER JOIN tickets_tickettype tt ON tn.ticket_type_id = tt.id
                    WHERE tn.event_id = %s
                """
                
                if search_term:
                    query += """ 
                        AND (
                            au.first_name ILIKE %s 
                            OR au.last_name ILIKE %s 
                            OR au.email ILIKE %s
                            OR upp.phone ILIKE %s
                            OR upp.document_number ILIKE %s
                        )
                    """
                    search_param = f'%{search_term}%'
                    cursor.execute(query + " ORDER BY bonos_sin_compartir DESC", 
                                 [event_id, event_id, search_param, search_param, search_param, search_param, search_param])
                else:
                    cursor.execute(query + " ORDER BY bonos_sin_compartir DESC", [event_id, event_id])
                
                columns = [col[0] for col in cursor.description]
                results = cursor.fetchall()

        else:
            results = []
            columns = []

        # Pagination
        total_results = len(results)
        total_pages = (total_results + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_results = results[start_idx:end_idx]

        context = {
            'events': events,
            'selected_event': event_id,
            'results': paginated_results,
            'columns': columns,
            'search_term': search_term,
            'page': page,
            'total_pages': total_pages,
            'has_next': page < total_pages,
            'has_prev': page > 1,
            'opts': self.model._meta,
        }
        
        return render(request, 'admin/tickets_report.html', context)

    def export_csv(self, request):
        event_id = request.GET.get('event_id')
        if not event_id:
            return HttpResponse('Event ID is required', status=400)

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    au.first_name,
                    au.last_name,
                    au.email,
                    upp.phone,
                    upp.document_type,
                    upp.document_number,
                    tt.name,
                    COALESCE(tn.volunteer_umpalumpa, false) as CAOS,
                    COALESCE(tn.volunteer_transmutator, false) as TRANSMUTADOR,
                    COALESCE(tn.volunteer_ranger, false) as RANGER,
                    COALESCE(tn.volunteer_mad, false) as MAD,
                    (SELECT COUNT(*)
                     FROM tickets_newticket tnh
                     WHERE tnh.holder_id = au.id
                       AND tnh.owner_id is null
                       AND tnh.event_id = %s) AS bonos_sin_compartir
                FROM auth_user au
                INNER JOIN user_profile_profile upp ON au.id = upp.user_id
                INNER JOIN tickets_newticket tn ON au.id = tn.owner_id
                INNER JOIN tickets_tickettype tt ON tn.ticket_type_id = tt.id
                WHERE tn.event_id = %s
                ORDER BY bonos_sin_compartir DESC
            """, [event_id, event_id])
            
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="event_report.csv"'
            
            writer = csv.writer(response)
            columns = [col[0] for col in cursor.description]
            writer.writerow(columns)
            
            for row in cursor.fetchall():
                writer.writerow(row)
            
            return response

    def pending_transfers_view(self, request):
        event_id = request.GET.get('event_id')
        events = Event.objects.all()
        
        with connection.cursor() as cursor:
            query = """
                SELECT tn.key, au.email as tx_from_email, tx_to_email, status, tt.name
                FROM tickets_newtickettransfer tntt
                INNER JOIN public.auth_user au ON au.id = tntt.tx_from_id
                INNER JOIN public.tickets_newticket tn ON tn.id = tntt.ticket_id
                INNER JOIN tickets_tickettype tt ON tn.ticket_type_id = tt.id
                WHERE status = 'PENDING'
            """
            
            if event_id:
                query += " AND tn.event_id = %s"
                cursor.execute(query, [event_id])
            else:
                cursor.execute(query)
                
            columns = [col[0] for col in cursor.description]
            results = cursor.fetchall()

        context = {
            'events': events,
            'selected_event': event_id,
            'results': results,
            'columns': columns,
            'opts': self.model._meta,
            'title': 'Transferencias Pendientes'
        }
        
        return render(request, 'admin/events/pending_transfers.html', context)

    def export_pending_transfers_csv(self, request):
        event_id = request.GET.get('event_id')
        
        with connection.cursor() as cursor:
            query = """
                SELECT tn.key, au.email as tx_from_email, tx_to_email, status, tt.name, e.name as event_name
                FROM tickets_newtickettransfer tntt
                INNER JOIN public.auth_user au ON au.id = tntt.tx_from_id
                INNER JOIN public.tickets_newticket tn ON tn.id = tntt.ticket_id
                INNER JOIN tickets_tickettype tt ON tn.ticket_type_id = tt.id
                INNER JOIN events_event e ON tn.event_id = e.id
                WHERE status = 'PENDING'
            """
            
            params = []
            if event_id:
                query += " AND tn.event_id = %s"
                params.append(event_id)
                
            cursor.execute(query, params)
            
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="pending_transfers.csv"'
            
            writer = csv.writer(response)
            columns = [col[0] for col in cursor.description]
            writer.writerow(columns)
            
            for row in cursor.fetchall():
                writer.writerow(row)
                
            return response

    def orders_report_view(self, request):
        event_id = request.GET.get('event_id')
        search_term = request.GET.get('search', '')
        events = Event.objects.all()
        
        if event_id:
            with connection.cursor() as cursor:
                query = """
                    select au.first_name,
                           au.last_name,
                           lower(too.email) as email,
                           upp.phone,
                           upp.document_type,
                           upp.document_number,
                           upp.profile_completion,
                           tt.name as ticket_type,
                           tot.quantity,
                           too.amount,
                           too.donation_art,
                           too.donation_venue,
                           too.donation_grant,
                           too.order_type,
                           too.response->>'id' as mercadopago_id,
                           too.status,
                           too.notes,
                           ae.email as emited_by
                    from tickets_order too
                         left join auth_user au on lower(au.email) = lower(too.email)
                         left join public.user_profile_profile upp on au.id = upp.user_id
                         inner join public.tickets_orderticket tot on too.id = tot.order_id
                         left join tickets_tickettype tt on tot.ticket_type_id = tt.id
                         left join auth_user ae on ae.id = too.generated_by_admin_user_id
                    where too.event_id = %s
                      and status = 'CONFIRMED'
                      and (
                          lower(au.first_name) LIKE %s 
                          OR lower(au.last_name) LIKE %s
                          OR lower(too.email) LIKE %s
                          OR upp.phone LIKE %s
                          OR upp.document_number LIKE %s
                      )
                    union
                    select au.first_name,
                           au.last_name,
                           lower(too.email) as email,
                           upp.phone,
                           upp.document_type,
                           upp.document_number,
                           upp.profile_completion,
                           'Dirigido' as ticket_type,
                           (too.amount/85000)::INTEGER as quantity,
                           too.amount,
                           too.donation_art,
                           too.donation_venue,
                           too.donation_grant,
                           too.order_type,
                           too.response->>'id' as mercadopago_id,
                           too.status,
                           too.notes,
                           ae.email as emited_by
                    from tickets_order too
                         left join auth_user au on lower(au.email) = lower(too.email)
                         left join public.user_profile_profile upp on au.id = upp.user_id
                         left join public.tickets_orderticket tot on too.id = tot.order_id
                         left join auth_user ae on ae.id = too.generated_by_admin_user_id
                    where too.event_id = %s
                      and status = 'CONFIRMED' 
                      and tot.id is NULL
                      and (
                          lower(au.first_name) LIKE %s 
                          OR lower(au.last_name) LIKE %s
                          OR lower(too.email) LIKE %s
                          OR upp.phone LIKE %s
                          OR upp.document_number LIKE %s
                      )
                """
                search_pattern = f'%{search_term.lower()}%'
                params = [
                    event_id, 
                    search_pattern, search_pattern, search_pattern, search_pattern, search_pattern,  # First union
                    event_id,
                    search_pattern, search_pattern, search_pattern, search_pattern, search_pattern   # Second union
                ]
                cursor.execute(query, params)
                columns = [col[0] for col in cursor.description]
                results = cursor.fetchall()
        else:
            results = []
            columns = []

        context = {
            'events': events,
            'selected_event': event_id,
            'search_term': search_term,
            'results': results,
            'columns': columns,
            'opts': self.model._meta,
            'title': 'Reporte de Ordenes'
        }
        
        return render(request, 'admin/events/orders_report.html', context)

    def export_orders_csv(self, request):
        event_id = request.GET.get('event_id')
        if not event_id:
            return HttpResponse('Event ID is required', status=400)

        with connection.cursor() as cursor:
            query = """
                select au.first_name,
                       au.last_name,
                       lower(too.email) as email,
                       upp.phone,
                       upp.document_type,
                       upp.document_number,
                       upp.profile_completion,
                       tt.name as ticket_type,
                       tot.quantity,
                       too.amount,
                       too.donation_art,
                       too.donation_venue,
                       too.donation_grant,
                       too.order_type,
                       too.response->>'id' as mercadopago_id,
                       too.status,
                       too.notes,
                       ae.email as emited_by
                from tickets_order too
                     left join auth_user au on lower(au.email) = lower(too.email)
                     left join public.user_profile_profile upp on au.id = upp.user_id
                     inner join public.tickets_orderticket tot on too.id = tot.order_id
                     left join tickets_tickettype tt on tot.ticket_type_id = tt.id
                     left join auth_user ae on ae.id = too.generated_by_admin_user_id
                where too.event_id = %s
                  and status = 'CONFIRMED'
                union
                select au.first_name,
                       au.last_name,
                       lower(too.email) as email,
                       upp.phone,
                       upp.document_type,
                       upp.document_number,
                       upp.profile_completion,
                       'Dirigido' as ticket_type,
                       (too.amount/85000)::INTEGER as quantity,
                       too.amount,
                       too.donation_art,
                       too.donation_venue,
                       too.donation_grant,
                       too.order_type,
                       too.response->>'id' as mercadopago_id,
                       too.status,
                       too.notes,
                       ae.email as emited_by
                from tickets_order too
                     left join auth_user au on lower(au.email) = lower(too.email)
                     left join public.user_profile_profile upp on au.id = upp.user_id
                     left join public.tickets_orderticket tot on too.id = tot.order_id
                     left join auth_user ae on ae.id = too.generated_by_admin_user_id
                where too.event_id = %s
                  and status = 'CONFIRMED' 
                  and tot.id is NULL
            """
            cursor.execute(query, [event_id, event_id])
            
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="orders_report.csv"'
            
            writer = csv.writer(response)
            columns = [col[0] for col in cursor.description]
            writer.writerow(columns)
            
            for row in cursor.fetchall():
                writer.writerow(row)
            
            return response

    def tickets_sold_report_view(self, request):
        # Check if user has permission to view tickets sold report
        if not (request.user.is_superuser or 
                request.user.groups.filter(name='Event Organizer').exists() or
                request.user.has_perm('events.view_tickets_sold_report')):
            return HttpResponse('Permission Denied', status=403)

        # Get only active events
        events = Event.objects.filter(active=True)
        
        # Get tickets sold per active event
        with connection.cursor() as cursor:
            query = """
                SELECT 
                    e.id,
                    e.name,
                    e.start,
                    e.end,
                    e.max_tickets,
                    COALESCE(SUM(tot.quantity), 0) as tickets_sold,
                    COALESCE(SUM(too.amount - COALESCE(too.donation_art, 0) - COALESCE(too.donation_venue, 0) - COALESCE(too.donation_grant, 0)), 0) as ticket_revenue,
                    COALESCE(SUM(too.donation_art), 0) as donations_art,
                    COALESCE(SUM(too.donation_venue), 0) as donations_venue,
                    COALESCE(SUM(too.donation_grant), 0) as donations_grant,
                    COALESCE(SUM(too.amount), 0) as total_revenue,
                    COUNT(DISTINCT too.id) as total_orders
                FROM events_event e
                LEFT JOIN tickets_order too ON e.id = too.event_id AND too.status = 'CONFIRMED'
                LEFT JOIN tickets_orderticket tot ON too.id = tot.order_id
                WHERE e.active = true
                GROUP BY e.id, e.name, e.start, e.end, e.max_tickets
                ORDER BY e.start DESC
            """
            cursor.execute(query)
            columns = [col[0] for col in cursor.description]
            results = cursor.fetchall()

        context = {
            'events': events,
            'results': results,
            'columns': columns,
            'opts': self.model._meta,
            'title': 'Reporte de Bonos Vendidos - Eventos Activos'
        }
        
        return render(request, 'admin/events/tickets_sold_report.html', context)

    def ingreso_anticipado_report_view(self, request):
        """Reporte de ingreso anticipado por grupo"""
        event_id = request.GET.get('event_id')
        events = Event.objects.all()
        
        if event_id:
            with connection.cursor() as cursor:
                query = """
                    SELECT 
                        g.nombre as grupo,
                        au.first_name as nombre,
                        au.last_name as apellido,
                        upp.document_type as documento_tipo,
                        upp.document_number as documento_numero,
                        au.email as email,
                        upp.phone as telefono,
                        g.ingreso_anticipado_desde as fecha_desde
                    FROM events_grupo g
                    INNER JOIN events_grupomiembro gm ON g.id = gm.grupo_id
                    INNER JOIN auth_user au ON gm.user_id = au.id
                    LEFT JOIN user_profile_profile upp ON au.id = upp.user_id
                    WHERE g.event_id = %s
                      AND gm.ingreso_anticipado = true
                    ORDER BY g.nombre, au.last_name, au.first_name
                """
                cursor.execute(query, [event_id])
                columns = [col[0] for col in cursor.description]
                results = cursor.fetchall()
        else:
            results = []
            columns = []

        context = {
            'events': events,
            'selected_event': event_id,
            'results': results,
            'columns': columns,
            'opts': self.model._meta,
            'title': 'Reporte de Ingreso Anticipado'
        }
        
        return render(request, 'admin/events/ingreso_anticipado_report.html', context)

    def export_ingreso_anticipado_csv(self, request):
        """Exportar reporte de ingreso anticipado a CSV"""
        event_id = request.GET.get('event_id')
        if not event_id:
            return HttpResponse('Event ID is required', status=400)

        with connection.cursor() as cursor:
            query = """
                SELECT 
                    g.nombre as grupo,
                    au.first_name as nombre,
                    au.last_name as apellido,
                    upp.document_type as documento_tipo,
                    upp.document_number as documento_numero,
                    au.email as email,
                    upp.phone as telefono,
                    g.ingreso_anticipado_desde as fecha_desde
                FROM events_grupo g
                INNER JOIN events_grupomiembro gm ON g.id = gm.grupo_id
                INNER JOIN auth_user au ON gm.user_id = au.id
                LEFT JOIN user_profile_profile upp ON au.id = upp.user_id
                WHERE g.event_id = %s
                  AND gm.ingreso_anticipado = true
                ORDER BY g.nombre, au.last_name, au.first_name
            """
            cursor.execute(query, [event_id])
            
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            event = Event.objects.get(id=event_id)
            filename = f'ingreso_anticipado_{event.slug or event.id}.csv'
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            writer = csv.writer(response)
            # Escribir encabezados en español
            writer.writerow(['Grupo', 'Nombre', 'Apellido', 'Tipo Documento', 'Número Documento', 'Email', 'Teléfono', 'Fecha Desde'])
            
            for row in cursor.fetchall():
                # Formatear la fecha si existe
                formatted_row = list(row)
                if formatted_row[7]:  # fecha_desde (ahora es el índice 7)
                    formatted_row[7] = formatted_row[7].strftime('%d/%m/%Y %H:%M')
                else:
                    formatted_row[7] = ''
                writer.writerow(formatted_row)
            
            return response

    def export_ingreso_anticipado_pdf(self, request):
        """Exportar reporte de ingreso anticipado a PDF"""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
        except ImportError:
            return HttpResponse(
                'ReportLab no está instalado. Por favor instala reportlab: pip install reportlab',
                status=500
            )
        
        from io import BytesIO
        from django.utils import timezone
        
        event_id = request.GET.get('event_id')
        if not event_id:
            return HttpResponse('Event ID is required', status=400)

        try:
            event = Event.objects.get(id=event_id)
        except Event.DoesNotExist:
            return HttpResponse('Event not found', status=404)

        # Crear el buffer para el PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []
        
        # Estilos
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1e1e1e'),
            spaceAfter=30,
        )
        
        # Título
        title = Paragraph(f'Reporte de Ingreso Anticipado - {event.name}', title_style)
        elements.append(title)
        elements.append(Spacer(1, 0.2*inch))
        
        # Obtener datos
        with connection.cursor() as cursor:
            query = """
                SELECT 
                    g.nombre as grupo,
                    au.first_name as nombre,
                    au.last_name as apellido,
                    upp.document_type as documento_tipo,
                    upp.document_number as documento_numero,
                    au.email as email,
                    upp.phone as telefono,
                    g.ingreso_anticipado_desde as fecha_desde
                FROM events_grupo g
                INNER JOIN events_grupomiembro gm ON g.id = gm.grupo_id
                INNER JOIN auth_user au ON gm.user_id = au.id
                LEFT JOIN user_profile_profile upp ON au.id = upp.user_id
                WHERE g.event_id = %s
                  AND gm.ingreso_anticipado = true
                ORDER BY g.nombre, au.last_name, au.first_name
            """
            cursor.execute(query, [event_id])
            results = cursor.fetchall()
        
        if not results:
            no_data = Paragraph('No hay registros de ingreso anticipado para este evento.', styles['Normal'])
            elements.append(no_data)
        else:
            # Preparar datos para la tabla
            data = [['Grupo', 'Nombre', 'Apellido', 'Tipo Doc.', 'Número Doc.', 'Email', 'Teléfono', 'Fecha Desde']]
            
            for row in results:
                fecha_str = ''
                if row[7]:  # fecha_desde (ahora es el índice 7)
                    fecha_str = row[7].strftime('%d/%m/%Y %H:%M')
                data.append([
                    row[0] or '',  # grupo
                    row[1] or '',  # nombre
                    row[2] or '',  # apellido
                    row[3] or '',  # documento_tipo
                    row[4] or '',  # documento_numero
                    row[5] or '',  # email
                    row[6] or '',  # telefono
                    fecha_str
                ])
            
            # Crear tabla
            table = Table(data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f5')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1e1e1e')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#1e1e1e')),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#d9d9d9')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            
            elements.append(table)
        
        # Fecha de generación
        elements.append(Spacer(1, 0.3*inch))
        fecha_gen = Paragraph(
            f'Generado el: {timezone.now().strftime("%d/%m/%Y %H:%M")}',
            styles['Normal']
        )
        elements.append(fecha_gen)
        
        # Construir PDF
        doc.build(elements)
        
        # Preparar respuesta
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        filename = f'ingreso_anticipado_{event.slug or event.id}.pdf'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response

    def late_checkout_report_view(self, request):
        """Reporte de late checkout por grupo"""
        event_id = request.GET.get('event_id')
        events = Event.objects.all()
        
        if event_id:
            with connection.cursor() as cursor:
                query = """
                    SELECT 
                        g.nombre as grupo,
                        au.first_name as nombre,
                        au.last_name as apellido,
                        upp.document_type as documento_tipo,
                        upp.document_number as documento_numero,
                        g.late_checkout_hasta as fecha_hasta
                    FROM events_grupo g
                    INNER JOIN events_grupomiembro gm ON g.id = gm.grupo_id
                    INNER JOIN auth_user au ON gm.user_id = au.id
                    LEFT JOIN user_profile_profile upp ON au.id = upp.user_id
                    WHERE g.event_id = %s
                      AND gm.late_checkout = true
                    ORDER BY g.nombre, au.last_name, au.first_name
                """
                cursor.execute(query, [event_id])
                columns = [col[0] for col in cursor.description]
                results = cursor.fetchall()
        else:
            results = []
            columns = []

        context = {
            'events': events,
            'selected_event': event_id,
            'results': results,
            'columns': columns,
            'opts': self.model._meta,
            'title': 'Reporte de Late Checkout'
        }
        
        return render(request, 'admin/events/late_checkout_report.html', context)

    def export_late_checkout_csv(self, request):
        """Exportar reporte de late checkout a CSV"""
        event_id = request.GET.get('event_id')
        if not event_id:
            return HttpResponse('Event ID is required', status=400)

        with connection.cursor() as cursor:
            query = """
                SELECT 
                    g.nombre as grupo,
                    au.first_name as nombre,
                    au.last_name as apellido,
                    upp.document_type as documento_tipo,
                    upp.document_number as documento_numero,
                    g.late_checkout_hasta as fecha_hasta
                FROM events_grupo g
                INNER JOIN events_grupomiembro gm ON g.id = gm.grupo_id
                INNER JOIN auth_user au ON gm.user_id = au.id
                LEFT JOIN user_profile_profile upp ON au.id = upp.user_id
                WHERE g.event_id = %s
                  AND gm.late_checkout = true
                ORDER BY g.nombre, au.last_name, au.first_name
            """
            cursor.execute(query, [event_id])
            
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            event = Event.objects.get(id=event_id)
            filename = f'late_checkout_{event.slug or event.id}.csv'
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            writer = csv.writer(response)
            # Escribir encabezados en español
            writer.writerow(['Grupo', 'Nombre', 'Apellido', 'Tipo Documento', 'Número Documento', 'Fecha Hasta'])
            
            for row in cursor.fetchall():
                # Formatear la fecha si existe
                formatted_row = list(row)
                if formatted_row[5]:  # fecha_hasta
                    formatted_row[5] = formatted_row[5].strftime('%d/%m/%Y %H:%M')
                else:
                    formatted_row[5] = ''
                writer.writerow(formatted_row)
            
            return response

    def export_late_checkout_pdf(self, request):
        """Exportar reporte de late checkout a PDF"""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
        except ImportError:
            return HttpResponse(
                'ReportLab no está instalado. Por favor instala reportlab: pip install reportlab',
                status=500
            )
        
        from io import BytesIO
        from django.utils import timezone
        
        event_id = request.GET.get('event_id')
        if not event_id:
            return HttpResponse('Event ID is required', status=400)

        try:
            event = Event.objects.get(id=event_id)
        except Event.DoesNotExist:
            return HttpResponse('Event not found', status=404)

        # Crear el buffer para el PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []
        
        # Estilos
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1e1e1e'),
            spaceAfter=30,
        )
        
        # Título
        title = Paragraph(f'Reporte de Late Checkout - {event.name}', title_style)
        elements.append(title)
        elements.append(Spacer(1, 0.2*inch))
        
        # Obtener datos
        with connection.cursor() as cursor:
            query = """
                SELECT 
                    g.nombre as grupo,
                    au.first_name as nombre,
                    au.last_name as apellido,
                    upp.document_type as documento_tipo,
                    upp.document_number as documento_numero,
                    g.late_checkout_hasta as fecha_hasta
                FROM events_grupo g
                INNER JOIN events_grupomiembro gm ON g.id = gm.grupo_id
                INNER JOIN auth_user au ON gm.user_id = au.id
                LEFT JOIN user_profile_profile upp ON au.id = upp.user_id
                WHERE g.event_id = %s
                  AND gm.late_checkout = true
                ORDER BY g.nombre, au.last_name, au.first_name
            """
            cursor.execute(query, [event_id])
            results = cursor.fetchall()
        
        if not results:
            no_data = Paragraph('No hay registros de late checkout para este evento.', styles['Normal'])
            elements.append(no_data)
        else:
            # Preparar datos para la tabla
            data = [['Grupo', 'Nombre', 'Apellido', 'Tipo Doc.', 'Número Doc.', 'Fecha Hasta']]
            
            for row in results:
                fecha_str = ''
                if row[5]:  # fecha_hasta
                    fecha_str = row[5].strftime('%d/%m/%Y %H:%M')
                data.append([
                    row[0] or '',  # grupo
                    row[1] or '',  # nombre
                    row[2] or '',  # apellido
                    row[3] or '',  # documento_tipo
                    row[4] or '',  # documento_numero
                    fecha_str
                ])
            
            # Crear tabla
            table = Table(data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f5')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1e1e1e')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#1e1e1e')),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#d9d9d9')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            
            elements.append(table)
        
        # Fecha de generación
        elements.append(Spacer(1, 0.3*inch))
        fecha_gen = Paragraph(
            f'Generado el: {timezone.now().strftime("%d/%m/%Y %H:%M")}',
            styles['Normal']
        )
        elements.append(fecha_gen)
        
        # Construir PDF
        doc.build(elements)
        
        # Preparar respuesta
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        filename = f'late_checkout_{event.slug or event.id}.pdf'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response

class EventTermsAndConditionsAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'event', 'order', 'has_description')
    list_filter = ('event',)
    search_fields = ('title', 'description', 'slug')
    ordering = ('event', 'order', 'id')
    prepopulated_fields = {"slug": ("title",)}
    
    def has_description(self, obj):
        return bool(obj.description)
    has_description.boolean = True
    has_description.short_description = 'Tiene Descripción'


class EventTermsAndConditionsAcceptanceAdmin(admin.ModelAdmin):
    list_display = ('user', 'term', 'order', 'accepted_at')
    list_filter = ('term__event', 'accepted_at')
    search_fields = ('user__email', 'term__title', 'order__key')
    readonly_fields = ('accepted_at',)
    ordering = ('-accepted_at',)


class GrupoTipoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'activo', 'created_at')
    list_filter = ('activo',)
    search_fields = ('nombre', 'descripcion')
    ordering = ('nombre',)


class GrupoMiembroInline(admin.TabularInline):
    model = GrupoMiembro
    extra = 0
    fields = ('user', 'ingreso_anticipado', 'late_checkout', 'restriccion')
    autocomplete_fields = ('user',)


class GrupoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo', 'event', 'lider', 'ingreso_anticipado_amount', 'ingreso_anticipado_desde', 'late_checkout_amount', 'late_checkout_hasta', 'miembros_count', 'ingreso_anticipado_count', 'late_checkout_count')
    list_filter = ('tipo', 'event', 'ingreso_anticipado_desde', 'late_checkout_hasta')
    search_fields = ('nombre', 'lider__email', 'lider__first_name', 'lider__last_name')
    autocomplete_fields = ('lider',)
    inlines = [GrupoMiembroInline]
    fieldsets = (
        ('Información Básica', {
            'fields': ('event', 'lider', 'nombre', 'tipo')
        }),
        ('Ingreso Anticipado', {
            'fields': ('ingreso_anticipado_amount', 'ingreso_anticipado_desde')
        }),
        ('Late Checkout', {
            'fields': ('late_checkout_amount', 'late_checkout_hasta')
        }),
    )
    
    def miembros_count(self, obj):
        return obj.miembros_count()
    miembros_count.short_description = 'Miembros'
    
    def ingreso_anticipado_count(self, obj):
        return f"{obj.ingreso_anticipado_count()}/{obj.ingreso_anticipado_amount}"
    ingreso_anticipado_count.short_description = 'Ingreso Anticipado'
    
    def late_checkout_count(self, obj):
        return f"{obj.late_checkout_count()}/{obj.late_checkout_amount}"
    late_checkout_count.short_description = 'Late Checkout'


class GrupoMiembroAdmin(admin.ModelAdmin):
    list_display = ('user', 'grupo', 'ingreso_anticipado', 'late_checkout', 'restriccion', 'created_at')
    list_filter = ('ingreso_anticipado', 'late_checkout', 'restriccion', 'grupo__tipo', 'grupo__event')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'grupo__nombre')
    autocomplete_fields = ('user', 'grupo')


class EventRequestTicketTypeInline(admin.TabularInline):
    model = EventRequestTicketType
    extra = 0


class EventRequestAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'requested_by', 'status', 'max_tickets', 'start',
        'slack_message_ts', 'chatwoot_conversation_id', 'created_at',
    )
    list_filter = ('status',)
    search_fields = ('name', 'requested_by__email', 'location')
    readonly_fields = (
        'chatwoot_contact_id', 'chatwoot_conversation_id',
        'slack_channel', 'slack_message_ts',
        'resolved_at', 'created_at', 'updated_at',
    )
    inlines = [EventRequestTicketTypeInline]
    actions = ['approve_selected_requests', 'reject_selected_requests']

    @admin.action(description='Aprobar propuestas seleccionadas')
    def approve_selected_requests(self, request, queryset):
        from events.services.event_request_processing import approve_event_request
        for event_request in queryset.filter(status=EventRequest.Status.PENDING):
            approve_event_request(event_request)

    @admin.action(description='Rechazar propuestas seleccionadas')
    def reject_selected_requests(self, request, queryset):
        from events.services.event_request_processing import reject_event_request
        for event_request in queryset.filter(status=EventRequest.Status.PENDING):
            reject_event_request(event_request, reason='Rechazada desde admin')


@admin.register(ArtProgram)
class ArtProgramAdmin(admin.ModelAdmin):
    list_display = ('event', 'is_current', 'registration_opens', 'registration_closes', 'grants_enabled', 'grant_deadline', 'guide_deadline', 'logistics_deadline')
    list_filter = ('is_current', 'grants_enabled', 'event')
    date_hierarchy = 'registration_opens'


class ArtworkGrantItemFormSet(BaseInlineFormSet):
    phase = None

    def save_new(self, form, commit=True):
        item = super().save_new(form, commit=False)
        item.phase = self.phase
        if commit:
            item.save()
        return item


class ArtworkBudgetItemFormSet(ArtworkGrantItemFormSet):
    phase = ArtworkGrantItem.Phase.BUDGET


class ArtworkExpenseItemFormSet(ArtworkGrantItemFormSet):
    phase = ArtworkGrantItem.Phase.EXPENSE


class ArtworkGrantItemAdminForm(ModelForm):
    amount = LocalizedDecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '150.000,00'}),
    )
    exchange_rate = LocalizedDecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal('0.0001'),
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '1.234,56'}),
    )
    confirm_large_amount = forms.BooleanField(
        required=False,
        label='Confirmo el monto si supera ARS 1.000.000',
    )

    class Meta:
        model = ArtworkGrantItem
        fields = ('item_type', 'concept', 'details', 'amount', 'currency', 'exchange_rate', 'rate_date', 'rate_source', 'review_status', 'review_notes')

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get('amount')
        if cleaned.get('currency') == ArtworkGrantItem.Currency.USD:
            amount = amount * (cleaned.get('exchange_rate') or Decimal('0')) if amount else None
        if amount and amount >= Decimal('1000000') and not cleaned.get('confirm_large_amount'):
            self.add_error('confirm_large_amount', 'Confirmá el monto convertido antes de guardar.')
        return cleaned


class ArtworkGrantItemInline(admin.StackedInline):
    model = ArtworkGrantItem
    form = ArtworkGrantItemAdminForm
    extra = 0
    phase = None
    fieldsets = (
        ('Concepto', {'fields': (('item_type', 'concept'), 'details')}),
        ('Importe', {'fields': (('amount', 'currency', 'exchange_rate'), ('rate_date', 'rate_source'))}),
        ('Revisión', {'fields': ('review_status', 'review_notes', 'confirm_large_amount')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(phase=self.phase)

    def _is_locked(self, obj):
        return obj and self.phase == ArtworkGrantItem.Phase.EXPENSE and obj.grant_status == Artwork.GrantStatus.CLOSED

    def has_add_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_delete_permission(request, obj)


class ArtworkBudgetItemInline(ArtworkGrantItemInline):
    phase = ArtworkGrantItem.Phase.BUDGET
    formset = ArtworkBudgetItemFormSet
    verbose_name = 'Ítem de presupuesto'
    verbose_name_plural = 'Presupuesto de la beca'


class ArtworkExpenseItemInline(ArtworkGrantItemInline):
    phase = ArtworkGrantItem.Phase.EXPENSE
    formset = ArtworkExpenseItemFormSet
    verbose_name = 'Ítem de rendición'
    verbose_name_plural = 'Rendición de la beca'


class ArtworkGrantItemPhotoAdminForm(ModelForm):
    class Meta:
        model = ArtworkGrantItemPhoto
        fields = '__all__'

    def clean_item(self):
        item = self.cleaned_data['item']
        if item.artwork.grant_status == Artwork.GrantStatus.CLOSED:
            raise ValidationError('La rendición está aprobada. Reabrila antes de cambiar comprobantes.')
        return item


@admin.register(ArtworkGrantItemPhoto)
class ArtworkGrantItemPhotoAdmin(admin.ModelAdmin):
    form = ArtworkGrantItemPhotoAdminForm

    def _is_locked(self, obj):
        return obj and obj.item.artwork.grant_status == Artwork.GrantStatus.CLOSED

    def has_change_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_delete_permission(request, obj)


class ArtworkPhotoInline(admin.TabularInline):
    model = ArtworkPhoto
    extra = 0


class ArtworkCheckoutPhotoInline(admin.TabularInline):
    model = ArtworkCheckoutPhoto
    extra = 0


class ArtworkInvitationInline(admin.TabularInline):
    model = ArtworkInvitation
    extra = 0


class ArtworkLogisticsPersonInline(admin.TabularInline):
    model = ArtworkLogisticsPerson
    extra = 0


class ArtworkProviderInline(admin.TabularInline):
    model = ArtworkProvider
    extra = 0


class ArtworkAdminForm(ModelForm):
    grant_approved_amount_ars = LocalizedDecimalField(
        required=False, max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        label='Monto de beca aprobado',
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '450.000,00'}),
    )
    confirm_large_grant_amount = forms.BooleanField(
        required=False,
        label='Confirmo el monto aprobado si supera ARS 1.000.000',
    )

    class Meta:
        model = Artwork
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get('grant_approved_amount_ars')
        if amount and amount >= Decimal('1000000') and not cleaned.get('confirm_large_grant_amount'):
            self.add_error('confirm_large_grant_amount', 'Confirmá el monto aprobado antes de guardar.')
        return cleaned


@admin.register(Artwork)
class ArtworkAdmin(admin.ModelAdmin):
    form = ArtworkAdminForm
    list_display = ('title', 'event', 'kind', 'status', 'owner', 'grant_status', 'assigned_location', 'checkout_verified_at', 'updated_at')
    list_filter = ('event', 'kind', 'status', 'grant_status', 'checkout_completed', 'understanding_letter_physical_received', 'submitted_at')
    search_fields = ('title', 'owner__email', 'public_description')
    autocomplete_fields = ('owner', 'collaborators', 'operations_group', 'checkin_art_by', 'checkout_art_responsible', 'checkout_verified_by', 'safety_responsible')
    readonly_fields = ('submitted_at', 'checkin_art_by', 'checkout_requested_at', 'checkout_verified_by', 'created_at', 'updated_at', 'version')
    fieldsets = (
        ('Obra', {'fields': ('event', 'owner', 'collaborators', 'operations_group', 'kind', 'title')}),
        ('Check-in de obra', {'fields': ('checkin_arrived_at', 'checkin_art_at', 'checkin_art_by', 'checkin_placed', 'checkin_placement_changed', 'checkin_placement_change_notes', 'understanding_letter', 'understanding_letter_physical_received', 'understanding_letter_physical_custodian', 'understanding_letter_physical_notes', 'understanding_letter_physical_waiver', 'understanding_letter_physical_waiver_reason')}),
        ('Propuesta y seguridad', {'fields': ('proposal', 'dimensions', 'materials', 'technical_needs', 'safety_plan', 'uses_fire', 'fire_details', 'extinguishing_plan', 'power_watts', 'safety_contact', 'safety_responsible')}),
        ('Beca', {'fields': ('grant_requested', 'grant_justification', 'grant_status', 'grant_approved_amount_ars', 'confirm_large_grant_amount', 'grant_decision_notes', 'grant_paid_at', 'grant_payment_reference', 'grant_report')}),
        ('Desplegable y placement', {'fields': ('public_title', 'public_description', 'preferred_location', 'assigned_location', 'placement_notes')}),
        ('Logística', {'fields': ('arrival_date', 'departure_date', 'crew', 'providers')}),
        ('Checkout, paso 1, equipo de la obra', {'fields': ('checkout_completed', 'checkout_team_responsible', 'checkout_notes', 'checkout_requested_at')}),
        ('Checkout, paso 2, Arte', {'fields': ('checkout_art_responsible', 'checkout_verified_at', 'checkout_verified_by')}),
        ('Seguimiento', {'fields': ('status', 'review_feedback', 'benefit_status', 'benefit_notes', 'submitted_at', 'version', 'created_at', 'updated_at')}),
    )
    inlines = [ArtworkBudgetItemInline, ArtworkExpenseItemInline, ArtworkPhotoInline, ArtworkCheckoutPhotoInline, ArtworkInvitationInline, ArtworkLogisticsPersonInline, ArtworkProviderInline]
    actions = ('approve_grant_reports', 'reopen_grant_reports')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.grant_status == Artwork.GrantStatus.CLOSED:
            fields.append('grant_report')
        return fields

    def save_model(self, request, obj, form, change):
        if obj.checkin_art_at and not obj.checkin_art_by_id:
            obj.checkin_art_by = request.user
        if obj.checkout_verified_at and not obj.checkout_verified_by_id:
            obj.checkout_verified_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Aprobar rendiciones seleccionadas')
    def approve_grant_reports(self, request, queryset):
        reports = queryset.filter(grant_status=Artwork.GrantStatus.REPORTED)
        count = reports.count()
        for artwork in reports:
            artwork.grant_status = Artwork.GrantStatus.CLOSED
            artwork.save(update_fields=['grant_status', 'updated_at'])
        self.message_user(request, f'{count} rendición(es) aprobada(s).')

    @admin.action(description='Desaprobar y reabrir rendiciones seleccionadas')
    def reopen_grant_reports(self, request, queryset):
        reports = queryset.filter(grant_status=Artwork.GrantStatus.CLOSED)
        count = reports.count()
        for artwork in reports:
            artwork.grant_status = Artwork.GrantStatus.REPORTED
            artwork.save(update_fields=['grant_status', 'updated_at'])
        self.message_user(request, f'{count} rendición(es) reabierta(s).')


admin.site.register(ArtworkCheckoutPhoto)
admin.site.register(ArtworkProviderVehicle)


admin.site.register(Event, EventAdmin)
admin.site.register(EventTermsAndConditions, EventTermsAndConditionsAdmin)
admin.site.register(EventTermsAndConditionsAcceptance, EventTermsAndConditionsAcceptanceAdmin)
admin.site.register(GrupoTipo, GrupoTipoAdmin)
admin.site.register(Grupo, GrupoAdmin)
admin.site.register(GrupoMiembro, GrupoMiembroAdmin)
admin.site.register(EventRequest, EventRequestAdmin)
