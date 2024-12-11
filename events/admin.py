from django.contrib import admin
from django.http import HttpResponse
from django.db import connection
from django.urls import path
from django.shortcuts import render
import csv
from .models import Event


class EventAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "active",
        "start",
        "end",
        "max_tickets",
        "donations_art",
        "donations_venue",
        "donations_grant",
    )
    list_filter = ("active",)
    search_fields = ("name",)
    change_list_template = 'admin/events/event/change_list.html'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('tickets_report/', self.custom_query_view, name='event_tickets_report'),
            path('export-csv/', self.export_csv, name='export_csv'),
        ]
        return custom_urls + urls

    def custom_query_view(self, request):
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

admin.site.register(Event, EventAdmin)
