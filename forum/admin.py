from django.contrib import admin
from .models import ForumSection, ForumThread, ForumMessage


@admin.register(ForumSection)
class ForumSectionAdmin(admin.ModelAdmin):
    list_display = ['name', 'order', 'is_active', 'get_thread_count', 'get_last_activity']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'description']
    ordering = ['order', 'name']
    
    def get_thread_count(self, obj):
        return obj.get_thread_count()
    get_thread_count.short_description = 'Hilos'
    
    def get_last_activity(self, obj):
        return obj.get_last_activity()
    get_last_activity.short_description = 'Última Actividad'


@admin.register(ForumThread)
class ForumThreadAdmin(admin.ModelAdmin):
    list_display = ['title', 'section', 'get_author_display_name', 'is_pinned', 'is_locked', 'is_active', 'get_message_count', 'created_at']
    list_filter = ['section', 'is_pinned', 'is_locked', 'is_active', 'created_at']
    search_fields = ['title', 'author__username', 'author__profile__forum_username']
    ordering = ['-is_pinned', '-created_at']
    
    def get_author_display_name(self, obj):
        return obj.get_author_display_name()
    get_author_display_name.short_description = 'Autor'
    
    def get_message_count(self, obj):
        return obj.get_message_count()
    get_message_count.short_description = 'Mensajes'


@admin.register(ForumMessage)
class ForumMessageAdmin(admin.ModelAdmin):
    list_display = ['thread', 'get_author_display_name', 'is_active', 'is_edited', 'created_at']
    list_filter = ['thread__section', 'is_active', 'is_edited', 'created_at']
    search_fields = ['content', 'author__username', 'author__profile__forum_username', 'thread__title']
    ordering = ['-created_at']
    
    def get_author_display_name(self, obj):
        return obj.get_author_display_name()
    get_author_display_name.short_description = 'Autor'