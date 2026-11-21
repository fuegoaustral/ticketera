import csv
import hashlib
import logging
import uuid
from decimal import Decimal
from email.utils import formataddr, parseaddr

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.core import signing
from django.core.mail import get_connection
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Count, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Lower
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.html import strip_tags
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from events.models import Event, Grupo, GrupoMiembro, GrupoTipo
from caja.stock import ticket_types_with_stock_queryset
from templated_email import get_templated_mail
from tickets.models import MessageIdempotency, NewTicket, TicketType
from user_profile.phone import whatsapp_number
from utils.email import send_mail

from . import direct_uploads, history, richtext
from .digest import (
    GRANT_REPORT_SECTION, GRANT_REQUEST_SECTION, record_change, send_grant_application_email,
    send_mad_request_email, send_new_artwork_email,
)
from .estafa import (
    BECAS_SLUG, MAD_SLUG, art_team_name, art_teams, can_access_estafa, can_coordinate, can_manage_grants, can_message_teams, can_view_art,
    estafa_events, estafa_members, is_estafa_member,
)
from .forms import (
    GALLERY_STAGES, ArtworkForm, ArtworkGrantForm, ArtworkGrantItemForm, ArtworkTeamAddForm,
    ArtworkCheckoutPhotoUploadForm, ArtworkFileUploadForm, ArtworkPhotoUploadForm, ArtworkProviderForm, ArtworkTeamBenefitsForm,
    ArtworkProviderVehicleForm, ArtworkContactForm, BulkTeamMessageForm, ArtworkGrantActionForm, ArtworkGrantItemReviewForm, ArtworkReviewForm,
    GRANT_ACTIONS, GRANT_REQUEST_EDITABLE, _localized_amount, grant_actions_for,
)
from .models import (
    ASKS_MAD, GRANT_INCOMPLETE_LABEL, ArtProgram, Artwork, ArtworkEvent, ArtworkFile, ArtworkGrantItem, ArtworkGrantItemPhoto,
    ArtworkPhoto, ArtworkCheckoutPhoto, ArtworkProvider,
    ArtworkProviderVehicle,
)
from .models import grant_incomplete_q, grant_started_q
from .review_sections import review_sections
from .templatetags.art_format import person_greeting, person_label, person_name
from .steps import CLOSED, OPEN, UPCOMING, artwork_steps, next_step


def _base_context(event=None):
    active_events = Event.get_current_events().order_by('-is_main', 'name')
    return {
        'event': event or Event.get_main_event(),
        'active_events': active_events,
        'nav_primary': 'art',
        'now': timezone.now(),
    }


def _steps_context(artwork, program, user, form=None, editable=None):
    """Pasos en el orden de la línea de tiempo: lo cerrado arriba, lo abierto en el medio y lo que viene abajo.

    `editable` dice qué pasos puede editar quien mira; el resto lo lee como texto.
    """
    steps = artwork_steps(artwork, program, form=form)
    if editable is not None:
        for step in steps:
            step.editable = editable.get(step.key, False)
    order = {CLOSED: 0, OPEN: 1, UPCOMING: 2}
    # ESTAFA puede editar fuera de fecha: ve cada paso completo, sin "Lo próximo".
    manager = bool(artwork and artwork.can_manage(user))
    editor = not artwork or artwork.can_edit(user)
    return {
        'steps': sorted(steps, key=lambda step: order[step.state]),
        # En una instalación nueva hay un solo paso: "Lo próximo" no suma nada.
        'next_step': next_step(steps) if artwork and editor and not manager else None,
        'expand_all_steps': manager,
    }


def _accessible_artworks(user, grant=False):
    """Las propuestas que la persona abre como equipo. Con grant=True, las de la pantalla de la beca:
    ahí también entran Becas y MAD."""
    if user.is_superuser:
        return Artwork.objects.all()
    team_ids = Artwork.objects.filter(
        Q(collaborators=user) | Q(operations_group__miembros__user=user),
    ).values('pk')
    access = Q(owner=user) | Q(pk__in=team_ids)
    if is_estafa_member(user) or (grant and art_teams(user)):
        access |= Q(event__has_volunteers=True)
    return Artwork.objects.filter(access)


def _send_team_member_added_email(artwork, user, added_by):
    if not user.email:
        return
    outlook = _ticket_outlook(artwork.event)
    try:
        send_mail(
            template_name='art_team_member_added',
            recipient_list=[user.email],
            context={
                'artwork': artwork,
                'added_by_name': person_name(added_by),
                'can_edit': artwork.can_edit(user),
                'ticket_outlook': outlook,
                'missing_ticket': outlook is not None and not _has_ticket(user, artwork.event),
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
                'reopened': artwork.status == Artwork.Status.PENDING,
                'checkout_verified': artwork.status == Artwork.Status.CHECKOUT_VERIFIED,
                'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
            },
        )
    except Exception:
        logging.exception('No se pudo avisar el cambio de estado de la instalación %s', artwork.pk)


def _send_grant_decision_email(artwork):
    recipients = _team_emails(artwork)
    if not recipients:
        return
    try:
        send_mail(
            template_name='art_grant_decision',
            recipient_list=recipients,
            context={'artwork': artwork, 'grant_path': reverse('artwork_grant', args=[artwork.pk])},
        )
    except Exception:
        logging.exception('No se pudo avisar la decisión de la beca de la instalación %s', artwork.pk)


def _team_message_context(artwork, sender, message):
    return {
        'artwork': artwork,
        'sender_name': person_name(sender),
        'sender_team': art_team_name(sender) or 'ESTAFA',
        'message': message,
        'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
    }


def _team_message_headers(sender):
    return {'Reply-To': formataddr((person_greeting(sender), sender.email))} if sender.email else None


def _send_team_message_email(artwork, sender, message):
    recipients = _team_emails(artwork)
    if not recipients:
        return
    try:
        send_mail(
            template_name='art_team_message',
            recipient_list=recipients,
            context=_team_message_context(artwork, sender, message),
            headers=_team_message_headers(sender),
        )
    except Exception:
        logging.exception('No se pudo enviar el mensaje de ESTAFA de la instalación %s', artwork.pk)


def _send_artwork_received_email(artwork):
    """Confirma a quien inscribe que la propuesta nueva llegó; solo al crearla."""
    if not artwork.owner or not artwork.owner.email:
        return
    try:
        send_mail(
            template_name='art_artwork_received',
            recipient_list=[artwork.owner.email],
            context={'artwork': artwork, 'artwork_path': reverse('artwork_edit', args=[artwork.pk])},
        )
    except Exception:
        logging.exception('No se pudo confirmar la inscripción de la instalación %s', artwork.pk)


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
                'contact_name': person_name(contact),
                'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
            },
            headers={'Reply-To': formataddr((person_greeting(contact), contact.email))} if contact.email else None,
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
            artwork.operations_group.nombre = artwork.title or f'Propuesta de Arte #{artwork.pk}'
            artwork.operations_group.save(update_fields=['nombre', 'updated_at'])
        return artwork.operations_group
    if not artwork.owner_id:
        return artwork.operations_group
    art_type, _ = GrupoTipo.objects.get_or_create(
        nombre='ARTE',
        defaults={'descripcion': 'Propuestas de Arte de Fuego Austral'},
    )
    group = Grupo.objects.create(
        event=artwork.event,
        lider=artwork.owner,
        nombre=artwork.title or f'Propuesta de Arte #{artwork.pk}',
        tipo=art_type,
        ingreso_anticipado_amount=program.early_entry_slots,
        ingreso_anticipado_desde=program.early_entry_from,
        late_checkout_amount=program.late_checkout_slots,
        late_checkout_hasta=program.late_checkout_until,
    )
    artwork.operations_group = group
    artwork.save(update_fields=['operations_group', 'updated_at'])
    return group


def _ticket_outlook(event):
    """En qué está la venta de bonos, para avisarle a quien todavía no tiene el suyo.

    Devuelve None si el evento todavía no tiene ninguna venta cargada.
    """
    now = timezone.now()
    sale_types = TicketType.objects.filter(event=event, is_direct_type=False)
    if not sale_types.exists():
        return None
    in_stock = ticket_types_with_stock_queryset(sale_types)
    if event.tickets_remaining() > 0:
        if in_stock.filter(
            Q(date_from__isnull=True) | Q(date_from__lte=now),
            Q(date_to__isnull=True) | Q(date_to__gte=now),
        ).exists():
            return {'state': 'on_sale'}
        upcoming = in_stock.filter(date_from__gt=now).order_by('date_from').first()
        if upcoming:
            return {'state': 'upcoming', 'date': upcoming.date_from}
    if not sale_types.filter(Q(date_from__isnull=True) | Q(date_from__lte=now)).exists():
        return None
    if event.transfer_period():
        return {'state': 'transfer_only', 'date': event.transfers_enabled_until}
    return {'state': 'closed'}


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
    outlook = _ticket_outlook(artwork.event)
    ticket_holders = set(
        NewTicket.objects.filter(
            event=artwork.event, holder__in=[member.user for member in members], owner=F('holder'),
        ).values_list('holder_id', flat=True)
    ) if outlook else set()
    for member in members:
        member.is_leader = member.user_id == artwork.owner_id
        member.is_self = member.user_id == user.pk
        member.can_edit = member.is_leader or member.user_id in editor_ids
        member.missing_ticket = outlook is not None and member.user_id not in ticket_holders
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
        'ticket_outlook': outlook,
    }


