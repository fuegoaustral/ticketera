from django.conf import settings
from django.contrib.auth.models import User

from templated_email import send_templated_mail
import logging


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

    logging.info('Sending email to %s', kwargs['recipient_list'])
    send_templated_mail(*args, **kwargs)
    logging.info('Email sent')


def send_staff_mail(*args, **kwargs):
    """
    Send email to is_staff=True users.
    Returns True if there was at least one recipient.
    """
    emails = list(
        User.objects.filter(is_staff=True, is_active=True)
        .exclude(email='')
        .exclude(email__isnull=True)
        .values_list('email', flat=True)
    )
    if not emails:
        logging.warning('send_staff_mail: no hay usuarios staff con email')
        return False
    kwargs['recipient_list'] = emails
    send_mail(*args, **kwargs)
    return True