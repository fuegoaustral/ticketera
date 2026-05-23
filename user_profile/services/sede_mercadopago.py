import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

import mercadopago
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from user_profile.models import Profile

logger = logging.getLogger(__name__)

ACTIVE_SUBSCRIPTION_STATUSES = {'authorized'}
SYNC_CACHE_TIMEOUT = 3600
SYNC_CACHE_PREFIX = 'sede_sync_'

PAYMENT_METHOD_LABELS = {
    'account_money': 'Dinero en cuenta',
    'visa': 'Visa',
    'master': 'Mastercard',
    'amex': 'American Express',
    'debvisa': 'Visa Débito',
    'debmaster': 'Mastercard Débito',
    'pagofacil': 'Pago Fácil',
    'rapipago': 'Rapipago',
    'consumer_credits': 'Créditos',
}


def get_sede_plan_ids():
    plan_ids = list(getattr(settings, 'SEDE_SUBSCRIPTION_PLAN_IDS', []) or [])
    default_plan = getattr(settings, 'SEDE_DEFAULT_PLAN_ID', '')
    if default_plan and default_plan not in plan_ids:
        plan_ids.append(default_plan)
    return plan_ids


def normalize_document_number(document_number):
    if not document_number:
        return ''
    digits = re.sub(r'\D', '', str(document_number))
    return digits.lstrip('0') or '0'


def format_payment_method(payment_method_id):
    if not payment_method_id:
        return '—'
    return PAYMENT_METHOD_LABELS.get(payment_method_id, payment_method_id.replace('_', ' ').title())


def parse_mp_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = parse_datetime(str(value))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def get_mp_sdk():
    return mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])


def fetch_all_subscriptions(sdk=None):
    sdk = sdk or get_mp_sdk()
    plan_ids = get_sede_plan_ids()
    seen_ids = set()
    subscriptions = []

    search_configs = [{'preapproval_plan_id': plan_id} for plan_id in plan_ids] if plan_ids else [{}]

    for base_filters in search_configs:
        for status in ACTIVE_SUBSCRIPTION_STATUSES:
            offset = 0
            limit = 50
            while True:
                filters = {**base_filters, 'status': status, 'limit': limit, 'offset': offset}
                response = sdk.preapproval().search(filters)
                if response.get('status') != 200:
                    logger.warning('MP preapproval search failed: %s', response)
                    break

                data = response.get('response', {})
                results = data.get('results', [])
                for item in results:
                    sub_id = item.get('id')
                    if sub_id and sub_id not in seen_ids:
                        seen_ids.add(sub_id)
                        subscriptions.append(item)

                paging = data.get('paging', {})
                total = paging.get('total', 0)
                offset += limit
                if offset >= total or not results:
                    break

    return subscriptions


def _extract_identification_from_payment(payment):
    payer = payment.get('payer') or {}
    identification = payer.get('identification') or {}
    doc_type = identification.get('type')
    doc_number = identification.get('number')
    if doc_number:
        return doc_type, doc_number
    return None, None


def _find_user_by_email(email):
    if not email:
        return None
    email = email.strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user:
        return user
    email_address = EmailAddress.objects.filter(email__iexact=email).select_related('user').first()
    return email_address.user if email_address else None


def _find_user_by_document(document_number):
    normalized = normalize_document_number(document_number)
    if not normalized:
        return None

    for profile in Profile.objects.exclude(document_number='').select_related('user'):
        if normalize_document_number(profile.document_number) == normalized:
            return profile.user
    return None


def _get_document_from_customer(sdk, payer_id):
    if not payer_id:
        return None, None
    try:
        response = sdk.customer().get(str(payer_id))
        if response.get('status') != 200:
            return None, None
        customer = response.get('response', {})
        identification = customer.get('identification') or {}
        return identification.get('type'), identification.get('number')
    except Exception:
        logger.exception('Failed to fetch MP customer %s', payer_id)
        return None, None


