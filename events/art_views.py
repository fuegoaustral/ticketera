import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from events.utils import get_admin_events_for_user
from utils.email import send_mail

from .forms import (
    ArtworkForm, ArtworkGrantItemForm, ArtworkLogisticsPersonForm,
    ArtworkPhotoUploadForm, ArtworkProviderForm, ArtworkProviderVehicleForm,
    ArtworkReviewForm,
)
from .models import (
    ArtProgram, Artwork, ArtworkGrantItem, ArtworkGrantItemPhoto,
    ArtworkInvitation, ArtworkLogisticsPerson, ArtworkPhoto, ArtworkProvider,
    ArtworkProviderVehicle, Event, Grupo, GrupoMiembro, GrupoTipo,
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
        {'key': 'proposal', 'label': 'Propuesta', 'deadline': program.proposal_deadline, 'state': program.checkpoint_state('proposal')},
    ]
    if program.grants_enabled:
        checkpoints.append({'key': 'grant', 'label': 'Beca', 'deadline': program.grant_deadline, 'state': program.checkpoint_state('grant')})
    checkpoints += [
        {'key': 'guide', 'label': 'Desplegable', 'deadline': program.guide_deadline, 'state': program.checkpoint_state('guide')},
        {'key': 'logistics', 'label': 'Ingreso y desarme', 'deadline': program.logistics_deadline, 'state': program.checkpoint_state('logistics')},
        {'key': 'checkout', 'label': 'Checkout', 'deadline': program.checkout_deadline, 'state': program.checkpoint_state('checkout')},
    ]
    if program.grants_enabled:
        checkpoints.append({'key': 'grant_report', 'label': 'Rendición', 'deadline': program.grant_report_deadline, 'state': program.checkpoint_state('grant_report')})
    return checkpoints


def _accessible_artworks(user):
    if user.is_superuser:
        return Artwork.objects.all()
    collaborator_ids = Artwork.objects.filter(collaborators=user).values('pk')
    managed_events = Event.objects.filter(admins=user).values('pk')
    return Artwork.objects.filter(
        Q(owner=user) | Q(pk__in=collaborator_ids) | Q(event_id__in=managed_events),
    )


def _send_invitations(invitations):
    for invitation in invitations:
        try:
            send_mail(
                template_name='art_collaboration_invitation',
                recipient_list=[invitation.email],
                context={
                    'invitation': invitation,
                    'accept_path': reverse('art_invitation_accept', kwargs={'token': invitation.token}),
                },
            )
        except Exception:
            logging.exception('No se pudo enviar la invitación de Arte a %s', invitation.email)


def _ensure_operations_group(artwork, program):
    if artwork.operations_group_id:
        if artwork.operations_group.nombre != artwork.title:
            artwork.operations_group.nombre = artwork.title or f'Obra #{artwork.pk}'
            artwork.operations_group.save(update_fields=['nombre', 'updated_at'])
        return artwork.operations_group
    if not artwork.owner_id:
        return artwork.operations_group
    art_type, _ = GrupoTipo.objects.get_or_create(
        nombre='ARTE',
        defaults={'descripcion': 'Obras de arte de Fuego Austral'},
    )
    group = Grupo.objects.create(
        event=artwork.event,
        lider=artwork.owner,
        nombre=artwork.title or f'Obra #{artwork.pk}',
        tipo=art_type,
        ingreso_anticipado_amount=program.early_entry_slots,
        ingreso_anticipado_desde=program.early_entry_from,
        late_checkout_amount=program.late_checkout_slots,
        late_checkout_hasta=program.late_checkout_until,
    )
    artwork.operations_group = group
    artwork.save(update_fields=['operations_group', 'updated_at'])
    return group


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
    people = list(artwork.logistics_people.all())
    providers = list(artwork.artwork_providers.prefetch_related('vehicles'))
    for person in people:
        person.inline_form = inline_forms.get(('person', person.pk)) or ArtworkLogisticsPersonForm(
            instance=person, auto_id=f'person-{person.pk}_%s',
        )
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
        'logistics_people': people,
        'person_create_form': inline_forms.get(('person-new', None)) or ArtworkLogisticsPersonForm(
            instance=ArtworkLogisticsPerson(artwork=artwork), auto_id='person-new_%s',
        ),
        'artwork_providers': providers,
        'entry_providers': [provider for provider in providers if provider.for_entry],
        'exit_providers': [provider for provider in providers if provider.for_exit],
        'provider_create_form': inline_forms.get(('provider-new', None)) or ArtworkProviderForm(
            instance=ArtworkProvider(artwork=artwork), auto_id='provider-new_%s',
        ),
        'can_manage': artwork.can_manage(form.actor),
        'can_edit_budget': _grant_item_editable(artwork, ArtworkGrantItem.Phase.BUDGET, form.actor),
        'can_edit_expenses': _grant_item_editable(artwork, ArtworkGrantItem.Phase.EXPENSE, form.actor),
        'can_edit_logistics': _logistics_editable(artwork, form.actor),
        'can_submit_grant': artwork.can_edit(form.actor) and program.is_current and program.checkpoint_state('grant') == 'open' and artwork.grant_status in (Artwork.GrantStatus.NOT_REQUESTED, Artwork.GrantStatus.INFO_REQUIRED),
        'can_submit_report': artwork.can_edit(form.actor) and program.is_current and program.checkpoint_state('grant_report') == 'open' and artwork.grant_status in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID),
    })
    context.update(_grant_context(artwork, inline_forms))
    return context


