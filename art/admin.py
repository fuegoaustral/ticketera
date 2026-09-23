from decimal import Decimal

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms import ModelForm
from django.forms.models import BaseInlineFormSet

from .forms import LocalizedDecimalField
from .models import (
    ArtProgram,
    Artwork,
    ArtworkGrantItem,
    ArtworkGrantItemPhoto,
    ArtworkInvitation,
    ArtworkLogisticsPerson,
    ArtworkPhoto,
    ArtworkCheckoutPhoto,
    ArtworkProvider,
    ArtworkProviderVehicle,
)


@admin.register(ArtProgram)
class ArtProgramAdmin(admin.ModelAdmin):
    list_display = ('event', 'is_current', 'registration_opens', 'registration_closes', 'grants_enabled', 'grant_deadline', 'guide_deadline', 'public_description_max_length', 'logistics_deadline')
    list_filter = ('is_current', 'grants_enabled', 'event')
    date_hierarchy = 'registration_opens'


class ArtworkGrantItemFormSet(BaseInlineFormSet):
    phase = None

    def save_new(self, form, commit=True):
        item = super().save_new(form, commit=False)
        item.phase = self.phase
        if commit:
            item.save()
        return item


class ArtworkBudgetItemFormSet(ArtworkGrantItemFormSet):
    phase = ArtworkGrantItem.Phase.BUDGET


class ArtworkExpenseItemFormSet(ArtworkGrantItemFormSet):
    phase = ArtworkGrantItem.Phase.EXPENSE


class ArtworkGrantItemAdminForm(ModelForm):
    amount = LocalizedDecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '150.000,00'}),
    )
    exchange_rate = LocalizedDecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal('0.0001'),
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '1.234,56'}),
    )
    confirm_large_amount = forms.BooleanField(
        required=False,
        label='Confirmo el monto si supera ARS 1.000.000',
    )

    class Meta:
        model = ArtworkGrantItem
        fields = ('item_type', 'concept', 'details', 'amount', 'currency', 'exchange_rate', 'rate_date', 'rate_source', 'review_status', 'review_notes')

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get('amount')
        if cleaned.get('currency') == ArtworkGrantItem.Currency.USD:
            amount = amount * (cleaned.get('exchange_rate') or Decimal('0')) if amount else None
        if amount and amount >= Decimal('1000000') and not cleaned.get('confirm_large_amount'):
            self.add_error('confirm_large_amount', 'Confirmá el monto convertido antes de guardar.')
        return cleaned


