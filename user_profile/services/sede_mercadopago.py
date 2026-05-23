import logging
import re
import unicodedata
import uuid
from difflib import SequenceMatcher
from datetime import datetime
from decimal import Decimal, InvalidOperation

import mercadopago
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from user_profile.models import Profile

logger = logging.getLogger(__name__)

ACTIVE_SUBSCRIPTION_STATUSES = {'authorized'}
SYNC_CACHE_TIMEOUT = 3600
SYNC_CACHE_PREFIX = 'sede_sync_'
LAST_NAME_SIMILARITY_THRESHOLD = 0.85
FIRST_NAME_SIMILARITY_THRESHOLD = 0.90
MAX_SUBSCRIPTION_PAYMENTS = 30
PRIMARY_SOURCES = {'subscription', 'customer'}
PAYMENT_SOURCES = {'payment'}

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


def normalize_name(name):
    if not name:
        return ''
    normalized = unicodedata.normalize('NFKD', str(name))
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized.lower())
    return re.sub(r'\s+', ' ', normalized).strip()


def name_similarity(left, right):
    left = normalize_name(left)
    right = normalize_name(right)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


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


def _paginated_search(search_fn, filters, limit=50):
    results = []
    offset = 0
    while True:
        response = search_fn({**filters, 'limit': limit, 'offset': offset})
        if response.get('status') != 200:
            logger.warning('MP search failed (%s): %s', filters, response)
            break

        data = response.get('response', {})
        batch = data.get('results', [])
        results.extend(batch)

        paging = data.get('paging', {})
        total = paging.get('total', len(results))
        offset += limit
        if offset >= total or not batch:
            break
    return results


def fetch_all_subscriptions(sdk=None):
    """Fetch every subscription on the MercadoPago account."""
    sdk = sdk or get_mp_sdk()
    seen_ids = set()
    subscriptions = []

    for item in _paginated_search(sdk.preapproval().search, {}):
        sub_id = item.get('id')
        if sub_id and sub_id not in seen_ids:
            seen_ids.add(sub_id)
            subscriptions.append(item)

    return subscriptions


def build_user_match_index():
    by_email = {}
    by_document = {}
    by_name = []

    profiles = Profile.objects.select_related('user').prefetch_related('user__emailaddress_set')
    for profile in profiles:
        user = profile.user
        emails = {user.email.strip().lower()} if user.email else set()
        emails.update(
            email.strip().lower()
            for email in user.emailaddress_set.values_list('email', flat=True)
            if email
        )
        for email in emails:
            by_email[email] = user

        normalized_document = normalize_document_number(profile.document_number)
        if normalized_document:
            by_document[normalized_document] = user

        by_name.append({
            'user': user,
            'first_name': normalize_name(user.first_name),
            'last_name': normalize_name(user.last_name),
        })

    return {
        'by_email': by_email,
        'by_document': by_document,
        'by_name': by_name,
    }


def _extract_identification_from_payment(payment):
    payer = payment.get('payer') or {}
    identification = payer.get('identification') or {}
    doc_type = identification.get('type')
    doc_number = identification.get('number')
    if doc_number:
        return doc_type, doc_number

    additional_info = payment.get('additional_info') or {}
    additional_payer = additional_info.get('payer') or {}
    identification = additional_payer.get('identification') or {}
    doc_type = identification.get('type')
    doc_number = identification.get('number')
    if doc_number:
        return doc_type, doc_number

    return None, None


def _extract_names_from_payment(payment):
    payer = payment.get('payer') or {}
    first_name = payer.get('first_name') or ''
    last_name = payer.get('last_name') or ''

    if not first_name and not last_name:
        additional_info = payment.get('additional_info') or {}
        additional_payer = additional_info.get('payer') or {}
        first_name = additional_payer.get('first_name') or first_name
        last_name = additional_payer.get('last_name') or last_name

    if not first_name and not last_name:
        card = payment.get('card') or {}
        cardholder = card.get('cardholder') or {}
        full_name = cardholder.get('name') or ''
        if full_name:
            parts = full_name.split()
            if len(parts) >= 2:
                first_name = parts[0]
                last_name = ' '.join(parts[1:])
            else:
                last_name = full_name

    return first_name, last_name


