from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import Profile


User.__str__ = lambda self: f'{self.first_name} {self.last_name} ({self.email})'


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'profile'


# Crea una nueva clase que extienda de LibraryUserAdmin
class CustomUserAdmin(UserAdmin):
    inlines = (ProfileInline,)
    list_display = (
        'email', 'is_staff', 'is_superuser', 'first_name', 'last_name', 'get_phone', 'get_document_type',
        'get_document_number')

    def get_phone(self, instance):
        return instance.profile.phone

    get_phone.short_description = 'Phone'

    def get_document_type(self, instance):
        return instance.profile.document_type

    get_document_type.short_description = 'Document Type'

    def get_document_number(self, instance):
        return instance.profile.document_number

    get_document_number.short_description = 'Document Number'


# Quitar el registro original y registrar el nuevo UserAdmin
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)