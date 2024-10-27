from django.contrib import admin

from .models import Event


class EventAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'start', 'end', 'max_tickets', ) # add: transfers active
    list_filter = ('active', )
    search_fields = ('name', )

    fieldsets = [
        (
            None,
            {
                'fields': ['active', 'name', 'start', 'end', 'max_tickets', 'transfers_enabled_until', 'show_multiple_tickets', 'has_volunteers', 'volunteers_enabled_until', ]
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
