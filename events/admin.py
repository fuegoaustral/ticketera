from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.http import HttpResponse
from django.db import connection
from django.urls import path
from django.shortcuts import render
from django.forms import ModelForm
import csv
from .models import Event, EventTermsAndConditions, EventTermsAndConditionsAcceptance, GrupoTipo, Grupo, GrupoMiembro


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
            writer.writerow(['Grupo', 'Nombre', 'Apellido', 'Tipo Documento', 'Número Documento', 'Fecha Desde'])
            
            for row in cursor.fetchall():
                # Formatear la fecha si existe
                formatted_row = list(row)
                if formatted_row[5]:  # fecha_desde
                    formatted_row[5] = formatted_row[5].strftime('%d/%m/%Y %H:%M')
                else:
                    formatted_row[5] = ''
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
            data = [['Grupo', 'Nombre', 'Apellido', 'Tipo Doc.', 'Número Doc.', 'Fecha Desde']]
            
            for row in results:
                fecha_str = ''
                if row[5]:  # fecha_desde
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


admin.site.register(Event, EventAdmin)
admin.site.register(EventTermsAndConditions, EventTermsAndConditionsAdmin)
admin.site.register(EventTermsAndConditionsAcceptance, EventTermsAndConditionsAcceptanceAdmin)
admin.site.register(GrupoTipo, GrupoTipoAdmin)
admin.site.register(Grupo, GrupoAdmin)
admin.site.register(GrupoMiembro, GrupoMiembroAdmin)