def _benefits_form(artwork, data=None):
    """Ingreso anticipado y late checkout por persona; sin equipo creado, no hay tabla."""
    if not artwork.operations_group_id:
        return None
    members = list(artwork.team_members().select_related('user').order_by('user__first_name', 'user__last_name', 'user__email'))
    members.sort(key=lambda member: member.user_id != artwork.owner_id)
    ticket_holders = set(
        NewTicket.objects.filter(
            event=artwork.event, holder__in=[member.user for member in members], owner=F('holder'),
        ).values_list('holder_id', flat=True)
    )
    return ArtworkTeamBenefitsForm(data, artwork=artwork, members=members, ticket_holders=ticket_holders, auto_id='benefits_%s')


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
        'budget_total': sum((item.amount for item in budget), Decimal('0.00')),
        # Lo que cubre la beca se muestra aparte sólo si el presupuesto tiene algo que no cubre.
        'budget_fundable_total': sum((item.amount for item in budget if item.is_fundable), Decimal('0.00')),
        'budget_has_unfundable': any(not item.is_fundable for item in budget),
        'expense_total': sum((item.amount for item in expenses), Decimal('0.00')),
        'grant_over_budget': bool(
            artwork.grant_approved_amount
            and sum((item.amount for item in expenses), Decimal('0.00')) > artwork.grant_approved_amount
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


def _summary_context(artwork, form):
    """Lo que leen los resúmenes de cada paso, en la instalación y en ESTAFA."""
    providers = list(artwork.artwork_providers.prefetch_related('vehicles'))
    for provider in providers:
        provider.inline_vehicles = list(provider.vehicles.all())
    return {
        # Un formulario que volvió con errores dejó sus valores en la instalación: el texto muestra lo guardado.
        'summary_artwork': Artwork.objects.get(pk=artwork.pk) if form.is_bound and form.errors else artwork,
        'artwork_files': artwork.files.all(),
        'gallery_photos': artwork.photos.filter(stage__in=GALLERY_STAGES),
        'checkout_photos': artwork.checkout_photos.all(),
        'artwork_providers': providers,
    }


def _editable_steps(form, permissions):
    """Qué pasos puede editar quien mira: los que tienen algún campo habilitado o algo para subir o cargar."""
    def fields_open(block):
        return any(not form.fields[name].disabled for name in form.BLOCK_FIELDS[block] if name in form.fields)

    return {
        'detalles': fields_open('proposal') or permissions['can_edit_files'],
        'equipo': permissions['can_edit_team'],
        'desplegable': fields_open('guide'),
        'carta': fields_open('understanding_letter_digital'),
        'ingreso': permissions['can_edit_logistics'],
        'galeria': permissions['can_edit_gallery'],
        'checkout': fields_open('checkout') or permissions['can_edit_checkout'] or permissions['can_submit_checkout'],
    }


def _artwork_context(artwork, program, form, inline_forms=None):
    inline_forms = inline_forms or {}
    context = _base_context(artwork.event)
    summary = _summary_context(artwork, form)
    for provider in summary['artwork_providers']:
        provider.inline_form = inline_forms.get(('provider', provider.pk)) or ArtworkProviderForm(
            instance=provider, auto_id=f'provider-{provider.pk}_%s',
        )
        provider.vehicle_create_form = inline_forms.get(('vehicle-new', provider.pk)) or ArtworkProviderVehicleForm(
            instance=ArtworkProviderVehicle(provider=provider), auto_id=f'vehicle-{provider.pk}-new_%s',
        )
        for vehicle in provider.inline_vehicles:
            vehicle.inline_form = inline_forms.get(('vehicle', vehicle.pk)) or ArtworkProviderVehicleForm(
                instance=vehicle, auto_id=f'vehicle-{vehicle.pk}_%s',
            )
    providers = summary['artwork_providers']
    permissions = {
        'can_edit_files': _files_editable(artwork, form.actor),
        'can_edit_gallery': _gallery_editable(artwork, form.actor),
        'can_edit_logistics': _logistics_editable(artwork, form.actor),
        'can_edit_checkout': _checkout_editable(artwork, form.actor),
        'can_submit_checkout': _can_submit_checkout(artwork, program, form.actor),
        'can_edit_team': _team_editable(artwork, form.actor),
    }
    context.update(summary)
    context.update(permissions)
    context.update({
        'form': form,
        'program': program,
        'artwork': artwork,
        **_steps_context(artwork, program, form.actor, form, _editable_steps(form, permissions)),
        'file_upload_form': inline_forms.get(('file-new', None)) or ArtworkFileUploadForm(auto_id='file-new_%s'),
        'direct_file_uploads': direct_uploads.s3_storage() is not None,
        'photo_upload_form': inline_forms.get(('photo-new', None)) or ArtworkPhotoUploadForm(auto_id='photo-new_%s'),
        'checkout_photo_upload_form': inline_forms.get(('checkout-photo-new', None)) or ArtworkCheckoutPhotoUploadForm(
            auto_id='checkout-photo-new_%s',
        ),
        'entry_providers': [provider for provider in providers if provider.for_entry],
        'exit_providers': [provider for provider in providers if provider.for_exit],
        'provider_create_form': inline_forms.get(('provider-new', None)) or ArtworkProviderForm(
            instance=ArtworkProvider(artwork=artwork), auto_id='provider-new_%s',
        ),
        'can_manage': artwork.can_manage(form.actor),
        'can_edit_artwork': artwork.can_edit(form.actor) or artwork.can_manage(form.actor),
        'grant_started': artwork.grant_started,
        'benefits_form': inline_forms.get(('benefits', None)) or _benefits_form(artwork),
    })
    # ESTAFA editando la instalación de otra persona: navega dentro de ESTAFA y auditlog registra quién guarda.
    if context['can_manage'] and not artwork.can_edit(form.actor):
        context.update(_estafa_context(form.actor, artwork.event), acting_as_estafa=True)
    context.update(_team_context(artwork, form.actor, inline_forms.get(('team-add', None))))
    return context


def _mad_only(user):
    """Es de MAD y de ningún otro equipo de Arte: ve la parte de MAD, los mensajes y la propuesta."""
    return not user.is_superuser and art_teams(user) == {MAD_SLUG}


def _estafa_landing(user):
    """La pestaña con la que cada quien entra: Becas, a las becas; MAD, a MAD; el resto, a las propuestas."""
    teams = art_teams(user)
    if user.is_superuser or is_estafa_member(user):
        return 'art_admin_dashboard'
    if BECAS_SLUG in teams:
        return 'art_grants_dashboard'
    return 'art_mad_dashboard' if MAD_SLUG in teams else 'art_admin_dashboard'


def _estafa_context(user, event, tab='artworks'):
    return {
        **_base_context(event), 'nav_primary': 'estafa', 'art_team_name': art_team_name(user),
        'estafa_events': estafa_events(user), 'current_estafa_event': event,
        'estafa_landing': _estafa_landing(user), 'estafa_tab': tab,
        'can_message_teams': bool(event and can_message_teams(user, event)),
    }


def _review_context(artwork, form, user, inline_forms=None):
    inline_forms = inline_forms or {}
    sections = review_sections(artwork, artwork.event.art_program)
    checkout_upload_form = inline_forms.get(('checkout-photo-new', None))
    for section in sections:
        section.fields = [form[name] for name in section.estafa_fields if name in form.fields]
        section.open = any(bound.errors for bound in section.fields) or (
            section.key == 'checkout' and checkout_upload_form is not None
        )
    team = _team_context(artwork, user)
    return {
        **_estafa_context(user, artwork.event),
        # Lo que cargó el equipo se lee con los mismos resúmenes que ve el equipo.
        **_summary_context(artwork, form),
        'team_members': team['team_members'],
        'ticket_outlook': team['ticket_outlook'],
        'artwork': artwork, 'form': form, 'sections': sections,
        'history': history.timeline(artwork, artwork.event.art_program),
        'news_count': 0,
        'contact_form': ArtworkContactForm(instance=artwork, auto_id='contact_%s'),
        'can_manage': artwork.can_manage(user), 'can_message': artwork.can_message(user),
        'show_grant': artwork.event.art_program.grants_enabled or artwork.grant_started,
        'checkout_photo_upload_form': inline_forms.get(('checkout-photo-new', None)) or ArtworkCheckoutPhotoUploadForm(
            auto_id='checkout-photo-new_%s',
        ),
    }


# Los formularios en línea que viven en la pantalla de la beca.
GRANT_INLINE_KEYS = ('grant', 'grant-new', 'report-photo-new', 'grant-decision')


def _inline_error_response(request, artwork, key, inline_form):
    if request.POST.get('return_to') == 'review' and artwork.can_manage(request.user):
        review_form = ArtworkReviewForm(instance=artwork, can_manage=artwork.can_manage(request.user))
        return render(
            request, 'art/review.html',
            _review_context(artwork, review_form, request.user, {key: inline_form}),
        )
    program = artwork.event.art_program
    if key[0] in GRANT_INLINE_KEYS:
        grant_form = ArtworkGrantForm(instance=artwork, program=program, actor=request.user)
        return render(
            request, 'art/grant.html',
            _grant_page_context(artwork, program, grant_form, request.user, {key: inline_form}),
        )
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


def _grant_page_redirect(artwork, anchor):
    return redirect(f"{reverse('artwork_grant', args=[artwork.pk])}#{anchor}")


def _grant_redirect(request, artwork, phase):
    if request.POST.get('return_to') == 'review' and artwork.can_manage(request.user):
        anchor = 'admin-budget' if phase == ArtworkGrantItem.Phase.BUDGET else 'admin-expenses'
        return redirect(f"{reverse('artwork_review', args=[artwork.event.slug, artwork.pk])}#{anchor}")
    return _grant_page_redirect(artwork, 'solicitud' if phase == ArtworkGrantItem.Phase.BUDGET else 'rendicion')


def _names_sentence(names):
    """«Ana, Beto y Caro»: los emails no se publican, así que quien no tiene nombre no aparece."""
    names = list(names)
    return ' y '.join([', '.join(names[:-1]), names[-1]]) if len(names) > 1 else ''.join(names)


@login_required
def art_dashboard(request):
    # La convocatoria abre meses antes de que el evento se active para vender bonos: alcanza con que esté vigente.
    programs = ArtProgram.objects.select_related('event').filter(is_current=True)
    artworks = (
        Artwork.objects.filter(
            Q(owner=request.user) | Q(collaborators=request.user) | Q(operations_group__miembros__user=request.user),
        )
        .select_related('event', 'estafa_contact')
        .prefetch_related('collaborators')
        .distinct()
    )
    context = _base_context()
    context.update({
        'programs': programs, 'artworks': artworks,
        'estafa_team': _names_sentence(
            person_name(member) for member in estafa_members().select_related('profile').order_by('first_name', 'last_name')
            if person_name(member) != member.email
        ),
    })
    return render(request, 'art/dashboard.html', context)


def _parse_creation_token(value):
    try:
        return uuid.UUID(str(value or ''))
    except ValueError:
        return None


def _artwork_created_redirect(request, artwork, program):
    messages.success(
        request,
        'La propuesta de Arte quedó inscripta. Queda pendiente de aprobación por ESTAFA. '
        'Podés seguir modificándola libremente a medida que la instalación avance.',
    )
    if request.POST.get('action') == 'beca' and program.grants_enabled:
        return redirect('artwork_grant', artwork_id=artwork.pk)
    return redirect('artwork_edit', artwork_id=artwork.pk)


@login_required
def artwork_create(request, event_slug):
    """Blank dossier: the artwork only exists after the first valid save."""
    program = get_object_or_404(
        ArtProgram.objects.select_related('event'),
        event__slug=event_slug,
        is_current=True,
    )
    if not program.registration_is_open():
        messages.error(request, 'La inscripción de propuestas de Arte está cerrada.')
        return redirect('art_dashboard')

    # Cada carga del formulario lleva un token de un solo uso. Si el mismo envío llega otra vez (doble
    # toque mientras el primero tarda), se vuelve a la obra ya creada en lugar de crear otra.
    creation_token = _parse_creation_token(request.POST.get('creation_token')) if request.method == 'POST' else None
    if creation_token:
        existing = Artwork.objects.filter(creation_token=creation_token).first()
        if existing and existing.owner_id == request.user.pk:
            return _artwork_created_redirect(request, existing, program)
        if existing:
            creation_token = None

    form = ArtworkForm(
        request.POST or None, request.FILES or None,
        instance=Artwork(event=program.event, owner=request.user, kind=Artwork.Kind.PLANNED),
        program=program, owner=request.user, actor=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                form.instance.submitted_at = timezone.now()
                form.instance.creation_token = creation_token
                artwork = form.save()
                _ensure_operations_group(artwork, program)
                history.log(artwork, history.STATUS, request.user, 'Inscribió la propuesta')
                transaction.on_commit(lambda: _send_artwork_received_email(artwork))
                transaction.on_commit(lambda: send_new_artwork_email(artwork))
        except IntegrityError:
            # Dos envíos simultáneos con el mismo token: el otro ya la creó (y mandó los emails). Los
            # on_commit de este bloque se descartan con el rollback.
            existing = creation_token and Artwork.objects.filter(
                creation_token=creation_token, owner=request.user,
            ).first()
            if not existing:
                raise
            artwork = existing
        return _artwork_created_redirect(request, artwork, program)

    context = _base_context(program.event)
    context.update({
        'form': form,
        'program': program,
        'artwork': None,
        'can_edit_artwork': True,
        'creation_token': creation_token or uuid.uuid4(),
        **_steps_context(None, program, request.user, form),
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
    asked_mad = artwork.asks_mad
    form = ArtworkForm(
        request.POST or None, request.FILES or None,
        instance=artwork, program=program, owner=artwork.owner, actor=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        if not form.instance.submitted_at:
            form.instance.submitted_at = timezone.now()
        changed_sections = _changed_sections(form)
        artwork = form.save()
        if artwork.asks_mad and not asked_mad:
            transaction.on_commit(lambda: send_mad_request_email(artwork, request.user))
        for section, fields in changed_sections:
            record_change(artwork, section, request.user, fields=fields)
        _ensure_operations_group(artwork, program)
        if action == 'checkout':
            _submit_checkout(request, artwork, program)
            return _artwork_redirect(artwork, 'checkout')
        messages.success(request, 'Cambios guardados.')
        if action == 'beca' and program.grants_enabled:
            return redirect('artwork_grant', artwork_id=artwork.pk)
        # Guardar cierra el formulario y vuelve a la pantalla de la que se vino.
        if artwork.can_manage(request.user) and not artwork.can_edit(request.user):
            return redirect('artwork_review', artwork.event.slug, artwork.pk)
        return redirect('art_dashboard')

    return render(request, 'art/form.html', _artwork_context(artwork, program, form))


# Cómo se llama cada bloque de los formularios en el resumen de cambios para ESTAFA.
CHANGE_SECTIONS = {
    'proposal': 'Detalles',
    'guide': 'Folleto',
    'checkout': 'Checkout',
    'understanding_letter_digital': 'Declaración',
    'grant': 'Beca',
    'grant_report': 'Rendición de beca',
}


def _changed_sections(form):
    """Los bloques del formulario con algún campo que de verdad cambió, cada uno con los nombres de esos campos."""
    changed = set(form.changed_data) - {'expected_version'}
    return [
        (CHANGE_SECTIONS[block], _field_labels(form, [name for name in fields if name in changed]))
        for block, fields in form.BLOCK_FIELDS.items() if changed.intersection(fields)
    ]


def _field_labels(form, names):
    return [strip_tags(str(form.fields[name].label or name)) for name in names if name in form.fields]


def _grant_section(phase):
    return GRANT_REQUEST_SECTION if phase == ArtworkGrantItem.Phase.BUDGET else GRANT_REPORT_SECTION


def _can_submit_checkout(artwork, program, user):
    return (
        artwork.can_edit(user) and program.is_current and program.checkpoint_state('checkout') == 'open'
        and artwork.status == Artwork.Status.ACTIVE
    )


def _submit_checkout(request, artwork, program):
    if not _can_submit_checkout(artwork, program, request.user):
        messages.error(request, 'Esta propuesta no tiene un checkout pendiente.')
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
        record_change(artwork, 'Checkout', request.user)
        history.log(artwork, history.STATUS, request.user, 'Envió el checkout')
        messages.success(request, 'El checkout fue enviado. El equipo de Arte lo va a verificar.')


def _grant_item_editable(artwork, phase, user):
    if phase == ArtworkGrantItem.Phase.EXPENSE and artwork.grant_status == Artwork.GrantStatus.CLOSED:
        return False
    block = 'grant' if phase == ArtworkGrantItem.Phase.BUDGET else 'grant_report'
    if artwork.event.art_program.checkpoint_state(block) == 'upcoming':
        return False
    if artwork.can_manage_grant(user):
        return True
    if not artwork.can_edit(user):
        return False
    program = artwork.event.art_program
    if not program.is_current or not program.grants_enabled or artwork.status == Artwork.Status.REJECTED:
        return False
    if phase == ArtworkGrantItem.Phase.BUDGET:
        return program.checkpoint_state('grant') == 'open' and artwork.grant_status in GRANT_REQUEST_EDITABLE
    return program.checkpoint_state('grant_report') == 'open' and artwork.grant_status in (
        Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID,
    )


def _logistics_editable(artwork, user):
    program = artwork.event.art_program
    if program.checkpoint_state('logistics') == 'upcoming':
        return False
    if artwork.can_manage(user):
        return True
    return (
        program.is_current and program.checkpoint_state('logistics') == 'open' and artwork.can_edit(user)
        and artwork.status != Artwork.Status.REJECTED
    )


def _checkout_editable(artwork, user):
    program = artwork.event.art_program
    if program.checkpoint_state('checkout') == 'upcoming':
        return False
    if artwork.can_manage(user):
        return True
    return (
        program.is_current and program.checkpoint_state('checkout') == 'open' and artwork.can_edit(user)
        and artwork.status in (Artwork.Status.ACTIVE, Artwork.Status.CHECKOUT_SUBMITTED)
    )


def _files_editable(artwork, user):
    """Los archivos de la propuesta siguen el plazo de Detalles."""
    program = artwork.event.art_program
    if program.checkpoint_state('proposal') == 'upcoming':
        return False
    if artwork.can_manage(user):
        return True
    return (
        program.is_current and program.checkpoint_state('proposal') == 'open' and artwork.can_edit(user)
        and artwork.status != Artwork.Status.REJECTED
    )


def _gallery_editable(artwork, user):
    """La galería de proceso y de la instalación terminada se habilita cuando empieza el evento."""
    program = artwork.event.art_program
    if program.checkpoint_state('gallery') == 'upcoming':
        return False
    if artwork.can_manage(user):
        return True
    return (
        program.is_current and program.checkpoint_state('gallery') == 'open' and artwork.can_edit(user)
        and artwork.status != Artwork.Status.REJECTED
    )


def _save_grant_item_images(item, images, user):
    for image in images:
        ArtworkGrantItemPhoto.objects.create(item=item, image=image, uploaded_by=user)


def _grant_page_context(artwork, program, form, user, inline_forms=None):
    inline_forms = inline_forms or {}
    context = _base_context(artwork.event)
    context.update({
        'form': form,
        'program': program,
        'artwork': artwork,
        'can_manage': artwork.can_manage_grant(user),
        'can_edit_budget': _grant_item_editable(artwork, ArtworkGrantItem.Phase.BUDGET, user),
        'can_edit_expenses': _grant_item_editable(artwork, ArtworkGrantItem.Phase.EXPENSE, user),
        # El aviso de «sin presentar» o «presentada» es para el equipo, mientras la beca no se decide.
        'show_grant_notice': artwork.can_edit(user) and artwork.grant_status in (
            Artwork.GrantStatus.NOT_REQUESTED, Artwork.GrantStatus.PENDING,
        ),
        'can_submit_report': artwork.can_edit(user) and program.is_current and program.checkpoint_state('grant_report') == 'open' and artwork.grant_status in (Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID),
        'report_opened': artwork.grant_status in (
            Artwork.GrantStatus.APPROVED, Artwork.GrantStatus.PAID, Artwork.GrantStatus.REPORTED, Artwork.GrantStatus.CLOSED,
        ),
        'grant_missing': _grant_missing(artwork),
        'report_photos': artwork.photos.filter(stage=ArtworkPhoto.Stage.GRANT_REPORT),
        'report_photo_upload_form': inline_forms.get(('report-photo-new', None)) or ArtworkPhotoUploadForm(
            stages=(ArtworkPhoto.Stage.GRANT_REPORT,), auto_id='report-photo-new_%s',
        ),
    })
    if artwork.can_view_all(user) and not artwork.can_edit(user):
        context.update(_estafa_context(user, artwork.event, tab='grants'), acting_as_estafa=True)
    if context['can_manage'] and artwork.grant_status != Artwork.GrantStatus.NOT_REQUESTED:
        context['grant_actions'] = [
            {
                'key': key, **GRANT_ACTIONS[key], 'open': ('grant-decision', key) in inline_forms,
                'form': inline_forms.get(('grant-decision', key)) or ArtworkGrantActionForm(
                    artwork=artwork, action=key, auto_id=f'grant-{key}_%s',
                ),
            }
            for key in grant_actions_for(artwork)
        ]
    context['can_message'] = context.get('acting_as_estafa', False) and artwork.can_message(user)
    context.update(_grant_context(artwork, inline_forms))
    return context


@login_required
def artwork_grant(request, artwork_id):
    """La beca, fuera de la inscripción: solicitud con presupuesto y, si se aprueba, la rendición."""
    if request.method == 'POST':
        with transaction.atomic():
            artwork = get_object_or_404(_accessible_artworks(request.user, grant=True).select_for_update(), pk=artwork_id)
            if not artwork.can_edit(request.user) and not artwork.can_manage_grant(request.user):
                raise Http404
            return _handle_artwork_grant(request, artwork)
    artwork = get_object_or_404(_accessible_artworks(request.user, grant=True), pk=artwork_id)
    return _handle_artwork_grant(request, artwork)


def _handle_artwork_grant(request, artwork):
    try:
        program = artwork.event.art_program
    except ArtProgram.DoesNotExist as exc:
        raise Http404('El programa de Arte ya no está disponible.') from exc
    if not program.grants_enabled and not artwork.can_manage_grant(request.user):
        raise Http404('Esta convocatoria no tiene becas.')
    form = ArtworkGrantForm(request.POST or None, instance=artwork, program=program, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        changed_sections = _changed_sections(form)
        form.save()
        for section, fields in changed_sections:
            record_change(artwork, section, request.user, fields=fields)
        if not _sync_grant_presentation(request, artwork):
            messages.success(request, 'Cambios guardados.')
        return redirect('artwork_grant', artwork_id=artwork.pk)
    return render(request, 'art/grant.html', _grant_page_context(artwork, program, form, request.user))


@login_required
def grant_item_create(request, artwork_id, phase):
    access = _accessible_artworks(request.user, grant=True)
    artwork = get_object_or_404(access, pk=artwork_id)
    if phase not in ArtworkGrantItem.Phase.values or not _grant_item_editable(artwork, phase, request.user):
        return HttpResponseForbidden('Este bloque ya no se puede editar.')
    if request.method != 'POST':
        return _grant_page_redirect(artwork, 'solicitud' if phase == ArtworkGrantItem.Phase.BUDGET else 'rendicion')
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
            record_change(artwork, _grant_section(phase), request.user)
            messages.success(request, 'Ítem agregado y total actualizado.')
            _sync_grant_presentation(request, artwork)
            return _grant_redirect(request, artwork, phase)
    return _inline_error_response(request, artwork, ('grant-new', phase), form)


@login_required
def grant_item_edit(request, artwork_id, item_id):
    access = _accessible_artworks(request.user, grant=True)
    artwork = get_object_or_404(access, pk=artwork_id)
    item = get_object_or_404(ArtworkGrantItem, pk=item_id, artwork=artwork)
    if not _grant_item_editable(artwork, item.phase, request.user):
        return HttpResponseForbidden('Este bloque ya no se puede editar.')
    if request.method != 'POST':
        return _grant_page_redirect(artwork, 'solicitud' if item.phase == ArtworkGrantItem.Phase.BUDGET else 'rendicion')
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
            changed = set(form.changed_data) - {'expected_updated_at'}
            item = form.save()
            _save_grant_item_images(item, form.cleaned_data['images'], request.user)
            if changed:
                record_change(artwork, _grant_section(item.phase), request.user)
            messages.success(request, 'Ítem actualizado.')
            _sync_grant_presentation(request, artwork)
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
        if not artwork.can_manage_grant(request.user):
            return HttpResponseForbidden('No tenés permisos para revisar becas en este evento.')
        item = get_object_or_404(ArtworkGrantItem.objects.select_for_update(), pk=item_id, artwork=artwork)
        form = ArtworkGrantItemReviewForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            history.log_edit(artwork, 'Revisión de beca', request.user, [item.concept], kind=history.ESTAFA, important=False)
            messages.success(request, f'Revisión de “{item.concept}” guardada.')
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
    return redirect(f"{reverse('artwork_review', args=[event_slug, artwork_id])}#{'admin-budget' if item.phase == ArtworkGrantItem.Phase.BUDGET else 'admin-expenses'}")


@login_required
@require_POST
def artwork_grant_decision(request, event_slug, artwork_id):
    """Una acción de Becas o ESTAFA sobre la beca (aprobar, pedir información, registrar el pago…)."""
    action = request.POST.get('action', '')
    with transaction.atomic():
        artwork = get_object_or_404(Artwork.objects.select_for_update(), pk=artwork_id, event__slug=event_slug)
        if not artwork.can_manage_grant(request.user):
            return HttpResponseForbidden('No tenés permisos para gestionar becas en este evento.')
        if action not in grant_actions_for(artwork):
            messages.error(request, 'La beca cambió de estado mientras la revisabas. Revisá su estado actual.')
            return _grant_page_redirect(artwork, 'decision')
        form = ArtworkGrantActionForm(request.POST, artwork=artwork, action=action, auto_id=f'grant-{action}_%s')
        if not form.is_valid():
            return _inline_error_response(request, artwork, ('grant-decision', action), form)
        summary, notify = _apply_grant_action(artwork, action, form.cleaned_data)
        history.log(artwork, history.ESTAFA, request.user, summary, detail=form.cleaned_data.get('notes', ''))
        if notify:
            transaction.on_commit(lambda: _send_grant_decision_email(artwork))
        messages.success(request, f'La beca quedó como «{artwork.get_grant_status_display()}».' + (
            ' Le avisamos al equipo por email.' if notify else ''
        ))
    return _grant_page_redirect(artwork, 'decision')


def _apply_grant_action(artwork, action, data):
    """Aplica la acción y devuelve la línea del historial y si hay que avisarle al equipo."""
    status = Artwork.GrantStatus
    previous = (artwork.grant_status, artwork.grant_approved_amount, artwork.grant_decision_notes, artwork.grant_paid_at)
    target = GRANT_ACTIONS[action]['to']
    if 'amount' in data:
        artwork.grant_approved_amount = data['amount']
    if 'notes' in data:
        artwork.grant_decision_notes = data['notes']
    if 'paid_at' in data:
        artwork.grant_paid_at = data['paid_at']
        artwork.grant_payment_reference = data.get('reference', '')
    if action == 'back_to_pending':
        artwork.grant_approved_amount = None
    if action == 'request_report_changes':
        # El equipo vuelve a editar la rendición en el estado en que la envió.
        target = status.PAID if artwork.grant_paid_at else status.APPROVED
    if target:
        artwork.grant_status = target
    if artwork.grant_status != previous[0]:
        artwork.grant_decided_at = timezone.now()
    artwork.version += 1
    artwork.save()
    money = f'{artwork.grant_currency} {_localized_amount(artwork.grant_approved_amount)}' if artwork.grant_approved_amount else ''
    paid = artwork.grant_paid_at.strftime('%d/%m/%Y') if artwork.grant_paid_at else ''
    summary = {
        'approve': f'Aprobó la beca por {money}',
        'request_info': 'Pidió más información sobre la beca',
        'reject': 'No aprobó la beca',
        'pay': f'Registró el pago de la beca del {paid}',
        'change_amount': f'Cambió el monto aprobado de la beca a {money}',
        'back_to_pending': 'Volvió la beca a pendiente',
        'reopen': 'Reabrió la solicitud de beca',
        'fix_payment': f'Corrigió el pago de la beca: {paid}',
        'approve_report': 'Aprobó la rendición de la beca',
        'request_report_changes': 'Pidió cambios en la rendición de la beca',
        'reopen_report': 'Reabrió la rendición de la beca',
    }[action]
    # Al equipo le avisamos cuando cambia algo de lo que ve: no por la referencia interna del pago.
    notify = previous != (artwork.grant_status, artwork.grant_approved_amount, artwork.grant_decision_notes, artwork.grant_paid_at)
    return summary, notify


@login_required
def grant_item_delete(request, artwork_id, item_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Este ítem no se puede eliminar.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user, grant=True).select_for_update(), pk=artwork_id)
        item = get_object_or_404(ArtworkGrantItem.objects.select_for_update(), pk=item_id, artwork=artwork)
        if not _grant_item_editable(artwork, item.phase, request.user):
            return HttpResponseForbidden('Este ítem no se puede eliminar.')
        item.delete()
        record_change(artwork, _grant_section(item.phase), request.user)
        messages.success(request, 'Ítem eliminado.')
        _sync_grant_presentation(request, artwork)
    return _grant_redirect(request, artwork, item.phase)


@login_required
def grant_item_photo_delete(request, artwork_id, item_id, photo_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Esta imagen no se puede eliminar.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user, grant=True).select_for_update(), pk=artwork_id)
        item = get_object_or_404(ArtworkGrantItem.objects.select_for_update(), pk=item_id, artwork=artwork)
        photo = get_object_or_404(ArtworkGrantItemPhoto.objects.select_for_update(), pk=photo_id, item=item)
        if not _grant_item_editable(artwork, item.phase, request.user):
            return HttpResponseForbidden('Esta imagen no se puede eliminar.')
        storage, image_name = photo.image.storage, photo.image.name
        photo.delete()
        transaction.on_commit(lambda: storage.delete(image_name))
        record_change(artwork, _grant_section(item.phase), request.user)
    messages.success(request, 'Imagen eliminada del ítem.')
    return _grant_redirect(request, artwork, item.phase)


def _grant_missing(artwork):
    """Lo que le falta a la solicitud de beca para quedar presentada, ya guardado."""
    missing = []
    if not artwork.grant_justification:
        missing.append('Contá por qué la beca hace posible la instalación.')
    # La beca se pide sólo sobre lo que cubre: materiales y equipamiento.
    fundable = artwork.fundable_budget_total
    if not fundable:
        missing.append('Agregá al presupuesto al menos un ítem de materiales o equipamiento.')
    if not artwork.grant_requested_amount:
        missing.append('Indicá el monto que pedís.')
    elif fundable and artwork.grant_requested_amount > artwork.grant_request_cap:
        percent = artwork.event.art_program.grant_max_percent
        cap = f'({artwork.grant_currency} {_localized_amount(artwork.grant_request_cap)})'
        missing.append(
            f'El monto que pedís no puede superar el {percent} % del presupuesto de materiales y equipamiento {cap}.'
            if percent else f'El monto que pedís no puede superar el presupuesto de materiales y equipamiento {cap}.'
        )
    if not (artwork.grant_payee_name and artwork.grant_payee_tax_id and artwork.grant_payee_account):
        missing.append('Completá quién recibiría la beca: nombre o razón social, CUIT/CUIL y CBU, CVU o alias.')
    if not artwork.grant_terms_accepted_at:
        missing.append('Aceptá las condiciones de la beca.')
    return missing


def _sync_grant_presentation(request, artwork):
    """Guardar es presentar: con todo completo la solicitud queda presentada; si deja de estarlo, vuelve atrás.

    Devuelve si cambió el estado (y ya dejó su mensaje).
    """
    status = Artwork.GrantStatus
    if artwork.grant_status not in GRANT_REQUEST_EDITABLE:
        return False
    missing = _grant_missing(artwork)
    if not missing and (
        artwork.grant_status == status.NOT_REQUESTED
        # Lo que pidió Becas lo responde el equipo: que guarde Becas no la da por respondida.
        or (artwork.grant_status == status.INFO_REQUIRED and artwork.can_edit(request.user))
    ):
        # A Becas se le avisa la primera vez y cuando el equipo responde un pedido de información.
        notify = artwork.grant_status == status.INFO_REQUIRED or not artwork.grant_submitted_at
        artwork.grant_status, artwork.grant_requested = status.PENDING, True
        artwork.grant_submitted_at = timezone.now()
        artwork.save(update_fields=['grant_status', 'grant_requested', 'grant_submitted_at', 'updated_at'])
        history.log(artwork, history.STATUS, request.user, 'Presentó la solicitud de beca')
        if notify:
            transaction.on_commit(lambda: send_grant_application_email(artwork, request.user))
        messages.success(request, 'Solicitud presentada. Podés seguir editándola hasta que se apruebe.')
        return True
    if missing and artwork.grant_status == status.PENDING:
        artwork.grant_status, artwork.grant_requested = status.NOT_REQUESTED, False
        artwork.save(update_fields=['grant_status', 'grant_requested', 'updated_at'])
        history.log(artwork, history.STATUS, request.user, 'La solicitud de beca dejó de estar presentada')
        messages.warning(
            request,
            'Tu solicitud de beca dejó de estar presentada porque quedó incompleta. '
            'Completá lo que falta y guardá para volver a presentarla.',
        )
        return True
    return False


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
            return HttpResponseForbidden('La propuesta no tiene una beca aprobada para rendir.')
        if not artwork.grant_report:
            messages.error(request, 'Completá y guardá el relato de la rendición.')
        elif not artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.EXPENSE).exists():
            messages.error(request, 'Agregá al menos un gasto a la rendición.')
        elif not artwork.photos.filter(stage__in=(ArtworkPhoto.Stage.FINAL, ArtworkPhoto.Stage.GRANT_REPORT)).exists():
            messages.error(request, 'Subí al menos una foto final o de rendición.')
        elif artwork.grant_approved_amount and artwork.expense_total > artwork.grant_approved_amount:
            messages.error(request, 'La rendición supera el monto aprobado. Revisá los montos antes de enviarla.')
        else:
            artwork.grant_status = Artwork.GrantStatus.REPORTED
            artwork.save(update_fields=['grant_status', 'updated_at'])
            record_change(artwork, 'Rendición de beca', request.user)
            messages.success(request, 'La rendición fue enviada para revisión.')
    return _grant_page_redirect(artwork, 'rendicion')


def _photo_redirect(artwork, stage):
    if stage == ArtworkPhoto.Stage.GRANT_REPORT:
        return _grant_page_redirect(artwork, 'rendicion')
    return _artwork_redirect(artwork, 'galeria')


@login_required
def artwork_photo_upload(request, artwork_id):
    """Fotos de la galería o, desde la pantalla de la beca, de la rendición."""
    stage = request.POST.get('stage')
    for_report = stage == ArtworkPhoto.Stage.GRANT_REPORT
    access = _accessible_artworks(request.user, grant=for_report)
    artwork = get_object_or_404(access, pk=artwork_id)
    if request.method != 'POST':
        return _photo_redirect(artwork, stage)
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        if for_report:
            allowed = _grant_item_editable(artwork, ArtworkGrantItem.Phase.EXPENSE, request.user)
            form = ArtworkPhotoUploadForm(
                request.POST, request.FILES, stages=(ArtworkPhoto.Stage.GRANT_REPORT,), auto_id='report-photo-new_%s',
            )
        else:
            allowed = _gallery_editable(artwork, request.user)
            form = ArtworkPhotoUploadForm(request.POST, request.FILES, auto_id='photo-new_%s')
        if not allowed:
            return HttpResponseForbidden('Estas fotos no se pueden subir ahora.')
        if form.is_valid():
            images = form.cleaned_data['images']
            if artwork.photos.count() + len(images) > 100:
                form.add_error('images', 'La galería admite hasta 100 fotos por propuesta.')
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
                record_change(artwork, 'Rendición de beca' if for_report else 'Galería', request.user)
                messages.success(request, f"Se subieron {len(images)} foto(s).")
                return _photo_redirect(artwork, form.cleaned_data['stage'])
    return _inline_error_response(request, artwork, ('report-photo-new' if for_report else 'photo-new', None), form)


@login_required
def artwork_photo_delete(request, artwork_id, photo_id):
    if request.method != 'POST':
        return HttpResponseForbidden('Método no permitido.')
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user, grant=True).select_for_update(), pk=artwork_id)
        photo = get_object_or_404(ArtworkPhoto.objects.select_for_update(), pk=photo_id, artwork=artwork)
        # Las fotos de la rendición también las gestiona Becas.
        manager = artwork.can_manage(request.user) or (
            photo.stage == ArtworkPhoto.Stage.GRANT_REPORT and artwork.can_manage_grant(request.user)
        )
        if not artwork.can_edit(request.user) and not manager:
            return HttpResponseForbidden('La galería de esta propuesta no se puede editar.')
        protected_evidence = (
            artwork.grant_status in (Artwork.GrantStatus.REPORTED, Artwork.GrantStatus.CLOSED)
            and photo.stage in (ArtworkPhoto.Stage.FINAL, ArtworkPhoto.Stage.GRANT_REPORT)
        )
        if protected_evidence and not manager:
            return HttpResponseForbidden('La evidencia de una rendición presentada no se puede eliminar.')
        storage = photo.image.storage
        image_name = photo.image.name
        stage = photo.stage
        photo.delete()
        transaction.on_commit(lambda: storage.delete(image_name))
        record_change(artwork, 'Rendición de beca' if stage == ArtworkPhoto.Stage.GRANT_REPORT else 'Galería', request.user)
    messages.success(request, 'Foto eliminada.')
    return _photo_redirect(artwork, stage)


@login_required
@require_POST
def artwork_team_benefits(request, artwork_id):
    """Guarda quién del equipo entra antes y quién se queda después."""
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        if not _logistics_editable(artwork, request.user):
            return HttpResponseForbidden('El ingreso anticipado de esta propuesta no se puede editar ahora.')
        form = _benefits_form(artwork, request.POST)
        if form is None:
            return HttpResponseForbidden('La propuesta todavía no tiene equipo.')
        if not form.is_valid():
            return _inline_error_response(request, artwork, ('benefits', None), form)
        if form.has_changed():
            record_change(artwork, 'Ingreso y proveedores', request.user)
        form.save()
    messages.success(request, 'Ingreso anticipado y late checkout guardados.')
    return _artwork_redirect(artwork, 'ingreso')


@login_required
@require_POST
def artwork_file_upload(request, artwork_id):
    """Archivos de la propuesta (imágenes o PDF), en Detalles."""
    access = _accessible_artworks(request.user)
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        if not _files_editable(artwork, request.user):
            return HttpResponseForbidden(FILES_LOCKED_ERROR)
        form = ArtworkFileUploadForm(request.POST, request.FILES, auto_id='file-new_%s')
        if form.is_valid():
            uploads = form.cleaned_data['files']
            if artwork.files.count() + len(uploads) > ArtworkFileUploadForm.MAX_FILES_PER_ARTWORK:
                form.add_error('files', FILES_PER_ARTWORK_ERROR)
            else:
                for upload in uploads:
                    ArtworkFile.objects.create(
                        artwork=artwork, file=upload, name=upload.name[:255], uploaded_by=request.user,
                    )
                record_change(artwork, 'Archivos', request.user)
                messages.success(request, f"Se subieron {len(uploads)} archivo(s).")
                return _artwork_redirect(artwork, 'detalles')
    return _inline_error_response(request, artwork, ('file-new', None), form)


FILES_PER_ARTWORK_ERROR = f'Cada propuesta admite hasta {ArtworkFileUploadForm.MAX_FILES_PER_ARTWORK} archivos.'
FILES_LOCKED_ERROR = 'Los archivos de esta propuesta ya no se pueden editar.'


@login_required
@require_POST
def artwork_file_presign(request, artwork_id):
    """Primer paso de la subida directa a S3: valida los archivos y da un permiso de subida por cada uno."""
    storage = direct_uploads.s3_storage()
    if storage is None:
        raise Http404
    artwork = get_object_or_404(_accessible_artworks(request.user), pk=artwork_id)
    if not _files_editable(artwork, request.user):
        return JsonResponse({'error': FILES_LOCKED_ERROR}, status=403)
    names, sizes = request.POST.getlist('name'), request.POST.getlist('size')
    try:
        uploads = list(zip(names, (int(size) for size in sizes), strict=True))
    except ValueError:
        return JsonResponse({'error': 'No se pudo leer qué archivos elegiste. Probá de nuevo.'}, status=400)
    if not uploads:
        return JsonResponse({'error': 'Seleccioná al menos un archivo.'}, status=400)
    error = ArtworkFileUploadForm.upload_error(uploads)
    if not error and artwork.files.count() + len(uploads) > ArtworkFileUploadForm.MAX_FILES_PER_ARTWORK:
        error = FILES_PER_ARTWORK_ERROR
    if error:
        return JsonResponse({'error': error}, status=400)
    return JsonResponse({'uploads': [direct_uploads.presign(storage, artwork, name, size) for name, size in uploads]})


@login_required
@require_POST
def artwork_file_confirm(request, artwork_id):
    """Segundo paso: los archivos ya están en S3 y se suman a la propuesta."""
    storage = direct_uploads.s3_storage()
    if storage is None:
        raise Http404
    tokens = request.POST.getlist('token')
    if len(tokens) > ArtworkFileUploadForm.MAX_FILES_PER_UPLOAD:
        return JsonResponse({'error': f'Podés subir hasta {ArtworkFileUploadForm.MAX_FILES_PER_UPLOAD} archivos por vez.'}, status=400)
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        if not _files_editable(artwork, request.user):
            return JsonResponse({'error': FILES_LOCKED_ERROR}, status=403)
        try:
            uploads = [direct_uploads.read_token(artwork, token) for token in tokens]
        except signing.BadSignature:
            return JsonResponse({'error': 'La subida venció. Volvé a elegir los archivos.'}, status=400)
        # Un reintento no duplica lo que ya se confirmó.
        confirmed = set(artwork.files.filter(file__in=[name for name, _ in uploads]).values_list('file', flat=True))
        uploads = [(name, filename) for name, filename in uploads if name not in confirmed]
        if not uploads and not confirmed:
            return JsonResponse({'error': 'Seleccioná al menos un archivo.'}, status=400)
        if artwork.files.count() + len(uploads) > ArtworkFileUploadForm.MAX_FILES_PER_ARTWORK:
            return JsonResponse({'error': FILES_PER_ARTWORK_ERROR}, status=400)
        for name, filename in uploads:
            try:
                direct_uploads.ensure_uploaded(storage, name)
            except direct_uploads.UploadNotFound:
                return JsonResponse({'error': f'“{filename}” no terminó de subirse. Probá de nuevo.'}, status=400)
        for name, filename in uploads:
            ArtworkFile.objects.create(artwork=artwork, file=name, name=filename, uploaded_by=request.user)
        if uploads:
            record_change(artwork, 'Archivos', request.user)
    count = len(uploads) + len(confirmed)
    messages.success(request, f"Se subieron {count} archivo(s).")
    return JsonResponse({'redirect': f"{reverse('artwork_edit', args=[artwork.pk])}#detalles"})


@login_required
@require_POST
def artwork_file_delete(request, artwork_id, file_id):
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        if not _files_editable(artwork, request.user):
            return HttpResponseForbidden(FILES_LOCKED_ERROR)
        artwork_file = get_object_or_404(ArtworkFile.objects.select_for_update(), pk=file_id, artwork=artwork)
        storage, name = artwork_file.file.storage, artwork_file.file.name
        artwork_file.delete()
        transaction.on_commit(lambda: storage.delete(name))
        record_change(artwork, 'Archivos', request.user)
    messages.success(request, 'Archivo eliminado.')
    return _artwork_redirect(artwork, 'detalles')


@login_required
def artwork_checkout_photo_upload(request, artwork_id):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if request.method != 'POST':
        return _checkout_redirect(request, artwork)
    with transaction.atomic():
        artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
        if not _checkout_editable(artwork, request.user):
            return HttpResponseForbidden('El checkout de esta propuesta ya no se puede editar.')
        if artwork.checkout_verified_at and not artwork.can_manage(request.user):
            return HttpResponseForbidden('La evidencia de un checkout verificado no se puede modificar.')
        form = ArtworkCheckoutPhotoUploadForm(request.POST, request.FILES, auto_id='checkout-photo-new_%s')
        if form.is_valid():
            images = form.cleaned_data['images']
            if artwork.checkout_photos.count() + len(images) > 100:
                form.add_error('images', 'El reporte de checkout admite hasta 100 fotos por propuesta.')
            else:
                for image in images:
                    ArtworkCheckoutPhoto.objects.create(
                        artwork=artwork, image=image, caption=form.cleaned_data['caption'], uploaded_by=request.user,
                    )
                record_change(artwork, 'Checkout', request.user)
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
        record_change(artwork, 'Checkout', request.user)
    messages.success(request, 'Foto eliminada del reporte de checkout.')
    return _checkout_redirect(request, artwork)


@login_required
@require_POST
def artwork_team_add(request, artwork_id):
    with transaction.atomic():
        artwork = get_object_or_404(_accessible_artworks(request.user).select_for_update(), pk=artwork_id)
        if not _team_editable(artwork, request.user):
            return HttpResponseForbidden('El equipo de esta propuesta no se puede editar.')
        form = ArtworkTeamAddForm(request.POST, artwork=artwork, auto_id='team-add_%s')
        if not form.is_valid():
            return _inline_error_response(request, artwork, ('team-add', None), form)
        group = _ensure_operations_group(artwork, artwork.event.art_program)
        GrupoMiembro.objects.create(grupo=group, user=form.user)
        record_change(artwork, 'Equipo', request.user)
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
            return HttpResponseForbidden('Solo la persona responsable puede quitar a quien edita la propuesta.')
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
        record_change(artwork, 'Equipo', request.user)
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
            return HttpResponseForbidden('No podés cambiar quién edita esta propuesta.')
        name = member.user.get_full_name() or member.user.email
        if request.POST.get('can_edit') == 'on':
            artwork.collaborators.add(member.user)
            messages.success(request, f'{name} ahora puede editar la propuesta.')
        else:
            artwork.collaborators.remove(member.user)
            messages.success(request, f'{name} ya no puede editar la propuesta.')
        history.log_edit(artwork, 'Equipo', request.user)
    return _artwork_redirect(artwork, 'equipo')


@login_required
def artwork_provider_edit(request, artwork_id, provider_id=None):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    if not _logistics_editable(artwork, request.user):
        return HttpResponseForbidden('La logística de esta propuesta ya no se puede editar.')
    provider = get_object_or_404(ArtworkProvider, artwork=artwork, pk=provider_id) if provider_id else ArtworkProvider(artwork=artwork, created_by=request.user)
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'ingreso')
    form = ArtworkProviderForm(
        request.POST, instance=provider,
        auto_id=f'provider-{provider_id}_%s' if provider_id else 'provider-new_%s',
    )
    if form.is_valid():
        with transaction.atomic():
            artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
            if not _logistics_editable(artwork, request.user):
                return HttpResponseForbidden('La logística de esta propuesta ya no se puede editar.')
            form.instance.artwork = artwork
            form.instance.created_by = form.instance.created_by or request.user
            changed = form.has_changed()
            provider = form.save()
            if changed:
                record_change(artwork, 'Ingreso y proveedores', request.user)
        messages.success(request, 'Proveedor guardado.')
        return _artwork_redirect(artwork, 'ingreso')
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
        record_change(artwork, 'Ingreso y proveedores', request.user)
    messages.success(request, 'Proveedor y sus vehículos fueron eliminados.')
    return _artwork_redirect(artwork, 'ingreso')


