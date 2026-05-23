from django.contrib import admin
from django.utils.html import format_html

from logros.models import Achievement, UserAchievement


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'condition_type', 'is_active', 'sort_order')
    list_filter = ('condition_type', 'is_active')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('image_preview',)

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 200px; border-radius: 8px;" />',
                obj.image.url,
            )
        return '—'

    image_preview.short_description = 'Vista previa'


@admin.register(UserAchievement)
class UserAchievementAdmin(admin.ModelAdmin):
    list_display = ('user', 'achievement', 'unlocked_at', 'celebration_shown')
    list_filter = ('achievement', 'celebration_shown')
    search_fields = ('user__email', 'user__username', 'achievement__name')
    raw_id_fields = ('user',)
