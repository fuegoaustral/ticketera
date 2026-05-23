from django.contrib import admin

from caja.models import (
    CajaSale,
    CajaSaleLine,
    EventCaja,
    EventCajaMercadoPagoConfig,
    EventCajaProduct,
    EventProduct,
    EventProductStock,
    EventProductStockRecord,
)


class EventCajaProductInline(admin.TabularInline):
    model = EventCajaProduct
    extra = 1


class EventCajaMercadoPagoConfigInline(admin.StackedInline):
    model = EventCajaMercadoPagoConfig
    extra = 0


@admin.register(EventProduct)
class EventProductAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'event', 'price', 'is_active', 'ticket_type']
    list_filter = ['event', 'is_active']
    search_fields = ['name', 'ticket_type__name']


@admin.register(EventProductStock)
class EventProductStockAdmin(admin.ModelAdmin):
    list_display = ['event_product', 'quantity']


@admin.register(EventProductStockRecord)
class EventProductStockRecordAdmin(admin.ModelAdmin):
    list_display = ['event_product', 'delta', 'reason', 'balance_after', 'created_at']
    list_filter = ['reason']


@admin.register(EventCaja)
class EventCajaAdmin(admin.ModelAdmin):
    list_display = ['name', 'event', 'is_active', 'sort_order']
    list_filter = ['event', 'is_active']
    inlines = [EventCajaProductInline, EventCajaMercadoPagoConfigInline]


class CajaSaleLineInline(admin.TabularInline):
    model = CajaSaleLine
    extra = 0


@admin.register(CajaSale)
class CajaSaleAdmin(admin.ModelAdmin):
    list_display = ['id', 'event_caja', 'payment_method', 'status', 'total_amount', 'created_at']
    list_filter = ['status', 'payment_method']
    inlines = [CajaSaleLineInline]