@login_required
def artwork_vehicle_edit(request, artwork_id, provider_id, vehicle_id=None):
    access = _accessible_artworks(request.user)
    artwork = get_object_or_404(access, pk=artwork_id)
    provider = get_object_or_404(ArtworkProvider, artwork=artwork, pk=provider_id)
    if not _logistics_editable(artwork, request.user):
        return HttpResponseForbidden('La logística de esta propuesta ya no se puede editar.')
    vehicle = get_object_or_404(ArtworkProviderVehicle, provider=provider, pk=vehicle_id) if vehicle_id else ArtworkProviderVehicle(provider=provider)
    if request.method != 'POST':
        return _artwork_redirect(artwork, 'ingreso')
    form = ArtworkProviderVehicleForm(
        request.POST, instance=vehicle,
        auto_id=f'vehicle-{vehicle_id}_%s' if vehicle_id else f'vehicle-{provider_id}-new_%s',
    )
    if form.is_valid():
        with transaction.atomic():
            artwork = get_object_or_404(access.select_for_update(), pk=artwork_id)
            provider = get_object_or_404(ArtworkProvider, artwork=artwork, pk=provider_id)
            if not _logistics_editable(artwork, request.user):
                return HttpResponseForbidden('La logística de esta propuesta ya no se puede editar.')
            form.instance.provider = provider
            changed = form.has_changed()
            form.save()
            if changed:
                record_change(artwork, 'Ingreso y proveedores', request.user)
        messages.success(request, 'Vehículo guardado.')
        return _artwork_redirect(artwork, 'ingreso')
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
        record_change(artwork, 'Ingreso y proveedores', request.user)
    messages.success(request, 'Vehículo eliminado.')
    return _artwork_redirect(artwork, 'ingreso')


