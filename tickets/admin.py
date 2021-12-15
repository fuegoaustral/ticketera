from django.conf import settings
from django.contrib import admin
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import Ticket, TicketType, Order, Coupon


class TicketAdmin(admin.ModelAdmin):
    list_display = ('order_id', 'get_status', 'first_name', 'last_name', 'email', 'phone', 'dni', 'price')
    list_filter = ('order__status', 'volunteer_ranger', 'volunteer_umpalumpa', 'volunteer_transmutator', )
    search_fields = ('first_name', 'last_name', )

    @admin.display(ordering='order__status', description='status')
    def get_status(self, obj):
        return obj.order.get_status_display()


class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'tickets_sold', 'ticket_count', 'price', 'price_with_coupon', 'date_from', 'date_to')

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
    list_display = ('id', 'status', 'first_name', 'last_name', 'ticket_type_link', 'ticket_count', 'amount')
    list_filter = ('status', 'ticket_type', )
    search_fields = ('first_name', 'last_name', 'ticket__first_name', 'ticket__last_name', )
    inlines = [TicketInline, ]

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
    list_display = ('token', 'ticket_type', 'max_tickets', 'tickets_remaining', 'display_url', )
    list_filter = ('ticket_type', )
    search_fields = ('token', 'ticket_type__name', )
    readonly_fields = ('tickets_remaining', 'display_url', )
    inlines = [OrdersInline, ]

    class Meta:
        model = Coupon
        fields = ('display_url', )

    def display_url(self, order):
        return f"{getattr(settings, 'APP_URL')}/?coupon={order.token}"

admin.site.register(Ticket, TicketAdmin)
admin.site.register(TicketType, TicketTypeAdmin)
admin.site.register(Order, OrderAdmin)
admin.site.register(Coupon, CouponAdmin)
