from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from .estafa import estafa_members
from .models import (
    Artwork, ArtworkFile, ArtworkGrantItem,
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
        'uses_sound', 'uses_fire', 'fire_details', 'extinguishing_plan', 'power_watts',
        'safety_plan', 'safety_responsible_email', 'burns', 'burn_preferred_time', 'burn_company', 'files_url',
    ),
    'guide': ('public_title', 'public_description', 'preferred_location'),
    'logistics': ('arrival_date', 'departure_date'),
    'checkout': ('checkout_team_responsible', 'checkout_notes'),
    'understanding_letter_digital': ('understanding_letter',),
}

# La beca se pide en su propia pantalla, con su propio formulario.
GRANT_BLOCK_FIELDS = {
    'grant': ('grant_requested', 'grant_justification'),
    'grant_report': ('grant_report',),
}


def _block_locked(program, block, is_manager):
    """Nadie edita un bloque antes de que se habilite; después del cierre, sólo ESTAFA."""
    state = program.checkpoint_state(block)
    return state == 'upcoming' or (state == 'closed' and not is_manager)


def _estafa_contacts(artwork):
    return User.objects.filter(
        Q(pk__in=estafa_members().values('pk')) | Q(pk=artwork.estafa_contact_id),
    ).order_by('first_name', 'last_name', 'email')


def _limit_to_team(field, artwork):
    """El responsable del checkout se elige entre las personas del equipo de la instalación."""
    field.queryset = User.objects.filter(
        pk__in=artwork.team_members().values('user'),
    ).order_by('first_name', 'last_name', 'email')
    field.label_from_instance = lambda user: user.get_full_name() or user.email
    field.empty_label = 'Elegí una persona del equipo'


class EstafaContactChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return user.get_full_name() or user.email


class InvalidFieldsMixin:
    """Marca en rojo cada campo con error y lo asocia a su mensaje para lectores de pantalla."""

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name not in self.fields:
                continue
            attrs = self.fields[name].widget.attrs
            attrs['aria-invalid'] = 'true'
            attrs['aria-describedby'] = f'{self[name].id_for_label}-error'
            if 'is-invalid' not in attrs.get('class', ''):
                attrs['class'] = f"{attrs.get('class', '')} is-invalid".strip()


