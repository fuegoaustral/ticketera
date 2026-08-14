from django import forms
from django.contrib.auth.models import User

from .models import Artwork


ARTWORK_BLOCK_FIELDS = {
    'proposal': ('kind', 'title', 'proposal', 'dimensions', 'materials', 'technical_needs', 'safety_plan'),
    'grant': ('grant_requested', 'grant_amount', 'grant_budget', 'grant_justification'),
    'guide': ('public_title', 'public_description', 'preferred_location'),
    'logistics': ('arrival_date', 'departure_date', 'crew', 'providers'),
    'checkout': ('checkout_completed', 'checkout_notes'),
    'grant_report': ('grant_report', 'grant_photo'),
}


class ArtworkForm(forms.ModelForm):
    BLOCK_FIELDS = ARTWORK_BLOCK_FIELDS

    collaborator_emails = forms.CharField(
        required=False,
        label='Colaboradores',
        help_text='Emails de usuarios de Mi Fuego, separados por coma.',
    )
    grant_photo = forms.ImageField(required=False, label='Agregar foto de la obra terminada')

    class Meta:
        model = Artwork
        fields = [field for fields in ARTWORK_BLOCK_FIELDS.values() for field in fields if field != 'grant_photo']
        widgets = {
            'proposal': forms.Textarea(attrs={'rows': 5}),
            'materials': forms.Textarea(attrs={'rows': 3}),
            'technical_needs': forms.Textarea(attrs={'rows': 3}),
            'safety_plan': forms.Textarea(attrs={'rows': 3}),
            'grant_budget': forms.Textarea(attrs={'rows': 3}),
            'grant_justification': forms.Textarea(attrs={'rows': 3}),
            'public_description': forms.Textarea(attrs={'rows': 4, 'maxlength': 500}),
            'arrival_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'departure_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'crew': forms.Textarea(attrs={'rows': 4}),
            'providers': forms.Textarea(attrs={'rows': 4}),
            'checkout_notes': forms.Textarea(attrs={'rows': 3}),
            'grant_report': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, program, owner, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        self.owner = owner
        self.actor = actor or owner

        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control')
        self.fields['kind'].widget.attrs['class'] = 'form-select'
        self.fields['collaborator_emails'].widget.attrs.update({'class': 'form-control', 'placeholder': 'persona@ejemplo.com, otra@ejemplo.com'})

        if not self.instance.pk and not program.registration_is_open():
            self.fields['kind'].choices = [(Artwork.Kind.POPUP, Artwork.Kind.POPUP.label)]
            self.fields['kind'].initial = Artwork.Kind.POPUP

        if not program.grants_enabled:
            for name in (*self.BLOCK_FIELDS['grant'], *self.BLOCK_FIELDS['grant_report']):
                self.fields.pop(name)
        elif self.instance.grant_status != Artwork.GrantStatus.APPROVED:
            for name in self.BLOCK_FIELDS['grant_report']:
                self.fields[name].disabled = True

        if self.instance.grant_status in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.REJECTED):
            for name in self.BLOCK_FIELDS['grant']:
                if name in self.fields:
                    self.fields[name].disabled = True

        if self.instance.pk:
            self.fields['collaborator_emails'].initial = ', '.join(
                self.instance.collaborators.order_by('email').values_list('email', flat=True)
            )
        if self.actor != owner:
            self.fields['collaborator_emails'].disabled = True

        for block, fields in self.BLOCK_FIELDS.items():
            if block == 'proposal' and not self.instance.pk:
                continue
            if program.checkpoint_state(block) != 'open':
                for name in fields:
                    if name in self.fields:
                        self.fields[name].disabled = True

    def clean_collaborator_emails(self):
        raw = self.cleaned_data['collaborator_emails'].replace('\n', ',').replace(';', ',')
        emails = list(dict.fromkeys(email.strip().lower() for email in raw.split(',') if email.strip()))
        users = []
        missing = []
        for email in emails:
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                missing.append(email)
            elif user != self.owner:
                users.append(user)
        if missing:
            raise forms.ValidationError(f"No hay usuarios de Mi Fuego con estos emails: {', '.join(missing)}")
        return users

    def clean_grant_photo(self):
        photo = self.cleaned_data.get('grant_photo')
        if photo and photo.size > 10 * 1024 * 1024:
            raise forms.ValidationError('La foto no puede superar 10 MB.')
        return photo

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and cleaned.get('kind') == Artwork.Kind.PLANNED and not self.program.registration_is_open():
            self.add_error('kind', 'La inscripción cerró. Podés registrar esta obra como espontánea (popup).')
        if cleaned.get('arrival_date') and cleaned.get('departure_date') and cleaned['departure_date'] < cleaned['arrival_date']:
            self.add_error('departure_date', 'La salida no puede ser anterior al ingreso.')
        if cleaned.get('grant_requested') and not self.fields['grant_requested'].disabled:
            for field in ('grant_amount', 'grant_budget', 'grant_justification'):
                if not cleaned.get(field):
                    self.add_error(field, 'Completá este campo para solicitar la beca.')
        return cleaned

    def save(self, commit=True):
        artwork = super().save(commit)
        if commit:
            artwork.collaborators.set(self.cleaned_data['collaborator_emails'])
            if self.program.grants_enabled:
                if not artwork.grant_requested:
                    artwork.grant_status = Artwork.GrantStatus.NOT_REQUESTED
                elif artwork.grant_status == Artwork.GrantStatus.NOT_REQUESTED:
                    artwork.grant_status = Artwork.GrantStatus.PENDING
                artwork.save(update_fields=['grant_status'])
        return artwork
