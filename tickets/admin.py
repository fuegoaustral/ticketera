import json
import re
import uuid
from urllib.parse import urlencode

from allauth.account.forms import ResetPasswordForm
from allauth.account.models import EmailAddress
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Value
from django.db.models.functions import Replace
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin, ExportActionMixin, ExportMixin

from deprepagos import settings
from events.models import Event
from user_profile.models import Profile
from utils.direct_sales import direct_sales_existing_user, direct_sales_new_user
from .forms import TicketPurchaseForm
from .models import TicketType, Order, OrderTicket, NewTicket, NewTicketTransfer, DirectTicketTemplate, \
    DirectTicketTemplateStatus, TicketPhoto
from .processing import mint_tickets
from .views import webhooks

admin.site.site_header = 'Bonos de Fuego Austral'


@staff_member_required
@permission_required("tickets.can_sell_tickets")
def email_has_account(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email', '').lower()
            
            # First try to find user by User.email
            user = User.objects.filter(email=email).first()
            
            # If not found, try to find user by EmailAddress (check all emails)
            if not user:
                email_address = EmailAddress.objects.filter(email=email).first()
                if email_address:
                    user = email_address.user
            
            if user:
                if user.profile.profile_completion == 'COMPLETE':
                    # Get all emails for this user
                    all_emails = EmailAddress.objects.filter(user=user).values_list('email', flat=True)
                    
                    return JsonResponse({
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'phone': user.profile.phone,
                        'document_type': user.profile.document_type,
                        'document_number': user.profile.document_number,
                        'emails': list(all_emails),
                    })
                return HttpResponse(status=206)
            return HttpResponse(status=204)
        except Exception as e:
            print(f"Error in email_has_account: {str(e)}")
            return HttpResponse(status=500)
    return HttpResponse(status=405)


CAJA_SEARCH_LIMIT = 15


def _caja_user_payload(user, tickets_in_event=0):
    profile = user.profile
    emails = sorted({a.email for a in user.emailaddress_set.all()} | ({user.email} if user.email else set()))
    return {
        'id': user.id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'nickname': profile.nickname,
        'email': user.email,
        'emails': emails,
        'document_type': profile.document_type,
        'document_number': profile.document_number,
        'phone': profile.phone,
        'profile_complete': profile.profile_completion == Profile.COMPLETE,
        'tickets_in_event': tickets_in_event,
    }


def search_caja_users(query, event=None, limit=CAJA_SEARCH_LIMIT):
    """Busca cuentas por nombre, apellido, apodo, email (principal o secundario), documento o teléfono.

    Cada palabra de la búsqueda tiene que matchear algún campo (así "laura 1000" encuentra a Laura con DNI 1000...).
    Los puntos y espacios de DNI/teléfono se ignoran.
    """
    terms = query.split()
    if not terms:
        return []
    users = User.objects.annotate(
        doc_clean=Replace(Replace('profile__document_number', Value('.'), Value('')), Value(' '), Value('')),
    )
    for term in terms:
        digits = re.sub(r'[\s.\-+()]', '', term)
        condition = (
            Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(profile__nickname__icontains=term)
            | Q(email__icontains=term)
            | Q(emailaddress__email__icontains=term)
        )
        if digits.isdigit() and len(digits) >= 3:
            condition |= Q(doc_clean__icontains=digits) | Q(profile__phone__icontains=digits)
        else:
            condition |= Q(profile__document_number__icontains=term)
        users = users.filter(condition)

    ids = list(users.order_by('first_name', 'last_name').values_list('id', flat=True).distinct()[:limit])
    found = User.objects.filter(id__in=ids).select_related('profile').prefetch_related('emailaddress_set')
    counts = _tickets_in_event_by_user(event, ids)
    by_id = {u.id: u for u in found}
    return [_caja_user_payload(by_id[i], counts.get(i, 0)) for i in ids if i in by_id]


def _tickets_in_event_by_user(event, user_ids):
    """Cantidad de bonos del evento que cada usuario tiene (como dueño o titular)."""
    if event is None or not user_ids:
        return {}
    counts = {}
    rows = NewTicket.objects.filter(event=event).filter(
        Q(owner_id__in=user_ids) | Q(holder_id__in=user_ids)
    ).values_list('owner_id', 'holder_id')
    for owner_id, holder_id in rows:
        for user_id in {owner_id, holder_id} & set(user_ids):
            counts[user_id] = counts.get(user_id, 0) + 1
    return counts


def _find_user_by_any_email(email):
    email = (email or '').strip().lower()
    if not email:
        return None
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        address = EmailAddress.objects.filter(email__iexact=email).select_related('user').first()
        user = address.user if address else None
    return user


@staff_member_required
@permission_required("tickets.can_sell_tickets")
def admin_caja_user_search(request):
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})
    event = Event.objects.filter(id=request.GET.get('event')).first() if request.GET.get('event') else None
    return JsonResponse({'results': search_caja_users(query, event)})