class ArtworkContactForm(forms.ModelForm):
    estafa_contact = EstafaContactChoiceField(
        queryset=User.objects.none(), required=False, empty_label='Sin contacto',
        label='Contacto de ESTAFA',
        widget=forms.Select(attrs={'class': 'form-select', 'aria-describedby': 'contact-help'}),
    )

    class Meta:
        model = Artwork
        fields = ('estafa_contact',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['estafa_contact'].queryset = _estafa_contacts(self.instance)


class ArtworkForm(InvalidFieldsMixin, forms.ModelForm):
    BLOCK_FIELDS = ARTWORK_BLOCK_FIELDS

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
            'files_url': forms.URLInput(attrs={'placeholder': 'https://…'}),
            'public_description': forms.Textarea(attrs={'rows': 6}),
            'arrival_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'departure_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'checkout_notes': forms.Textarea(attrs={'rows': 5}),
        }

    def __init__(self, *args, program, owner, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        self.owner = owner
        self.actor = actor or owner
        self.is_manager = self.instance.pk and self.instance.can_manage(self.actor)
        self.is_contributor = self.instance.pk and self.instance.can_edit(self.actor)

        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control')
            field.widget.attrs['form'] = 'artwork-form'
        self.fields['checkout_team_responsible'].widget.attrs['class'] = 'form-select'
        self.fields['burn_company'].widget.attrs['class'] = 'form-select'
        self.fields['burn_company'].choices = [('', 'Elegí una opción'), *Artwork.BurnCompany.choices]
        self.fields['safety_responsible_email'].widget.attrs.update({'class': 'form-control', 'placeholder': 'persona@ejemplo.com'})
        description_limit = program.public_description_max_length
        self.fields['public_description'].max_length = description_limit
        self.fields['public_description'].widget.attrs.update({
            'maxlength': description_limit,
            'data-character-count': 'public-description-count',
        })
        self.fields['public_description'].help_text = f'Máximo {description_limit} caracteres.'
        if self.instance.safety_responsible_id:
            self.fields['safety_responsible_email'].initial = self.instance.safety_responsible.email
        self.fields['expected_version'].initial = self.instance.version if self.instance.pk else None
        _limit_to_team(self.fields['checkout_team_responsible'], self.instance)
        if self.instance.pk and not self.is_manager and not self.is_contributor:
            for name, field in self.fields.items():
                if name != 'expected_version':
                    field.disabled = True

        # El título identifica la instalación; la descripción se puede completar después.
        self.fields['title'].required = True
        self.fields['title'].help_text = 'Podés cambiar el nombre cuando quieras.'
        self.fields['proposal'].required = False
        self.fields['safety_plan'].label = 'Plan de seguridad'
        self.fields['proposal'].help_text = (
            'Esto lo lee ESTAFA para entender qué querés construir. Contalo con todo el detalle que puedas: '
            'qué es, cómo se ve, cómo se arma y qué puede hacer la gente ahí. No es el texto para el público: '
            'ese va en Desplegable.'
        )
        self.fields['proposal'].widget.attrs['placeholder'] = (
            'Por ejemplo: una pata de conejo de madera de 3 m de alto, forrada en tela. De noche se ilumina desde adentro '
            'con tiras LED. La gente puede tocarla y pedir un deseo. La armamos en dos días con cuatro personas.'
        )

        checkout_locked = self.instance.status not in (Artwork.Status.ACTIVE, Artwork.Status.CHECKOUT_SUBMITTED)
        if self.instance.checkout_verified_at or (checkout_locked and not self.is_manager):
            for name in self.BLOCK_FIELDS['checkout']:
                self.fields[name].disabled = True

        if not self.is_manager and (not program.is_current or self.instance.status == Artwork.Status.REJECTED):
            for name, field in self.fields.items():
                if name != 'expected_version':
                    field.disabled = True
        for block, fields in self.BLOCK_FIELDS.items():
            if block == 'proposal' and not self.instance.pk:
                continue
            if _block_locked(program, block, self.is_manager):
                for name in fields:
                    if name in self.fields:
                        self.fields[name].disabled = True

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
            self.add_error(None, 'La inscripción de instalaciones está cerrada.')

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
            for block, fields in self.BLOCK_FIELDS.items():
                if _block_locked(self.program, block, self.is_manager) and any(name in self.data for name in fields):
                    closed.append(block)
            if closed:
                self.add_error(None, 'Una fecha límite venció mientras editabas. Recargá la página: no se guardó ningún cambio.')

        if cleaned.get('uses_fire') and not self.fields['uses_fire'].disabled:
            for field in ('fire_details', 'extinguishing_plan', 'safety_responsible_email'):
                if not cleaned.get(field) and not self.fields[field].disabled:
                    self.add_error(field, 'Completá este campo para una instalación que utiliza fuego.')
        return cleaned

    def save(self, commit=True):
        artwork = super().save(commit=False)
        artwork.safety_responsible = self.cleaned_data.get('safety_responsible_email')
        if artwork.pk:
            artwork.version += 1
        if commit:
            artwork.save()
        return artwork


class ArtworkGrantForm(InvalidFieldsMixin, forms.ModelForm):
    """Solicitud y rendición de la beca, en la pantalla de la beca."""

    BLOCK_FIELDS = GRANT_BLOCK_FIELDS

    expected_version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Artwork
        fields = [field for fields in GRANT_BLOCK_FIELDS.values() for field in fields]
        widgets = {
            'grant_justification': forms.Textarea(attrs={'rows': 6}),
            'grant_report': forms.Textarea(attrs={'rows': 8}),
        }

    def __init__(self, *args, program, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        self.actor = actor
        self.is_manager = self.instance.can_manage(actor)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control')
            field.widget.attrs['form'] = 'grant-form'
        self.fields['expected_version'].initial = self.instance.version

        locked = set()
        if self.instance.grant_status not in (Artwork.GrantStatus.NOT_REQUESTED, Artwork.GrantStatus.INFO_REQUIRED):
            locked.update(self.BLOCK_FIELDS['grant'])
        if self.instance.grant_status == Artwork.GrantStatus.CLOSED or (
            not self.is_manager
            and self.instance.grant_status not in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID)
        ):
            locked.add('grant_report')
        if not self.is_manager and (
            not self.instance.can_edit(actor) or not program.is_current
            or self.instance.status == Artwork.Status.REJECTED
        ):
            locked.update(self.fields)
        for block, fields in self.BLOCK_FIELDS.items():
            if _block_locked(program, block, self.is_manager):
                locked.update(fields)
        for name in locked:
            if name in self.fields and name != 'expected_version':
                self.fields[name].disabled = True

    @property
    def is_read_only(self):
        return all(field.disabled for name, field in self.fields.items() if name != 'expected_version')

    def clean(self):
        cleaned = super().clean()
        if self.is_bound:
            current = Artwork.objects.filter(pk=self.instance.pk).values_list('version', flat=True).first()
            if cleaned.get('expected_version') != current:
                self.add_error(None, 'Otra persona guardó cambios mientras editabas. Recargá la página antes de volver a guardar.')
            if any(
                _block_locked(self.program, block, self.is_manager) and any(name in self.data for name in fields)
                for block, fields in self.BLOCK_FIELDS.items()
            ):
                self.add_error(None, 'Una fecha límite venció mientras editabas. Recargá la página: no se guardó ningún cambio.')
        return cleaned

    def save(self, commit=True):
        artwork = super().save(commit=False)
        artwork.version += 1
        if commit:
            artwork.save()
        return artwork


class ArtworkGrantItemForm(InvalidFieldsMixin, forms.ModelForm):
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


class MultipleFileField(forms.FileField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        if not any(files):
            if self.required:
                raise forms.ValidationError('Seleccioná al menos un archivo.')
            return []
        clean_one = super().clean
        return [clean_one(file, initial) for file in files if file]


class ArtworkFileUploadForm(InvalidFieldsMixin, forms.Form):
    """Archivos de la propuesta: imágenes o PDF."""

    ALLOWED_EXTENSIONS = (*ArtworkFile.IMAGE_EXTENSIONS, '.pdf')

    files = MultipleFileField(label='Archivos')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['files'].widget.attrs.update({
            'class': 'form-control', 'accept': 'image/*,application/pdf',
        })

    def clean_files(self):
        files = self.cleaned_data['files']
        if len(files) > 10:
            raise forms.ValidationError('Podés subir hasta 10 archivos por vez.')
        for upload in files:
            if not upload.name.lower().endswith(self.ALLOWED_EXTENSIONS):
                raise forms.ValidationError(f'“{upload.name}” no es una imagen ni un PDF.')
            if upload.size > 20 * 1024 * 1024:
                raise forms.ValidationError(f'“{upload.name}” pesa más de 20 MB.')
        return files


# La galería junta el proceso y la instalación terminada; la propuesta va en Detalles
# y las fotos de rendición, en la pantalla de la beca.
GALLERY_STAGES = (ArtworkPhoto.Stage.PROCESS, ArtworkPhoto.Stage.FINAL)


class ArtworkPhotoUploadForm(InvalidFieldsMixin, forms.Form):
    images = MultipleImageField(label='Fotos')
    stage = forms.ChoiceField(
        choices=[(stage.value, stage.label) for stage in GALLERY_STAGES], label='Etapa',
    )
    caption = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4}), label='Descripción o crédito')
    publication_authorized = forms.BooleanField(
        required=False,
        label='Autorizo a Fuego Austral a publicar estas fotos en el archivo de la instalación',
    )

    def __init__(self, *args, stages=GALLERY_STAGES, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['stage'].choices = [(stage.value, stage.label) for stage in stages]
        if len(stages) == 1:
            # Una sola etapa posible (por ejemplo, las fotos de rendición): no hay nada para elegir.
            self.fields['stage'].initial = stages[0].value
            self.fields['stage'].widget = forms.HiddenInput()
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


class ArtworkCheckoutPhotoUploadForm(InvalidFieldsMixin, forms.Form):
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


class ArtworkTeamBenefitsForm(InvalidFieldsMixin, forms.Form):
    """Ingreso anticipado y late checkout de cada persona del equipo (GrupoMiembro), con las reglas de Mis grupos."""

    def __init__(self, *args, artwork, members, ticket_holders, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = artwork.operations_group
        self.event = artwork.event
        self.members = members
        limit = self.event.ingreso_anticipado_limite_carga
        self.early_closed = bool(limit and timezone.now() > limit)
        date_attrs = {'type': 'date', 'class': 'form-control'}
        if self.group.ingreso_anticipado_desde:
            date_attrs['min'] = timezone.localtime(self.group.ingreso_anticipado_desde).date().isoformat()
        date_attrs['max'] = timezone.localtime(self.event.start).date().isoformat()
        for member in members:
            name = member.user.get_full_name() or member.user.email
            member.has_ticket = member.user_id in ticket_holders
            self.fields[f'early_{member.pk}'] = forms.DateField(
                required=False, label=f'Ingreso anticipado de {name}', initial=member.ingreso_anticipado_fecha,
                widget=forms.DateInput(attrs=dict(date_attrs), format='%Y-%m-%d'),
                disabled=not member.has_ticket or self.early_closed,
            )
            self.fields[f'late_{member.pk}'] = forms.BooleanField(
                required=False, label=f'Late checkout de {name}', initial=member.late_checkout,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
                disabled=not member.has_ticket,
            )
            member.early_field = self[f'early_{member.pk}']
            member.late_field = self[f'late_{member.pk}']

    def clean(self):
        cleaned = super().clean()
        start = timezone.localtime(self.event.start).date()
        desde = self.group.ingreso_anticipado_desde and timezone.localtime(self.group.ingreso_anticipado_desde).date()
        early_count = late_count = 0
        for member in self.members:
            early = cleaned.get(f'early_{member.pk}')
            if early and desde and early < desde:
                self.add_error(f'early_{member.pk}', f'Elegí una fecha desde el {desde:%d/%m}.')
            if early and early > start:
                self.add_error(f'early_{member.pk}', f'Elegí una fecha hasta el {start:%d/%m}, cuando empieza el evento.')
            early_count += bool(early)
            late_count += bool(cleaned.get(f'late_{member.pk}'))
        if early_count > self.group.ingreso_anticipado_amount:
            self.add_error(None, f'Esta instalación tiene {self.group.ingreso_anticipado_amount} cupos de ingreso anticipado.')
        if late_count > self.group.late_checkout_amount:
            self.add_error(None, f'Esta instalación tiene {self.group.late_checkout_amount} cupos de late checkout.')
        return cleaned

    def save(self):
        for member in self.members:
            early_field, late_field = self.fields[f'early_{member.pk}'], self.fields[f'late_{member.pk}']
            if not early_field.disabled:
                member.ingreso_anticipado_fecha = self.cleaned_data[f'early_{member.pk}']
                member.ingreso_anticipado = bool(member.ingreso_anticipado_fecha)
            if not late_field.disabled:
                member.late_checkout = self.cleaned_data[f'late_{member.pk}']
            member.save()


class ArtworkTeamAddForm(forms.Form):
    identifier = forms.CharField(
        max_length=254, label='Email o DNI',
        help_text='La persona necesita una cuenta en Fuego Austral.',
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'autocomplete': 'off', 'autocapitalize': 'none', 'spellcheck': 'false',
            'placeholder': 'persona@ejemplo.com',
        }),
    )

    def __init__(self, *args, artwork, **kwargs):
        super().__init__(*args, **kwargs)
        self.artwork = artwork
        self.user = None

    def clean_identifier(self):
        from allauth.account.models import EmailAddress

        identifier = self.cleaned_data['identifier'].strip()
        user = User.objects.filter(email__iexact=identifier).first()
        if not user:
            address = EmailAddress.objects.select_related('user').filter(email__iexact=identifier).first()
            user = address.user if address else None
        document = identifier.replace('.', '').replace(' ', '')
        if not user and document.isdigit():
            user = User.objects.filter(profile__document_number__in=(identifier, document)).first()
        if not user:
            raise forms.ValidationError(
                'No encontramos una cuenta con ese email o DNI. Pedile que se registre en Fuego Austral y volvé a sumarla.'
            )
        if self.artwork.is_team_member(user):
            raise forms.ValidationError('Esa persona ya es parte del equipo de la instalación.')
        self.user = user
        return identifier


