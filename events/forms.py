from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from .models import (
    Artwork, ArtworkGrantItem, ArtworkInvitation, ArtworkLogisticsPerson,
    ArtworkPhoto, ArtworkCheckoutPhoto, ArtworkProvider, ArtworkProviderVehicle,
)


class LocalizedDecimalField(forms.DecimalField):
    """Accept both Argentine (150.000,00) and canonical (150000.00) input."""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().replace(' ', '')
            if ',' in value:
                value = value.replace('.', '').replace(',', '.')
        return super().to_python(value)


ARTWORK_BLOCK_FIELDS = {
    'proposal': (
        'title', 'proposal', 'dimensions', 'materials', 'technical_needs',
        'uses_fire', 'fire_details', 'extinguishing_plan', 'power_watts',
        'safety_plan', 'safety_responsible_email',
    ),
    'grant': ('grant_requested', 'grant_justification'),
    'guide': ('public_title', 'public_description', 'preferred_location'),
    'logistics': ('arrival_date', 'departure_date'),
    'checkout': (
        'checkout_completed', 'checkout_team_responsible', 'checkout_art_responsible', 'checkout_notes',
    ),
    'understanding_letter_digital': ('understanding_letter',),
    'grant_report': ('grant_report',),
}


def _art_responsibles(artwork):
    if not artwork.event_id:
        return User.objects.none()
    return User.objects.filter(
        Q(is_superuser=True) | Q(admin_events=artwork.event) | Q(pk=artwork.checkout_art_responsible_id),
    ).distinct().order_by('first_name', 'last_name', 'email')