def _extract_email_from_payment(payment):
    payer = payment.get('payer') or {}
    email = payer.get('email') or ''
    if email:
        return email
    additional_info = payment.get('additional_info') or {}
    additional_payer = additional_info.get('payer') or {}
    return additional_payer.get('email') or ''


def _payment_belongs_to_subscription(payment, subscription_id):
    if not subscription_id:
        return False

    metadata = payment.get('metadata') or {}
    metadata_values = {
        str(metadata.get('preapproval_id') or ''),
        str(metadata.get('subscription_id') or ''),
    }
    if subscription_id in metadata_values:
        return True

    description = str(payment.get('description') or '')
    external_reference = str(payment.get('external_reference') or '')
    if subscription_id in description or subscription_id in external_reference:
        return True

    order = payment.get('order') or {}
    order_type = str(order.get('type') or '').lower()
    if order_type == 'mercadopago' and subscription_id in str(order.get('id') or ''):
        return True

    return False


def _get_collector_payer_id():
    collector_id = settings.MERCADOPAGO.get('COLLECTOR_USER_ID')
    if not collector_id:
        return None
    try:
        return int(collector_id)
    except (TypeError, ValueError):
        return None


def fetch_payments_for_subscription(sdk, subscription_id, payer_id=None):
    """Fetch only payments that belong to this specific subscription."""
    if not subscription_id:
        return []

    seen_payment_ids = set()
    payments = []
    collector_payer_id = _get_collector_payer_id()

    if payer_id and collector_payer_id and int(payer_id) == collector_payer_id:
        payer_id = None

    if payer_id:
        for payment in _paginated_search(
            sdk.payment().search,
            {'payer.id': payer_id, 'sort': 'date_created', 'criteria': 'desc'},
            limit=50,
        ):
            payment_id = payment.get('id')
            if payment_id in seen_payment_ids:
                continue
            if not _payment_belongs_to_subscription(payment, subscription_id):
                continue
            seen_payment_ids.add(payment_id)
            payments.append(payment)
            if len(payments) >= MAX_SUBSCRIPTION_PAYMENTS:
                break

    if not payments:
        for payment in _paginated_search(
            sdk.payment().search,
            {'q': subscription_id, 'sort': 'date_created', 'criteria': 'desc'},
            limit=50,
        ):
            payment_id = payment.get('id')
            if payment_id in seen_payment_ids:
                continue
            if not _payment_belongs_to_subscription(payment, subscription_id):
                continue
            seen_payment_ids.add(payment_id)
            payments.append(payment)
            if len(payments) >= MAX_SUBSCRIPTION_PAYMENTS:
                break

    return payments


def _add_payer_hint(hints, source, email=None, doc_type=None, doc_number=None,
                    first_name=None, last_name=None):
    if email:
        normalized_email = email.strip().lower()
        hints['emails'].add(normalized_email)
        hints['email_sources'].setdefault(normalized_email, set()).add(source)
    if doc_number:
        hints['documents'].append({
            'source': source,
            'type': doc_type,
            'number': doc_number,
        })
    if first_name or last_name:
        hints['names'].append({
            'source': source,
            'first_name': first_name or '',
            'last_name': last_name or '',
        })


