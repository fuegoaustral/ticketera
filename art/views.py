import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from events.models import Event, Grupo, GrupoMiembro, GrupoTipo
from tickets.models import NewTicket, TicketType
from utils.email import send_mail

from .estafa import can_access_estafa, can_coordinate, estafa_events, is_estafa_member
from .forms import (
    ArtworkForm, ArtworkGrantItemForm, ArtworkTeamAddForm,
    ArtworkCheckoutPhotoUploadForm, ArtworkPhotoUploadForm, ArtworkProviderForm, ArtworkProviderVehicleForm,
    ArtworkContactForm, ArtworkGrantItemReviewForm, ArtworkReviewForm,
)
from .models import (
    ArtProgram, Artwork, ArtworkGrantItem, ArtworkGrantItemPhoto,
    ArtworkPhoto, ArtworkCheckoutPhoto, ArtworkProvider,
    ArtworkProviderVehicle,
)


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
        {'key': 'proposal', 'anchor': 'propuesta', 'label': 'Propuesta', 'deadline': program.proposal_deadline, 'state': program.checkpoint_state('proposal')},
    ]
    if program.grants_enabled:
        checkpoints.append({'key': 'grant', 'anchor': 'beca', 'label': 'Beca', 'deadline': program.grant_deadline, 'state': program.checkpoint_state('grant')})
    checkpoints += [
        {'key': 'guide', 'anchor': 'placement', 'label': 'Desplegable', 'deadline': program.guide_deadline, 'state': program.checkpoint_state('guide')},
        {'key': 'logistics', 'anchor': 'logistica', 'label': 'Ingreso y desarme', 'deadline': program.logistics_deadline, 'state': program.checkpoint_state('logistics')},
        {'key': 'checkout', 'anchor': 'checkout', 'label': 'Checkout', 'deadline': program.checkout_deadline, 'state': program.checkpoint_state('checkout')},
        {'key': 'understanding_letter_digital', 'anchor': 'carta-entendimiento', 'label': 'Carta de entendimiento digital', 'deadline': program.understanding_letter_digital_deadline, 'state': program.checkpoint_state('understanding_letter_digital')},
        {'key': 'understanding_letter_physical', 'anchor': 'carta-entendimiento', 'label': 'Carta de entendimiento física', 'deadline': program.understanding_letter_physical_deadline, 'state': program.checkpoint_state('understanding_letter_physical')},
    ]
    if program.grants_enabled:
        checkpoints.append({'key': 'grant_report', 'anchor': 'rendicion', 'label': 'Rendición', 'deadline': program.grant_report_deadline, 'state': program.checkpoint_state('grant_report')})
    return checkpoints


def _accessible_artworks(user):
    if user.is_superuser:
        return Artwork.objects.all()
    team_ids = Artwork.objects.filter(
        Q(collaborators=user) | Q(operations_group__miembros__user=user),
    ).values('pk')
    access = Q(owner=user) | Q(pk__in=team_ids)
    if is_estafa_member(user):
        access |= Q(event__has_volunteers=True)
    return Artwork.objects.filter(access)


def _send_team_member_added_email(artwork, user, added_by):
    if not user.email:
        return
    try:
        send_mail(
            template_name='art_team_member_added',
            recipient_list=[user.email],
            context={
                'artwork': artwork,
                'added_by_name': added_by.get_full_name() or added_by.email,
                'can_edit': artwork.can_edit(user),
                'missing_ticket': _ticket_sales_started(artwork.event) and not _has_ticket(user, artwork.event),
                'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
                'tickets_path': _tickets_path(artwork.event),
            },
        )
    except Exception:
        logging.exception('No se pudo avisar a %s que la sumaron a la instalación %s', user.email, artwork.pk)


def _send_status_email(artwork):
    recipients = _team_emails(artwork)
    if not recipients:
        return
    try:
        send_mail(
            template_name='art_status_changed',
            recipient_list=recipients,
            context={
                'artwork': artwork,
                'approved': artwork.status == Artwork.Status.ACTIVE,
                'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
            },
        )
    except Exception:
        logging.exception('No se pudo avisar el cambio de estado de la instalación %s', artwork.pk)


def _team_emails(artwork):
    return list(dict.fromkeys(
        user.email for user in (artwork.owner, *artwork.collaborators.all()) if user and user.email
    ))


def _send_contact_email(artwork):
    recipients = _team_emails(artwork)
    contact = artwork.estafa_contact
    if not recipients:
        return
    try:
        send_mail(
            template_name='art_contact_assigned',
            recipient_list=recipients,
            context={
                'artwork': artwork,
                'contact_name': contact.get_full_name() or contact.email,
                'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
            },
            headers={'Reply-To': contact.email} if contact.email else None,
        )
    except Exception:
        logging.exception('No se pudo avisar el contacto de ESTAFA de la instalación %s', artwork.pk)


def _send_checkout_submitted_email(artwork):
    contact = artwork.estafa_contact
    if not contact or not contact.email:
        return
    try:
        send_mail(
            template_name='art_checkout_submitted',
            recipient_list=[contact.email],
            context={'artwork': artwork, 'review_path': reverse('artwork_review', args=[artwork.event.slug, artwork.pk])},
        )
    except Exception:
        logging.exception('No se pudo avisar el checkout enviado de la instalación %s', artwork.pk)


def _ensure_operations_group(artwork, program):
    if artwork.operations_group_id:
        if artwork.operations_group.nombre != artwork.title:
            artwork.operations_group.nombre = artwork.title or f'Instalación #{artwork.pk}'
            artwork.operations_group.save(update_fields=['nombre', 'updated_at'])
        return artwork.operations_group
    if not artwork.owner_id:
        return artwork.operations_group
    art_type, _ = GrupoTipo.objects.get_or_create(
        nombre='ARTE',
        defaults={'descripcion': 'Instalaciones de arte de Fuego Austral'},
    )
    group = Grupo.objects.create(
        event=artwork.event,
        lider=artwork.owner,
        nombre=artwork.title or f'Instalación #{artwork.pk}',
        tipo=art_type,
        ingreso_anticipado_amount=program.early_entry_slots,
        ingreso_anticipado_desde=program.early_entry_from,
        late_checkout_amount=program.late_checkout_slots,
        late_checkout_hasta=program.late_checkout_until,
    )
    artwork.operations_group = group
    artwork.save(update_fields=['operations_group', 'updated_at'])
    return group


