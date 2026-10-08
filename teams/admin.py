from django.contrib import admin

from .models import Team, TeamMembership


class SuperuserOnlyAdmin:
    """Los equipos y sus membresías los administran sólo superusuarios."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class TeamMembershipInline(SuperuserOnlyAdmin, admin.TabularInline):
    model = TeamMembership
    fields = ('user', 'started_on', 'ended_on', 'notes')
    autocomplete_fields = ('user',)
    extra = 0


@admin.register(Team)
class TeamAdmin(SuperuserOnlyAdmin, admin.ModelAdmin):
    list_display = ('name', 'active_members')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [TeamMembershipInline]

    @admin.display(description='Miembros activos')
    def active_members(self, obj):
        return obj.memberships.active().count()


@admin.register(TeamMembership)
class TeamMembershipAdmin(SuperuserOnlyAdmin, admin.ModelAdmin):
    list_display = ('user', 'team', 'started_on', 'ended_on', 'active')
    list_filter = ('team',)
    search_fields = ('user__email', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('user',)
    date_hierarchy = 'started_on'

    @admin.display(description='Activa', boolean=True)
    def active(self, obj):
        return obj.is_active