def collect_payer_hints(sdk, subscription_summary, subscription_detail, payments):
    hints = {
        'emails': set(),
        'email_sources': {},
        'documents': [],
        'names': [],
    }

    detail = subscription_detail or subscription_summary or {}
    _add_payer_hint(
        hints,
        'subscription',
        email=detail.get('payer_email'),
        first_name=detail.get('payer_first_name'),
        last_name=detail.get('payer_last_name'),
    )

    payer_id = detail.get('payer_id') or subscription_summary.get('payer_id')
    if payer_id:
        try:
            response = sdk.customer().get(str(payer_id))
            if response.get('status') == 200:
                customer = response.get('response', {})
                identification = customer.get('identification') or {}
                _add_payer_hint(
                    hints,
                    'customer',
                    email=customer.get('email'),
                    doc_type=identification.get('type'),
                    doc_number=identification.get('number'),
                    first_name=customer.get('first_name'),
                    last_name=customer.get('last_name'),
                )
        except Exception:
            logger.exception('Failed to fetch MP customer %s', payer_id)

    for payment in payments:
        doc_type, doc_number = _extract_identification_from_payment(payment)
        first_name, last_name = _extract_names_from_payment(payment)
        _add_payer_hint(
            hints,
            'payment',
            email=_extract_email_from_payment(payment),
            doc_type=doc_type,
            doc_number=doc_number,
            first_name=first_name,
            last_name=last_name,
        )

    return hints


def _find_user_by_email(email, user_index, payer_first_name='', payer_last_name=''):
    if not email:
        return None
    user = user_index['by_email'].get(email.strip().lower())
    if not user:
        return None
    if payer_last_name and user.last_name:
        if name_similarity(payer_last_name, user.last_name) < 0.5:
            return None
    return user


def _find_user_by_document(document_number, user_index):
    normalized = normalize_document_number(document_number)
    if not normalized:
        return None
    return user_index['by_document'].get(normalized)


def _find_user_by_name(first_name, last_name, user_index):
    target_last = normalize_name(last_name)
    target_first = normalize_name(first_name)
    if not target_last:
        return None, 0.0

    best_user = None
    best_score = 0.0

    for candidate in user_index['by_name']:
        last_sim = name_similarity(target_last, candidate['last_name'])
        if last_sim < LAST_NAME_SIMILARITY_THRESHOLD:
            continue

        if target_first and candidate['first_name']:
            first_sim = name_similarity(target_first, candidate['first_name'])
        elif not target_first or not candidate['first_name']:
            first_sim = 1.0
        else:
            first_sim = 0.0

        if first_sim < FIRST_NAME_SIMILARITY_THRESHOLD:
            continue

        score = (last_sim + first_sim) / 2
        if score > best_score:
            best_score = score
            best_user = candidate['user']

    return best_user, best_score


def _match_from_hints(hints, user_index, allowed_sources, payer_first_name='', payer_last_name=''):
    allowed_documents = [doc for doc in hints['documents'] if doc.get('source') in allowed_sources]
    allowed_names = [name for name in hints['names'] if name.get('source') in allowed_sources]
    allowed_emails = {
        email for email in hints['emails']
        if any(source in allowed_sources for source in hints.get('email_sources', {}).get(email, []))
    } if hints.get('email_sources') else set()

    for document in allowed_documents:
        user = _find_user_by_document(document.get('number'), user_index)
        if user:
            return user, 'dni', {
                'document_type': document.get('type'),
                'document_number': document.get('number'),
                'source': document.get('source'),
            }

    best_user = None
    best_meta = None
    best_score = 0.0

    for name_hint in allowed_names:
        user, score = _find_user_by_name(
            name_hint.get('first_name'),
            name_hint.get('last_name'),
            user_index,
        )
        if user and score > best_score:
            best_user = user
            best_meta = {
                'first_name': name_hint.get('first_name'),
                'last_name': name_hint.get('last_name'),
                'score': round(score, 3),
                'source': name_hint.get('source'),
            }
            best_score = score

    if best_user:
        return best_user, 'name', best_meta

    for email in allowed_emails:
        user = _find_user_by_email(
            email,
            user_index,
            payer_first_name=payer_first_name,
            payer_last_name=payer_last_name,
        )
        if user:
            return user, 'email', {'email': email}

    return None, None, None


