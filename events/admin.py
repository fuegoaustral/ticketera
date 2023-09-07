from django.contrib import admin

from .models import Event


class EventAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'start', 'end', ) # add: transfers active
    list_filter = ('active', )
    search_fields = ('name', 'titke' )

    fieldsets = [
        (
            None,
            {
                'fields': ['active', 'name', 'has_volunteers', 'start', 'end', 'transfers_enabled_until', 'show_multiple_tickets', ]
            },
        ),
        (
            'Homepage',
            {
                'fields': ['header_image', 'header_bg_color', 'title', 'description', ]
            },
        ),
        (
            'Buy Ticket Form',
            {
                'fields': ['pre_ticket_form_info', ]
            },
        ),
        (
            'Email',
            {
                'fields': ['email_info', ]
            },
        ),
    ]

    # @admin.display(ordering='order__status', description='status')
    # def get_status(self, obj):
    #     return obj.order.get_status_display()


admin.site.register(Event, EventAdmin)
