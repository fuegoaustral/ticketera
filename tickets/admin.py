from django.contrib import admin

from .models import Ticket, TicketType, Order, Coupon


class TicketAdmin(admin.ModelAdmin):
    pass


class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'ticket_count', 'price', 'price_with_coupon', 'coupon', 'date_from', 'date_to')


admin.site.register(Ticket, TicketAdmin)
admin.site.register(TicketType, TicketTypeAdmin)
admin.site.register(Order)
admin.site.register(Coupon)