def _managed_event(request, event_slug):
    event = get_object_or_404(Event, slug=event_slug, art_program__isnull=False)
    if not can_view_art(request.user, event):
        return None
    return event


def _contact_counts(event):
    """Cuántas propuestas tiene asignadas cada contacto de ESTAFA (None: sin contacto). No cuenta las rechazadas."""
    return dict(
        event.artworks.exclude(status=Artwork.Status.REJECTED)
        .values_list('estafa_contact_id').annotate(total=Count('pk')).order_by()
    )


def _estafa_contact_choices(event, counts):
    contacts = User.objects.filter(
        Q(pk__in=estafa_members().values('pk')) | Q(estafa_contact_artworks__event=event),
    ).distinct().order_by('first_name', 'last_name', 'email')
    return [(str(user.pk), person_label(user), counts.get(user.pk, 0)) for user in contacts.select_related('profile')]


# Marcas del listado de ESTAFA: (clave, etiqueta, condición). El dibujo de cada una está en art/_flag_icon.html.
ARTWORK_FLAGS = (
    ('risk', 'Riesgosa', Q(is_risky=True)),
    ('camp', 'Con camp soporte', Q(wants_support_camp=True)),
    # Callable: la condición arma una subconsulta y se resuelve al filtrar.
    ('grant', 'Pidió beca', grant_started_q),
    ('fire', 'Se quema', Q(burns=True)),
    ('sound', 'Tiene sonido', Q(uses_sound=True)),
    ('mad', 'Necesita asistencia de MAD', Q(needs_mad_assistance=True)),
    ('movable', 'Se mueve de lugar (sin motor)', Q(is_movable=True)),
    ('ufo', 'Movilidad propia (art car)', Q(has_own_mobility=True)),
)


