from django import template

from art.estafa import can_access_estafa as _can_access_estafa

register = template.Library()


@register.filter
def can_access_estafa(user):
    return _can_access_estafa(user)