def match_payer_hints_to_user(hints, user_index, payer_first_name='', payer_last_name=''):
    user, method, meta = _match_from_hints(
        hints,
        user_index,
        PRIMARY_SOURCES,
        payer_first_name=payer_first_name,
        payer_last_name=payer_last_name,
    )
    if user:
        return user, method, meta

    user, method, meta = _match_from_hints(
        hints,
        user_index,
        PAYMENT_SOURCES,
        payer_first_name=payer_first_name,
        payer_last_name=payer_last_name,
    )
    if user:
        return user, method, meta

    return None, None, _summarize_unmatched_hints(hints)


def _summarize_unmatched_hints(hints):
    document_numbers = []
    seen_documents = set()
    for document in hints['documents']:
        normalized = normalize_document_number(document.get('number'))
        if normalized and normalized not in seen_documents:
            seen_documents.add(normalized)
            document_numbers.append(document.get('number'))

    names = [
        ' '.join(filter(None, [name.get('first_name'), name.get('last_name')])).strip()
        for name in hints['names']
    ]

    return {
        'emails': sorted(hints['emails']),
        'document_numbers': document_numbers,
        'names': [name for name in names if name],
    }


def _pick_latest_payment(payments):
    if not payments:
        return None

    def sort_key(payment):
        dt = parse_mp_datetime(payment.get('date_approved') or payment.get('date_created'))
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    approved = [payment for payment in payments if payment.get('status') == 'approved']
    candidates = approved or payments
    return max(candidates, key=sort_key)


def _extract_subscription_details(subscription_summary, subscription_detail, payments=None):
    detail = subscription_detail or subscription_summary or {}
    summarized = detail.get('summarized') or {}
    auto_recurring = detail.get('auto_recurring') or {}

    last_payment_date = parse_mp_datetime(summarized.get('last_charged_date'))
    last_amount = summarized.get('last_charged_amount')
    payment_method = detail.get('payment_method_id') or subscription_summary.get('payment_method_id') or ''

    latest_payment = _pick_latest_payment(payments or [])
    if latest_payment:
        if not last_payment_date:
            last_payment_date = parse_mp_datetime(
                latest_payment.get('date_approved') or latest_payment.get('date_created')
            )
        if last_amount in (None, ''):
            last_amount = latest_payment.get('transaction_amount')
        if not payment_method:
            payment_method = latest_payment.get('payment_method_id') or ''

    next_payment_date = parse_mp_datetime(detail.get('next_payment_date'))
    member_since = parse_mp_datetime(detail.get('date_created'))

    try:
        last_payment_amount = Decimal(str(last_amount)) if last_amount not in (None, '') else None
    except (InvalidOperation, TypeError, ValueError):
        last_payment_amount = None

    return {
        'subscription_id': detail.get('id') or subscription_summary.get('id'),
        'status': detail.get('status') or subscription_summary.get('status') or '',
        'payment_method': payment_method,
        'payer_email': detail.get('payer_email') or subscription_summary.get('payer_email') or '',
        'payer_id': detail.get('payer_id') or subscription_summary.get('payer_id'),
        'payer_first_name': detail.get('payer_first_name') or subscription_summary.get('payer_first_name') or '',
        'payer_last_name': detail.get('payer_last_name') or subscription_summary.get('payer_last_name') or '',
        'last_payment_date': last_payment_date,
        'last_payment_amount': last_payment_amount,
        'next_payment_date': next_payment_date,
        'member_since': member_since,
    }


