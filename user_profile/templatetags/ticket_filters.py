from django import template

register = template.Library()

@register.filter
def last_uuid_part(value):
    if not value:
        return ''
    # Convert UUID to string and split by hyphens
    parts = str(value).split('-')
    # Return the last part
    return parts[-1] if parts else ''

@register.filter
def map_field(queryset, field_name):
    """
    Custom filter to extract a specific field from a queryset.
    Usage: queryset|map_field:"field_name"
    """
    if not queryset:
        return []
    try:
        return [getattr(obj, field_name) for obj in queryset]
    except (AttributeError, TypeError):
        return [] 