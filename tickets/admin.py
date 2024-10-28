import json
import uuid
from urllib.parse import urlencode

from allauth.account.forms import ResetPasswordForm
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialApp, SocialAccount, SocialToken
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User, Group
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse

from deprepagos import settings
from events.models import Event
from utils.direct_sales import direct_sales_existing_user, direct_sales_new_user
from .forms import TicketPurchaseForm
from .models import Profile, TicketType, Order, OrderTicket, NewTicket, NewTicketTransfer, DirectTicketTemplate, \
    DirectTicketTemplateStatus
from .views import webhooks

admin.site.site_header = 'Bonos de Fuego Austral'


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'profile'


# Crea una nueva clase que extienda de LibraryUserAdmin
class CustomUserAdmin(UserAdmin):
    inlines = (ProfileInline,)
    list_display = (
        'is_staff', 'username', 'email', 'first_name', 'last_name', 'get_phone', 'get_document_type',
        'get_document_number')

    def get_phone(self, instance):
        return instance.profile.phone

    get_phone.short_description = 'Phone'

    def get_document_type(self, instance):
        return instance.profile.document_type

    get_document_type.short_description = 'Document Type'

    def get_document_number(self, instance):
        return instance.profile.document_number

    get_document_number.short_description = 'Document Number'


@staff_member_required
def email_has_account(request):
    if request.method == 'POST':

        data = json.loads(request.body)
        email = data.get('email')

        user = User.objects.filter(email=email).first()
        if user:
            if user.profile.profile_completion == 'COMPLETE':
                return JsonResponse({
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'phone': user.profile.phone,
                    'document_type': user.profile.document_type,
                    'document_number': user.profile.document_number,

                })
            return HttpResponse(status=206)
        else:
            return HttpResponse(status=204)
    return HttpResponse(status=405)


@staff_member_required
def admin_caja_view(request):
    events = Event.objects.all()
    default_event = Event.objects.filter(active=True).first()
    ticket_types = TicketType.objects.filter(event_id=default_event.id) if default_event else None

    form = TicketPurchaseForm(event=default_event)

    if request.method == 'POST':
        selected_event_id = request.POST.get('event')
        action = request.POST.get('action')

        print(action)
        if action == "event" and selected_event_id:
            ticket_types = TicketType.objects.filter(event_id=selected_event_id)
            default_event = Event.objects.get(id=selected_event_id)
            form = TicketPurchaseForm(request.POST, event=default_event)
        elif action == "order":
            form = TicketPurchaseForm(request.POST, event=default_event)
            if form.is_valid():
                total_amount = 0
                tickets_quantity = []

                for ticket in form.cleaned_data:
                    if ticket.startswith('ticket_quantity_'):
                        ticket_type_id = ticket.split('_')[2]
                        ticket_type = TicketType.objects.get(id=ticket_type_id)
                        quantity = form.cleaned_data[ticket]
                        total_amount += ticket_type.price * quantity
                        tickets_quantity.append({
                            'ticket_type': ticket_type,
                            'quantity': quantity
                        })

                user = User.objects.filter(email=form.cleaned_data['email']).first()
                new_user = False
                with transaction.atomic():
                    if not user:
                        new_user = True
                        user = User.objects.create_user(
                            username=str(uuid.uuid4()),
                            email=form.cleaned_data['email'],
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
                            email=form.cleaned_data['email'],
                            verified=True,
                            primary=True
                        )

                        reset_form = ResetPasswordForm(data={'email': user.email})

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
                        email=user.email,
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
                        generated_by=request.user
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

                    new_minted_tickets = webhooks.mint_tickets(order)
                    Order.objects.get(key=order.key).send_confirmation_email()

                    # Build the base URL
                    base_url = reverse('admin_caja_order_view', args=[order.key])

                    # Define query parameters
                    query_params = {'new_user': new_user}

                    # Construct the full URL with query parameters
                    url = f"{base_url}?{urlencode(query_params)}"

                    return redirect(url)

    context = {
        'events': events,
        'default_event': default_event,
        'ticket_types': ticket_types,
        'form': form,
    }
    return render(request, 'admin/admin_caja.html', context)


@staff_member_required
def admin_direct_tickets_view(request):
    direct_ticket_summary = request.session.pop('direct_ticket_summary', {})
    events = Event.objects.all()
    default_event = Event.objects.filter(active=True).first()
    direct_tickets = DirectTicketTemplate.objects.filter(event_id=default_event.id,
                                                         status=DirectTicketTemplateStatus.AVAILABLE) if default_event else None
    ticket_type = TicketType.objects.filter(event_id=default_event.id,
                                            is_direct_type=True).first() if default_event else None

    if request.method == 'POST':
        selected_event_id = request.POST.get('event')
        action = request.POST.get('action')

        print(action)
        if action == "event" and selected_event_id:
            ticket_type = TicketType.objects.filter(event_id=selected_event_id,
                                                    is_direct_type=True).first() if selected_event_id else None
            default_event = Event.objects.get(id=selected_event_id)

            direct_tickets = DirectTicketTemplate.objects.filter(event_id=default_event.id,
                                                                 status=DirectTicketTemplateStatus.AVAILABLE) if default_event else None
        elif action == "order":

            print(request.POST)

            email = request.POST.get('email')
            notes = request.POST.get('notes')
            ticket_amounts = {int(k.split('_')[2]): int(v) for k, v in request.POST.items() if
                              k.startswith('ticket_amount_')}

            order_type = request.POST.get('order_type')

            request.session['direct_ticket_summary'] = {
                'email': email,
                'notes': notes,
                'ticket_amounts': ticket_amounts,
                'order_type': order_type,
            }
            return redirect('admin_direct_tickets_buyer_view')

    context = {
        'ticket_type': ticket_type,
        'events': events,
        'default_event': default_event,
        'direct_tickets': direct_tickets,
    }
    return render(request, 'admin/admin_direct_tickets.html', context)


@staff_member_required
def admin_direct_tickets_buyer_view(request):
    direct_ticket_summary = request.session['direct_ticket_summary']

    email = direct_ticket_summary.get('email')
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
        user = User.objects.filter(email=email).first()
        new_order_id = None
        if user is None:
            new_order_id = direct_sales_new_user(email, template_tickets, order_type, notes, request.user)
        else:
            new_order_id = direct_sales_existing_user(user, template_tickets, order_type, notes, request.user)

        return redirect('admin_direct_tickets_congrats_view', new_order_id=new_order_id)

    # if request.method == 'GET':
    return render(request, 'admin/admin_direct_tickets_buyer.html', {
        'user': user,
        'email': email,
        'tickets': template_tickets,
        'notes': notes,
        'order_type': order_type

    })


@staff_member_required
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


@admin.register(DirectTicketTemplate)
class DirectTicketTemplateAdmin(admin.ModelAdmin):
    list_display = ['id', 'origin', 'name', 'amount', 'used', 'event']
    list_editable = ['origin', 'name', 'amount', 'event']  # Campos que pueden ser editados directamente
    list_display_links = ['id']  # El campo 'name' será el enlace a los detalles
    list_filter = ['event__name']  # Filtro por evento
    search_fields = ['event__name']  # Buscar por nombre y evento


# Quitar el registro original y registrar el nuevo UserAdmin
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
admin.site.unregister(SocialApp)
admin.site.unregister(SocialAccount)
admin.site.unregister(SocialToken)
admin.site.unregister(EmailAddress)
admin.site.unregister(Group)

admin.site.register(Order)
admin.site.register(TicketType)
admin.site.register(NewTicket)
admin.site.register(NewTicketTransfer)