def _get_document_from_payments(sdk, payer_id, preapproval_id):
    if not payer_id:
        return None, None
    try:
        response = sdk.payment().search({
            'payer.id': payer_id,
            'sort': 'date_created',
            'criteria': 'desc',
            'limit': 20,
        })
        if response.get('status') != 200:
            return None, None

        for payment in response.get('response', {}).get('results', []):
            metadata = payment.get('metadata') or {}
            if preapproval_id and metadata.get('preapproval_id') not in (None, preapproval_id):
                continue
            doc_type, doc_number = _extract_identification_from_payment(payment)
            if doc_number:
                return doc_type, doc_number
    except Exception:
        logger.exception('Failed to fetch MP payments for payer %s', payer_id)
    return None, None


def _extract_subscription_details(subscription_summary, subscription_detail):
    detail = subscription_detail or subscription_summary or {}
    summarized = detail.get('summarized') or {}
    auto_recurring = detail.get('auto_recurring') or {}

    last_payment_date = parse_mp_datetime(summarized.get('last_charged_date'))
    next_payment_date = parse_mp_datetime(detail.get('next_payment_date'))
    member_since = parse_mp_datetime(detail.get('date_created'))

    last_amount = summarized.get('last_charged_amount')
    if last_amount in (None, ''):
        last_amount = auto_recurring.get('transaction_amount')

    try:
        last_payment_amount = Decimal(str(last_amount)) if last_amount not in (None, '') else None
    except (InvalidOperation, TypeError, ValueError):
        last_payment_amount = None

    return {
        'subscription_id': detail.get('id') or subscription_summary.get('id'),
        'status': detail.get('status') or subscription_summary.get('status') or '',
        'payment_method': detail.get('payment_method_id') or subscription_summary.get('payment_method_id') or '',
        'payer_email': (
            detail.get('payer_email')
            or subscription_summary.get('payer_email')
            or ''
        ),
        'payer_id': detail.get('payer_id') or subscription_summary.get('payer_id'),
        'payer_first_name': detail.get('payer_first_name') or subscription_summary.get('payer_first_name') or '',
        'payer_last_name': detail.get('payer_last_name') or subscription_summary.get('payer_last_name') or '',
        'last_payment_date': last_payment_date,
        'last_payment_amount': last_payment_amount,
        'next_payment_date': next_payment_date,
        'member_since': member_since,
    }


def match_subscription_to_user(sdk, subscription_summary, subscription_detail=None):
    if subscription_detail is None:
        sub_id = subscription_summary.get('id')
        if not sub_id:
            return None, 'missing_id', None
        response = sdk.preapproval().get(sub_id)
        if response.get('status') != 200:
            return None, 'fetch_failed', None
        subscription_detail = response.get('response', {})

    details = _extract_subscription_details(subscription_summary, subscription_detail)
    payer_email = details['payer_email']
    payer_id = details['payer_id']
    preapproval_id = details['subscription_id']

    user = _find_user_by_email(payer_email)
    match_method = 'email' if user else None
    document_number = None
    document_type = None

    if not user:
        document_type, document_number = _get_document_from_customer(sdk, payer_id)
        if document_number:
            user = _find_user_by_document(document_number)
            match_method = 'customer_dni' if user else None

    if not user:
        document_type, document_number = _get_document_from_payments(sdk, payer_id, preapproval_id)
        if document_number:
            user = _find_user_by_document(document_number)
            match_method = 'payment_dni' if user else None

    return user, match_method, {
        **details,
        'document_type': document_type,
        'document_number': document_number,
    }


def apply_subscription_to_profile(profile, details):
    profile.miembro_sede = details.get('status') in ACTIVE_SUBSCRIPTION_STATUSES
    profile.sede_subscription_id = details.get('subscription_id') or ''
    profile.sede_subscription_status = details.get('status') or ''
    profile.sede_payment_method = details.get('payment_method') or ''
    profile.sede_last_payment_date = details.get('last_payment_date')
    profile.sede_last_payment_amount = details.get('last_payment_amount')
    profile.sede_next_payment_date = details.get('next_payment_date')
    profile.sede_member_since = details.get('member_since')
    profile.sede_synced_at = timezone.now()
    profile.save(update_fields=[
        'miembro_sede',
        'sede_subscription_id',
        'sede_subscription_status',
        'sede_payment_method',
        'sede_last_payment_date',
        'sede_last_payment_amount',
        'sede_next_payment_date',
        'sede_member_since',
        'sede_synced_at',
        'updated_at',
    ])


