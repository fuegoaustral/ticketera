from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ArtworkForm
from .models import ArtProgram, Artwork, ArtworkGrantPhoto, Event


def _base_context(event=None):
    active_events = Event.get_active_events().order_by('-is_main', 'name')
    return {
        'event': event or Event.get_main_event(),
        'active_events': active_events,
        'nav_primary': 'art',
        'now': timezone.now(),
    }


def _checkpoints(program):
    checkpoints = [
        {'key': 'proposal', 'label': 'Propuesta', 'deadline': program.proposal_deadline, 'state': program.checkpoint_state('proposal')},
    ]
    if program.grants_enabled:
        checkpoints.append({'key': 'grant', 'label': 'Beca', 'deadline': program.grant_deadline, 'state': program.checkpoint_state('grant')})
    checkpoints += [
        {'key': 'guide', 'label': 'Desplegable', 'deadline': program.guide_deadline, 'state': program.checkpoint_state('guide')},
        {'key': 'logistics', 'label': 'Ingreso y salida', 'deadline': program.logistics_deadline, 'state': program.checkpoint_state('logistics')},
        {'key': 'checkout', 'label': 'Checkout', 'deadline': program.checkout_deadline, 'state': program.checkpoint_state('checkout')},
    ]
    if program.grants_enabled:
        checkpoints.append({'key': 'grant_report', 'label': 'Rendición', 'deadline': program.grant_report_deadline, 'state': program.checkpoint_state('grant_report')})
    return checkpoints


def _save_photo(form, artwork, user):
    photo = form.cleaned_data.get('grant_photo')
    if photo:
        ArtworkGrantPhoto.objects.create(artwork=artwork, image=photo, uploaded_by=user)


@login_required
def art_dashboard(request):
    programs = ArtProgram.objects.select_related('event').filter(event__active=True).order_by('event__start')
    artworks = (
        Artwork.objects.filter(Q(owner=request.user) | Q(collaborators=request.user))
        .select_related('event', 'owner')
        .prefetch_related('collaborators')
        .distinct()
    )
    context = _base_context()
    context.update({'programs': programs, 'artworks': artworks})
    return render(request, 'mi_fuego/art/dashboard.html', context)


@login_required
def artwork_create(request, event_slug):
    program = get_object_or_404(ArtProgram.objects.select_related('event'), event__slug=event_slug, event__active=True)
    artwork = Artwork(event=program.event, owner=request.user)
    form = ArtworkForm(request.POST or None, request.FILES or None, instance=artwork, program=program, owner=request.user, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        artwork = form.save()
        _save_photo(form, artwork, request.user)
        if request.POST.get('action') == 'submit' and not artwork.submitted_at:
            artwork.submitted_at = timezone.now()
            artwork.save(update_fields=['submitted_at', 'updated_at'])
        messages.success(request, 'La obra quedó guardada en Mi Fuego.')
        return redirect('artwork_edit', artwork_id=artwork.pk)

    context = _base_context(program.event)
    context.update({'form': form, 'program': program, 'checkpoints': _checkpoints(program), 'is_new': True})
    return render(request, 'mi_fuego/art/form.html', context)


@login_required
def artwork_edit(request, artwork_id):
    artwork = get_object_or_404(
        Artwork.objects.filter(Q(owner=request.user) | Q(collaborators=request.user)).distinct(),
        pk=artwork_id,
    )
    try:
        program = artwork.event.art_program
    except ArtProgram.DoesNotExist as exc:
        raise Http404('El programa de Arte ya no está disponible.') from exc

    form = ArtworkForm(request.POST or None, request.FILES or None, instance=artwork, program=program, owner=artwork.owner, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        artwork = form.save()
        _save_photo(form, artwork, request.user)
        if request.POST.get('action') == 'submit' and not artwork.submitted_at:
            artwork.submitted_at = timezone.now()
            artwork.save(update_fields=['submitted_at', 'updated_at'])
        messages.success(request, 'Los cambios quedaron guardados.')
        return redirect('artwork_edit', artwork_id=artwork.pk)

    context = _base_context(artwork.event)
    context.update({'form': form, 'program': program, 'artwork': artwork, 'checkpoints': _checkpoints(program)})
    return render(request, 'mi_fuego/art/form.html', context)