def _caja_event(request):
    event_id = request.POST.get('event_id') or request.GET.get('event')
    if event_id:
        event = Event.objects.filter(id=event_id).first()
        if event:
            return event
    return Event.get_main_event()


@staff_member_required
@permission_required("tickets.can_sell_tickets")
def admin_caja_view(request, event_id=None):
    events = Event.objects.order_by('-start')
    default_event = Event.objects.filter(id=event_id).first() if event_id else _caja_event(request)

    form = TicketPurchaseForm(event=default_event)
    selected_user = None

    if request.method == 'POST':
        data = request.POST.copy()
        user_id = data.get('user_id')
        if user_id:
            selected_user = User.objects.filter(id=user_id).select_related('profile').first()
            if selected_user is None:
                messages.error(request, 'La cuenta elegida no existe.')
                return redirect(f"{reverse('admin_caja_view')}?event={default_event.id}")
            # Los datos personales salen de la cuenta, no del formulario.
            data.update({
                'email': selected_user.email,
                'first_name': selected_user.first_name,
                'last_name': selected_user.last_name,
                'document_type': selected_user.profile.document_type,
                'document_number': selected_user.profile.document_number,
                'phone': selected_user.profile.phone,
            })
        form = TicketPurchaseForm(data, event=default_event)

        if selected_user and selected_user.profile.profile_completion != Profile.COMPLETE:
            form.add_error(None, f'La cuenta {selected_user.email} todavía no completó su perfil.')
        if not selected_user and form.is_valid() and _find_user_by_any_email(form.cleaned_data['email']):
            form.add_error('email', 'Ya existe una cuenta con ese email: buscala y elegila.')

        if form.is_valid():
            total_amount = 0
            tickets_quantity = []

            for ticket in form.cleaned_data:
                if ticket.startswith('ticket_quantity_'):
                    ticket_type_id = ticket.split('_')[2]
                    ticket_type = TicketType.objects.get(id=ticket_type_id)
                    quantity = form.cleaned_data[ticket] or 0
                    total_amount += (ticket_type.price or 0) * quantity
                    tickets_quantity.append({
                        'ticket_type': ticket_type,
                        'quantity': quantity
                    })

            user = selected_user
            new_user = False
            with transaction.atomic():
                if not user:
                    new_user = True
                    user = User.objects.create_user(
                        username=str(uuid.uuid4()),
                        email=form.cleaned_data['email'].lower(),
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                    )
                    user.profile.phone = form.cleaned_data['phone']
                    user.profile.document_type = form.cleaned_data['document_type']
                    user.profile.document_number = form.cleaned_data['document_number']
                    user.profile.profile_completion = 'COMPLETE'
                    user.save()

                    EmailAddress.objects.create(
                        user=user,
                        email=form.cleaned_data['email'].lower(),
                        verified=True,
                        primary=True
                    )

                    reset_form = ResetPasswordForm(data={'email': user.email.lower()})

                    if reset_form.is_valid():
                        reset_form.save(
                            subject_template_name='account/email/password_reset_subject.txt',
                            email_template_name='account/email/password_reset_email.html',
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            request=None,  # Not needed in this context
                            use_https=False,  # Use True if your site uses HTTPS
                            html_email_template_name=None,
                            extra_email_context=None
                        )

                order = Order(
                    first_name=user.first_name,
                    last_name=user.last_name,
                    email=user.email.lower(),
                    phone=user.profile.phone,
                    dni=user.profile.document_number,
                    amount=total_amount,
                    status=Order.OrderStatus.CONFIRMED,
                    event=default_event,
                    user=user,
                    order_type=form.cleaned_data['order_type'],
                    donation_art=0,
                    donation_venue=0,
                    donation_grant=0,
                    notes=form.cleaned_data['notes'],
                    generated_by_admin_user=request.user
                )
                order.save()

                order_tickets = [
                    OrderTicket(
                        order=order,
                        ticket_type=ticket_type,
                        quantity=quantity
                    )
                    for ticket_quantity in tickets_quantity
                    if (ticket_type := ticket_quantity['ticket_type']) and (
                        quantity := ticket_quantity['quantity']) > 0
                ]
                if order_tickets:
                    OrderTicket.objects.bulk_create(order_tickets)

                mint_tickets(order)
                Order.objects.get(key=order.key).send_confirmation_email()

            url = f"{reverse('admin_caja_order_view', args=[order.key])}?{urlencode({'new_user': new_user})}"
            return redirect(url)

    ticket_types = list(
        TicketType.objects.filter(event_id=default_event.id).order_by('cardinality', 'price', 'name')
        if default_event else []
    )
    for ticket_type in ticket_types:
        try:
            ticket_type.selected_quantity = max(0, int(request.POST.get(f'ticket_quantity_{ticket_type.id}', 0)))
        except ValueError:
            ticket_type.selected_quantity = 0
    selected_user_json = None
    if selected_user:
        selected_user_json = _caja_user_payload(
            selected_user, _tickets_in_event_by_user(default_event, [selected_user.id]).get(selected_user.id, 0),
        )

    context = {
        'events': events,
        'default_event': default_event,
        'ticket_types': ticket_types,
        'form': form,
        'selected_user_json': selected_user_json,
        'search_url': reverse('admin_caja_user_search'),
    }
    return render(request, 'admin/admin_caja.html', context)