class ArtworkForm(forms.ModelForm):
    BLOCK_FIELDS = ARTWORK_BLOCK_FIELDS

    collaborator_emails = forms.CharField(
        required=False,
        label='Colaboradores',
        help_text='Emails separados por coma. Si todavía no tienen cuenta, recibirán una invitación.',
    )
    safety_responsible_email = forms.EmailField(
        required=False,
        label='Responsable de seguridad',
        help_text='Debe ser el email de un perfil ya registrado en la aplicación.',
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Artwork
        fields = [field for fields in ARTWORK_BLOCK_FIELDS.values() for field in fields]
        widgets = {
            'proposal': forms.Textarea(attrs={'rows': 8}),
            'materials': forms.Textarea(attrs={'rows': 5}),
            'technical_needs': forms.Textarea(attrs={'rows': 5}),
            'fire_details': forms.Textarea(attrs={'rows': 5}),
            'extinguishing_plan': forms.Textarea(attrs={'rows': 5}),
            'safety_plan': forms.Textarea(attrs={'rows': 5}),
            'grant_justification': forms.Textarea(attrs={'rows': 6}),
            'public_description': forms.Textarea(attrs={'rows': 6, 'maxlength': 500}),
            'arrival_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'departure_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'checkout_notes': forms.Textarea(attrs={'rows': 5}),
            'grant_report': forms.Textarea(attrs={'rows': 8}),
        }

    def __init__(self, *args, program, owner, actor=None, action='save', **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        self.owner = owner
        self.actor = actor or owner
        self.action = action
        self.is_manager = self.instance.pk and self.instance.can_manage(self.actor)
        self.is_contributor = self.instance.pk and self.instance.can_edit(self.actor)
        self.new_invitations = []

        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control')
            field.widget.attrs['form'] = 'artwork-form'
        self.fields['checkout_team_responsible'].widget.attrs['class'] = 'form-select'
        self.fields['checkout_art_responsible'].widget.attrs['class'] = 'form-select'
        self.fields['collaborator_emails'].widget.attrs.update({'class': 'form-control', 'placeholder': 'persona@ejemplo.com, otra@ejemplo.com'})
        self.fields['safety_responsible_email'].widget.attrs.update({'class': 'form-control', 'placeholder': 'persona@ejemplo.com'})
        if self.instance.safety_responsible_id:
            self.fields['safety_responsible_email'].initial = self.instance.safety_responsible.email
        self.fields['expected_version'].initial = self.instance.version if self.instance.pk else None
        self.fields['checkout_team_responsible'].queryset = self.instance.logistics_people.all() if self.instance.pk else ArtworkLogisticsPerson.objects.none()
        self.fields['checkout_art_responsible'].queryset = _art_responsibles(self.instance)
        self.fields['checkout_art_responsible'].help_text = 'La coordinación de Arte asigna este responsable.'
        if self.instance.pk and not self.is_manager and not self.is_contributor:
            for name, field in self.fields.items():
                if name != 'expected_version':
                    field.disabled = True
        elif not self.is_manager:
            self.fields['checkout_art_responsible'].disabled = True

        # El título identifica la obra; la descripción se puede completar después.
        self.fields['title'].required = True
        self.fields['proposal'].required = False

        if not program.grants_enabled:
            for name in (*self.BLOCK_FIELDS['grant'], *self.BLOCK_FIELDS['grant_report']):
                self.fields.pop(name)
        elif not self.is_manager and self.instance.grant_status not in (
            Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID,
        ):
            self.fields['grant_report'].disabled = True

        if self.instance.grant_status not in (Artwork.GrantStatus.NOT_REQUESTED, Artwork.GrantStatus.INFO_REQUIRED):
            for name in self.BLOCK_FIELDS['grant']:
                if name in self.fields:
                    self.fields[name].disabled = True

        if self.instance.grant_status == Artwork.GrantStatus.CLOSED:
            self.fields['grant_report'].disabled = True

        if self.instance.checkout_verified_at:
            for name in self.BLOCK_FIELDS['checkout']:
                self.fields[name].disabled = True

        if self.instance.pk:
            emails = list(self.instance.collaborators.order_by('email').values_list('email', flat=True))
            emails += list(self.instance.invitations.filter(accepted_at=None, revoked_at=None).values_list('email', flat=True))
            self.fields['collaborator_emails'].initial = ', '.join(dict.fromkeys(emails))
        if self.actor != owner:
            self.fields['collaborator_emails'].disabled = True

        if not self.is_manager:
            if not program.is_current:
                for name, field in self.fields.items():
                    if name != 'expected_version':
                        field.disabled = True
            for block, fields in self.BLOCK_FIELDS.items():
                if block == 'proposal' and not self.instance.pk:
                    continue
                if program.checkpoint_state(block) != 'open':
                    for name in fields:
                        if name in self.fields:
                            self.fields[name].disabled = True

    def clean_collaborator_emails(self):
        if self.fields['collaborator_emails'].disabled:
            return []
        raw = self.cleaned_data['collaborator_emails'].replace('\n', ',').replace(';', ',')
        validate = forms.EmailField().clean
        emails = []
        for value in raw.split(','):
            if value.strip():
                email = validate(value.strip()).lower()
                owner_email = (self.owner.email or '').lower() if self.owner else ''
                if email not in emails and email != owner_email:
                    emails.append(email)
        if len(emails) > 20:
            raise forms.ValidationError('Podés sumar hasta 20 colaboradores por obra.')
        return emails

    def clean_safety_responsible_email(self):
        email = self.cleaned_data['safety_responsible_email'].lower()
        if not email:
            return None
        user = User.objects.filter(email__iexact=email, profile__isnull=False).first()
        if not user:
            raise forms.ValidationError('Ese email no corresponde a un perfil registrado.')
        return user

    def clean(self):
        cleaned = super().clean()
        now = timezone.now()
        if not self.instance.pk and not self.program.registration_is_open(now):
            self.add_error(None, 'La inscripción de obras está cerrada.')

        arrival = cleaned.get('arrival_date')
        departure = cleaned.get('departure_date')
        if arrival and departure and departure < arrival:
            self.add_error('departure_date', 'La salida no puede ser anterior al ingreso.')

        if self.instance.pk and self.is_bound:
            expected = cleaned.get('expected_version')
            current = Artwork.objects.filter(pk=self.instance.pk).values_list('version', flat=True).first()
            if expected != current:
                self.add_error(None, 'Otra persona guardó cambios mientras editabas. Recargá la página antes de volver a guardar.')

            closed = []
            if not self.is_manager:
                for block, fields in self.BLOCK_FIELDS.items():
                    if self.program.checkpoint_state(block) != 'open' and any(name in self.data for name in fields):
                        closed.append(block)
            if closed:
                self.add_error(None, 'Una fecha límite venció mientras editabas. Recargá la página: no se guardó ningún cambio.')

        if self.action == 'submit':
            if not self.program.is_current:
                self.add_error(None, 'Esta convocatoria ya es histórica y no admite nuevas presentaciones.')
            if self.instance.pk and self.instance.status not in (
                Artwork.Status.DRAFT, Artwork.Status.CHANGES_REQUESTED,
            ):
                self.add_error(None, 'Esta obra ya fue presentada. La coordinación gestiona su estado desde la revisión.')
            if cleaned.get('uses_fire'):
                for field in ('fire_details', 'extinguishing_plan', 'safety_responsible_email'):
                    if not cleaned.get(field):
                        self.add_error(field, 'Completá este campo para una obra que utiliza fuego.')
        return cleaned

    def save(self, commit=True):
        artwork = super().save(commit=False)
        artwork.safety_responsible = self.cleaned_data.get('safety_responsible_email')
        if artwork.pk:
            artwork.version += 1
        if commit:
            artwork.save()
            if self.actor == self.owner and not self.fields['collaborator_emails'].disabled:
                self._sync_collaborators(artwork, self.cleaned_data['collaborator_emails'])
        return artwork

    def _sync_collaborators(self, artwork, emails):
        users = []
        pending = []
        current_users = {
            user.email.lower(): user
            for user in artwork.collaborators.all()
            if user.email
        }
        for email in emails:
            if email in current_users:
                users.append(current_users[email])
                continue
            existing = ArtworkInvitation.objects.filter(artwork=artwork, email=email).first()
            should_send = not existing or not existing.is_pending
            invitation, created = ArtworkInvitation.objects.update_or_create(
                artwork=artwork,
                email=email,
                defaults={
                    'invited_by': self.actor,
                    'expires_at': timezone.now() + timedelta(days=30),
                    'accepted_at': None,
                    'revoked_at': None,
                },
            )
            pending.append(email)
            if created or should_send:
                self.new_invitations.append(invitation)
        artwork.collaborators.set(users)
        artwork.invitations.filter(accepted_at=None, revoked_at=None).exclude(email__in=pending).update(revoked_at=timezone.now())


class ArtworkGrantItemForm(forms.ModelForm):
    expected_updated_at = forms.CharField(widget=forms.HiddenInput, required=False)
    amount = LocalizedDecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.TextInput(attrs={
            'inputmode': 'decimal', 'autocomplete': 'off', 'data-money-input': 'true',
            'placeholder': '150.000,00',
        }),
    )
    exchange_rate = LocalizedDecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal('0.0001'),
        widget=forms.TextInput(attrs={
            'inputmode': 'decimal', 'autocomplete': 'off', 'data-money-input': 'true',
            'placeholder': '1.234,56', 'data-exchange-rate': 'true',
        }),
    )
    confirm_large_amount = forms.BooleanField(
        required=False,
        label='Confirmo el monto si supera ARS 1.000.000',
        help_text='Se pide una confirmación extra para evitar errores de ceros o separadores.',
    )

    class Meta:
        model = ArtworkGrantItem
        fields = ('item_type', 'concept', 'details', 'amount', 'currency', 'exchange_rate', 'rate_date', 'rate_source')
        widgets = {
            'details': forms.Textarea(attrs={'rows': 5}),
            'rate_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, phase, **kwargs):
        super().__init__(*args, **kwargs)
        self.phase = phase
        self.fields['images'] = MultipleImageField(required=False, label='Imágenes o comprobantes')
        self.fields['images'].help_text = 'Podés seleccionar varias imágenes. Máximo 10 MB por archivo.'
        self.fields['images'].widget.attrs.update({'accept': 'image/*', 'data-image-preview': 'true'})
        self.fields['rate_date'].label = 'Fecha de entrega del presupuesto' if phase == ArtworkGrantItem.Phase.BUDGET else 'Fecha real de pago'
        self.fields['rate_date'].initial = self.instance.rate_date if self.instance.pk else timezone.localdate()
        self.fields['exchange_rate'].label = 'Cotización'
        self.fields['exchange_rate'].help_text = 'Pesos por cada USD. Para ARS se guarda 1 automáticamente.'
        self.fields['rate_source'].help_text = 'Para USD: por ejemplo “BNA vendedor” o “MEP”, con referencia verificable.'
        self.fields['expected_updated_at'].initial = self.instance.updated_at.isoformat() if self.instance.pk else ''
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')
        self.fields['currency'].widget.attrs['data-currency-select'] = 'true'

    def clean_images(self):
        images = self.cleaned_data['images']
        if len(images) > 10:
            raise forms.ValidationError('Podés subir hasta 10 imágenes por vez.')
        if any(image.size > 10 * 1024 * 1024 for image in images):
            raise forms.ValidationError('Cada imagen puede pesar hasta 10 MB.')
        if self.instance.pk and self.instance.photos.count() + len(images) > 30:
            raise forms.ValidationError('Cada ítem admite hasta 30 imágenes.')
        return images

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and self.is_bound:
            expected = cleaned.get('expected_updated_at')
            if not expected or expected != self.instance.updated_at.isoformat():
                self.add_error(None, 'Otra persona modificó este ítem. Recargá la página antes de volver a guardar.')
        currency = cleaned.get('currency')
        if currency == ArtworkGrantItem.Currency.ARS:
            cleaned['exchange_rate'] = 1
            cleaned['rate_source'] = ''
        elif currency == ArtworkGrantItem.Currency.USD:
            if not cleaned.get('exchange_rate') or cleaned['exchange_rate'] <= 0:
                self.add_error('exchange_rate', 'Ingresá una cotización mayor a cero.')
            if not cleaned.get('rate_source'):
                self.add_error('rate_source', 'Indicá la fuente y el tipo de dólar utilizado.')
        amount_ars = cleaned.get('amount')
        if currency == ArtworkGrantItem.Currency.USD and amount_ars and cleaned.get('exchange_rate'):
            amount_ars *= cleaned['exchange_rate']
        if amount_ars and amount_ars >= Decimal('1000000') and not cleaned.get('confirm_large_amount'):
            self.add_error('confirm_large_amount', 'Confirmá el monto convertido antes de guardar.')
        return cleaned

    def save(self, commit=True):
        item = super().save(commit=False)
        item.phase = self.phase
        if item.currency == ArtworkGrantItem.Currency.ARS:
            item.exchange_rate = 1
            item.rate_source = ''
        if commit:
            item.save()
        return item


class ArtworkGrantItemReviewForm(forms.ModelForm):
    class Meta:
        model = ArtworkGrantItem
        fields = ('review_status', 'review_notes')
        widgets = {'review_notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('review_status') == ArtworkGrantItem.ReviewStatus.REJECTED and not cleaned.get('review_notes'):
            self.add_error('review_notes', 'Explicá qué necesita corregirse para rechazar el ítem.')
        return cleaned


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        if not any(files):
            if self.required:
                raise forms.ValidationError('Seleccioná al menos una foto.')
            return []
        clean_one = super().clean
        return [clean_one(file, initial) for file in files if file]


class ArtworkPhotoUploadForm(forms.Form):
    images = MultipleImageField(label='Fotos')
    stage = forms.ChoiceField(choices=ArtworkPhoto.Stage.choices, label='Etapa')
    caption = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4}), label='Descripción o crédito')
    publication_authorized = forms.BooleanField(
        required=False,
        label='Autorizo a Fuego Austral a publicar estas fotos en el archivo de la obra',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else ('form-select' if isinstance(field.widget, forms.Select) else 'form-control'))
        self.fields['images'].widget.attrs.update({'accept': 'image/*', 'data-image-preview': 'true'})

    def clean_images(self):
        images = self.cleaned_data['images']
        if len(images) > 10:
            raise forms.ValidationError('Podés subir hasta 10 fotos por vez.')
        if any(image.size > 10 * 1024 * 1024 for image in images):
            raise forms.ValidationError('Cada foto puede pesar hasta 10 MB.')
        return images