def start_sync():
    sdk = get_mp_sdk()
    subscriptions = fetch_all_subscriptions(sdk)
    sync_id = str(uuid.uuid4())
    cache.set(
        f'{SYNC_CACHE_PREFIX}{sync_id}',
        {
            'subscriptions': subscriptions,
            'total': len(subscriptions),
            'processed': 0,
            'active_subscription_ids': [],
            'results': [],
            'done': False,
        },
        SYNC_CACHE_TIMEOUT,
    )
    return sync_id, len(subscriptions)


def process_next_subscription(sync_id):
    cache_key = f'{SYNC_CACHE_PREFIX}{sync_id}'
    state = cache.get(cache_key)
    if not state:
        return {'error': 'Sync session expired. Please start again.'}

    if state['done']:
        return {
            'done': True,
            'total': state['total'],
            'processed': state['processed'],
            'result': state['results'][-1] if state['results'] else None,
            'summary': _build_sync_summary(state),
        }

    subscriptions = state['subscriptions']
    index = state['processed']
    if index >= len(subscriptions):
        _finalize_sync(state)
        cache.set(cache_key, state, SYNC_CACHE_TIMEOUT)
        return {
            'done': True,
            'total': state['total'],
            'processed': state['processed'],
            'summary': _build_sync_summary(state),
        }

    sdk = get_mp_sdk()
    subscription_summary = subscriptions[index]
    sub_id = subscription_summary.get('id', '')
    payer_name = ' '.join(filter(None, [
        subscription_summary.get('payer_first_name'),
        subscription_summary.get('payer_last_name'),
    ])).strip()

    result = {
        'subscription_id': sub_id,
        'payer_name': payer_name or '—',
        'payer_email': subscription_summary.get('payer_email') or '—',
        'status': subscription_summary.get('status') or '—',
        'matched': False,
        'match_method': None,
        'user_email': None,
        'message': '',
    }

    try:
        user, match_method, details = match_subscription_to_user(sdk, subscription_summary)
        if user:
            apply_subscription_to_profile(user.profile, details)
            state['active_subscription_ids'].append(sub_id)
            result.update({
                'matched': True,
                'match_method': match_method,
                'user_email': user.email,
                'message': f'Vinculado con {user.email} ({match_method})',
            })
        else:
            doc_hint = details.get('document_number') if details else None
            if doc_hint:
                result['message'] = f'Sin match (DNI MP: {doc_hint})'
            elif subscription_summary.get('payer_email'):
                result['message'] = f'Sin match (email MP: {subscription_summary.get("payer_email")})'
            else:
                result['message'] = 'Sin match (no se encontró DNI ni email)'
    except Exception as exc:
        logger.exception('Error syncing subscription %s', sub_id)
        result['message'] = f'Error: {exc}'

    state['processed'] += 1
    state['results'].append(result)

    if state['processed'] >= state['total']:
        _finalize_sync(state)
        cache.set(cache_key, state, SYNC_CACHE_TIMEOUT)
        return {
            'done': True,
            'total': state['total'],
            'processed': state['processed'],
            'result': result,
            'summary': _build_sync_summary(state),
        }

    cache.set(cache_key, state, SYNC_CACHE_TIMEOUT)
    return {
        'done': False,
        'total': state['total'],
        'processed': state['processed'],
        'result': result,
    }


def _finalize_sync(state):
    active_ids = set(state.get('active_subscription_ids', []))
    stale_profiles = Profile.objects.filter(miembro_sede=True).exclude(
        Q(sede_subscription_id='') | Q(sede_subscription_id__in=active_ids)
    )
    deactivated = stale_profiles.update(
        miembro_sede=False,
        sede_subscription_status='inactive',
        sede_synced_at=timezone.now(),
    )
    state['deactivated'] = deactivated
    state['matched'] = len(active_ids)
    state['done'] = True


def _build_sync_summary(state):
    results = state.get('results', [])
    matched = sum(1 for item in results if item.get('matched'))
    unmatched = len(results) - matched
    return {
        'total': state.get('total', 0),
        'matched': matched,
        'unmatched': unmatched,
        'deactivated': state.get('deactivated', 0),
    }