def _ticket_sales_started(event):
    now = timezone.now()
    return TicketType.objects.filter(event=event, is_direct_type=False).filter(
        Q(date_from__isnull=True) | Q(date_from__lte=now),
    ).exists()


def _has_ticket(user, event):
    return NewTicket.objects.filter(event=event, holder=user, owner=user).exists()


def _tickets_path(event):
    return reverse('event_home', args=[event.slug]) if event.slug else reverse('home')


def _team_editable(artwork, user):
    if artwork.can_manage(user):
        return True
    program = artwork.event.art_program
    return program.is_current and artwork.status != Artwork.Status.REJECTED and artwork.can_manage_team(user)


def _team_context(artwork, user, team_add_form=None):
    members = list(artwork.team_members().order_by('user__first_name', 'user__last_name', 'user__email'))
    editor_ids = set(artwork.collaborators.values_list('pk', flat=True))
    sales_started = _ticket_sales_started(artwork.event)
    ticket_holders = set(
        NewTicket.objects.filter(
            event=artwork.event, holder__in=[member.user for member in members], owner=F('holder'),
        ).values_list('holder_id', flat=True)
    ) if sales_started else set()
    for member in members:
        member.is_leader = member.user_id == artwork.owner_id
        member.is_self = member.user_id == user.pk
        member.can_edit = member.is_leader or member.user_id in editor_ids
        member.missing_ticket = sales_started and member.user_id not in ticket_holders
    # La persona responsable primero; después, el orden alfabético.
    members.sort(key=lambda member: not member.is_leader)
    editable = _team_editable(artwork, user)
    can_grant = editable and artwork.can_grant_edit(user)
    for member in members:
        member.can_remove = editable and not member.is_leader and (can_grant or not member.can_edit)
    return {
        'team_members': members,
        'team_add_form': team_add_form or ArtworkTeamAddForm(artwork=artwork, auto_id='team-add_%s'),
        'can_edit_team': editable,
        'can_grant_edit': can_grant,
        'tickets_path': _tickets_path(artwork.event),
    }


def _grant_context(artwork, inline_forms=None):
    inline_forms = inline_forms or {}
    budget = list(artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.BUDGET).prefetch_related('photos'))
    expenses = list(artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.EXPENSE).prefetch_related('photos'))
    for item in budget + expenses:
        item.inline_form = inline_forms.get(('grant', item.pk)) or ArtworkGrantItemForm(
            instance=item, phase=item.phase, auto_id=f'grant-{item.pk}_%s',
        )
    return {
        'budget_items': budget,
        'expense_items': expenses,
        'budget_total_ars': artwork.grant_total_ars(ArtworkGrantItem.Phase.BUDGET),
        'expense_total_ars': artwork.grant_total_ars(ArtworkGrantItem.Phase.EXPENSE),
        'grant_over_budget': bool(
            artwork.grant_approved_amount_ars
            and artwork.grant_total_ars(ArtworkGrantItem.Phase.EXPENSE) > artwork.grant_approved_amount_ars
        ),
        'budget_create_form': inline_forms.get(('grant-new', ArtworkGrantItem.Phase.BUDGET)) or ArtworkGrantItemForm(
            instance=ArtworkGrantItem(artwork=artwork, phase=ArtworkGrantItem.Phase.BUDGET),
            phase=ArtworkGrantItem.Phase.BUDGET, auto_id='grant-budget-new_%s',
        ),
        'expense_create_form': inline_forms.get(('grant-new', ArtworkGrantItem.Phase.EXPENSE)) or ArtworkGrantItemForm(
            instance=ArtworkGrantItem(artwork=artwork, phase=ArtworkGrantItem.Phase.EXPENSE),
            phase=ArtworkGrantItem.Phase.EXPENSE, auto_id='grant-expense-new_%s',
        ),
    }


def _artwork_context(artwork, program, form, inline_forms=None):
    inline_forms = inline_forms or {}
    context = _base_context(artwork.event)
    providers = list(artwork.artwork_providers.prefetch_related('vehicles'))
    for provider in providers:
        provider.inline_form = inline_forms.get(('provider', provider.pk)) or ArtworkProviderForm(
            instance=provider, auto_id=f'provider-{provider.pk}_%s',
        )
        provider.inline_vehicles = list(provider.vehicles.all())
        provider.vehicle_create_form = inline_forms.get(('vehicle-new', provider.pk)) or ArtworkProviderVehicleForm(
            instance=ArtworkProviderVehicle(provider=provider), auto_id=f'vehicle-{provider.pk}-new_%s',
        )
        for vehicle in provider.inline_vehicles:
            vehicle.inline_form = inline_forms.get(('vehicle', vehicle.pk)) or ArtworkProviderVehicleForm(
                instance=vehicle, auto_id=f'vehicle-{vehicle.pk}_%s',
            )
    context.update({
        'form': form,
        'program': program,
        'artwork': artwork,
        'checkpoints': _checkpoints(program),
        'photo_upload_form': inline_forms.get(('photo-new', None)) or ArtworkPhotoUploadForm(auto_id='photo-new_%s'),
        'checkout_photo_upload_form': inline_forms.get(('checkout-photo-new', None)) or ArtworkCheckoutPhotoUploadForm(
            auto_id='checkout-photo-new_%s',
        ),
        'checkout_photos': artwork.checkout_photos.all(),
        'artwork_providers': providers,
        'entry_providers': [provider for provider in providers if provider.for_entry],
        'exit_providers': [provider for provider in providers if provider.for_exit],
        'provider_create_form': inline_forms.get(('provider-new', None)) or ArtworkProviderForm(
            instance=ArtworkProvider(artwork=artwork), auto_id='provider-new_%s',
        ),
        'can_manage': artwork.can_manage(form.actor),
        'can_edit_artwork': artwork.can_edit(form.actor) or artwork.can_manage(form.actor),
        'can_edit_budget': _grant_item_editable(artwork, ArtworkGrantItem.Phase.BUDGET, form.actor),
        'can_edit_expenses': _grant_item_editable(artwork, ArtworkGrantItem.Phase.EXPENSE, form.actor),
        'can_edit_logistics': _logistics_editable(artwork, form.actor),
        'can_edit_checkout': _checkout_editable(artwork, form.actor),
        'can_submit_grant': artwork.can_edit(form.actor) and program.is_current and program.checkpoint_state('grant') == 'open' and artwork.grant_status in (Artwork.GrantStatus.NOT_REQUESTED, Artwork.GrantStatus.INFO_REQUIRED),
        'can_submit_report': artwork.can_edit(form.actor) and program.is_current and program.checkpoint_state('grant_report') == 'open' and artwork.grant_status in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID),
        'can_submit_checkout': _can_submit_checkout(artwork, program, form.actor),
    })
    # ESTAFA editando la instalación de otra persona: navega dentro de ESTAFA y auditlog registra quién guarda.
    if context['can_manage'] and not artwork.can_edit(form.actor):
        context.update(_estafa_context(form.actor, artwork.event), acting_as_estafa=True)
    context.update(_grant_context(artwork, inline_forms))
    context.update(_team_context(artwork, form.actor, inline_forms.get(('team-add', None))))
    return context