@staff_member_required
@permission_required("tickets.can_sell_tickets")
def admin_direct_tickets_view(request, event_id=None):
    events = Event.get_active_events()
    default_event = events.first()
    
    # Obtener todos los bonos dirigidos del evento (no solo los disponibles)
    direct_tickets = DirectTicketTemplate.objects.filter(event_id=default_event.id) if default_event else DirectTicketTemplate.objects.none()
    ticket_type = TicketType.objects.filter(event_id=default_event.id, is_direct_type=True).first() if default_event else None

    if request.method == 'POST':
        selected_event_id = request.POST.get('event')
        action = request.POST.get('action')
        
        if action == 'event' and selected_event_id:
            default_event = Event.objects.get(id=selected_event_id)
            direct_tickets = DirectTicketTemplate.objects.filter(event_id=default_event.id)
            ticket_type = TicketType.objects.filter(event_id=default_event.id, is_direct_type=True).first()
        elif action == 'assign':
            new_order_id, error = _assign_direct_ticket_template(
                request.POST.get('template_id'), request.POST.get('quantity'), request.user,
            )
            if new_order_id:
                return redirect('admin_direct_tickets_congrats_view', new_order_id=new_order_id)
            messages.error(request, error)
            return redirect('admin_direct_tickets_view')

    direct_tickets = list(direct_tickets.order_by('name').prefetch_related(
        Prefetch('orders', queryset=Order.objects.select_related('generated_by_admin_user').order_by('created_at')),
        Prefetch('orders__new_tickets', queryset=NewTicket.objects.select_related('holder', 'owner', 'ticket_type')),
    ))
    _attach_direct_ticket_users(direct_tickets)
    for template in direct_tickets:
        template.assigned_orders = list(template.orders.all())

    context = {
        'events': events,
        'default_event': default_event,
        'direct_tickets': direct_tickets,
        'ticket_type': ticket_type,
        'totals': {
            'amount': sum(t.amount for t in direct_tickets),
            'used': sum(t.amount_used or 0 for t in direct_tickets),
            'available': sum(max(t.available, 0) for t in direct_tickets),
        },
    }
    return render(request, 'admin/admin_direct_tickets.html', context)


