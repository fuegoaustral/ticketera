from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def cajas_v2_for_event(context, event):
    by_event_id = context.get('cajas_v2_by_event_id', {})
    return by_event_id.get(event.id, [])
