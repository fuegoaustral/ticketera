from django import template

from art.estafa import art_team_name as _art_team_name, can_access_estafa as _can_access_estafa

register = template.Library()


@register.filter
def can_access_estafa(user):
    return _can_access_estafa(user)


@register.filter
def art_team_name(user):
    return _art_team_name(user)
