from django import forms
from django.conf import settings

from twilio.rest import Client

from .models import Profile
from tickets.models import NewTicket


class ProfileStep1Form(forms.ModelForm):
    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=30, required=True)

    class Meta:
        model = Profile
        fields = ["document_type", "document_number"]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super(ProfileStep1Form, self).__init__(*args, **kwargs)
        if user:
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name

    def clean_document_number(self):
        document_number = self.cleaned_data.get("document_number", "")
        # Remove periods from the document_number
        cleaned_document_number = document_number.replace(".", "")
        return cleaned_document_number

    def clean(self):
        cleaned_data = super(ProfileStep1Form, self).clean()
        document_type = cleaned_data.get("document_type")
        document_number = self.clean_document_number()

        # Check for duplicate document number and type
        if (
            Profile.objects.filter(
                document_type=document_type, document_number=document_number
            )
            .exclude(user=self.instance.user)
            .exists()
        ):
            raise forms.ValidationError(
                "Otro usuario ya tiene este tipo de documento y número."
            )

        return cleaned_data

    def save(self, commit=True):
        # Create a profile instance, but don't save it yet
        profile = super(ProfileStep1Form, self).save(commit=False)

        # Clean document_number before saving
        profile.document_number = self.clean_document_number()

        # Update the user's first and last name
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]

        # If we are saving, also update and save the profile instance
        if commit:
            user.save()
            profile.save()

        return profile


class ProfileStep2Form(forms.ModelForm):
    code = forms.CharField(max_length=6, required=False, label="Código de verificación")
    full_phone_number = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Profile
        fields = ["phone"]

    def __init__(self, *args, **kwargs):
        code_sent = kwargs.pop("code_sent", False)
        super(ProfileStep2Form, self).__init__(*args, **kwargs)

        if code_sent:
            self.fields["phone"].required = (
                False  # Make phone non-required if code is sent
            )
            self.fields["phone"].disabled = (
                True  # Disable the phone field if code is sent
            )

    def clean_phone(self):
        # Use the full_phone_number if provided
        full_phone_number = self.cleaned_data.get("full_phone_number")
        if full_phone_number:
            return full_phone_number
        return self.cleaned_data["phone"]

    def clean(self):
        cleaned_data = super(ProfileStep2Form, self).clean()
        phone = cleaned_data.get("phone")

        # Check for duplicate phone number
        if (
            Profile.objects.filter(phone=phone)
            .exclude(user=self.instance.user)
            .exists()
        ):
            raise forms.ValidationError(
                "Otro usuario ya tiene este número de teléfono."
            )

        return cleaned_data

    def send_verification_code(self):
        phone = self.cleaned_data["phone"]

        if settings.ENV == "local" or settings.MOCK_PHONE_VERIFICATION:
            return "123456"

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verifications.create(to=phone, channel="sms")
        return verification.sid

    def verify_code(self):
        phone = self.cleaned_data["phone"]
        code = self.cleaned_data.get("code")

        if settings.ENV == "local" or settings.MOCK_PHONE_VERIFICATION:
            return True

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification_check = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verification_checks.create(to=phone, code=code)
        return verification_check.status == "approved"


class VolunteeringForm(forms.ModelForm):

    volunteer_ranger = forms.BooleanField(label='Ranger', required=False)
    volunteer_transmutator = forms.BooleanField(label='Transmutadores', required=False)
    volunteer_umpalumpa = forms.BooleanField(label='CAOS (Desarme de la Ciudad)', required=False)

    class Meta:
        model = NewTicket
        fields = ["volunteer_ranger", "volunteer_transmutator", "volunteer_umpalumpa"]