def _review_context(artwork, form, user, inline_forms=None):
    admin_events = Event.objects.order_by('-id') if user.is_superuser else get_admin_events_for_user(user)
    return {
        **_base_context(artwork.event), **_grant_context(artwork, inline_forms),
        'artwork': artwork, 'form': form, 'current_admin_event': artwork.event,
        'admin_events': admin_events, 'nav_primary': 'events',
        'nav_secondary': f'art_admin_{artwork.event.slug}',
    }


def _inline_error_response(request, artwork, key, inline_form):
    if request.POST.get('return_to') == 'review' and artwork.can_manage(request.user):
        review_form = ArtworkReviewForm(instance=artwork)
        return render(
            request, 'mi_fuego/art/review.html',
            _review_context(artwork, review_form, request.user, {key: inline_form}),
        )
    program = artwork.event.art_program
    artwork_form = ArtworkForm(
        instance=artwork, program=program, owner=artwork.owner, actor=request.user,
    )
    return render(
        request, 'mi_fuego/art/form.html',
        _artwork_context(artwork, program, artwork_form, {key: inline_form}),
    )


def _artwork_redirect(artwork, anchor):
    return redirect(f"{reverse('artwork_edit', args=[artwork.pk])}#{anchor}")


def _grant_redirect(request, artwork, phase):
    if request.POST.get('return_to') == 'review' and artwork.can_manage(request.user):
        anchor = 'admin-budget' if phase == ArtworkGrantItem.Phase.BUDGET else 'admin-expenses'
        return redirect(f"{reverse('artwork_review', args=[artwork.event.slug, artwork.pk])}#{anchor}")
    return _artwork_redirect(artwork, 'beca' if phase == ArtworkGrantItem.Phase.BUDGET else 'rendicion')


@login_required
def art_dashboard(request):
    programs = ArtProgram.objects.select_related('event').filter(is_current=True, event__active=True)
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
    program = get_object_or_404(
        ArtProgram.objects.select_related('event'),
        event__slug=event_slug,
        event__active=True,
        is_current=True,
    )
    artwork = Artwork(event=program.event, owner=request.user)
    action = request.POST.get('action', 'save')
    form = ArtworkForm(
        request.POST or None, request.FILES or None,
        instance=artwork, program=program, owner=request.user, actor=request.user, action=action,
    )
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if action == 'submit':
                form.instance.status = Artwork.Status.SUBMITTED
                form.instance.submitted_at = timezone.now()
            artwork = form.save()
            if action == 'submit':
                _ensure_operations_group(artwork, program)
            transaction.on_commit(lambda invitations=list(form.new_invitations): _send_invitations(invitations))
        messages.success(request, 'La propuesta fue enviada.' if action == 'submit' else 'El borrador quedó guardado.')
        return redirect('artwork_edit', artwork_id=artwork.pk)

    context = _base_context(program.event)
    context.update({'form': form, 'program': program, 'checkpoints': _checkpoints(program), 'is_new': True})
    return render(request, 'mi_fuego/art/form.html', context)


@login_required
def artwork_edit(request, artwork_id):
    access = _accessible_artworks(request.user)
    if request.method == 'POST':
        with transaction.atomic():
            artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
            response = _handle_artwork_edit(request, artwork)
        return response
    artwork = get_object_or_404(access, pk=artwork_id)
    return _handle_artwork_edit(request, artwork)


