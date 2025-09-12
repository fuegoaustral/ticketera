from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.http import HttpResponse
from django.db import connection
from django.urls import path
from django.shortcuts import render
from django.forms import ModelForm
import csv
from .models import Event


class EventAdminForm(ModelForm):
    class Meta:
        model = Event
        fields = '__all__'
        widgets = {
            'admins': FilteredSelectMultiple(
                verbose_name='Administradores',
                is_stacked=False
            ),
        }


class EventAdmin(admin.ModelAdmin):
    form = EventAdminForm
    filter_horizontal = ('admins',)  # Esto también ayuda con la interfaz
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
                        (SELECT COUNT(*)
                         FROM tickets_newticket tnh
                         WHERE tnh.holder_id = au.id
                           AND tnh.owner_id is null) AS bonos_sin_compartir
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
                                 [event_id, search_param, search_param, search_param, search_param, search_param])
                else:
                    cursor.execute(query + " ORDER BY bonos_sin_compartir DESC", [event_id])
                
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
                    (SELECT COUNT(*)
                     FROM tickets_newticket tnh
                     WHERE tnh.holder_id = au.id
                       AND tnh.owner_id is null) AS bonos_sin_compartir
                FROM auth_user au
                INNER JOIN user_profile_profile upp ON au.id = upp.user_id
                INNER JOIN tickets_newticket tn ON au.id = tn.owner_id
                INNER JOIN tickets_tickettype tt ON tn.ticket_type_id = tt.id
                WHERE tn.event_id = %s
                ORDER BY bonos_sin_compartir DESC
            """, [event_id])
            
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
            'title': 'Reporte de Tickets Vendidos - Eventos Activos'
        }
        
        return render(request, 'admin/events/tickets_sold_report.html', context)

admin.site.register(Event, EventAdmin)
