from django.conf import settings
from django.contrib.auth.models import User

from templated_email import send_templated_mail


def send_mail(*args, **kwargs):
    """
    Wrapper around send_templated_mail cause context_processors don't work
    """
    if 'context' not in kwargs:
        kwargs['context'] = {}

    kwargs['context'].update({
        'base_url': settings.APP_URL,
    })

    if 'from_email' not in kwargs:
        kwargs['from_email'] = settings.DEFAULT_FROM_EMAIL

    send_templated_mail(*args, **kwargs)


def send_staff_mail(*args, **kwargs):
    """
    Send email to is_staff=True users
    """
    kwargs['recipient_list'] = User.objects.filter(is_staff=True).values_list('email', flat=True)
    send_mail(*args, **kwargs)