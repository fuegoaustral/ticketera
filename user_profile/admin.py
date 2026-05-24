from django.contrib import admin
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html, format_html_join
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse

from .models import Profile, SedeSubscription, SedeSubscriptionPlan, SedeUnmatchedSubscription
from .impersonation import IMPERSONATION_ADMIN_USER_ID_SESSION_KEY

User.__str__ = lambda self: f'{self.first_name} {self.last_name} ({self.email})'


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'profile'
    readonly_fields = (
        'sede_subscriptions_summary',
    )
    fieldsets = (
        (None, {
            'fields': (
                'document_type', 'document_number', 'phone', 'profile_completion',
            ),
        }),
        ('La Sede', {
            'fields': (
                'sede_subscriptions_summary',
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
        'get_document_number', 'get_miembro_sede', 'impersonate_link')
    
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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:user_id>/impersonate/',
                self.admin_site.admin_view(self.impersonate_user_view),
                name='user_profile_user_impersonate',
            ),
        ]
        return custom_urls + urls

    def impersonate_link(self, instance):
        if instance.is_superuser:
            return '-'
        url = reverse('admin:user_profile_user_impersonate', args=[instance.pk])
        return format_html('<a class="button" href="{}">Impersonalizar</a>', url)

    impersonate_link.short_description = 'Impersonalizar'

    def impersonate_user_view(self, request, user_id):
        if not request.user.is_superuser:
            self.message_user(
                request,
                'Solo superusuarios pueden impersonalizar usuarios.',
                level=messages.ERROR,
            )
            return redirect('admin:auth_user_changelist')

        target_user = get_object_or_404(User, pk=user_id)
        if target_user.pk == request.user.pk:
            self.message_user(
                request,
                'No podés impersonalizar tu propio usuario.',
                level=messages.WARNING,
            )
            return redirect('admin:auth_user_change', target_user.pk)

        if target_user.is_superuser:
            self.message_user(
                request,
                'No se permite impersonalizar otro superusuario.',
                level=messages.ERROR,
            )
            return redirect('admin:auth_user_change', target_user.pk)

        request.session.setdefault(IMPERSONATION_ADMIN_USER_ID_SESSION_KEY, request.user.pk)
        login(request, target_user, backend=settings.AUTHENTICATION_BACKENDS[0])

        self.message_user(
            request,
            f'Ahora navegás como {target_user.email}.',
            level=messages.INFO,
        )
        return redirect('mi_fuego')


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


@admin.register(SedeSubscriptionPlan)
class SedeSubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('plan_id', 'plan_name', 'billing_cycle', 'is_enabled', 'subscriptions_count', 'last_seen_at')
    list_filter = ('is_enabled',)
    search_fields = ('plan_id', 'plan_name', 'billing_cycle')