def _estafa_context(user, event):
    return {
        **_base_context(event), 'nav_primary': 'estafa',
        'estafa_events': estafa_events(user), 'current_estafa_event': event,
    }


def _review_context(artwork, form, user, inline_forms=None):
    inline_forms = inline_forms or {}
    grant_context = _grant_context(artwork, inline_forms)
    for item in grant_context['budget_items'] + grant_context['expense_items']:
        item.review_form = ArtworkGrantItemReviewForm(instance=item, auto_id=f'grant-review-{item.pk}_%s')
    return {
        **_estafa_context(user, artwork.event), **grant_context,
        'artwork': artwork, 'form': form,
        'contact_form': ArtworkContactForm(instance=artwork, auto_id='contact_%s'),
        'can_manage': artwork.can_manage(user),
        'checkout_photo_upload_form': inline_forms.get(('checkout-photo-new', None)) or ArtworkCheckoutPhotoUploadForm(
            auto_id='checkout-photo-new_%s',
        ),
        'checkout_photos': artwork.checkout_photos.all(),
    }


def _inline_error_response(request, artwork, key, inline_form):
    if request.POST.get('return_to') == 'review' and artwork.can_manage(request.user):
        review_form = ArtworkReviewForm(instance=artwork, can_manage=artwork.can_manage(request.user))
        return render(
            request, 'art/review.html',
            _review_context(artwork, review_form, request.user, {key: inline_form}),
        )
    program = artwork.event.art_program
    artwork_form = ArtworkForm(
        instance=artwork, program=program, owner=artwork.owner, actor=request.user,
    )
    return render(
        request, 'art/form.html',
        _artwork_context(artwork, program, artwork_form, {key: inline_form}),
    )


def _artwork_redirect(artwork, anchor):
    return redirect(f"{reverse('artwork_edit', args=[artwork.pk])}#{anchor}")


def _checkout_redirect(request, artwork):
    if request.POST.get('return_to') == 'review' and artwork.can_manage(request.user):
        return redirect(f"{reverse('artwork_review', args=[artwork.event.slug, artwork.pk])}#checkout-report")
    return _artwork_redirect(artwork, 'checkout')


def _grant_redirect(request, artwork, phase):
    if request.POST.get('return_to') == 'review' and artwork.can_manage(request.user):
        anchor = 'admin-budget' if phase == ArtworkGrantItem.Phase.BUDGET else 'admin-expenses'
        return redirect(f"{reverse('artwork_review', args=[artwork.event.slug, artwork.pk])}#{anchor}")
    return _artwork_redirect(artwork, 'beca' if phase == ArtworkGrantItem.Phase.BUDGET else 'rendicion')


@login_required
def art_dashboard(request):
    programs = ArtProgram.objects.select_related('event').filter(is_current=True, event__active=True)
    artworks = (
        Artwork.objects.filter(
            Q(owner=request.user) | Q(collaborators=request.user) | Q(operations_group__miembros__user=request.user),
        )
        .select_related('event', 'estafa_contact')
        .prefetch_related('collaborators')
        .distinct()
    )
    context = _base_context()
    context.update({'programs': programs, 'artworks': artworks})
    return render(request, 'art/dashboard.html', context)