class ArtworkCheckoutPhotoUploadForm(forms.Form):
    images = MultipleImageField(label='Fotos de checkout')
    category = forms.ChoiceField(choices=ArtworkCheckoutPhoto.Category.choices, label='Categoría')
    caption = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4}), label='Detalle')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')
        self.fields['images'].widget.attrs.update({'accept': 'image/*', 'data-image-preview': 'true'})

    def clean_images(self):
        images = self.cleaned_data['images']
        if len(images) > 10:
            raise forms.ValidationError('Podés subir hasta 10 fotos por vez.')
        if any(image.size > 10 * 1024 * 1024 for image in images):
            raise forms.ValidationError('Cada foto puede pesar hasta 10 MB.')
        return images


class ArtworkLogisticsPersonForm(forms.ModelForm):
    class Meta:
        model = ArtworkLogisticsPerson
        fields = (
            'first_name', 'last_name', 'email', 'phone', 'document_type', 'document_number',
            'early_entry', 'early_entry_date', 'dismantling', 'dismantling_date',
        )
        widgets = {
            'phone': forms.TextInput(attrs={'type': 'tel'}),
            'early_entry_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'dismantling_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else ('form-select' if isinstance(field.widget, forms.Select) else 'form-control'))

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('early_entry') and not cleaned.get('dismantling'):
            self.add_error(None, 'Indicá si participa del ingreso anticipado, del desarme o de ambos.')
        if not cleaned.get('early_entry'):
            cleaned['early_entry_date'] = None
        if not cleaned.get('dismantling'):
            cleaned['dismantling_date'] = None
        if cleaned.get('early_entry') and not cleaned.get('early_entry_date'):
            self.add_error('early_entry_date', 'Indicá la fecha de ingreso anticipado.')
        if cleaned.get('dismantling') and not cleaned.get('dismantling_date'):
            self.add_error('dismantling_date', 'Indicá la fecha de desarme y salida.')
        return cleaned


