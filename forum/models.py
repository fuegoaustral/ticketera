from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from utils.models import BaseModel


class ForumSection(BaseModel):
    """Secciones del foro (ej: General, Eventos, etc.)"""
    name = models.CharField(max_length=100, help_text="Nombre de la sección")
    description = models.TextField(help_text="Descripción de la sección")
    order = models.PositiveIntegerField(default=0, help_text="Orden de visualización")
    is_active = models.BooleanField(default=True, help_text="Sección activa")
    
    class Meta:
        ordering = ['order', 'name']
        verbose_name = "Sección del Foro"
        verbose_name_plural = "Secciones del Foro"
    
    def __str__(self):
        return self.name
    
    def get_thread_count(self):
        return self.threads.filter(is_active=True).count()
    
    def get_message_count(self):
        """Obtiene el total de mensajes en todos los hilos de esta sección"""
        total_messages = 0
        for thread in self.threads.filter(is_active=True):
            total_messages += thread.get_message_count()
        return total_messages
    
    def get_last_activity(self):
        last_thread = self.threads.filter(is_active=True).order_by('-updated_at').first()
        if last_thread:
            return last_thread.updated_at
        return None


class ForumThread(BaseModel):
    """Hilos/temas dentro de las secciones"""
    section = models.ForeignKey(ForumSection, on_delete=models.CASCADE, related_name='threads')
    title = models.CharField(max_length=200, help_text="Título del hilo")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='forum_threads')
    is_active = models.BooleanField(default=True, help_text="Hilo activo")
    is_pinned = models.BooleanField(default=False, help_text="Hilo fijado")
    is_locked = models.BooleanField(default=False, help_text="Hilo bloqueado")
    
    class Meta:
        ordering = ['-is_pinned', '-updated_at']
        verbose_name = "Hilo del Foro"
        verbose_name_plural = "Hilos del Foro"
    
    def __str__(self):
        return f"{self.title} - {self.section.name}"
    
    def get_message_count(self):
        return self.messages.filter(is_active=True).count()
    
    def get_last_message(self):
        return self.messages.filter(is_active=True).order_by('-created_at').first()
    
    def get_author_display_name(self):
        """Obtiene el nombre a mostrar del autor (forum_username o username)"""
        if hasattr(self.author, 'profile') and self.author.profile.forum_username:
            return self.author.profile.forum_username
        return self.author.username


class ForumMessage(BaseModel):
    """Mensajes dentro de los hilos"""
    thread = models.ForeignKey(ForumThread, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='forum_messages')
    content = models.TextField(help_text="Contenido del mensaje")
    is_active = models.BooleanField(default=True, help_text="Mensaje activo")
    is_edited = models.BooleanField(default=False, help_text="Mensaje editado")
    edited_at = models.DateTimeField(null=True, blank=True, help_text="Fecha de edición")
    
    class Meta:
        ordering = ['created_at']
        verbose_name = "Mensaje del Foro"
        verbose_name_plural = "Mensajes del Foro"
    
    def __str__(self):
        return f"{self.thread.title} - {self.get_author_display_name()}"
    
    def get_author_display_name(self):
        """Obtiene el nombre a mostrar del autor (forum_username o username)"""
        if hasattr(self.author, 'profile') and self.author.profile.forum_username:
            return self.author.profile.forum_username
        return self.author.username
    
    def save(self, *args, **kwargs):
        if self.pk:  # Si es una edición
            self.is_edited = True
            self.edited_at = timezone.now()
        super().save(*args, **kwargs)