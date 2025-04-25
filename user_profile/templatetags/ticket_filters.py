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