from django.conf import settings
from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import Ticket, TicketType, Order, Coupon


class TicketAdmin(admin.ModelAdmin):
    list_display = ('order', 'last_name', 'first_name', 'email', 'phone', 'dni', 'price')
    list_filter = ('volunteer_ranger', 'volunteer_umpalumpa', 'volunteer_transmutator', )


class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'ticket_count', 'price', 'price_with_coupon', 'date_from', 'date_to')


class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'first_name', 'last_name', 'ticket_type_link', 'ticket_count', 'amount')

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

class CouponAdmin(admin.ModelAdmin):
    list_display = ('token', 'ticket_type', 'max_tickets', 'tickets_remaining', 'display_url', )
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