@login_required
def artwork_create(request, event_slug):
    """Blank dossier: the artwork only exists after the first valid save."""
    program = get_object_or_404(
        ArtProgram.objects.select_related('event'),
        event__slug=event_slug,
        event__active=True,
        is_current=True,
    )
    if not program.registration_is_open():
        messages.error(request, 'La inscripción de instalaciones está cerrada.')
        return redirect('art_dashboard')

    form = ArtworkForm(
        request.POST or None, request.FILES or None,
        instance=Artwork(event=program.event, owner=request.user, kind=Artwork.Kind.PLANNED),
        program=program, owner=request.user, actor=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.instance.submitted_at = timezone.now()
            artwork = form.save()
            _ensure_operations_group(artwork, program)
        messages.success(
            request,
            'La instalación quedó inscripta. Queda pendiente de aprobación por ESTAFA. '
            'Podés seguir modificándola libremente a medida que la instalación avance.',
        )
        return redirect('artwork_edit', artwork_id=artwork.pk)

    context = _base_context(program.event)
    context.update({
        'form': form,
        'program': program,
        'artwork': None,
        'can_edit_artwork': True,
        'checkpoints': _checkpoints(program),
        'budget_total_ars': 0,
    })
    return render(request, 'art/form.html', context)


@login_required
def artwork_edit(request, artwork_id):
    if request.method == 'POST':
        with transaction.atomic():
            artwork = get_object_or_404(Artwork.objects.select_for_update(), pk=artwork_id)
            if not artwork.can_edit(request.user) and not artwork.can_manage(request.user):
                raise Http404
            response = _handle_artwork_edit(request, artwork)
        return response
    artwork = get_object_or_404(_accessible_artworks(request.user), pk=artwork_id)
    return _handle_artwork_edit(request, artwork)


def _handle_artwork_edit(request, artwork):
    try:
        program = artwork.event.art_program
    except ArtProgram.DoesNotExist as exc:
        raise Http404('El programa de Arte ya no está disponible.') from exc

    action = request.POST.get('action', 'save')
    form = ArtworkForm(
        request.POST or None, request.FILES or None,
        instance=artwork, program=program, owner=artwork.owner, actor=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        if not form.instance.submitted_at:
            form.instance.submitted_at = timezone.now()
        artwork = form.save()
        _ensure_operations_group(artwork, program)
        if action == 'checkout':
            _submit_checkout(request, artwork, program)
            return _artwork_redirect(artwork, 'checkout')
        messages.success(request, 'Cambios guardados.')
        return redirect('artwork_edit', artwork_id=artwork.pk)

    return render(request, 'art/form.html', _artwork_context(artwork, program, form))


def _can_submit_checkout(artwork, program, user):
    return (
        artwork.can_edit(user) and program.is_current and program.checkpoint_state('checkout') == 'open'
        and artwork.status == Artwork.Status.ACTIVE
    )


def _submit_checkout(request, artwork, program):
    if not _can_submit_checkout(artwork, program, request.user):
        messages.error(request, 'Esta instalación no tiene un checkout pendiente.')
    elif not artwork.checkout_photos.exists():
        messages.error(request, 'Los cambios quedaron guardados. Para enviar el checkout, subí al menos una foto del estado final del espacio.')
    else:
        artwork.checkout_completed = True
        artwork.checkout_requested_at = timezone.now()
        artwork.set_status(Artwork.Status.CHECKOUT_SUBMITTED, request.user)
        artwork.save(update_fields=[
            'checkout_completed', 'checkout_requested_at', 'status',
            'status_changed_at', 'status_changed_by', 'updated_at',
        ])
        transaction.on_commit(lambda: _send_checkout_submitted_email(artwork))
        messages.success(request, 'El checkout fue enviado. El equipo de Arte lo va a verificar.')


def _grant_item_editable(artwork, phase, user):
    if phase == ArtworkGrantItem.Phase.EXPENSE and artwork.grant_status == Artwork.GrantStatus.CLOSED:
        return False
    if artwork.can_manage(user):
        return True
    if not artwork.can_edit(user):
        return False
    program = artwork.event.art_program
    if not program.is_current or not program.grants_enabled or artwork.status == Artwork.Status.REJECTED:
        return False
    if phase == ArtworkGrantItem.Phase.BUDGET:
        return program.checkpoint_state('grant') == 'open' and artwork.grant_status in (
            Artwork.GrantStatus.NOT_REQUESTED, Artwork.GrantStatus.INFO_REQUIRED,
        )
    return program.checkpoint_state('grant_report') == 'open' and artwork.grant_status in (
        Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID,
    )


def _logistics_editable(artwork, user):
    if artwork.can_manage(user):
        return True
    program = artwork.event.art_program
    return (
        program.is_current and program.checkpoint_state('logistics') == 'open' and artwork.can_edit(user)
        and artwork.status != Artwork.Status.REJECTED
    )


def _checkout_editable(artwork, user):
    if artwork.can_manage(user):
        return True
    program = artwork.event.art_program
    return (
        program.is_current and program.checkpoint_state('checkout') == 'open' and artwork.can_edit(user)
        and artwork.status in (Artwork.Status.ACTIVE, Artwork.Status.CHECKOUT_SUBMITTED)
    )


def _save_grant_item_images(item, images, user):
    for image in images:
        ArtworkGrantItemPhoto.objects.create(item=item, image=image, uploaded_by=user)


@login_required
def grant_item_create(request, artwork_id, phase):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if phase not in ArtworkGrantItem.Phase.values or not _grant_item_editable(artwork, phase, request.user):
        return HttpResponseForbidden('Este bloque ya no se puede editar.')
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'beca' if phase == ArtworkGrantItem.Phase.BUDGET else 'rendicion')
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        if not _grant_item_editable(artwork, phase, request.user):
            return HttpResponseForbidden('Este bloque ya no se puede editar.')
        item = ArtworkGrantItem(artwork=artwork, phase=phase, created_by=request.user)
        form = ArtworkGrantItemForm(
            request.POST, request.FILES, instance=item, phase=phase,
            auto_id=f'grant-{phase}-new_%s',
        )
        if form.is_valid():
            item = form.save()
            _save_grant_item_images(item, form.cleaned_data['images'], request.user)
            messages.success(request, 'Ítem agregado y total actualizado.')
            return _grant_redirect(request, artwork, phase)
    return _inline_error_response(request, artwork, ('grant-new', phase), form)


@login_required
def grant_item_edit(request, artwork_id, item_id):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    item = get_object_or_404(ArtworkGrantItem, pk=item_id, artwork=artwork)
    if not _grant_item_editable(artwork, item.phase, request.user):
        return HttpResponseForbidden('Este bloque ya no se puede editar.')
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'beca' if item.phase == ArtworkGrantItem.Phase.BUDGET else 'rendicion')
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        item = get_object_or_404(ArtworkGrantItem.objects.select_for_update(), pk=item_id, artwork=artwork)
        if not _grant_item_editable(artwork, item.phase, request.user):
            return HttpResponseForbidden('Este bloque ya no se puede editar.')
        form = ArtworkGrantItemForm(
            request.POST, request.FILES, instance=item, phase=item.phase,
            auto_id=f'grant-{item.pk}_%s',
        )
        if form.is_valid():
            item = form.save()
            _save_grant_item_images(item, form.cleaned_data['images'], request.user)
            messages.success(request, 'Ítem actualizado.')
            return _grant_redirect(request, artwork, item.phase)
    return _inline_error_response(request, artwork, ('grant', item.pk), form)


@login_required
def grant_item_review(request, event_slug, artwork_id, item_id):
    if request.method != 'POST':
        return HttpResponseForbidden('La revisión del ítem requiere una confirmación.')
    with transaction.atomic():
        artwork = get_object_or_404(
            Artwork.objects.select_for_update(), pk=artwork_id, event__slug=event_slug,
        )
        if not artwork.can_manage(request.user):
            return HttpResponseForbidden('No tenés permisos para revisar becas en este evento.')
        item = get_object_or_404(ArtworkGrantItem.objects.select_for_update(), pk=item_id, artwork=artwork)
        form = ArtworkGrantItemReviewForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f'Revisión de “{item.concept}” guardada.')
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
    return redirect(f"{reverse('artwork_review', args=[event_slug, artwork_id])}#{'admin-budget' if item.phase == ArtworkGrantItem.Phase.BUDGET else 'admin-expenses'}")