def _attach_direct_ticket_users(templates):
    """Agrega a cada bono dirigido `assign_user` (la cuenta de su email, principal o secundario, o None) y `available`."""
    emails = {t.email.strip().lower() for t in templates if t.email}
    users_by_email = {}
    if emails:
        for user in User.objects.filter(email__in=emails).select_related('profile'):
            users_by_email[user.email.lower()] = user
        for address in EmailAddress.objects.filter(email__in=emails).select_related('user__profile'):
            users_by_email.setdefault(address.email.lower(), address.user)
    for template in templates:
        template.assign_user = users_by_email.get((template.email or '').strip().lower())
        template.available = template.amount - (template.amount_used or 0)


def _assign_direct_ticket_template(template_id, quantity, request_user):
    """Asigna `quantity` bonos (parte o totalidad) de un dirigido a la cuenta de su email.

    Devuelve (order_id, None) o (None, error).
    """
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return None, 'La cantidad tiene que ser un número.'
    if quantity < 1:
        return None, 'La cantidad tiene que ser al menos 1.'

    with transaction.atomic():
        template = DirectTicketTemplate.objects.select_for_update().filter(id=template_id).first()
        if template is None:
            return None, 'El bono dirigido no existe.'
        available = template.amount - (template.amount_used or 0)
        if available <= 0:
            return None, f'"{template.name}" ya no tiene bonos disponibles.'
        if quantity > available:
            return None, f'"{template.name}": la cantidad excede el máximo disponible ({available}).'

        user = template.get_user()
        if user is None:
            return None, f'"{template.name}": no existe ninguna cuenta con el email {template.email or "(vacío)"}.'
        if user.profile.profile_completion != Profile.COMPLETE:
            return None, f'"{template.name}": la cuenta {user.email} todavía no completó su perfil.'

        new_order_id = direct_sales_existing_user(
            user,
            [{
                'id': template.id,
                'name': template.name,
                'origin': template.origin,
                'amount': quantity,
                'event_id': template.event_id,
            }],
            Order.OrderType.OTHER,
            f'Bono dirigido: {template.name}',
            request_user,
        )
    return new_order_id, None


@staff_member_required
@permission_required("tickets.can_sell_tickets")
def admin_direct_tickets_buyer_view(request):
    direct_ticket_summary = request.session['direct_ticket_summary']

    email = direct_ticket_summary.get('email').lower()
    notes = direct_ticket_summary.get('notes')
    ticket_amounts = direct_ticket_summary.get('ticket_amounts')
    order_type = direct_ticket_summary.get('order_type')

    user = User.objects.filter(email=email).first()

    templates = DirectTicketTemplate.objects.filter(id__in=ticket_amounts.keys()).all()

    template_tickets = []
    for template in templates:
        template_tickets.append({
            'id': template.id,
            'name': template.name,
            'origin': template.origin,
            'amount': ticket_amounts.get(str(template.id), 0),
            'event_id': template.event.id

        })

    if request.method == 'POST':
        new_order_id = None
        if user is None:
            new_order_id = direct_sales_new_user(email.lower(), template_tickets, order_type, notes, request.user)
        else:
            new_order_id = direct_sales_existing_user(user, template_tickets, order_type, notes, request.user)

        return redirect('admin_direct_tickets_congrats_view', new_order_id=new_order_id)

    elif request.method == 'GET':
        return render(request, 'admin/admin_direct_tickets_buyer.html', {
            'user': user,
            'email': email,
            'tickets': template_tickets,
            'notes': notes,
            'order_type': order_type

        })


@staff_member_required
@permission_required("tickets.can_sell_tickets")
def admin_direct_tickets_congrats_view(request, new_order_id):
    tickets = NewTicket.objects.filter(order_id=new_order_id).all()
    order = Order.objects.get(id=new_order_id)
    return render(request, 'admin/admin_direct_tickets_congrats.html', {'tickets': tickets, 'order': order})


