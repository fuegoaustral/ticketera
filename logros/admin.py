from django.contrib import admin
from django.utils.html import format_html

from logros.models import Achievement, UserAchievement


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'condition_type', 'has_redeem_code', 'is_active', 'sort_order')
    list_filter = ('condition_type', 'is_active')
    search_fields = ('name', 'slug', 'redeem_code')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('image_preview',)
    fields = (
        'name',
        'slug',
        'image',
        'image_preview',
        'description',
        'condition_type',
        'condition_config',
        'redeem_code',
        'is_active',
        'sort_order',
    )

    @admin.display(boolean=True, description='Código de canje')
    def has_redeem_code(self, obj):
        return bool(obj.redeem_code)

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
    list_display = ('user', 'achievement', 'unlocked_at', 'celebration_shown', 'revoked', 'granted_manually')
    list_filter = ('achievement', 'celebration_shown', 'revoked', 'granted_manually')
    search_fields = ('user__email', 'user__username', 'achievement__name')
    raw_id_fields = ('user',)
