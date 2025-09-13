import json
import uuid
from urllib.parse import urlencode

from allauth.account.forms import ResetPasswordForm
from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin, ExportActionMixin, ExportMixin

from deprepagos import settings
from events.models import Event
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
            return HttpResponse(status=204)
        except Exception as e:
            print(f"Error in email_has_account: {str(e)}")
            return HttpResponse(status=500)
    return HttpResponse(status=405)


@staff_member_required
@permission_required("tickets.can_sell_tickets")
def admin_caja_view(request, event_id=None):
    events = Event.objects.all()
    if event_id:
        default_event = Event.objects.get(id=event_id)
    else:
        default_event = Event.get_main_event()
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
@permission_required("tickets.can_sell_tickets")
def admin_direct_tickets_view(request, event_id=None):
    direct_ticket_summary = request.session.pop('direct_ticket_summary', {})
    events = Event.get_active_events()
    default_event = events.first()
    direct_tickets = DirectTicketTemplate.objects.filter(event_id=default_event.id,
                                                         status=DirectTicketTemplateStatus.AVAILABLE) if default_event else None
    ticket_type = TicketType.objects.filter(event_id=default_event.id,
                                            is_direct_type=True).first() if default_event else None

    if request.method == 'POST':
        selected_event_id = request.POST.get('event')
        action = request.POST.get('action')

        if action == "event" and selected_event_id:
            ticket_type = TicketType.objects.filter(event_id=selected_event_id,
                                                    is_direct_type=True).first() if selected_event_id else None
            default_event = Event.objects.get(id=selected_event_id)

            direct_tickets = DirectTicketTemplate.objects.filter(event_id=default_event.id,
                                                                 status=DirectTicketTemplateStatus.AVAILABLE) if default_event else None
        elif action == "order":

            email = request.POST.get('email').lower()
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


class NewTicketInline(admin.StackedInline):
    model = NewTicket
    extra = 0


class DirectTicketTemplateImportResource(resources.ModelResource):

    class Meta:
        model = DirectTicketTemplate
        fields = ['origin', 'name', 'amount']
        exclude = ('id')
        force_init_instance = True

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        if event:
            self.event = event

    def before_save_instance(self, instance, row, **kwargs):
        instance.event = self.event


class DirectTicketTemplateExportResource(DirectTicketTemplateImportResource):
    class Meta(DirectTicketTemplateImportResource.Meta):
        fields = ['origin', 'name', 'amount', 'status']


@admin.register(DirectTicketTemplate)
class DirectTicketTemplateAdmin(ImportExportModelAdmin, ExportActionMixin):
    resource_classes = [DirectTicketTemplateImportResource]
    list_display = ['id', 'origin', 'name', 'amount', 'status', 'event']
    list_display_links = ['id']
    list_filter = ['event__name', 'origin', 'status']
    search_fields = ['event__name', 'name']

    def get_import_resource_kwargs(self, request, *args, **kwargs):
        kwargs = super().get_resource_kwargs(request, *args, **kwargs)
        event = Event.get_main_event()
        kwargs.update({"event": event})
        return kwargs

    def get_export_resource_class(self):
        return DirectTicketTemplateExportResource


class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'event', 'price', 'is_direct_type', 'ticket_count', 'date_from', 'date_to']
    list_filter = ['event__name', 'is_direct_type']
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
            'fields': ('volunteer_ranger', 'volunteer_transmutator', 'volunteer_umpalumpa'),
            'classes': ('collapse',)
        }),
    )


class NewTicketInline(admin.StackedInline):
    model = NewTicket
    extra = 0
    readonly_fields = ['key']


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


class OrderAdmin(ExportActionMixin, ExportMixin, admin.ModelAdmin):
    resource_classes = [OrderResource]
    list_display = ['id', 'first_name', 'last_name', 'email', 'phone', 'dni', 'amount', 'status', 'event', 'order_type', 'created_at', 'donation_art', 'donation_venue', 'donation_grant']
    list_filter = ['event', 'status', 'order_type']
    search_fields = ['key', 'first_name', 'last_name', 'email', 'phone', 'dni']
    inlines = [NewTicketInline]
    readonly_fields = ['key']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            tickets_count=Count('new_tickets')
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
admin.site.register(TicketType, TicketTypeAdmin)
admin.site.register(NewTicket, NewTicketAdmin)
admin.site.register(NewTicketTransfer)
admin.site.register(TicketPhoto, TicketPhotoAdmin)