class ArtworkProviderForm(forms.ModelForm):
    class Meta:
        model = ArtworkProvider
        fields = (
            'company_name', 'contact_first_name', 'contact_last_name', 'email', 'phone',
            'service_description', 'for_entry', 'early_entry_at', 'early_exit_at',
            'for_exit', 'dismantling_entry_at', 'dismantling_exit_at',
        )
        widgets = {
            'phone': forms.TextInput(attrs={'type': 'tel'}),
            'service_description': forms.Textarea(attrs={'rows': 5}),
            'early_entry_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'early_exit_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'dismantling_entry_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'dismantling_exit_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else ('form-select' if isinstance(field.widget, forms.Select) else 'form-control'))

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('for_entry') and not cleaned.get('for_exit'):
            self.add_error(None, 'Elegí si el proveedor participa del ingreso, de la salida o de ambos.')
        if not cleaned.get('for_entry'):
            cleaned['early_entry_at'] = cleaned['early_exit_at'] = None
        else:
            if not cleaned.get('early_entry_at'):
                self.add_error('early_entry_at', 'Indicá cuándo entra al predio.')
            if not cleaned.get('early_exit_at'):
                self.add_error('early_exit_at', 'Indicá cuándo sale del predio.')
            if cleaned.get('early_entry_at') and cleaned.get('early_exit_at') and cleaned['early_exit_at'] < cleaned['early_entry_at']:
                self.add_error('early_exit_at', 'La salida no puede ser anterior a la entrada.')
        if not cleaned.get('for_exit'):
            cleaned['dismantling_entry_at'] = cleaned['dismantling_exit_at'] = None
        else:
            if not cleaned.get('dismantling_entry_at'):
                self.add_error('dismantling_entry_at', 'Indicá cuándo entra para el desarme.')
            if not cleaned.get('dismantling_exit_at'):
                self.add_error('dismantling_exit_at', 'Indicá cuándo sale definitivamente.')
            if cleaned.get('dismantling_entry_at') and cleaned.get('dismantling_exit_at') and cleaned['dismantling_exit_at'] < cleaned['dismantling_entry_at']:
                self.add_error('dismantling_exit_at', 'La salida no puede ser anterior a la entrada.')
        if cleaned.get('for_entry') and cleaned.get('for_exit') and cleaned.get('early_exit_at') and cleaned.get('dismantling_entry_at') and cleaned['dismantling_entry_at'] < cleaned['early_exit_at']:
            self.add_error('dismantling_entry_at', 'El desarme no puede comenzar antes de que termine el ingreso anticipado.')
        return cleaned