class ArtworkGrantItemInline(admin.StackedInline):
    model = ArtworkGrantItem
    form = ArtworkGrantItemAdminForm
    extra = 0
    phase = None
    fieldsets = (
        ('Concepto', {'fields': (('item_type', 'concept'), 'details')}),
        ('Importe', {'fields': (('amount', 'currency', 'exchange_rate'), ('rate_date', 'rate_source'))}),
        ('Revisión', {'fields': ('review_status', 'review_notes', 'confirm_large_amount')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(phase=self.phase)

    def _is_locked(self, obj):
        return obj and self.phase == ArtworkGrantItem.Phase.EXPENSE and obj.grant_status == Artwork.GrantStatus.CLOSED

    def has_add_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_delete_permission(request, obj)


class ArtworkBudgetItemInline(ArtworkGrantItemInline):
    phase = ArtworkGrantItem.Phase.BUDGET
    formset = ArtworkBudgetItemFormSet
    verbose_name = 'Ítem de presupuesto'
    verbose_name_plural = 'Presupuesto de la beca'


class ArtworkExpenseItemInline(ArtworkGrantItemInline):
    phase = ArtworkGrantItem.Phase.EXPENSE
    formset = ArtworkExpenseItemFormSet
    verbose_name = 'Ítem de rendición'
    verbose_name_plural = 'Rendición de la beca'


class ArtworkGrantItemPhotoAdminForm(ModelForm):
    class Meta:
        model = ArtworkGrantItemPhoto
        fields = '__all__'

    def clean_item(self):
        item = self.cleaned_data['item']
        if item.artwork.grant_status == Artwork.GrantStatus.CLOSED:
            raise ValidationError('La rendición está aprobada. Reabrila antes de cambiar comprobantes.')
        return item


@admin.register(ArtworkGrantItemPhoto)
class ArtworkGrantItemPhotoAdmin(admin.ModelAdmin):
    form = ArtworkGrantItemPhotoAdminForm

    def _is_locked(self, obj):
        return obj and obj.item.artwork.grant_status == Artwork.GrantStatus.CLOSED

    def has_change_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._is_locked(obj) and super().has_delete_permission(request, obj)


class ArtworkPhotoInline(admin.TabularInline):
    model = ArtworkPhoto
    extra = 0


class ArtworkCheckoutPhotoInline(admin.TabularInline):
    model = ArtworkCheckoutPhoto
    extra = 0


class ArtworkInvitationInline(admin.TabularInline):
    model = ArtworkInvitation
    extra = 0


class ArtworkLogisticsPersonInline(admin.TabularInline):
    model = ArtworkLogisticsPerson
    extra = 0


class ArtworkProviderInline(admin.TabularInline):
    model = ArtworkProvider
    extra = 0


class ArtworkAdminForm(ModelForm):
    grant_approved_amount_ars = LocalizedDecimalField(
        required=False, max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        label='Monto de beca aprobado',
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '450.000,00'}),
    )
    confirm_large_grant_amount = forms.BooleanField(
        required=False,
        label='Confirmo el monto aprobado si supera ARS 1.000.000',
    )

    class Meta:
        model = Artwork
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get('grant_approved_amount_ars')
        if amount and amount >= Decimal('1000000') and not cleaned.get('confirm_large_grant_amount'):
            self.add_error('confirm_large_grant_amount', 'Confirmá el monto aprobado antes de guardar.')
        return cleaned


@admin.register(Artwork)
class ArtworkAdmin(admin.ModelAdmin):
    form = ArtworkAdminForm
    list_display = ('title', 'event', 'kind', 'status', 'owner', 'grant_status', 'assigned_location', 'checkout_verified_at', 'updated_at')
    list_filter = ('event', 'kind', 'status', 'grant_status', 'checkout_completed', 'understanding_letter_physical_received', 'submitted_at')
    search_fields = ('title', 'owner__email', 'public_description')
    autocomplete_fields = ('owner', 'collaborators', 'operations_group', 'checkin_art_by', 'checkout_art_responsible', 'checkout_verified_by', 'safety_responsible')
    readonly_fields = ('submitted_at', 'checkin_art_by', 'checkout_requested_at', 'checkout_verified_by', 'created_at', 'updated_at', 'version')
    fieldsets = (
        ('Obra', {'fields': ('event', 'owner', 'collaborators', 'operations_group', 'kind', 'title')}),
        ('Check-in de obra', {'fields': ('checkin_arrived_at', 'checkin_art_at', 'checkin_art_by', 'checkin_placed', 'checkin_placement_changed', 'checkin_placement_change_notes', 'understanding_letter', 'understanding_letter_physical_received', 'understanding_letter_physical_custodian', 'understanding_letter_physical_notes', 'understanding_letter_physical_waiver', 'understanding_letter_physical_waiver_reason')}),
        ('Propuesta y seguridad', {'fields': ('proposal', 'dimensions', 'materials', 'technical_needs', 'uses_sound', 'safety_plan', 'uses_fire', 'fire_details', 'extinguishing_plan', 'power_watts', 'safety_contact', 'safety_responsible')}),
        ('Beca', {'fields': ('grant_requested', 'grant_justification', 'grant_status', 'grant_approved_amount_ars', 'confirm_large_grant_amount', 'grant_decision_notes', 'grant_paid_at', 'grant_payment_reference', 'grant_report')}),
        ('Desplegable y placement', {'fields': ('public_title', 'public_description', 'preferred_location', 'assigned_location', 'placement_notes')}),
        ('Logística', {'fields': ('arrival_date', 'departure_date', 'crew', 'providers')}),
        ('Checkout, paso 1, equipo de la obra', {'fields': ('checkout_completed', 'checkout_team_responsible', 'checkout_notes', 'checkout_requested_at')}),
        ('Checkout, paso 2, Arte', {'fields': ('checkout_art_responsible', 'checkout_verified_at', 'checkout_verified_by')}),
        ('Seguimiento', {'fields': ('status', 'review_feedback', 'benefit_status', 'benefit_notes', 'submitted_at', 'version', 'created_at', 'updated_at')}),
    )
    inlines = [ArtworkBudgetItemInline, ArtworkExpenseItemInline, ArtworkPhotoInline, ArtworkCheckoutPhotoInline, ArtworkInvitationInline, ArtworkLogisticsPersonInline, ArtworkProviderInline]
    actions = ('approve_grant_reports', 'reopen_grant_reports')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.grant_status == Artwork.GrantStatus.CLOSED:
            fields.append('grant_report')
        return fields

    def save_model(self, request, obj, form, change):
        if obj.checkin_art_at and not obj.checkin_art_by_id:
            obj.checkin_art_by = request.user
        if obj.checkout_verified_at and not obj.checkout_verified_by_id:
            obj.checkout_verified_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Aprobar rendiciones seleccionadas')
    def approve_grant_reports(self, request, queryset):
        reports = queryset.filter(grant_status=Artwork.GrantStatus.REPORTED)
        count = reports.count()
        for artwork in reports:
            artwork.grant_status = Artwork.GrantStatus.CLOSED
            artwork.save(update_fields=['grant_status', 'updated_at'])
        self.message_user(request, f'{count} rendición(es) aprobada(s).')

    @admin.action(description='Desaprobar y reabrir rendiciones seleccionadas')
    def reopen_grant_reports(self, request, queryset):
        reports = queryset.filter(grant_status=Artwork.GrantStatus.CLOSED)
        count = reports.count()
        for artwork in reports:
            artwork.grant_status = Artwork.GrantStatus.REPORTED
            artwork.save(update_fields=['grant_status', 'updated_at'])
        self.message_user(request, f'{count} rendición(es) reabierta(s).')


admin.site.register(ArtworkCheckoutPhoto)
admin.site.register(ArtworkProviderVehicle)