def _selected_flags(params):
    chosen = params.getlist('flag')
    return [flag for flag in ARTWORK_FLAGS if flag[0] in chosen]


def _selected_stages(params):
    return [stage for stage in Artwork.STAGES if stage in params.getlist('status')]


def _contact_filter(params, user, counts):
    """El contacto elegido. Sin elegir nada, quien tiene propuestas asignadas ve las suyas."""
    contact = params.get('contact', '')
    if contact:
        return '' if contact == 'all' else contact
    return 'me' if counts.get(user.pk) else ''


def _filter_summary(params, contact_choices, counts, user):
    """Lo que muestra cada botón de filtro cuando tiene algo elegido."""
    stages = _selected_stages(params)
    if len(stages) == 1:
        status = Artwork.STAGES[stages[0]][0]
    else:
        status = f'{len(stages)} estados' if stages else ''
    contact = _contact_filter(params, user, counts)
    contact_label = {
        'me': f'Asignadas a vos ({counts.get(user.pk, 0)})', 'none': f'Sin contacto ({counts.get(None, 0)})',
        **{value: f'{label} ({total})' for value, label, total in contact_choices},
    }.get(contact, '')
    flags = _selected_flags(params)
    flag_summary = flags[0][1] if len(flags) == 1 else (f'{len(flags)} marcas' if flags else '')
    return {
        'selected_statuses': stages, 'status_summary': status, 'contact_summary': contact_label, 'selected_contact': contact,
        'selected_flags': [key for key, *_rest in flags], 'flag_summary': flag_summary,
        # "Limpiar" vuelve a lo que se ve al entrar: aparece sólo si se eligió algo.
        'only_news': bool(params.get('news')),
        'filters_active': bool(stages or flags or params.get('contact') or params.get('news') or params.get('q', '').strip()),
    }


