import csv

from django.conf import settings
from django.contrib import admin
from django.db.models import Count, Q
from django.http import HttpResponse
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import Ticket, TicketType, Order, Coupon, TicketTransfer


admin.site.site_header = 'Bonos de Fuego Austral'


class TicketAdmin(admin.ModelAdmin):
    list_display = ('order_id', 'get_status', 'get_event',  'first_name', 'last_name', 'email', 'phone', 'dni', 'price', 'key', )
    list_filter = ('order__ticket_type__event', 'order__status', 'volunteer_ranger', 'volunteer_umpalumpa', 'volunteer_transmutator', )
    search_fields = ('first_name', 'last_name', 'key', )
    actions = ['export']
    readonly_fields = ('key', )

    def get_queryset(self, request):
        return super(TicketAdmin,self).get_queryset(request).select_related('order__ticket_type__event')

    @admin.action(description='Exportar tickets seleccionados')
    def export(self, request, queryset):
        response = HttpResponse(
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="tickets.csv"'},
        )

        writer = csv.writer(response)
        writer.writerow(['Nombre', 'Apellido', 'Email', 'Teléfono', 'DNI',
            'Precio', 'Orden #', 'Voluntario', 'Ranger', 'Transmutador',
            'Umpalumpa', 'Ticket #', ])

        for ticket in queryset:
            writer.writerow([
                ticket.first_name,
                ticket.last_name,
                ticket.email,
                ticket.phone,
                ticket.dni,
                ticket.price,
                ticket.order_id,
                ticket.get_volunteer_display(),
                'Sí' if ticket.volunteer_ranger else 'No',
                'Sí' if ticket.volunteer_transmutator else 'No',
                'Sí' if ticket.volunteer_umpalumpa else 'No',
                ticket.key, 
            ])

        return response

    @admin.display(ordering='order__status', description='status')
    def get_status(self, obj):
        return obj.order.get_status_display()

    @admin.display(ordering='order__ticket_type__event__event_id', description='Event')
    def get_event(self, obj):
        return obj.order.ticket_type.event

class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'event', 'tickets_sold', 'ticket_count', 'price', 'price_with_coupon', 'date_from', 'date_to')
    list_filter = ('event', )

    def get_queryset(self, *args, **kwargs):
        qs = super().get_queryset(*args, **kwargs)
        qs = qs.annotate(tickets_sold=Count('order__ticket', filter=Q(order__status=Order.OrderStatus.CONFIRMED)))
        return qs

    def tickets_sold(self, obj):
        return obj.tickets_sold


class TicketInline(admin.StackedInline):
    model = Ticket
    show_change_link = True
    extra = 0


class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'get_event', 'first_name', 'last_name', 'ticket_type_link', 'ticket_count', 'amount')
    list_filter = ('ticket_type__event', 'status', 'ticket_type', )
    search_fields = ('first_name', 'last_name', 'ticket__first_name', 'ticket__last_name', )
    inlines = [TicketInline, ]
    actions = ['export']

    def get_queryset(self, request):
        return super(OrderAdmin,self).get_queryset(request).select_related('ticket_type__event')

    @admin.display(ordering='ticket_type__event__event_id', description='Event')
    def get_event(self, obj):
        return obj.ticket_type.event

    @admin.action(description='Exportar órdenes seleccionadas')
    def export(self, request, queryset):
        response = HttpResponse(
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="órdenes.csv"'},
        )

        writer = csv.writer(response)
        writer.writerow(['#', 'Estado', 'Nombre', 'Apellido', 'Email', 'Teléfono', 'DNI',
            'Becas de Arte', 'Becas NTUM', 'Donaciones a La Sede', 'Valor Total', 'Cupón',
            'Tipo de Ticket', '# Tickets', ])

        for order in queryset:
            writer.writerow([
                order.id,
                order.status,
                order.first_name,
                order.last_name,
                order.email,
                order.phone,
                order.dni,
                order.donation_art,
                order.donation_grant,
                order.donation_venue,
                order.amount,
                order.coupon,
                order.ticket_type, 
                order.ticket_set.count(), 
            ])

        return response

    def ticket_count(self, order):
        return order.ticket_set.count()

    def ticket_type_link(self, order):
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:tickets_tickettype_change", args=(order.ticket_type.pk,)),
            order.ticket_type.name
        ))

class OrdersInline(admin.StackedInline):
    model = Order
    fields = ('first_name', 'last_name', 'email', 'amount', 'status', )
    show_change_link = True
    extra = 0

class CouponAdmin(admin.ModelAdmin):
    list_display = ('token', 'ticket_type', 'get_event', 'max_tickets', 'tickets_remaining', 'display_url', )
    list_filter = ('ticket_type__event', 'ticket_type', )
    search_fields = ('token', 'ticket_type__name', )
    readonly_fields = ('tickets_remaining', 'display_url', )
    inlines = [OrdersInline, ]

    class Meta:
        model = Coupon
        fields = ('display_url', )

    def get_queryset(self, request):
        return super(CouponAdmin,self).get_queryset(request).select_related('ticket_type__event')

    @admin.display(ordering='ticket_type__event__event_id', description='Event')
    def get_event(self, obj):
        return obj.ticket_type.event

    def display_url(self, order):
        return f"{getattr(settings, 'APP_URL')}/?coupon={order.token}"


class TicketTransferAdmin(admin.ModelAdmin):
    fields = ('ticket', 'first_name', 'last_name', 'email', 'phone', 'dni', 'transferred', )
    list_filter = ('ticket__order__ticket_type__event', 'transferred', )
    list_display = ('first_name', 'last_name', 'ticket', 'get_event', 'transferred', )

    def get_queryset(self, request):
        return super(TicketTransferAdmin,self).get_queryset(request).select_related('ticket__order__ticket_type__event')

    @admin.display(ordering='ticket__order__ticket_type__event__event_id', description='Event')
    def get_event(self, obj):
        return obj.ticket.order.ticket_type.event

    class Meta:
        model = TicketTransfer


admin.site.register(Ticket, TicketAdmin)
admin.site.register(TicketType, TicketTypeAdmin)
admin.site.register(Order, OrderAdmin)
admin.site.register(Coupon, CouponAdmin)
admin.site.register(TicketTransfer, TicketTransferAdmin)
