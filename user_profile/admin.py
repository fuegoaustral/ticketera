from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import Profile


User.__str__ = lambda self: f'{self.first_name} {self.last_name} ({self.email})'


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'profile'
    readonly_fields = (
        'sede_subscription_id', 'sede_subscription_status', 'sede_payment_method',
        'sede_last_payment_date', 'sede_last_payment_amount', 'sede_next_payment_date',
        'sede_member_since', 'sede_synced_at',
    )
    fieldsets = (
        (None, {
            'fields': (
                'document_type', 'document_number', 'phone', 'profile_completion',
            ),
        }),
        ('La Sede', {
            'fields': (
                'miembro_sede', 'sede_subscription_id', 'sede_subscription_status',
                'sede_payment_method', 'sede_last_payment_date', 'sede_last_payment_amount',
                'sede_next_payment_date', 'sede_member_since', 'sede_synced_at',
            ),
        }),
    )


# Crea una nueva clase que extienda de LibraryUserAdmin
class CustomUserAdmin(UserAdmin):
    inlines = (ProfileInline,)
    list_display = (
        'email', 'is_staff', 'is_superuser', 'first_name', 'last_name', 'get_phone', 'get_document_type',
        'get_document_number', 'get_miembro_sede')
    
    search_fields = ('email', 'first_name', 'last_name', 'profile__document_number', 'profile__phone')

    def get_phone(self, instance):
        return instance.profile.phone

    get_phone.short_description = 'Phone'

    def get_document_type(self, instance):
        return instance.profile.document_type

    get_document_type.short_description = 'Document Type'

    def get_document_number(self, instance):
        return instance.profile.document_number

    get_document_number.short_description = 'Document Number'

    def get_miembro_sede(self, instance):
        return instance.profile.miembro_sede

    get_miembro_sede.short_description = 'La Sede'
    get_miembro_sede.boolean = True


# Quitar el registro original y registrar el nuevo UserAdmin
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)