def _search_artworks(artworks, params):
    """El buscador de los listados de ESTAFA y de Becas: propuesta, persona o email."""
    query = params.get('q', '').strip()
    if not query:
        return artworks
    return artworks.filter(
        Q(title__icontains=query)
        | Q(owner__email__icontains=query)
        | Q(owner__first_name__icontains=query)
        | Q(owner__last_name__icontains=query)
        | Q(owner__profile__nickname__icontains=query)
        | Q(public_title__icontains=query)
        | Q(assigned_location__icontains=query)
    )


def _filter_by_contact(artworks, contact, user):
    if contact == 'me':
        return artworks.filter(estafa_contact=user)
    if contact == 'none':
        return artworks.filter(estafa_contact__isnull=True)
    if contact.isdigit():
        return artworks.filter(estafa_contact_id=contact)
    return artworks


def _filtered_artworks(event, params, user):
    artworks = event.artworks.select_related(
        'event__art_program', 'owner__profile', 'safety_responsible', 'estafa_contact__profile',
    ).prefetch_related(
        'checkout_photos', 'operations_group__miembros', 'artwork_providers__vehicles',
    )
    artworks = _search_artworks(artworks, params)
    stages = _selected_stages(params)
    if stages:
        try:
            checkout_open = event.art_program.checkout_is_open()
        except ArtProgram.DoesNotExist:
            checkout_open = False
        # Con el checkout abierto, una inscripción aprobada se muestra como "Checkout pendiente".
        statuses = set()
        for stage in stages:
            if stage == Artwork.CHECKOUT_PENDING:
                if checkout_open:
                    statuses.add(Artwork.Status.ACTIVE)
            elif not (stage == Artwork.Status.ACTIVE and checkout_open):
                statuses.add(stage)
        artworks = artworks.filter(status__in=statuses)
    else:
        # Sin filtro de estado no se listan las rechazadas: se ven eligiendo «Rechazada».
        artworks = artworks.exclude(status=Artwork.Status.REJECTED)
    # Varias marcas a la vez: la propuesta tiene que cumplir todas.
    for _key, _label, condition in _selected_flags(params):
        artworks = artworks.filter(condition() if callable(condition) else condition)
    if params.get('grant') in Artwork.GrantStatus.values:
        artworks = artworks.filter(grant_status=params['grant'])
    artworks = _filter_by_contact(artworks, _contact_filter(params, user, _contact_counts(event)), user)
    artworks = history.with_news(artworks, user)
    if params.get('news'):
        artworks = artworks.filter(news_count__gt=0)
    # La marca «Pidió beca» del listado, sin una consulta por fila.
    artworks = artworks.annotate(grant_listed=ExpressionWrapper(grant_started_q(), output_field=BooleanField()))
    return artworks.distinct().order_by(Lower('title'), 'pk')


@login_required
def estafa_home(request):
    if not can_access_estafa(request.user):
        return HttpResponseForbidden('Esta sección es sólo para ESTAFA, Becas y MAD.')
    events = estafa_events(request.user)
    event = events.filter(art_program__is_current=True).first() or events.first()
    if event:
        return redirect(_estafa_landing(request.user), event_slug=event.slug)
    return render(request, 'art/admin_dashboard.html', _estafa_context(request.user, None))


@login_required
def art_admin_dashboard(request, event_slug):
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para ver las propuestas de Arte de este evento.')
    artworks = _filtered_artworks(event, request.GET, request.user)
    counts = _contact_counts(event)
    contact_choices = _estafa_contact_choices(event, counts)
    news_total = history.with_news(event.artworks.exclude(status=Artwork.Status.REJECTED), request.user).filter(news_count__gt=0).count()
    return render(request, 'art/admin_dashboard.html', {
        **_estafa_context(request.user, event), 'artworks': artworks, 'news_total': news_total,
        'status_choices': [(stage, label) for stage, (label, _hint) in Artwork.STAGES.items()],
        'flag_choices': [(key, label) for key, label, _condition in ARTWORK_FLAGS],
        'contact_choices': contact_choices, 'mine_count': counts.get(request.user.pk, 0), 'no_contact_count': counts.get(None, 0),
        **_filter_summary(request.GET, contact_choices, counts, request.user),
    })


@login_required
@require_POST
def art_admin_mark_seen(request, event_slug):
    """Marca como leídas las novedades de todas las propuestas del evento, para quien lo pide."""
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para ver las propuestas de Arte de este evento.')
    history.mark_all_seen(event.artworks.all(), request.user)
    messages.success(request, 'Marcaste todas las novedades como leídas.')
    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse('art_admin_dashboard', args=[event.slug])
    return redirect(next_url)


@login_required
def artwork_review(request, event_slug, artwork_id):
    event = get_object_or_404(Event, slug=event_slug)
    artwork = get_object_or_404(Artwork, pk=artwork_id, event=event)
    if not artwork.can_view_all(request.user):
        return HttpResponseForbidden('No tenés permisos para ver las propuestas de Arte de este evento.')
    if request.method == 'GET' and _mad_only(request.user):
        # MAD lee la propuesta en su propia vista, sin la coordinación de ESTAFA.
        return redirect('artwork_mad', event_slug=event.slug, artwork_id=artwork.pk)
    # Becas lee la revisión completa; sólo ESTAFA la completa.
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
                previous_status = artwork.status
                if artwork.checkin_art_at and not had_art_checkin:
                    artwork.checkin_art_by = request.user
                if artwork.checkout_verified_at:
                    artwork.checkout_verified_by = request.user
                    if artwork.status != Artwork.Status.CHECKOUT_VERIFIED:
                        artwork.set_status(Artwork.Status.CHECKOUT_VERIFIED, request.user)
                        transaction.on_commit(lambda: _send_status_email(artwork))
                elif was_verified:
                    artwork.checkout_verified_by = None
                    if artwork.status == Artwork.Status.CHECKOUT_VERIFIED:
                        artwork.set_status(Artwork.Status.CHECKOUT_SUBMITTED, request.user)
                artwork.save()
                if artwork.status != previous_status:
                    history.log(artwork, history.STATUS, request.user, (
                        'Verificó el checkout' if artwork.status == Artwork.Status.CHECKOUT_VERIFIED
                        else 'Quitó la verificación del checkout'
                    ))
                tracked = [name for name in form.changed_data if name not in ('expected_updated_at', 'checkout_verified_at')]
                if tracked:
                    history.log_edit(
                        artwork, 'Seguimiento de ESTAFA', request.user, _field_labels(form, tracked),
                        kind=history.ESTAFA, important=False,
                    )
                messages.success(request, 'La revisión quedó guardada.')
                return redirect('artwork_review', event_slug=event.slug, artwork_id=artwork.pk)
    else:
        form = ArtworkReviewForm(instance=artwork, read_only=not can_manage)
    context = _review_context(artwork, form, request.user)
    if request.method == 'GET':
        # Abrir la revisión es ponerse al día: lo que pasó desde la visita anterior se marca como nuevo.
        context['news_count'], context['previous_visit'] = history.visit(artwork, request.user, context['history'])
    return render(request, 'art/review.html', context)


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
            history.log(artwork, history.ESTAFA, request.user, f'Asignó a {contact.get_full_name() or contact.email} como contacto de ESTAFA')
            transaction.on_commit(lambda: _send_contact_email(artwork))
            messages.success(request, f'{contact.get_full_name() or contact.email} es el contacto de ESTAFA. Le avisamos al equipo por email.')
        elif not form.cleaned_data['estafa_contact'] and previous_id:
            form.save()
            history.log(artwork, history.ESTAFA, request.user, 'Quitó el contacto de ESTAFA')
            messages.success(request, 'La propuesta quedó sin contacto de ESTAFA.')
    return redirect('artwork_review', event_slug=event.slug, artwork_id=artwork.pk)


