from django import forms
from django.contrib.auth.models import User

from .models import ForumSection, ForumThread, ForumMessage


class ForumUsernameForm(forms.Form):
    """Formulario para establecer el nombre de usuario del foro"""
    forum_username = forms.CharField(
        max_length=50,
        label="Nombre de usuario para el foro",
        help_text="Este será tu nombre visible en el foro. Puedes cambiarlo más tarde.",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ingresa tu nombre de usuario'
        })
    )
    
    def clean_forum_username(self):
        username = self.cleaned_data.get('forum_username')
        if username:
            # Verificar que no esté en uso
            from user_profile.models import Profile
            if Profile.objects.filter(forum_username__iexact=username).exists():
                raise forms.ValidationError("Este nombre de usuario ya está en uso.")
            
            # Verificar que no sea un username existente
            if User.objects.filter(username__iexact=username).exists():
                raise forms.ValidationError("Este nombre de usuario ya está en uso.")
        
        return username


class NewThreadForm(forms.ModelForm):
    """Formulario para crear un nuevo hilo"""
    class Meta:
        model = ForumThread
        fields = ['title']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Título del hilo'
            })
        }


class NewMessageForm(forms.ModelForm):
    """Formulario para crear un nuevo mensaje"""
    class Meta:
        model = ForumMessage
        fields = ['content']
        widgets = {
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 6,
                'placeholder': 'Escribe tu mensaje aquí...'
            })
        }


class EditMessageForm(forms.ModelForm):
    """Formulario para editar un mensaje existente"""
    class Meta:
        model = ForumMessage
        fields = ['content']
        widgets = {
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 6
            })
        }