def admin_caja_order_view(request, order_key):
    new_user = request.GET.get('new_user', True)  # Default to True if not provided
    new_user = new_user in ['true', 'True', True]

    order = Order.objects.get(key=order_key)
    tickets = NewTicket.objects.filter(order=order).all()

    return render(request, 'admin/admin_caja_summary.html', {
        'order': order,
        'tickets': tickets,
        'new_user': new_user,
    })


class EventListFilter(admin.SimpleListFilter):
    """Filtro por evento, del más nuevo al más viejo."""
    title = 'evento'
    parameter_name = 'event'

    def lookups(self, request, model_admin):
        return [(e.id, e.name) for e in Event.objects.order_by('-start')]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(event_id=self.value())
        return queryset


class DirectTicketTemplateImportResource(resources.ModelResource):

    class Meta:
        model = DirectTicketTemplate
        fields = ['origin', 'name', 'amount', 'email']
        exclude = ('id')
        force_init_instance = True
        # Corre DirectTicketTemplate.clean(): rechaza filas cuyo email no tiene cuenta.
        clean_model_instances = True

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event

    def after_init_instance(self, instance, new, row, **kwargs):
        # Se asigna antes de validar, para que full_clean() no falle por evento vacío.
        instance.event = self.event


class DirectTicketTemplateExportResource(DirectTicketTemplateImportResource):
    class Meta(DirectTicketTemplateImportResource.Meta):
        fields = ['origin', 'name', 'amount', 'email', 'status']


@admin.register(DirectTicketTemplate)
class DirectTicketTemplateAdmin(ImportExportModelAdmin, ExportActionMixin):
    resource_classes = [DirectTicketTemplateImportResource]
    list_display = ['origin', 'name', 'amount', 'amount_used', 'email', 'status', 'event', 'orders_display']
    list_display_links = ['name']
    list_filter = [EventListFilter, 'origin', 'status']
    list_select_related = ['event']
    search_fields = ['event__name', 'name', 'email']
    exclude = ['orders']
    raw_id_fields = ['order']
    readonly_fields = ['orders_display']

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('orders')

    @admin.display(description='Órdenes generadas')
    def orders_display(self, template):
        orders = sorted(template.orders.all(), key=lambda o: o.created_at)
        if not orders:
            return '-'
        return format_html_join(
            ', ', '<a href="{}">#{}</a>',
            ((reverse('admin:tickets_order_change', args=[o.pk]), o.pk) for o in orders),
        )

    def get_import_resource_kwargs(self, request, *args, **kwargs):
        kwargs = super().get_resource_kwargs(request, *args, **kwargs)
        event = Event.get_main_event()
        kwargs.update({"event": event})
        return kwargs

    def get_export_resource_class(self):
        return DirectTicketTemplateExportResource


@admin.register(TicketType)
class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'event', 'price', 'is_direct_type', 'show_in_caja', 'do_not_show_in_checkout', 'ignore_max_amount', 'ticket_count', 'date_from', 'date_to']
    list_filter = ['event__name', 'is_direct_type', 'show_in_caja', 'do_not_show_in_checkout', 'ignore_max_amount']
    search_fields = ['name', 'event__name']


class TicketPhotoInline(admin.TabularInline):
    model = TicketPhoto
    extra = 0
    readonly_fields = ['photo', 'uploaded_by', 'created_at']
    fields = ['photo', 'uploaded_by', 'created_at']
    
    def has_add_permission(self, request, obj=None):
        return False  # Prevent adding photos through admin


class NewTicketAdmin(admin.ModelAdmin):
    list_display = ['owner', 'ticket_type', 'holder', 'order_id', 'event', 'is_used', 'scanned_by', 'used_at', 'created_at']
    list_filter = ['event__name', 'ticket_type__name', 'order__status', 'is_used', 'scanned_by']
    search_fields = [
        'holder__first_name',
        'holder__last_name',
        'holder__email',
        'owner__first_name',
        'owner__last_name',
        'owner__email',
        'scanned_by__first_name',
        'scanned_by__last_name',
        'scanned_by__email',
        'key',
    ]
    readonly_fields = ['key', 'used_at', 'scanned_by']
    inlines = [TicketPhotoInline]
    fieldsets = (
        ('Información del Ticket', {
            'fields': ('key', 'event', 'ticket_type', 'order', 'owner', 'holder')
        }),
        ('Estado de Uso', {
            'fields': ('is_used', 'used_at', 'scanned_by', 'notes')
        }),
        ('Voluntariado', {
            'fields': ('volunteer_ranger', 'volunteer_transmutator', 'volunteer_umpalumpa', 'volunteer_mad'),
            'classes': ('collapse',)
        }),
    )