class ArtworkProviderForm(InvalidFieldsMixin, forms.ModelForm):
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


class ArtworkProviderVehicleForm(InvalidFieldsMixin, forms.ModelForm):
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


class ArtworkReviewForm(InvalidFieldsMixin, forms.ModelForm):
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
            'understanding_letter_physical_received_at',
            'understanding_letter_physical_custodian', 'understanding_letter_physical_notes',
            'understanding_letter_physical_waiver', 'understanding_letter_physical_waiver_reason',
            'grant_status', 'grant_approved_amount_ars',
            'grant_decision_notes', 'grant_paid_at', 'grant_payment_reference',
            'assigned_location', 'placement_notes', 'checkout_team_responsible',
            'checkout_verified_at',
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
            'understanding_letter_physical_received_at': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, can_manage=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_manage:
            allowed = {
                'checkin_arrived_at', 'checkin_art_at', 'checkin_placed',
                'checkin_placement_changed', 'checkin_placement_change_notes',
                'checkout_verified_at', 'understanding_letter',
                'understanding_letter_physical_received', 'understanding_letter_physical_received_at',
                'understanding_letter_physical_custodian',
                'understanding_letter_physical_notes', 'understanding_letter_physical_waiver',
                'understanding_letter_physical_waiver_reason',
            }
            for name in tuple(self.fields):
                if name not in allowed and name != 'expected_updated_at':
                    self.fields.pop(name)
        self.fields['expected_updated_at'].initial = self.instance.updated_at.isoformat() if self.instance.pk else ''
        if self.instance.pk:
            if 'checkout_team_responsible' in self.fields:
                _limit_to_team(self.fields['checkout_team_responsible'], self.instance)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and self.is_bound:
            expected = cleaned.get('expected_updated_at')
            if not expected or expected != self.instance.updated_at.isoformat():
                self.add_error(None, 'Otra coordinación modificó esta instalación. Recargá la página antes de guardar.')
        if cleaned.get('grant_status') in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID) and not cleaned.get('grant_approved_amount_ars'):
            self.add_error('grant_approved_amount_ars', 'Indicá el monto aprobado.')
        if cleaned.get('grant_approved_amount_ars') and cleaned['grant_approved_amount_ars'] >= Decimal('1000000') and not cleaned.get('confirm_large_grant_amount'):
            self.add_error('confirm_large_grant_amount', 'Confirmá el monto aprobado antes de guardar.')
        if cleaned.get('grant_status') == Artwork.GrantStatus.PAID and not cleaned.get('grant_paid_at'):
            self.add_error('grant_paid_at', 'Indicá cuándo se pagó la beca.')
        if cleaned.get('checkout_verified_at') and not self.instance.checkout_completed:
            self.add_error('checkout_verified_at', 'Esperá la solicitud de checkout del equipo de la instalación.')
        if cleaned.get('understanding_letter_physical_received') and not (
            cleaned.get('understanding_letter_physical_custodian')
            or cleaned.get('understanding_letter_physical_notes')
        ):
            self.add_error('understanding_letter_physical_notes', 'Indicá quién tiene la copia física o dónde está guardada.')
        if 'understanding_letter_physical_received_at' in self.fields:
            # Al marcarla como recibida, la fecha es hoy salvo que ESTAFA indique otra.
            if not cleaned.get('understanding_letter_physical_received'):
                cleaned['understanding_letter_physical_received_at'] = None
            elif not cleaned.get('understanding_letter_physical_received_at'):
                cleaned['understanding_letter_physical_received_at'] = timezone.localdate()
        if cleaned.get('understanding_letter_physical_waiver') and not cleaned.get('understanding_letter_physical_waiver_reason'):
            self.add_error('understanding_letter_physical_waiver_reason', 'Indicá por qué corresponde la excepción por distancia a CABA.')
        return cleaned
