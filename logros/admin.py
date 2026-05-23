from django.contrib import admin

from logros.models import Achievement, UserAchievement


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'condition_type', 'is_active', 'sort_order')
    list_filter = ('condition_type', 'is_active')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(UserAchievement)
class UserAchievementAdmin(admin.ModelAdmin):
    list_display = ('user', 'achievement', 'unlocked_at', 'celebration_shown')
    list_filter = ('achievement', 'celebration_shown')
    search_fields = ('user__email', 'user__username', 'achievement__name')
    raw_id_fields = ('user',)