class OrderResource(resources.ModelResource):

    tickets_count = fields.Field(attribute='tickets_count')

    class Meta:
        model = Order
        fields = [
            'id',
            'key',
            'first_name',
            'last_name',
            'email',
            'phone',
            'dni',
            'donation_art',
            'donation_venue',
            'donation_grant',
            'amount',
            'tickets_count',
            'coupon',
            'event__name',
            'user__first_name',
            'user__last_name',
            'status',
            'order_type',
            'notes',
            'generated_by_admin_user__last_name',
            'generated_by_admin_user__first_name',
            'created_at',
            'updated_at',
        ]


class NewTicketInline(admin.TabularInline):
    model = NewTicket
    extra = 0
    verbose_name = 'Bono emitido'
    verbose_name_plural = 'Bonos emitidos'
    fields = ['ticket_link', 'ticket_type', 'owner', 'holder', 'is_used', 'used_at']
    readonly_fields = fields
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('ticket_type', 'owner', 'holder')

    @admin.display(description='Bono')
    def ticket_link(self, ticket):
        url = reverse('admin:tickets_newticket_change', args=[ticket.pk])
        return format_html('<a href="{}">{}…</a>', url, str(ticket.key)[:8])


class OrderTicketInline(admin.TabularInline):
    model = OrderTicket
    extra = 0
    verbose_name = 'Tipo de bono comprado'
    verbose_name_plural = 'Tipos de bono comprados'
    fields = ['ticket_type', 'quantity']
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class OrderTicketTypeListFilter(admin.SimpleListFilter):
    """Tipo de bono emitido en la orden; si hay un evento elegido, solo muestra los tipos de ese evento."""
    title = 'tipo de bono'
    parameter_name = 'ticket_type'

    def lookups(self, request, model_admin):
        ticket_types = TicketType.objects.select_related('event').order_by('-event__start', 'cardinality', 'name')
        event_id = request.GET.get(EventListFilter.parameter_name)
        if event_id:
            return [(t.id, t.name) for t in ticket_types.filter(event_id=event_id)]
        return [(t.id, f'{t.name} ({t.event.name})') for t in ticket_types]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(
                Q(new_tickets__ticket_type_id=self.value()) | Q(order_tickets__ticket_type_id=self.value())
            ).distinct()
        return queryset