@login_required
@require_POST
def artwork_risk(request, event_slug, artwork_id):
    event = get_object_or_404(Event, slug=event_slug)
    artwork = get_object_or_404(Artwork, pk=artwork_id, event=event)
    if not artwork.can_manage(request.user):
        return HttpResponseForbidden('No tenés permisos para coordinar Arte en este evento.')
    risky = request.POST.get('risky') == '1'
    # Sin tocar updated_at: es una marca interna y no tiene que pisar una revisión abierta en otra pestaña.
    Artwork.objects.filter(pk=artwork.pk).update(is_risky=risky)
    if risky != artwork.is_risky:
        history.log(artwork, history.ESTAFA, request.user, 'Marcó la propuesta como riesgosa' if risky else 'Quitó la marca de riesgo')
    messages.success(request, 'Marcaste la propuesta como riesgosa.' if risky else 'La propuesta ya no está marcada como riesgosa.')
    return redirect('artwork_review', event_slug=event.slug, artwork_id=artwork.pk)


@login_required
@require_POST
def artwork_message(request, event_slug, artwork_id):
    """Un mensaje de ESTAFA, Becas o MAD al equipo, en cualquier momento y sin cambiar el estado."""
    event = get_object_or_404(Event, slug=event_slug)
    artwork = get_object_or_404(Artwork, pk=artwork_id, event=event)
    if not artwork.can_message(request.user):
        return HttpResponseForbidden('No tenés permisos para escribirle al equipo de esta propuesta.')
    message = request.POST.get('message', '').strip()
    if not message:
        messages.error(request, 'Escribí el mensaje para el equipo.')
    elif not _team_emails(artwork):
        messages.error(request, 'El equipo de la propuesta no tiene un email al que escribirle.')
    else:
        history.log(artwork, history.MESSAGE, request.user, 'Le escribió al equipo', detail=message)
        _send_team_message_email(artwork, request.user, message)
        messages.success(request, 'Le enviamos tu mensaje al equipo por email.')
    if request.POST.get('return_to') == 'grant':
        return _grant_page_redirect(artwork, 'mensaje')
    if request.POST.get('return_to') == 'mad':
        return redirect(reverse('artwork_mad', args=[event.slug, artwork.pk]) + '#mensajes')
    if _mad_only(request.user):
        return redirect('artwork_mad', event_slug=event.slug, artwork_id=artwork.pk)
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
            messages.error(request, 'La propuesta cambió de estado mientras la revisabas. Revisá su estado actual.')
            return redirect(next_url)
        status = transition[1]
        message = request.POST.get('message', '').strip()
        if status == Artwork.Status.REJECTED and not message:
            messages.error(request, 'Contale al equipo por qué se rechaza la propuesta.')
            return redirect(next_url)
        artwork.set_status(status, request.user)
        artwork.review_feedback = message
        artwork.save(update_fields=['status', 'status_changed_at', 'status_changed_by', 'review_feedback', 'updated_at'])
        # review_feedback guarda sólo el último mensaje; el historial los guarda todos.
        history.log(artwork, history.STATUS, request.user, {
            Artwork.Status.ACTIVE: 'Aprobó la inscripción',
            Artwork.Status.REJECTED: 'Rechazó la propuesta',
            Artwork.Status.PENDING: 'Volvió a inscripción pendiente',
        }[status], detail=message)
        if status == Artwork.Status.ACTIVE:
            _ensure_operations_group(artwork, event.art_program)
        transaction.on_commit(lambda: _send_status_email(artwork))
    title = artwork.title or 'Propuesta de Arte sin título'
    messages.success(request, {
        Artwork.Status.ACTIVE: f'Aprobaste «{title}». Le avisamos al equipo por email.',
        Artwork.Status.REJECTED: f'Rechazaste «{title}». Le avisamos al equipo por email.',
        Artwork.Status.PENDING: f'«{title}» volvió a inscripción pendiente. Le avisamos al equipo por email.',
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
        'Propuesta de Arte', 'Modalidad', 'Responsable', 'Responsable (nombre)', 'WhatsApp del responsable', 'Estado', 'Contacto de ESTAFA', 'Riesgosa', 'Responsable de seguridad',
        'Asistencia de MAD', 'Se mueve de lugar (sin motor)', 'Movilidad propia (art car)',
        'Título público', 'Descripción pública',
        'Declaración de entendimiento digital', 'Declaración de entendimiento física', 'Personas del equipo',
        'Proveedores', 'Vehículos', 'Fotos checkout', 'Checkout', 'Actualizada',
    ])
    for artwork in _filtered_artworks(event, request.GET, request.user):
        providers = list(artwork.artwork_providers.all())
        writer.writerow([_csv_cell(value) for value in [
            artwork.title,
            artwork.get_kind_display(),
            artwork.owner.email if artwork.owner else '',
            person_label(artwork.owner),
            whatsapp_number(_owner_phone(artwork)),
            artwork.stage_label,
            artwork.estafa_contact.email if artwork.estafa_contact else '',
            'Sí' if artwork.is_risky else 'No',
            artwork.safety_responsible.email if artwork.safety_responsible else '',
            'Sí' if artwork.needs_mad_assistance else 'No',
            'Sí' if artwork.is_movable else 'No',
            'Sí' if artwork.has_own_mobility else 'No',
            artwork.public_title,
            artwork.public_description,
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


GRANT_INCOMPLETE_FILTER = 'incomplete'
# Los estados del filtro de Becas: primero las solicitudes a medio cargar, después los de la beca presentada.
GRANT_STATUS_FILTERS = [(GRANT_INCOMPLETE_FILTER, GRANT_INCOMPLETE_LABEL)] + [
    (value, label) for value, label in Artwork.GrantStatus.choices if value != Artwork.GrantStatus.NOT_REQUESTED
]


def _listed_grants(event):
    """Las becas que se listan: presentadas o a medio cargar. Quien nunca tocó la beca no aparece."""
    return event.artworks.filter(grant_started_q())


def _grant_statuses(params):
    chosen = params.getlist('status') if hasattr(params, 'getlist') else []
    return [value for value, _label in GRANT_STATUS_FILTERS if value in chosen]


def _grant_missing_short(artwork):
    """Qué le falta a una solicitud incompleta, en pocas palabras, con lo ya anotado en el listado."""
    missing = []
    if not artwork.grant_justification:
        missing.append('justificación')
    if not artwork.fundable_sum:
        missing.append('presupuesto')
    if not artwork.grant_requested_amount:
        missing.append('monto pedido')
    if not (artwork.grant_payee_name and artwork.grant_payee_tax_id and artwork.grant_payee_account):
        missing.append('datos de cobro')
    if not artwork.grant_terms_accepted_at:
        missing.append('condiciones')
    return ', '.join(missing) or 'revisar el monto pedido'


def _grant_artworks(event, params, user):
    """Las solicitudes de beca, presentadas o incompletas, con los totales de su presupuesto.

    Mismo buscador que el listado de propuestas; el estado es el de la beca.
    """
    artworks = _search_artworks(_listed_grants(event).select_related('owner__profile', 'estafa_contact__profile'), params)
    statuses = _grant_statuses(params)
    if statuses:
        chosen = Q(grant_status__in=[value for value in statuses if value != GRANT_INCOMPLETE_FILTER])
        if GRANT_INCOMPLETE_FILTER in statuses:
            chosen |= grant_incomplete_q()
        artworks = artworks.filter(chosen)
    budget = Q(grant_items__phase=ArtworkGrantItem.Phase.BUDGET)
    artworks = list(artworks.annotate(
        budget_sum=Sum('grant_items__amount', filter=budget),
        fundable_sum=Sum(
            'grant_items__amount', filter=budget & Q(grant_items__item_type__in=ArtworkGrantItem.FUNDABLE_TYPES),
        ),
    ).order_by(Lower('title'), 'pk'))
    for artwork in artworks:
        artwork.is_grant_incomplete = artwork.grant_status == Artwork.GrantStatus.NOT_REQUESTED
        artwork.grant_label = GRANT_INCOMPLETE_LABEL if artwork.is_grant_incomplete else artwork.get_grant_status_display()
        artwork.grant_missing_short = _grant_missing_short(artwork) if artwork.is_grant_incomplete else ''
    return artworks


@login_required
def art_grants_dashboard(request, event_slug):
    """La pestaña de Becas: cada solicitud con su estado y sus montos. La leen ESTAFA, Becas y MAD."""
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para ver las becas de este evento.')
    params = request.GET
    listed = _listed_grants(event)
    status_counts = dict(
        listed.exclude(grant_status=Artwork.GrantStatus.NOT_REQUESTED)
        .values_list('grant_status').annotate(total=Count('pk')).order_by()
    )
    incomplete = listed.filter(grant_status=Artwork.GrantStatus.NOT_REQUESTED).count()
    if incomplete:
        status_counts[GRANT_INCOMPLETE_FILTER] = incomplete
    statuses = _grant_statuses(params)
    labels = dict(GRANT_STATUS_FILTERS)
    return render(request, 'art/grants_dashboard.html', {
        **_estafa_context(request.user, event, tab='grants'),
        'artworks': _grant_artworks(event, params, request.user),
        'status_choices': [(value, label, status_counts.get(value, 0)) for value, label in GRANT_STATUS_FILTERS],
        'selected_statuses': statuses,
        'status_summary': labels[statuses[0]] if len(statuses) == 1 else (f'{len(statuses)} estados' if statuses else ''),
        'filters_active': bool(statuses or params.get('q', '').strip()),
        'has_grants': bool(status_counts),
        'can_see_payee': can_manage_grants(request.user, event),
        'can_export': can_manage_grants(request.user, event),
    })


# Lo que una propuesta le pide a MAD: (clave, etiqueta, etiqueta corta, condición).
MAD_ASKS = (
    ('assist', 'Asistencia para mover materiales', 'Asistencia', Q(needs_mad_assistance=True)),
    ('car', 'Movilidad propia (art car)', 'Art car', Q(has_own_mobility=True)),
)


def _mad_base(event):
    """Las propuestas que piden MAD, sin las rechazadas."""
    return event.artworks.filter(ASKS_MAD).exclude(status=Artwork.Status.REJECTED)


def _mad_artworks(event, params):
    """Las propuestas que piden MAD, con el buscador y el filtro de qué piden: (pedidos elegidos, propuestas)."""
    chosen = [ask for ask in MAD_ASKS if ask[0] in params.getlist('ask')]
    artworks = _search_artworks(
        _mad_base(event).select_related('event__art_program', 'owner__profile', 'estafa_contact__profile'), params,
    )
    if chosen:
        condition = Q()
        for _key, _label, _short, ask_condition in chosen:
            condition |= ask_condition
        artworks = artworks.filter(condition)
    return chosen, artworks.order_by(Lower('title'), 'pk')


# Los listados desde los que se le escribe a las propuestas filtradas: (vista del listado, cómo se llama).
BULK_MESSAGE_LISTS = {
    'artworks': ('art_admin_dashboard', 'Propuestas'),
    'grants': ('art_grants_dashboard', 'Becas'),
    'mad': ('art_mad_dashboard', 'MAD'),
}


def _bulk_message_targets(event, source, params, user):
    """Las propuestas que se ven en el listado con esos filtros, en el mismo orden."""
    if source == 'grants':
        artworks = _grant_artworks(event, params, user)
    elif source == 'mad':
        artworks = _mad_artworks(event, params)[1]
    else:
        artworks = _filtered_artworks(event, params, user)
    targets = []
    for artwork in artworks:
        artwork.team_emails = _team_emails(artwork)
        targets.append(artwork)
    return targets


@login_required
def art_bulk_message(request, event_slug):
    """Un mismo mensaje al equipo de cada propuesta filtrada en un listado; queda como mensaje en cada una."""
    event = _managed_event(request, event_slug)
    if not event or not can_message_teams(request.user, event):
        return HttpResponseForbidden('No tenés permisos para escribirle a los equipos de este evento.')
    source = request.GET.get('lista', 'artworks')
    if source not in BULK_MESSAGE_LISTS or (_mad_only(request.user) and source != 'mad'):
        return HttpResponseForbidden('No tenés permisos para escribirle a los equipos de este listado.')
    params = request.GET.copy()
    params.pop('lista', None)
    list_view, list_label = BULK_MESSAGE_LISTS[source]
    list_url = reverse(list_view, args=[event.slug]) + (f'?{params.urlencode()}' if params else '')

    targets = _bulk_message_targets(event, source, params, request.user)
    reachable = [artwork for artwork in targets if artwork.team_emails]
    recipients = _bulk_recipients(reachable)

    form = BulkTeamMessageForm(request.POST or None, initial={'count': len(reachable), 'batch': uuid.uuid4()})
    if request.method == 'POST' and request.POST.get('action') == 'test':
        # Una prueba a quien escribe, tal como le llega a cada persona: no queda registrada ni gasta el envío.
        form.is_valid()
        form.errors.pop('confirm', None)
        if not reachable:
            messages.error(request, 'No hay propuestas con un email al que escribirle con esos filtros.')
        elif not request.user.email:
            messages.error(request, 'Tu cuenta no tiene un email al que mandarte la prueba.')
        elif not form.errors:
            try:
                with get_connection() as connection:
                    connection.send_messages([_bulk_message_email(
                        request.user, form.cleaned_data['subject'], form.cleaned_data['message'],
                        to=[request.user.email],
                    )])
            except Exception:
                logging.exception('No se pudo enviar la prueba del mensaje a las filtradas a %s', request.user.pk)
                messages.error(request, 'No pudimos mandarte la prueba. Volvé a intentar.')
            else:
                messages.success(request, f'Te mandamos una prueba a {request.user.email}.')
    elif request.method == 'POST' and form.is_valid():
        if not reachable:
            messages.error(request, 'No hay propuestas con un email al que escribirle con esos filtros.')
        elif form.cleaned_data['count'] != len(reachable):
            # El listado cambió desde que se abrió la pantalla: se vuelve a mostrar con la cantidad nueva.
            messages.warning(
                request,
                f'El listado cambió: ahora son {len(reachable)} propuestas. Revisalas y volvé a confirmar el envío.',
            )
            data = request.POST.copy()
            data['count'] = len(reachable)
            data.pop('confirm', None)
            form = BulkTeamMessageForm(data)
            form.is_valid()
            form.errors.pop('confirm', None)
        else:
            sent, skipped, failed = _send_bulk_team_message(
                reachable, recipients, request.user, form.cleaned_data['subject'], form.cleaned_data['message'],
                form.cleaned_data['batch'],
            )
            if sent:
                messages.success(
                    request,
                    f'Enviado a {sent} persona{"" if sent == 1 else "s"} de {len(reachable)} '
                    f'propuesta{"" if len(reachable) == 1 else "s"}.',
                )
            if skipped and not sent and not failed:
                messages.info(request, 'Ese mensaje ya se había enviado.')
            if failed:
                messages.error(
                    request,
                    f'No se pudo enviar a {failed} persona{"" if failed == 1 else "s"}. '
                    'Volvé atrás en el navegador y enviá de nuevo: sólo sale a quienes faltan.',
                )
            return redirect(list_url)

    return render(request, 'art/bulk_message.html', {
        **_estafa_context(request.user, event, tab=source),
        'form': form, 'targets': reachable, 'unreachable': [artwork for artwork in targets if not artwork.team_emails],
        'people_count': len(recipients), 'list_url': list_url, 'list_label': list_label,
        'filters_active': bool(params),
    })


# SES acepta hasta 50 destinatarios por mensaje: cada tanda lleva al remitente en «Para» y al resto en copia oculta.
BULK_BCC_CHUNK = 45


def _bulk_recipients(artworks):
    """Cada persona una sola vez aunque esté en varias propuestas, en orden estable."""
    emails = {}
    for artwork in artworks:
        for email in artwork.team_emails:
            emails.setdefault(email.lower(), email)
    return [emails[key] for key in sorted(emails)]


def _no_reply_address():
    """La casilla que no recibe respuestas, en el mismo dominio desde el que salen los emails."""
    domain = parseaddr(settings.DEFAULT_FROM_EMAIL)[1].rpartition('@')[2]
    return getattr(settings, 'NO_REPLY_EMAIL', '') or (f'no-reply@{domain}' if domain else '')


def _bulk_message_email(sender, subject, message, to, bcc=None):
    """El mismo email para todas las personas: no nombra a ninguna propuesta ni recibe respuestas."""
    no_reply = _no_reply_address()
    return get_templated_mail(
        template_name='art_bulk_message',
        context={
            'subject': subject,
            'message_html': mark_safe(richtext.to_email_html(message)),
            'sender_name': person_name(sender),
            'sender_team': art_team_name(sender) or 'ESTAFA',
            'art_path': reverse('art_dashboard'),
            'base_url': settings.APP_URL,
        },
        from_email=settings.DEFAULT_FROM_EMAIL, to=to, bcc=bcc,
        headers={'Reply-To': no_reply} if no_reply else None,
    )


def _send_bulk_team_message(artworks, recipients, sender, subject, message, batch):
    """Una tanda de copia oculta cada 45 personas por una sola conexión SMTP; queda como mensaje en cada propuesta.

    Cada tanda y el registro en el historial se reservan: un doble envío no repite, y un reintento
    sólo manda las tandas que fallaron. Devuelve personas (enviadas, ya enviadas antes, fallidas).
    """
    def reserve(key, email):
        return MessageIdempotency.objects.get_or_create(
            hash=hashlib.sha256(key.encode()).hexdigest(),
            defaults={'email': email, 'payload': {'key': key, 'subject': subject}},
        )

    sent = skipped = failed = 0
    own_address = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    chunks = [recipients[start:start + BULK_BCC_CHUNK] for start in range(0, len(recipients), BULK_BCC_CHUNK)]
    with get_connection() as connection:
        for index, chunk in enumerate(chunks):
            digest = hashlib.sha256(','.join(email.lower() for email in chunk).encode()).hexdigest()
            reservation, created = reserve(f'art-bulk-message:{batch}:chunk:{digest}', chunk[0])
            if not created:
                skipped += len(chunk)
                continue
            try:
                connection.send_messages([_bulk_message_email(sender, subject, message, to=[own_address], bcc=chunk)])
            except Exception:
                reservation.delete()
                failed += len(chunk)
                logging.exception(
                    'No se pudo enviar la tanda %s de %s (%s personas) del mensaje a las filtradas',
                    index + 1, len(chunks), len(chunk),
                )
                continue
            sent += len(chunk)
    if sent or skipped:
        _, created = reserve(f'art-bulk-message:{batch}:history', own_address)
        if created:
            detail = f'{subject}\n\n{richtext.to_text(message)}'
            actor = sender if sender.is_authenticated else None
            ArtworkEvent.objects.bulk_create([
                ArtworkEvent(artwork=artwork, kind=history.MESSAGE, actor=actor, summary='Le escribió al equipo', detail=detail)
                for artwork in artworks
            ])
    return sent, skipped, failed


@login_required
def art_mad_dashboard(request, event_slug):
    """La pestaña de MAD: las propuestas que piden MAD y qué piden. La leen ESTAFA, Becas y MAD."""
    event = _managed_event(request, event_slug)
    if not event:
        return HttpResponseForbidden('No tenés permisos para ver las propuestas de Arte de este evento.')
    params = request.GET
    chosen, artworks = _mad_artworks(event, params)
    return render(request, 'art/mad_dashboard.html', {
        **_estafa_context(request.user, event, tab='mad'),
        'artworks': artworks,
        'ask_choices': [
            (key, label, _mad_base(event).filter(condition).count()) for key, label, _short, condition in MAD_ASKS
        ],
        'selected_asks': [ask[0] for ask in chosen],
        'ask_summary': chosen[0][2] if len(chosen) == 1 else (f'{len(chosen)} pedidos' if chosen else ''),
        'filters_active': bool(chosen or params.get('q', '').strip()),
        'has_artworks': _mad_base(event).exists(),
    })


@login_required
def artwork_mad(request, event_slug, artwork_id):
    """Una propuesta vista desde MAD: lo que pide a MAD, los mensajes al equipo y la propuesta creativa.

    La plantilla nombra uno por uno los campos que muestra: nada de la coordinación de ESTAFA
    (declaración, checkout, proveedores, equipo, seguridad, beca, historial) llega a esta vista.
    """
    event = get_object_or_404(Event, slug=event_slug)
    artwork = get_object_or_404(
        Artwork.objects.select_related('event__art_program', 'owner__profile'), pk=artwork_id, event=event,
    )
    if not artwork.can_view_all(request.user):
        return HttpResponseForbidden('No tenés permisos para ver las propuestas de Arte de este evento.')
    return render(request, 'art/mad_artwork.html', {
        **_estafa_context(request.user, event, tab='mad'),
        'artwork': artwork, 'artwork_files': artwork.files.all(),
        'team_messages': artwork.history.filter(kind=history.MESSAGE).select_related('actor').order_by('-created_at'),
        'can_message': artwork.can_message(request.user),
        'show_full_link': not _mad_only(request.user),
    })


@login_required
def art_grants_export(request, event_slug):
    """El listado de becas con los datos de cobro: sólo para quienes gestionan becas."""
    event = get_object_or_404(Event, slug=event_slug, art_program__isnull=False)
    if not can_manage_grants(request.user, event):
        return HttpResponseForbidden('No tenés permisos para exportar las becas de este evento.')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="becas_{event.slug}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([
        'Propuesta de Arte', 'Estado de la propuesta', 'Responsable', 'Email del responsable', 'WhatsApp del responsable',
        'Estado de la beca', 'Falta', 'Moneda', 'Total del presupuesto', 'Cubre la beca', 'Monto pedido', 'Monto aprobado',
        'Nombre o razón social', 'CUIT/CUIL', 'CBU, CVU o alias',
        'Por qué la beca hace posible la instalación', 'Condiciones aceptadas', 'Solicitud presentada',
        'Última decisión', 'Devolución sobre la beca', 'Fecha de pago', 'Referencia del pago',
    ])

    def moment(value):
        return timezone.localtime(value).strftime('%d/%m/%Y %H:%M') if value else ''

    for artwork in _grant_artworks(event, request.GET, request.user):
        owner = artwork.owner
        writer.writerow([_csv_cell(value) for value in [
            artwork.title,
            artwork.stage_label,
            (owner.get_full_name() or owner.email) if owner else '',
            owner.email if owner else '',
            whatsapp_number(_owner_phone(artwork)),
            artwork.grant_label,
            artwork.grant_missing_short,
            artwork.grant_currency,
            artwork.budget_sum or 0,
            artwork.fundable_sum or 0,
            artwork.grant_requested_amount,
            artwork.grant_approved_amount,
            artwork.grant_payee_name,
            artwork.grant_payee_tax_id,
            artwork.grant_payee_account,
            artwork.grant_justification,
            moment(artwork.grant_terms_accepted_at),
            moment(artwork.grant_submitted_at),
            moment(artwork.grant_decided_at),
            artwork.grant_decision_notes,
            artwork.grant_paid_at.strftime('%d/%m/%Y') if artwork.grant_paid_at else '',
            artwork.grant_payment_reference,
        ]])
    return response


def _owner_phone(artwork):
    try:
        return artwork.owner.profile.phone
    except (AttributeError, ObjectDoesNotExist):
        return ''


def _csv_cell(value):
    text = '' if value is None else str(value)
    return f"'{text}" if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