@login_required
def grant_item_delete(request, artwork_id, item_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Este ítem no se puede eliminar.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        item = get_object_or_404(ArtworkGrantItem.objects.select_for_update(), pk=item_id, artwork=artwork)
        if not _grant_item_editable(artwork, item.phase, request.user):
            return HttpResponseForbidden('Este ítem no se puede eliminar.')
        item.delete()
    messages.success(request, 'Ítem eliminado.')
    return _grant_redirect(request, artwork, item.phase)


@login_required
def grant_item_photo_delete(request, artwork_id, item_id, photo_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Esta imagen no se puede eliminar.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        item = get_object_or_404(ArtworkGrantItem.objects.select_for_update(), pk=item_id, artwork=artwork)
        photo = get_object_or_404(ArtworkGrantItemPhoto.objects.select_for_update(), pk=photo_id, item=item)
        if not _grant_item_editable(artwork, item.phase, request.user):
            return HttpResponseForbidden('Esta imagen no se puede eliminar.')
        storage, image_name = photo.image.storage, photo.image.name
        photo.delete()
        transaction.on_commit(lambda: storage.delete(image_name))
    messages.success(request, 'Imagen eliminada del ítem.')
    return _grant_redirect(request, artwork, item.phase)


@login_required
def grant_submit(request, artwork_id):
    if request.method != 'POST':
        return HttpResponseForbidden('La convocatoria de becas no está abierta.')
    with transaction.atomic():
        artwork = get_object_or_404(
            _accessible_artworks(request.user).filter(
                Q(owner=request.user) | Q(pk__in=Artwork.objects.filter(collaborators=request.user).values('pk')),
            ).select_for_update(),
            pk=artwork_id,
        )
        program = artwork.event.art_program
        if not program.is_current or not program.grants_enabled or program.checkpoint_state('grant') != 'open':
            return HttpResponseForbidden('La convocatoria de becas no está abierta.')
        if artwork.grant_status not in (Artwork.GrantStatus.NOT_REQUESTED, Artwork.GrantStatus.INFO_REQUIRED):
            return HttpResponseForbidden('La solicitud de beca ya fue presentada.')
        errors = []
        if not artwork.grant_requested:
            errors.append('Marcá que querés solicitar una beca y guardá la instalación.')
        if not artwork.grant_justification:
            errors.append('Completá la justificación de la beca.')
        if not artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.BUDGET).exists():
            errors.append('Agregá al menos un ítem al presupuesto.')
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            artwork.grant_status = Artwork.GrantStatus.PENDING
            artwork.save(update_fields=['grant_status', 'updated_at'])
            messages.success(request, 'La solicitud de beca fue presentada para revisión.')
    return _artwork_redirect(artwork, 'beca')


@login_required
def grant_report_submit(request, artwork_id):
    if request.method != 'POST':
        return HttpResponseForbidden('La rendición no está abierta.')
    with transaction.atomic():
        artwork = get_object_or_404(
            _accessible_artworks(request.user).filter(
                Q(owner=request.user) | Q(pk__in=Artwork.objects.filter(collaborators=request.user).values('pk')),
            ).select_for_update(),
            pk=artwork_id,
        )
        program = artwork.event.art_program
        if not program.is_current or not program.grants_enabled or program.checkpoint_state('grant_report') != 'open':
            return HttpResponseForbidden('La rendición no está abierta.')
        if artwork.grant_status not in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID):
            return HttpResponseForbidden('La instalación no tiene una beca aprobada para rendir.')
        if not artwork.grant_report:
            messages.error(request, 'Completá y guardá el relato de la rendición.')
        elif not artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.EXPENSE).exists():
            messages.error(request, 'Agregá al menos un gasto a la rendición.')
        elif not artwork.photos.filter(stage__in=(ArtworkPhoto.Stage.FINAL, ArtworkPhoto.Stage.GRANT_REPORT)).exists():
            messages.error(request, 'Subí al menos una foto final o de rendición.')
        elif artwork.grant_approved_amount_ars and artwork.expense_total_ars > artwork.grant_approved_amount_ars:
            messages.error(request, 'La rendición supera el monto aprobado. Revisá los montos antes de enviarla.')
        else:
            artwork.grant_status = Artwork.GrantStatus.REPORTED
            artwork.save(update_fields=['grant_status', 'updated_at'])
            messages.success(request, 'La rendición fue enviada para revisión.')
    return _artwork_redirect(artwork, 'rendicion')


@login_required
def artwork_photo_upload(request, artwork_id):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if not artwork.can_edit(request.user) and not artwork.can_manage(request.user):
        return HttpResponseForbidden('La galería de esta instalación no se puede editar.')
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'galeria')
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        if not artwork.can_edit(request.user) and not artwork.can_manage(request.user):
            return HttpResponseForbidden('La galería de esta instalación no se puede editar.')
        form = ArtworkPhotoUploadForm(request.POST, request.FILES, auto_id='photo-new_%s')
        if form.is_valid():
            images = form.cleaned_data['images']
            if artwork.photos.count() + len(images) > 100:
                form.add_error('images', 'La galería admite hasta 100 fotos por instalación.')
            else:
                for image in images:
                    ArtworkPhoto.objects.create(
                        artwork=artwork,
                        image=image,
                        stage=form.cleaned_data['stage'],
                        caption=form.cleaned_data['caption'],
                        publication_authorized=form.cleaned_data['publication_authorized'],
                        uploaded_by=request.user,
                    )
                messages.success(request, f"Se subieron {len(images)} foto(s).")
                return _artwork_redirect(artwork, 'galeria')
    return _inline_error_response(request, artwork, ('photo-new', None), form)