class GeneratedByAdminListFilter(admin.SimpleListFilter):
    title = 'origen'
    parameter_name = 'origen'

    def lookups(self, request, model_admin):
        return [
            ('online', 'Compra en la web'),
            ('admin', 'Generada por admin (caja / dirigidos)'),
            ('dirigido', 'Bono dirigido'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'online':
            return queryset.filter(generated_by_admin_user__isnull=True)
        if self.value() == 'admin':
            return queryset.filter(generated_by_admin_user__isnull=False)
        if self.value() == 'dirigido':
            return queryset.filter(direct_ticket_templates__isnull=False).distinct()
        return queryset


class OrderAdmin(ExportActionMixin, ExportMixin, admin.ModelAdmin):
    resource_classes = [OrderResource]
    list_display = [
        'id', 'created_at', 'event', 'buyer', 'tickets_summary', 'amount', 'status_badge', 'order_type',
        'generated_by_admin_user',
    ]
    list_display_links = ['id', 'buyer']
    list_filter = [
        EventListFilter, OrderTicketTypeListFilter, 'status', 'order_type', GeneratedByAdminListFilter,
        'created_at',
    ]
    list_select_related = ['event', 'generated_by_admin_user']
    list_per_page = 50
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    search_fields = ['=id', 'key', 'first_name', 'last_name', 'email', 'phone', 'dni', 'user__email', 'notes']
    search_help_text = 'Buscá por número de orden, key, nombre, email, teléfono, DNI o notas.'
    inlines = [NewTicketInline, OrderTicketInline]
    raw_id_fields = ['user', 'coupon', 'generated_by_admin_user']
    readonly_fields = ['key', 'tickets_summary', 'direct_ticket_templates_display', 'created_at', 'updated_at']
    fieldsets = (
        ('Orden', {
            'fields': (
                ('event', 'status'),
                ('order_type', 'amount', 'net_received_amount'),
                'tickets_summary',
                'direct_ticket_templates_display',
                'notes',
                ('generated_by_admin_user', 'coupon'),
                ('created_at', 'updated_at'),
                'key',
            ),
        }),
        ('Comprador', {
            'fields': (('first_name', 'last_name'), ('email', 'phone', 'dni'), 'user'),
        }),
        ('Donaciones', {
            'fields': (('donation_art', 'donation_venue', 'donation_grant'),),
            'classes': ('collapse',),
        }),
        ('Pago (datos crudos)', {
            'fields': ('response', 'processor_callback'),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            tickets_count=Count('new_tickets', distinct=True)
        ).prefetch_related('new_tickets__ticket_type', 'order_tickets__ticket_type')

    @admin.display(description='Comprador', ordering='last_name')
    def buyer(self, order):
        name = f'{order.first_name} {order.last_name}'.strip() or '(sin nombre)'
        return format_html('{}<br><small style="color: var(--body-quiet-color)">{}</small>', name, order.email)

    @admin.display(description='Bonos', ordering='tickets_count')
    def tickets_summary(self, order):
        """Bonos emitidos por tipo, ej. "2 × General · 1 × Menor". Si no se emitieron, lo comprado."""
        counts = {}
        for ticket in order.new_tickets.all():
            counts[ticket.ticket_type.name] = counts.get(ticket.ticket_type.name, 0) + 1
        if counts:
            return ' · '.join(f'{qty} × {name}' for name, qty in counts.items())
        bought = [f'{ot.quantity} × {ot.ticket_type.name}' for ot in order.order_tickets.all() if ot.quantity]
        if bought:
            return format_html('<span title="Comprados, todavía sin emitir">{} (sin emitir)</span>', ' · '.join(bought))
        return '-'

    @admin.display(description='Estado', ordering='status')
    def status_badge(self, order):
        colors = {
            Order.OrderStatus.CONFIRMED: '#2e7d32',
            Order.OrderStatus.PENDING: '#b26a00',
            Order.OrderStatus.PROCESSING: '#1565c0',
            Order.OrderStatus.ERROR: '#c62828',
            Order.OrderStatus.REFUNDED: '#6a1b9a',
        }
        return format_html(
            '<span style="color: {}; font-weight: bold">{}</span>',
            colors.get(order.status, 'inherit'), order.get_status_display(),
        )

    @admin.display(description='Bonos dirigidos')
    def direct_ticket_templates_display(self, order):
        templates = order.direct_ticket_templates.all()
        if not templates:
            return '-'
        return format_html_join(
            format_html('<br>'), '<a href="{}">{}</a> ({})',
            ((reverse('admin:tickets_directtickettemplate_change', args=[t.pk]), t.name, t.get_origin_display())
             for t in templates),
        )


class TicketPhotoAdmin(admin.ModelAdmin):
    list_display = ['ticket', 'uploaded_by', 'created_at']
    list_filter = ['ticket__event__name', 'uploaded_by', 'created_at']
    search_fields = [
        'ticket__key',
        'ticket__holder__first_name',
        'ticket__holder__last_name',
        'ticket__holder__email',
        'uploaded_by__first_name',
        'uploaded_by__last_name',
        'uploaded_by__email',
    ]
    readonly_fields = ['ticket', 'uploaded_by', 'created_at']
    
    def has_add_permission(self, request):
        return False  # Prevent adding photos through admin


admin.site.register(Order, OrderAdmin)
admin.site.register(NewTicket, NewTicketAdmin)
admin.site.register(NewTicketTransfer)
admin.site.register(TicketPhoto, TicketPhotoAdmin)