def _handle_artwork_edit(request, artwork):
    try:
        program = artwork.event.art_program
    except ArtProgram.DoesNotExist as exc:
        raise Http404('El programa de Arte ya no está disponible.') from exc

    action = request.POST.get('action', 'save')
    form = ArtworkForm(
        request.POST or None, request.FILES or None,
        instance=artwork, program=program, owner=artwork.owner, actor=request.user, action=action,
    )
    if request.method == 'POST' and form.is_valid():
        if action == 'submit':
            form.instance.status = Artwork.Status.SUBMITTED
            form.instance.submitted_at = timezone.now()
        if form.cleaned_data.get('checkout_completed') and not artwork.checkout_requested_at:
            form.instance.checkout_requested_at = timezone.now()
        artwork = form.save()
        if action == 'submit' or artwork.operations_group_id:
            _ensure_operations_group(artwork, program)
        transaction.on_commit(lambda invitations=list(form.new_invitations): _send_invitations(invitations))
        messages.success(request, 'La propuesta fue enviada.' if action == 'submit' else 'Los cambios quedaron guardados.')
        return redirect('artwork_edit', artwork_id=artwork.pk)

    return render(request, 'mi_fuego/art/form.html', _artwork_context(artwork, program, form))


def _grant_item_editable(artwork, phase, user):
    if artwork.can_manage(user):
        return True
    program = artwork.event.art_program
    if not program.is_current or not program.grants_enabled:
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
    return program.is_current and program.checkpoint_state('logistics') == 'open' and artwork.can_edit(user)


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
            errors.append('Marcá que querés solicitar una beca y guardá la obra.')
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
    return redirect('artwork_edit', artwork_id=artwork.pk)


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
            return HttpResponseForbidden('La obra no tiene una beca aprobada para rendir.')
        if not artwork.grant_report:
            messages.error(request, 'Completá y guardá el relato de la rendición.')
        elif not artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.EXPENSE).exists():
            messages.error(request, 'Agregá al menos un gasto a la rendición.')
        elif not artwork.photos.filter(stage__in=(ArtworkPhoto.Stage.FINAL, ArtworkPhoto.Stage.GRANT_REPORT)).exists():
            messages.error(request, 'Subí al menos una foto final o de rendición.')
        else:
            artwork.grant_status = Artwork.GrantStatus.REPORTED
            artwork.save(update_fields=['grant_status', 'updated_at'])
            messages.success(request, 'La rendición fue enviada para revisión.')
    return redirect('artwork_edit', artwork_id=artwork.pk)


@login_required
def artwork_photo_upload(request, artwork_id):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'galeria')
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        form = ArtworkPhotoUploadForm(request.POST, request.FILES, auto_id='photo-new_%s')
        if form.is_valid():
            images = form.cleaned_data['images']
            if artwork.photos.count() + len(images) > 100:
                form.add_error('images', 'La galería admite hasta 100 fotos por obra.')
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
def logistics_person_edit(request, artwork_id, person_id=None):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if not _logistics_editable(artwork, request.user):
        return HttpResponseForbidden('La logística de esta obra ya no se puede editar.')
    person = get_object_or_404(ArtworkLogisticsPerson, artwork=artwork, pk=person_id) if person_id else ArtworkLogisticsPerson(artwork=artwork, created_by=request.user)
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'logistica')
    form = ArtworkLogisticsPersonForm(
        request.POST, instance=person,
        auto_id=f'person-{person_id}_%s' if person_id else 'person-new_%s',
    )
    if form.is_valid():
        with transaction.atomic():
            artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
            if not _logistics_editable(artwork, request.user):
                return HttpResponseForbidden('La logística de esta obra ya no se puede editar.')
            form.instance.artwork = artwork
            form.instance.created_by = form.instance.created_by or request.user
            form.save()
        messages.success(request, 'Persona guardada en la logística de la obra.')
        return _artwork_redirect(artwork, 'logistica')
    return _inline_error_response(
        request, artwork, ('person', person.pk) if person_id else ('person-new', None), form,
    )