@login_required
def artwork_photo_delete(request, artwork_id, photo_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Método no permitido.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        if not artwork.can_edit(request.user) and not artwork.can_manage(request.user):
            return HttpResponseForbidden('La galería de esta instalación no se puede editar.')
        photo = get_object_or_404(ArtworkPhoto.objects.select_for_update(), pk=photo_id, artwork=artwork)
        protected_evidence = (
            artwork.grant_status in (Artwork.GrantStatus.REPORTED, Artwork.GrantStatus.CLOSED)
            and photo.stage in (ArtworkPhoto.Stage.FINAL, ArtworkPhoto.Stage.GRANT_REPORT)
        )
        if protected_evidence and not artwork.can_manage(request.user):
            return HttpResponseForbidden('La evidencia de una rendición presentada no se puede eliminar.')
        storage = photo.image.storage
        image_name = photo.image.name
        photo.delete()
        transaction.on_commit(lambda: storage.delete(image_name))
    messages.success(request, 'Foto eliminada de la galería.')
    return _artwork_redirect(artwork, 'galeria')


@login_required
def artwork_checkout_photo_upload(request, artwork_id):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if request.method != 'POST':
        return _checkout_redirect(request, artwork)
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        if not _checkout_editable(artwork, request.user):
            return HttpResponseForbidden('El checkout de esta instalación ya no se puede editar.')
        if artwork.checkout_verified_at and not artwork.can_manage(request.user):
            return HttpResponseForbidden('La evidencia de un checkout verificado no se puede modificar.')
        form = ArtworkCheckoutPhotoUploadForm(request.POST, request.FILES, auto_id='checkout-photo-new_%s')
        if form.is_valid():
            images = form.cleaned_data['images']
            if artwork.checkout_photos.count() + len(images) > 100:
                form.add_error('images', 'El reporte de checkout admite hasta 100 fotos por instalación.')
            else:
                for image in images:
                    ArtworkCheckoutPhoto.objects.create(
                        artwork=artwork, image=image, category=form.cleaned_data['category'],
                        caption=form.cleaned_data['caption'], uploaded_by=request.user,
                    )
                messages.success(request, f"Se subieron {len(images)} foto(s) al reporte de checkout.")
                return _checkout_redirect(request, artwork)
    return _inline_error_response(request, artwork, ('checkout-photo-new', None), form)


@login_required
def artwork_checkout_photo_delete(request, artwork_id, photo_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Método no permitido.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        photo = get_object_or_404(ArtworkCheckoutPhoto.objects.select_for_update(), pk=photo_id, artwork=artwork)
        if not _checkout_editable(artwork, request.user):
            return HttpResponseForbidden('Esta foto no se puede eliminar.')
        if artwork.checkout_verified_at and not artwork.can_manage(request.user):
            return HttpResponseForbidden('La evidencia de un checkout verificado no se puede eliminar.')
        storage, image_name = photo.image.storage, photo.image.name
        photo.delete()
        transaction.on_commit(lambda: storage.delete(image_name))
    messages.success(request, 'Foto eliminada del reporte de checkout.')
    return _checkout_redirect(request, artwork)


@login_required
@require_POST
def artwork_team_add(request, artwork_id):
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        if not _team_editable(artwork, request.user):
            return HttpResponseForbidden('El equipo de esta instalación no se puede editar.')
        form = ArtworkTeamAddForm(request.POST, artwork=artwork, auto_id='team-add_%s')
        if not form.is_valid():
            return _inline_error_response(request, artwork, ('team-add', None), form)
        group = _ensure_operations_group(artwork, artwork.event.art_program)
        GrupoMiembro.objects.create(grupo=group, user=form.user)
        transaction.on_commit(lambda: _send_team_member_added_email(artwork, form.user, request.user))
    messages.success(request, f'{form.user.get_full_name() or form.user.email} ya es parte del equipo. Le avisamos por email.')
    return _artwork_redirect(artwork, 'equipo')


@login_required
@require_POST
def artwork_team_remove(request, artwork_id, member_id):
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        member = get_object_or_404(artwork.team_members(), pk=member_id)
        if not _team_editable(artwork, request.user) or member.user_id == artwork.owner_id:
            return HttpResponseForbidden('Esta persona no se puede quitar del equipo.')
        if artwork.can_edit(member.user) and not artwork.can_grant_edit(request.user):
            return HttpResponseForbidden('Solo la persona responsable puede quitar a quien edita la instalación.')
        if artwork.checkout_completed and artwork.checkout_team_responsible_id == member.user_id:
            messages.error(request, 'Es la persona responsable del checkout enviado. Cambiala en el checkout antes de quitarla.')
            return _artwork_redirect(artwork, 'equipo')
        limit = artwork.event.ingreso_anticipado_limite_carga
        if (member.ingreso_anticipado or member.ingreso_anticipado_fecha) and limit and timezone.now() > limit:
            messages.error(request, 'Tiene ingreso anticipado y ya cerró la carga de ingresos anticipados.')
            return _artwork_redirect(artwork, 'equipo')
        artwork.collaborators.remove(member.user)
        if artwork.checkout_team_responsible_id == member.user_id:
            artwork.checkout_team_responsible = None
            artwork.save(update_fields=['checkout_team_responsible', 'updated_at'])
        member.delete()
    messages.success(request, f'{member.user.get_full_name() or member.user.email} ya no es parte del equipo.')
    return _artwork_redirect(artwork, 'equipo')


@login_required
@require_POST
def artwork_team_access(request, artwork_id, member_id):
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        member = get_object_or_404(artwork.team_members(), pk=member_id)
        if (
            not _team_editable(artwork, request.user) or not artwork.can_grant_edit(request.user)
            or member.user_id == artwork.owner_id
        ):
            return HttpResponseForbidden('No podés cambiar quién edita esta instalación.')
        name = member.user.get_full_name() or member.user.email
        if request.POST.get('can_edit') == 'on':
            artwork.collaborators.add(member.user)
            messages.success(request, f'{name} ahora puede editar la instalación.')
        else:
            artwork.collaborators.remove(member.user)
            messages.success(request, f'{name} ya no puede editar la instalación.')
    return _artwork_redirect(artwork, 'equipo')


@login_required
def artwork_provider_edit(request, artwork_id, provider_id=None):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if not _logistics_editable(artwork, request.user):
        return HttpResponseForbidden('La logística de esta instalación ya no se puede editar.')
    provider = get_object_or_404(ArtworkProvider, artwork=artwork, pk=provider_id) if provider_id else ArtworkProvider(artwork=artwork, created_by=request.user)
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'logistica')
    form = ArtworkProviderForm(
        request.POST, instance=provider,
        auto_id=f'provider-{provider_id}_%s' if provider_id else 'provider-new_%s',
    )
    if form.is_valid():
        with transaction.atomic():
            artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
            if not _logistics_editable(artwork, request.user):
                return HttpResponseForbidden('La logística de esta instalación ya no se puede editar.')
            form.instance.artwork = artwork
            form.instance.created_by = form.instance.created_by or request.user
            provider = form.save()
        messages.success(request, 'Proveedor guardado.')
        return _artwork_redirect(artwork, 'logistica')
    return _inline_error_response(
        request, artwork, ('provider', provider.pk) if provider_id else ('provider-new', None), form,
    )


@login_required
def artwork_provider_delete(request, artwork_id, provider_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Este proveedor no se puede eliminar.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        provider = get_object_or_404(ArtworkProvider.objects.select_for_update(), artwork=artwork, pk=provider_id)
        if not _logistics_editable(artwork, request.user):
            return HttpResponseForbidden('Este proveedor no se puede eliminar.')
        provider.delete()
    messages.success(request, 'Proveedor y sus vehículos fueron eliminados.')
    return _artwork_redirect(artwork, 'logistica')


@login_required
def artwork_vehicle_edit(request, artwork_id, provider_id, vehicle_id=None):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    provider = get_object_or_404(ArtworkProvider, artwork=artwork, pk=provider_id)
    if not _logistics_editable(artwork, request.user):
        return HttpResponseForbidden('La logística de esta instalación ya no se puede editar.')
    vehicle = get_object_or_404(ArtworkProviderVehicle, provider=provider, pk=vehicle_id) if vehicle_id else ArtworkProviderVehicle(provider=provider)
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'logistica')
    form = ArtworkProviderVehicleForm(
        request.POST, instance=vehicle,
        auto_id=f'vehicle-{vehicle_id}_%s' if vehicle_id else f'vehicle-{provider_id}-new_%s',
    )
    if form.is_valid():
        with transaction.atomic():
            artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
            provider = get_object_or_404(ArtworkProvider, artwork=artwork, pk=provider_id)
            if not _logistics_editable(artwork, request.user):
                return HttpResponseForbidden('La logística de esta instalación ya no se puede editar.')
            form.instance.provider = provider
            form.save()
        messages.success(request, 'Vehículo guardado.')
        return _artwork_redirect(artwork, 'logistica')
    return _inline_error_response(
        request, artwork, ('vehicle', vehicle.pk) if vehicle_id else ('vehicle-new', provider.pk), form,
    )


@login_required
def artwork_vehicle_delete(request, artwork_id, provider_id, vehicle_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Este vehículo no se puede eliminar.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        provider = get_object_or_404(ArtworkProvider, artwork=artwork, pk=provider_id)
        vehicle = get_object_or_404(ArtworkProviderVehicle.objects.select_for_update(), provider=provider, pk=vehicle_id)
        if not _logistics_editable(artwork, request.user):
            return HttpResponseForbidden('Este vehículo no se puede eliminar.')
        vehicle.delete()
    messages.success(request, 'Vehículo eliminado.')
    return _artwork_redirect(artwork, 'logistica')


def _managed_event(request, event_slug):
    event = get_object_or_404(Event, slug=event_slug, art_program__isnull=False)
    if not can_coordinate(request.user, event):
        return None
    return event


def _filtered_artworks(event, params):
    artworks = event.artworks.select_related('owner', 'safety_responsible', 'estafa_contact').prefetch_related(
        'grant_items', 'checkout_photos', 'operations_group__miembros', 'artwork_providers__vehicles',
    )
    query = params.get('q', '').strip()
    if query:
        artworks = artworks.filter(
            Q(title__icontains=query)
            | Q(owner__email__icontains=query)
            | Q(public_title__icontains=query)
            | Q(assigned_location__icontains=query)
        )
    stage = params.get('status')
    if stage in Artwork.STAGES:
        try:
            checkout_open = event.art_program.checkout_is_open()
        except ArtProgram.DoesNotExist:
            checkout_open = False
        if stage == Artwork.CHECKOUT_PENDING:
            artworks = artworks.filter(status=Artwork.Status.ACTIVE) if checkout_open else artworks.none()
        elif stage == Artwork.Status.ACTIVE and checkout_open:
            artworks = artworks.none()
        else:
            artworks = artworks.filter(status=stage)
    if params.get('grant') in Artwork.GrantStatus.values:
        artworks = artworks.filter(grant_status=params['grant'])
    if params.get('kind') in Artwork.Kind.values:
        artworks = artworks.filter(kind=params['kind'])
    for parameter, field in (('fire', 'uses_fire'), ('sound', 'uses_sound')):
        if params.get(parameter) in ('yes', 'no'):
            artworks = artworks.filter(**{field: params[parameter] == 'yes'})
    # Primero lo que espera a ESTAFA: inscripciones y checkouts por revisar.
    turn = Case(
        When(status=Artwork.Status.PENDING, then=Value(0)),
        When(status=Artwork.Status.CHECKOUT_SUBMITTED, then=Value(1)),
        When(status=Artwork.Status.ACTIVE, then=Value(2)),
        When(status=Artwork.Status.CHECKOUT_VERIFIED, then=Value(3)),
        default=Value(4), output_field=IntegerField(),
    )
    return artworks.distinct().order_by(turn, 'title')


@login_required
def estafa_home(request):
    if not can_access_estafa(request.user):
        return HttpResponseForbidden('Esta sección es sólo para ESTAFA.')
    events = estafa_events(request.user)
    event = events.filter(art_program__is_current=True).first() or events.first()
    if event:
        return redirect('art_admin_dashboard', event_slug=event.slug)
    return render(request, 'art/admin_dashboard.html', _estafa_context(request.user, None))


@login_required
def art_admin_dashboard(request, event_slug):
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
    artworks = _filtered_artworks(event, request.GET)
    assigned = event.artworks.filter(estafa_contact=request.user).select_related('owner').order_by('status', 'title')
    return render(request, 'art/admin_dashboard.html', {
        **_estafa_context(request.user, event), 'artworks': artworks, 'assigned_artworks': assigned,
        'status_choices': [(stage, label) for stage, (label, _hint) in Artwork.STAGES.items()],
        'grant_choices': Artwork.GrantStatus.choices,
        'kind_choices': Artwork.Kind.choices,
    })


@login_required
def artwork_review(request, event_slug, artwork_id):
    event = get_object_or_404(Event, slug=event_slug)
    artwork = get_object_or_404(Artwork, pk=artwork_id, event=event)
    if not artwork.can_manage(request.user):
        return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
    can_manage = artwork.can_manage(request.user)
    if request.method == 'POST':
        with transaction.atomic():
            artwork = get_object_or_404(Artwork.objects.select_for_update(), pk=artwork_id, event=event)
            was_verified = bool(artwork.checkout_verified_at)
            had_art_checkin = bool(artwork.checkin_art_at)
            if not artwork.can_manage(request.user):
                return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
            can_manage = artwork.can_manage(request.user)
            form = ArtworkReviewForm(request.POST, request.FILES, instance=artwork, can_manage=can_manage)
            if form.is_valid():
                artwork = form.save(commit=False)
                if artwork.checkin_art_at and not had_art_checkin:
                    artwork.checkin_art_by = request.user
                if artwork.checkout_verified_at:
                    artwork.checkout_verified_by = request.user
                    if artwork.status != Artwork.Status.CHECKOUT_VERIFIED:
                        artwork.set_status(Artwork.Status.CHECKOUT_VERIFIED, request.user)
                elif was_verified:
                    artwork.checkout_verified_by = None
                    if artwork.status == Artwork.Status.CHECKOUT_VERIFIED:
                        artwork.set_status(Artwork.Status.CHECKOUT_SUBMITTED, request.user)
                artwork.save()
                messages.success(request, 'La revisión quedó guardada.')
                return redirect('artwork_review', event_slug=event.slug, artwork_id=artwork.pk)
    else:
        form = ArtworkReviewForm(instance=artwork, can_manage=can_manage)
    return render(request, 'art/review.html', _review_context(artwork, form, request.user))


@login_required
@require_POST
def artwork_contact(request, event_slug, artwork_id):
    event = get_object_or_404(Event, slug=event_slug)
    with transaction.atomic():
        artwork = get_object_or_404(Artwork.objects.select_for_update(), pk=artwork_id, event=event)
        if not artwork.can_manage(request.user):
            return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
        previous_id = artwork.estafa_contact_id
        form = ArtworkContactForm(request.POST, instance=artwork)
        if not form.is_valid():
            messages.error(request, 'Elegí una persona activa de ESTAFA.')
        elif form.cleaned_data['estafa_contact'] and form.cleaned_data['estafa_contact'].pk != previous_id:
            artwork = form.save()
            contact = artwork.estafa_contact
            transaction.on_commit(lambda: _send_contact_email(artwork))
            messages.success(request, f'{contact.get_full_name() or contact.email} es el contacto de ESTAFA. Le avisamos al equipo por email.')
        elif not form.cleaned_data['estafa_contact'] and previous_id:
            form.save()
            messages.success(request, 'La instalación quedó sin contacto de ESTAFA.')
    return redirect('artwork_review', event_slug=event.slug, artwork_id=artwork.pk)


STATUS_TRANSITIONS = {
    'approve': ((Artwork.Status.PENDING,), Artwork.Status.ACTIVE),
    'reject': ((Artwork.Status.PENDING,), Artwork.Status.REJECTED),
    'reopen': ((Artwork.Status.ACTIVE, Artwork.Status.REJECTED), Artwork.Status.PENDING),
}


@login_required
@require_POST
def artwork_status(request, event_slug, artwork_id):
    event = get_object_or_404(Event, slug=event_slug)
    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse('artwork_review', args=[event.slug, artwork_id])
    with transaction.atomic():
        artwork = get_object_or_404(Artwork.objects.select_for_update(), pk=artwork_id, event=event)
        if not artwork.can_manage(request.user):
            return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
        transition = STATUS_TRANSITIONS.get(request.POST.get('transition'))
        if not transition or artwork.status not in transition[0]:
            messages.error(request, 'La instalación cambió de estado mientras la revisabas. Revisá su estado actual.')
            return redirect(next_url)
        status = transition[1]
        message = request.POST.get('message', '').strip()
        if status == Artwork.Status.REJECTED and not message:
            messages.error(request, 'Contale al equipo por qué se rechaza la instalación.')
            return redirect(next_url)
        artwork.set_status(status, request.user)
        artwork.review_feedback = message
        artwork.save(update_fields=['status', 'status_changed_at', 'status_changed_by', 'review_feedback', 'updated_at'])
        if status == Artwork.Status.ACTIVE:
            _ensure_operations_group(artwork, event.art_program)
        if status != Artwork.Status.PENDING:
            transaction.on_commit(lambda: _send_status_email(artwork))
    title = artwork.title or 'Instalación sin título'
    messages.success(request, {
        Artwork.Status.ACTIVE: f'Aprobaste «{title}». Le avisamos al equipo por email.',
        Artwork.Status.REJECTED: f'Rechazaste «{title}». Le avisamos al equipo por email.',
        Artwork.Status.PENDING: f'«{title}» volvió a inscripción pendiente.',
    }[status])
    return redirect(next_url)


@login_required
def art_admin_export(request, event_slug):
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para exportar Arte en este evento.')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="arte_{event.slug}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([
        'Instalación', 'Modalidad', 'Responsable', 'Estado', 'Etiquetas', 'Responsable de seguridad',
        'Beca', 'Presupuesto ARS', 'Rendición ARS', 'Título público', 'Descripción pública',
        'Ubicación asignada', 'Carta digital', 'Carta física', 'Personas del equipo',
        'Proveedores', 'Vehículos', 'Fotos checkout', 'Checkout', 'Actualizada',
    ])
    for artwork in _filtered_artworks(event, request.GET):
        tags = ', '.join(label for enabled, label in (
            (artwork.uses_fire, 'Tiene fuego'),
            (artwork.uses_sound, 'Tiene sonido'),
        ) if enabled)
        providers = list(artwork.artwork_providers.all())
        writer.writerow([_csv_cell(value) for value in [
            artwork.title,
            artwork.get_kind_display(),
            artwork.owner.email if artwork.owner else '',
            artwork.stage_label,
            tags,
            artwork.safety_responsible.email if artwork.safety_responsible else '',
            artwork.get_grant_status_display(),
            artwork.grant_total_ars(ArtworkGrantItem.Phase.BUDGET),
            artwork.grant_total_ars(ArtworkGrantItem.Phase.EXPENSE),
            artwork.public_title,
            artwork.public_description,
            artwork.assigned_location,
            artwork.understanding_letter.name if artwork.understanding_letter else '',
            'Sí' if artwork.understanding_letter_physical_received else 'No',
            len(artwork.operations_group.miembros.all()) if artwork.operations_group else 0,
            len(providers),
            sum(len(provider.vehicles.all()) for provider in providers),
            artwork.checkout_photos.count(),
            'Verificado' if artwork.checkout_verified_at else ('Solicitado' if artwork.checkout_completed else 'Pendiente'),
            artwork.updated_at.isoformat(),
        ]])
    return response


def _csv_cell(value):
    text = '' if value is None else str(value)
    return f"'{text}" if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
