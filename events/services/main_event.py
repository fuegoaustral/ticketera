import logging

from django.db import transaction
from django.utils import timezone

from events.models import Event

logger = logging.getLogger(__name__)


def _best_active_event(*, exclude_pk=None):
    """Evento activo vigente más apto para ser principal."""
    now = timezone.now()
    qs = Event.objects.filter(active=True, end__gte=now)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    running = qs.filter(start__lte=now).order_by('-start', '-id').first()
    if running:
        return running
    return qs.filter(start__gt=now).order_by('start', '-id').first()


def _valid_main(event):
    if not event:
        return False
    now = timezone.now()
    return event.active and event.end >= now


def _transfer_main(*, new_main, previous_main=None):
    with transaction.atomic():
        Event.objects.filter(is_main=True).exclude(pk=new_main.pk).update(is_main=False)
        if not new_main.is_main:
            new_main.is_main = True
            new_main.save(update_fields=['is_main', 'updated_at'])
    if previous_main and previous_main.pk != new_main.pk:
        logger.info(
            'Evento principal: #%s (%s) → #%s (%s)',
            previous_main.pk,
            previous_main.slug,
            new_main.pk,
            new_main.slug,
        )
    else:
        logger.info('Evento principal asignado: #%s (%s)', new_main.pk, new_main.slug)


def reconcile_main_event():
    """
    Si el main actual ya finalizó y hay otro evento activo vigente, pasa el main a ese.
    Si no hay reemplazo, el main actual queda aunque haya terminado.
    """
    now = timezone.now()
    current_main = Event.objects.filter(is_main=True).first()

    if _valid_main(current_main):
        return current_main

    replacement = _best_active_event(
        exclude_pk=current_main.pk if current_main else None,
    )
    if current_main and current_main.end < now and replacement:
        _transfer_main(new_main=replacement, previous_main=current_main)
        return replacement

    if not current_main and replacement:
        _transfer_main(new_main=replacement)
        return replacement

    return current_main


def promote_if_no_valid_main(event):
    """
    Al crear un evento: si no hay main vigente, el nuevo pasa a ser main.
    """
    if not event.pk or not event.active or event.end < timezone.now():
        return

    current_main = Event.objects.filter(is_main=True).exclude(pk=event.pk).first()
    if _valid_main(current_main):
        return

    _transfer_main(new_main=event, previous_main=current_main)
