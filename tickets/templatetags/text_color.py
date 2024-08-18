from django import template

register = template.Library()

def luminance(color):
    r, g, b = (int(color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

@register.filter(is_safe=True)
def text_color(color):
    if not color:
        return '#000'

    return '#fff' if luminance(color) < 0.5 else '#000'