@login_required
def logistics_person_delete(request, artwork_id, person_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Esta persona no se puede eliminar.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        person = get_object_or_404(ArtworkLogisticsPerson.objects.select_for_update(), artwork=artwork, pk=person_id)
        if not _logistics_editable(artwork, request.user):
            return HttpResponseForbidden('Esta persona no se puede eliminar.')
        if artwork.checkout_team_responsible_id == person.pk and artwork.checkout_completed:
            return HttpResponseForbidden('No se puede eliminar al responsable de un checkout solicitado.')
        person.delete()
    messages.success(request, 'Persona eliminada de la logística.')
    return _artwork_redirect(artwork, 'logistica')


@login_required
def artwork_provider_edit(request, artwork_id, provider_id=None):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if not _logistics_editable(artwork, request.user):
        return HttpResponseForbidden('La logística de esta obra ya no se puede editar.')
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
                return HttpResponseForbidden('La logística de esta obra ya no se puede editar.')
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
        return HttpResponseForbidden('La logística de esta obra ya no se puede editar.')
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
                return HttpResponseForbidden('La logística de esta obra ya no se puede editar.')
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


@login_required
def art_invitation_accept(request, token):
    invitation = get_object_or_404(ArtworkInvitation.objects.select_related('artwork'), token=token)
    verified_email = request.user.email and request.user.email.lower() == invitation.email.lower()
    try:
        verified_email = verified_email and request.user.emailaddress_set.filter(email__iexact=invitation.email, verified=True).exists()
    except AttributeError:
        verified_email = False
    if not invitation.is_pending or not verified_email:
        return HttpResponseForbidden('La invitación venció o no corresponde al email verificado de tu cuenta.')
    if request.method == 'POST':
        invitation.artwork.collaborators.add(request.user)
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=['accepted_at', 'updated_at'])
        messages.success(request, f'Ya colaborás en “{invitation.artwork.title or "esta obra"}”.')
        return redirect('artwork_edit', artwork_id=invitation.artwork_id)
    return render(request, 'mi_fuego/art/invitation.html', {
        **_base_context(invitation.artwork.event), 'invitation': invitation,
    })


def _managed_event(request, event_slug):
    event = get_object_or_404(Event, slug=event_slug)
    if not request.user.is_superuser and not event.admins.filter(pk=request.user.pk).exists():
        return None
    return event


@login_required
def art_admin_dashboard(request, event_slug):
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
    artworks = event.artworks.select_related('owner').prefetch_related('grant_items', 'photos').order_by('status', 'title')
    admin_events = Event.objects.order_by('-id') if request.user.is_superuser else get_admin_events_for_user(request.user)
    return render(request, 'mi_fuego/art/admin_dashboard.html', {
        **_base_context(event), 'artworks': artworks, 'current_admin_event': event,
        'admin_events': admin_events,
        'nav_primary': 'events', 'nav_secondary': f'art_admin_{event.slug}',
    })


@login_required
def artwork_review(request, event_slug, artwork_id):
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
    if request.method == 'POST':
        with transaction.atomic():
            artwork = get_object_or_404(Artwork.objects.select_for_update(), pk=artwork_id, event=event)
            was_verified = bool(artwork.checkout_verified_at)
            form = ArtworkReviewForm(request.POST, instance=artwork)
            if form.is_valid():
                artwork = form.save(commit=False)
                if artwork.checkout_verified_at:
                    artwork.checkout_verified_by = request.user
                    artwork.status = Artwork.Status.COMPLETED
                elif was_verified:
                    artwork.checkout_verified_by = None
                    if artwork.status == Artwork.Status.COMPLETED:
                        artwork.status = Artwork.Status.INSTALLED
                artwork.save()
                messages.success(request, 'La revisión quedó guardada.')
                return redirect('artwork_review', event_slug=event.slug, artwork_id=artwork.pk)
    else:
        artwork = get_object_or_404(Artwork, pk=artwork_id, event=event)
        form = ArtworkReviewForm(instance=artwork)
    return render(request, 'mi_fuego/art/review.html', _review_context(artwork, form, request.user))


@login_required
def art_admin_export(request, event_slug):
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para exportar Arte en este evento.')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="arte_{event.slug}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['Obra', 'Responsable', 'Estado', 'Beca', 'Presupuesto ARS', 'Rendición ARS', 'Título público', 'Descripción pública', 'Ubicación asignada', 'Checkout verificado'])
    for artwork in event.artworks.select_related('owner').prefetch_related('grant_items'):
        writer.writerow([_csv_cell(value) for value in [
            artwork.title,
            artwork.owner.email if artwork.owner else '',
            artwork.get_status_display(),
            artwork.get_grant_status_display(),
            artwork.grant_total_ars(ArtworkGrantItem.Phase.BUDGET),
            artwork.grant_total_ars(ArtworkGrantItem.Phase.EXPENSE),
            artwork.public_title,
            artwork.public_description,
            artwork.assigned_location,
            artwork.checkout_verified_at.isoformat() if artwork.checkout_verified_at else '',
        ]])
    return response


def _csv_cell(value):
    text = '' if value is None else str(value)
    return f"'{text}" if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