class ArtworkProviderVehicleForm(forms.ModelForm):
    class Meta:
        model = ArtworkProviderVehicle
        fields = (
            'vehicle_type', 'plate', 'make_model', 'driver_name',
            'driver_document_type', 'driver_document_number', 'notes',
        )
        widgets = {'notes': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def clean_plate(self):
        return self.cleaned_data['plate'].replace(' ', '').upper()


class ArtworkReviewForm(forms.ModelForm):
    expected_updated_at = forms.CharField(widget=forms.HiddenInput, required=False)
    grant_approved_amount_ars = LocalizedDecimalField(
        required=False, max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        label='Monto de beca aprobado',
        widget=forms.TextInput(attrs={
            'inputmode': 'decimal', 'autocomplete': 'off', 'data-money-input': 'true',
            'placeholder': '450.000,00',
        }),
    )
    confirm_large_grant_amount = forms.BooleanField(
        required=False,
        label='Confirmo el monto aprobado si supera ARS 1.000.000',
        help_text='Verificá los separadores y la cantidad de ceros antes de guardar.',
    )

    class Meta:
        model = Artwork
        fields = (
            'checkin_arrived_at', 'checkin_art_at', 'checkin_placed',
            'checkin_placement_changed', 'checkin_placement_change_notes',
            'understanding_letter', 'understanding_letter_physical_received',
            'understanding_letter_physical_custodian', 'understanding_letter_physical_notes',
            'understanding_letter_physical_waiver', 'understanding_letter_physical_waiver_reason',
            'status', 'review_feedback', 'grant_status', 'grant_approved_amount_ars',
            'grant_decision_notes', 'grant_paid_at', 'grant_payment_reference',
            'assigned_location', 'placement_notes', 'checkout_team_responsible',
            'checkout_art_responsible', 'checkout_verified_at',
            'benefit_status', 'benefit_notes',
        )
        widgets = {
            'checkin_arrived_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'checkin_art_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'checkin_placement_change_notes': forms.Textarea(attrs={'rows': 4}),
            'review_feedback': forms.Textarea(attrs={'rows': 5}),
            'grant_decision_notes': forms.Textarea(attrs={'rows': 5}),
            'grant_paid_at': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'placement_notes': forms.Textarea(attrs={'rows': 5}),
            'checkout_verified_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'benefit_notes': forms.Textarea(attrs={'rows': 4}),
            'understanding_letter_physical_notes': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, can_manage=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_manage:
            allowed = {
                'checkin_arrived_at', 'checkin_art_at', 'checkin_placed',
                'checkin_placement_changed', 'checkin_placement_change_notes',
                'checkout_verified_at', 'understanding_letter',
                'understanding_letter_physical_received', 'understanding_letter_physical_custodian',
                'understanding_letter_physical_notes', 'understanding_letter_physical_waiver',
                'understanding_letter_physical_waiver_reason',
            }
            for name in tuple(self.fields):
                if name not in allowed and name != 'expected_updated_at':
                    self.fields.pop(name)
        self.fields['expected_updated_at'].initial = self.instance.updated_at.isoformat() if self.instance.pk else ''
        if self.instance.pk:
            if 'checkout_team_responsible' in self.fields:
                self.fields['checkout_team_responsible'].queryset = self.instance.logistics_people.all()
            if 'checkout_art_responsible' in self.fields:
                self.fields['checkout_art_responsible'].queryset = _art_responsibles(self.instance)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and self.is_bound:
            expected = cleaned.get('expected_updated_at')
            if not expected or expected != self.instance.updated_at.isoformat():
                self.add_error(None, 'Otra coordinación modificó esta obra. Recargá la página antes de guardar.')
        if cleaned.get('grant_status') in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID) and not cleaned.get('grant_approved_amount_ars'):
            self.add_error('grant_approved_amount_ars', 'Indicá el monto aprobado.')
        if cleaned.get('grant_approved_amount_ars') and cleaned['grant_approved_amount_ars'] >= Decimal('1000000') and not cleaned.get('confirm_large_grant_amount'):
            self.add_error('confirm_large_grant_amount', 'Confirmá el monto aprobado antes de guardar.')
        if cleaned.get('grant_status') == Artwork.GrantStatus.PAID and not cleaned.get('grant_paid_at'):
            self.add_error('grant_paid_at', 'Indicá cuándo se pagó la beca.')
        if cleaned.get('checkout_verified_at') and not self.instance.checkout_completed:
            self.add_error('checkout_verified_at', 'Esperá la solicitud de checkout del equipo de la obra.')
        if cleaned.get('understanding_letter_physical_received') and not (
            cleaned.get('understanding_letter_physical_custodian')
            or cleaned.get('understanding_letter_physical_notes')
        ):
            self.add_error('understanding_letter_physical_notes', 'Indicá quién tiene la carta física o dónde está guardada.')
        if cleaned.get('understanding_letter_physical_waiver') and not cleaned.get('understanding_letter_physical_waiver_reason'):
            self.add_error('understanding_letter_physical_waiver_reason', 'Indicá por qué corresponde la excepción por distancia a CABA.')
        return cleaned