def match_subscription_to_user(sdk, subscription_summary, subscription_detail=None, user_index=None):
    if user_index is None:
        user_index = build_user_match_index()

    sub_id = subscription_summary.get('id')
    if not sub_id:
        return None, 'missing_id', None

    if subscription_detail is None:
        response = sdk.preapproval().get(sub_id)
        if response.get('status') != 200:
            return None, 'fetch_failed', None
        subscription_detail = response.get('response', {})

    payer_id = (
        subscription_detail.get('payer_id')
        or subscription_summary.get('payer_id')
    )
    payments = fetch_payments_for_subscription(sdk, sub_id, payer_id=payer_id)
    hints = collect_payer_hints(sdk, subscription_summary, subscription_detail, payments)
    payer_first_name = (
        subscription_detail.get('payer_first_name')
        or subscription_summary.get('payer_first_name')
        or ''
    )
    payer_last_name = (
        subscription_detail.get('payer_last_name')
        or subscription_summary.get('payer_last_name')
        or ''
    )
    user, match_method, match_meta = match_payer_hints_to_user(
        hints,
        user_index,
        payer_first_name=payer_first_name,
        payer_last_name=payer_last_name,
    )
    details = _extract_subscription_details(subscription_summary, subscription_detail, payments)

    document_number = None
    document_type = None
    if match_meta and match_method == 'dni':
        document_number = match_meta.get('document_number')
        document_type = match_meta.get('document_type')
    elif hints['documents']:
        document_number = hints['documents'][0].get('number')
        document_type = hints['documents'][0].get('type')

    return user, match_method, {
        **details,
        'document_type': document_type,
        'document_number': document_number,
        'payments_checked': len(payments),
        'match_meta': match_meta,
        'hints': _summarize_unmatched_hints(hints),
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
    user_index = build_user_match_index()
    sync_id = str(uuid.uuid4())
    cache.set(
        f'{SYNC_CACHE_PREFIX}{sync_id}',
        {
            'subscriptions': subscriptions,
            'user_index': user_index,
            'total': len(subscriptions),
            'processed': 0,
            'active_subscription_ids': [],
            'assigned_users': {},
            'results': [],
            'done': False,
        },
        SYNC_CACHE_TIMEOUT,
    )
    return sync_id, len(subscriptions)


def _format_unmatched_message(details):
    hints = details.get('hints') or {}
    parts = []

    if hints.get('document_numbers'):
        parts.append('DNI: ' + ', '.join(hints['document_numbers'][:3]))
    if hints.get('names'):
        parts.append('Nombre: ' + ', '.join(hints['names'][:3]))
    if hints.get('emails'):
        parts.append('Email: ' + ', '.join(hints['emails'][:3]))

    payments_checked = details.get('payments_checked')
    if payments_checked is not None:
        parts.append(f'{payments_checked} pago(s) revisados')

    if parts:
        return 'Sin match (' + ' | '.join(parts) + ')'
    return 'Sin match (sin datos de pagador)'


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
        user, match_method, details = match_subscription_to_user(
            sdk,
            subscription_summary,
            user_index=state.get('user_index'),
        )
        if user:
            assigned_users = state.setdefault('assigned_users', {})
            previous_name = assigned_users.get(user.id)
            if previous_name and payer_name and name_similarity(previous_name, payer_name) < 0.5:
                result['message'] = (
                    f'Conflicto: {user.email} ya vinculado a "{previous_name}", '
                    f'no coincide con "{payer_name}"'
                )
            else:
                apply_subscription_to_profile(user.profile, details)
                assigned_users[user.id] = payer_name or user.get_full_name()
                if details.get('status') in ACTIVE_SUBSCRIPTION_STATUSES:
                    state['active_subscription_ids'].append(sub_id)
                result.update({
                    'matched': True,
                    'match_method': match_method,
                    'user_email': user.email,
                    'message': f'Vinculado con {user.email} ({match_method})',
                })
                if match_method == 'name' and details.get('match_meta'):
                    result['message'] += f" — score {details['match_meta'].get('score')}"
                if details.get('payments_checked'):
                    result['message'] += f" — {details['payments_checked']} pago(s)"
        else:
            result['message'] = _format_unmatched_message(details or {})
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
