from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html_join

from .models import Profile, SedeSubscription, SedeUnmatchedSubscription


User.__str__ = lambda self: f'{self.first_name} {self.last_name} ({self.email})'


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'profile'
    readonly_fields = (
        'sede_subscription_id', 'sede_subscription_status', 'sede_payment_method',
        'sede_last_payment_date', 'sede_last_payment_amount', 'sede_next_payment_date',
        'sede_member_since', 'sede_synced_at', 'sede_subscriptions_summary',
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
                'sede_next_payment_date', 'sede_member_since', 'sede_synced_at', 'sede_subscriptions_summary',
            ),
        }),
    )

    def sede_subscriptions_summary(self, instance):
        rows = instance.sede_subscriptions.order_by('-is_active', '-last_payment_date')[:20]
        if not rows:
            return '-'
        return format_html_join(
            '\n',
            '{}',
            (
                (
                    f'[{ "ACTIVE" if row.is_active else "inactive"}] '
                    f'{row.subscription_id} | plan={row.plan_id or "-"} | '
                    f'tier={row.tier_name or "-"} | status={row.status or "-"}'
                ,)
                for row in rows
            ),
        )

    sede_subscriptions_summary.short_description = 'La Sede subscriptions'


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


@admin.register(SedeSubscription)
class SedeSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'subscription_id', 'get_user_email', 'plan_id', 'tier_name',
        'status', 'is_active', 'matched_via', 'synced_at',
    )
    list_filter = ('is_active', 'status', 'plan_id', 'matched_via')
    search_fields = (
        'subscription_id', 'plan_id', 'tier_name', 'profile__user__email',
        'profile__user__first_name', 'profile__user__last_name',
    )
    readonly_fields = [field.name for field in SedeSubscription._meta.fields]

    def get_user_email(self, instance):
        return instance.profile.user.email

    get_user_email.short_description = 'User email'


@admin.register(SedeUnmatchedSubscription)
class SedeUnmatchedSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'subscription_id', 'payer_email', 'document_number',
        'plan_id', 'tier_name', 'status', 'last_seen_at',
    )
    list_filter = ('status', 'plan_id')
    search_fields = (
        'subscription_id', 'payer_email', 'payer_first_name', 'payer_last_name',
        'document_number', 'plan_id', 'tier_name',
    )
    readonly_fields = [field.name for field in SedeUnmatchedSubscription._meta.fields]