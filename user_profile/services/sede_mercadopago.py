import logging
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import mercadopago
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from user_profile.models import Profile

logger = logging.getLogger(__name__)

ACTIVE_SUBSCRIPTION_STATUSES = {'authorized'}
LAST_NAME_SIMILARITY_THRESHOLD = 0.85
FIRST_NAME_SIMILARITY_THRESHOLD = 0.90
SUBSCRIPTION_PAYMENTS_MONTHS = 2
ALL_HINT_SOURCES = {'subscription', 'customer', 'payment', 'invoice'}

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


def _mp_api_get(sdk, uri, filters=None):
    return sdk.preapproval()._get(uri=uri, filters=filters or {})


def _is_within_last_months(value, months=SUBSCRIPTION_PAYMENTS_MONTHS):
    dt = parse_mp_datetime(value)
    if not dt:
        return False
    cutoff = timezone.now() - timedelta(days=months * 31)
    return dt >= cutoff


def fetch_subscription_recent_payments(sdk, subscription_id, log=None, months=SUBSCRIPTION_PAYMENTS_MONTHS):
    """
    Fetch subscription invoices via /authorized_payments/search, filter last N months,
    then load full payment records for payer identification data.
    """
    log = log or logger
    if not subscription_id:
        return [], []

    log.info('  Searching authorized payments for subscription %s', subscription_id)
    invoices = _paginated_search(
        lambda filters: _mp_api_get(sdk, '/authorized_payments/search', filters),
        {'preapproval_id': subscription_id},
        limit=50,
    )
    log.info('  Found %d invoice(s) total', len(invoices))

    recent_invoices = [
        invoice for invoice in invoices
        if _is_within_last_months(invoice.get('debit_date') or invoice.get('date_created'), months)
    ]
    log.info(
        '  %d invoice(s) within last %d month(s)',
        len(recent_invoices),
        months,
    )

    payments = []
    seen_payment_ids = set()
    for invoice in recent_invoices:
        payment_ref = invoice.get('payment') or {}
        payment_id = payment_ref.get('id')
        if not payment_id or payment_id in seen_payment_ids:
            continue
        seen_payment_ids.add(payment_id)
        try:
            response = sdk.payment().get(str(payment_id))
            if response.get('status') == 200:
                payments.append(response.get('response', {}))
                log.info('    Loaded payment %s (status=%s)', payment_id, payment_ref.get('status'))
            else:
                log.warning('    Payment %s fetch failed: %s', payment_id, response.get('status'))
        except Exception:
            log.exception('    Failed to fetch payment %s', payment_id)

    log.info('  Loaded %d full payment record(s)', len(payments))
    return payments, recent_invoices


def _add_payer_hint(hints, source, doc_type=None, doc_number=None,
                    first_name=None, last_name=None):
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


def collect_payer_hints(sdk, subscription_summary, subscription_detail, payments, log=None):
    log = log or logger
    hints = {
        'documents': [],
        'names': [],
    }

    detail = subscription_detail or subscription_summary or {}
    _add_payer_hint(
        hints,
        'subscription',
        first_name=detail.get('payer_first_name'),
        last_name=detail.get('payer_last_name'),
    )
    log.info(
        '  Subscription payer: %s %s',
        detail.get('payer_first_name') or '—',
        detail.get('payer_last_name') or '—',
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
                    doc_type=identification.get('type'),
                    doc_number=identification.get('number'),
                    first_name=customer.get('first_name'),
                    last_name=customer.get('last_name'),
                )
                log.info(
                    '  Customer %s: DNI=%s, %s %s',
                    payer_id,
                    identification.get('number') or '—',
                    customer.get('first_name') or '—',
                    customer.get('last_name') or '—',
                )
            else:
                log.warning('  Customer %s fetch failed: %s', payer_id, response.get('status'))
        except Exception:
            log.exception('  Failed to fetch MP customer %s', payer_id)

    for payment in payments:
        doc_type, doc_number = _extract_identification_from_payment(payment)
        first_name, last_name = _extract_names_from_payment(payment)
        _add_payer_hint(
            hints,
            'payment',
            doc_type=doc_type,
            doc_number=doc_number,
            first_name=first_name,
            last_name=last_name,
        )
        if doc_number or first_name or last_name:
            log.info(
                '  Payment %s: DNI=%s, %s %s',
                payment.get('id') or '—',
                doc_number or '—',
                first_name or '—',
                last_name or '—',
            )

    return hints


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


def match_payer_hints_to_user(hints, user_index):
    seen_documents = set()
    for document in hints['documents']:
        normalized = normalize_document_number(document.get('number'))
        if not normalized or normalized in seen_documents:
            continue
        seen_documents.add(normalized)
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

    for name_hint in hints['names']:
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


def _extract_subscription_details(subscription_summary, subscription_detail, payments=None, invoices=None):
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
    elif invoices:
        latest_invoice = max(
            invoices,
            key=lambda inv: parse_mp_datetime(inv.get('debit_date') or inv.get('date_created'))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        if not last_payment_date:
            last_payment_date = parse_mp_datetime(
                latest_invoice.get('debit_date') or latest_invoice.get('date_created')
            )
        if last_amount in (None, ''):
            last_amount = latest_invoice.get('transaction_amount')

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


def match_subscription_to_user(sdk, subscription_summary, subscription_detail=None, user_index=None, log=None):
    log = log or logger
    if user_index is None:
        user_index = build_user_match_index()

    sub_id = subscription_summary.get('id')
    if not sub_id:
        return None, 'missing_id', None

    if subscription_detail is None:
        log.info('  Fetching subscription detail %s', sub_id)
        response = sdk.preapproval().get(sub_id)
        if response.get('status') != 200:
            log.warning('  Subscription detail fetch failed: %s', response.get('status'))
            return None, 'fetch_failed', None
        subscription_detail = response.get('response', {})

    payments, invoices = fetch_subscription_recent_payments(sdk, sub_id, log=log)
    hints = collect_payer_hints(sdk, subscription_summary, subscription_detail, payments, log=log)
    user, match_method, match_meta = match_payer_hints_to_user(hints, user_index)
    details = _extract_subscription_details(subscription_summary, subscription_detail, payments, invoices)

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
        'invoices_checked': len(invoices),
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


def _deactivate_stale_members(active_subscription_ids, log):
    active_ids = set(active_subscription_ids)
    stale_profiles = Profile.objects.filter(miembro_sede=True).exclude(
        Q(sede_subscription_id='') | Q(sede_subscription_id__in=active_ids)
    )
    stale_count = stale_profiles.count()
    if stale_count:
        log.info('Deactivating %d stale member(s)', stale_count)
        for profile in stale_profiles.select_related('user'):
            log.info(
                '  Deactivated %s (subscription %s no longer active)',
                profile.user.email,
                profile.sede_subscription_id,
            )
    deactivated = stale_profiles.update(
        miembro_sede=False,
        sede_subscription_status='inactive',
        sede_synced_at=timezone.now(),
    )
    return deactivated


def _process_subscription(sdk, subscription_summary, user_index, assigned_users, log):
    sub_id = subscription_summary.get('id', '')
    payer_name = ' '.join(filter(None, [
        subscription_summary.get('payer_first_name'),
        subscription_summary.get('payer_last_name'),
    ])).strip()
    payer_email = subscription_summary.get('payer_email') or '—'
    status = subscription_summary.get('status') or '—'

    result = {
        'subscription_id': sub_id,
        'payer_name': payer_name or '—',
        'payer_email': payer_email,
        'status': status,
        'matched': False,
        'match_method': None,
        'user_email': None,
        'message': '',
    }

    user, match_method, details = match_subscription_to_user(
        sdk,
        subscription_summary,
        user_index=user_index,
        log=log,
    )
    payments_checked = (details or {}).get('payments_checked', 0)
    log.info('  Payments checked: %d', payments_checked)

    if not user:
        result['message'] = _format_unmatched_message(details or {})
        log.info('  No match: %s', result['message'])
        return result, None

    previous_name = assigned_users.get(user.id)
    if previous_name and payer_name and name_similarity(previous_name, payer_name) < 0.5:
        result['message'] = (
            f'Conflicto: {user.email} ya vinculado a "{previous_name}", '
            f'no coincide con "{payer_name}"'
        )
        log.warning('  %s', result['message'])
        return result, None

    apply_subscription_to_profile(user.profile, details)
    assigned_users[user.id] = payer_name or user.get_full_name()

    result.update({
        'matched': True,
        'match_method': match_method,
        'user_email': user.email,
        'message': f'Vinculado con {user.email} ({match_method})',
    })
    if match_method == 'name' and details.get('match_meta'):
        result['message'] += f" — score {details['match_meta'].get('score')}"
    if payments_checked:
        result['message'] += f' — {payments_checked} pago(s)'

    active = details.get('status') in ACTIVE_SUBSCRIPTION_STATUSES
    log.info(
        '  Matched -> %s via %s (active=%s)',
        user.email,
        match_method,
        active,
    )
    return result, sub_id if active else None


def run_full_sync(log=None):
    """Sync all MercadoPago subscriptions with platform users. Returns summary dict."""
    log = log or logger
    log.info('=' * 80)
    log.info('La Sede membership sync started')

    access_token = settings.MERCADOPAGO.get('ACCESS_TOKEN')
    if not access_token:
        log.error('MERCADOPAGO_ACCESS_TOKEN not configured')
        return {'error': 'MERCADOPAGO_ACCESS_TOKEN not configured'}

    plan_ids = get_sede_plan_ids()
    log.info('MercadoPago configured. Plan IDs: %s', plan_ids or 'all')

    sdk = get_mp_sdk()

    log.info('Fetching subscriptions from MercadoPago...')
    subscriptions = fetch_all_subscriptions(sdk)
    total = len(subscriptions)
    log.info('Found %d subscription(s)', total)

    log.info('Building user match index...')
    user_index = build_user_match_index()
    log.info(
        'User index ready: %d emails, %d documents, %d name entries',
        len(user_index['by_email']),
        len(user_index['by_document']),
        len(user_index['by_name']),
    )

    assigned_users = {}
    active_subscription_ids = []
    matched_count = 0
    unmatched_count = 0
    conflict_count = 0
    error_count = 0

    for index, subscription_summary in enumerate(subscriptions, start=1):
        sub_id = subscription_summary.get('id', '—')
        payer_name = ' '.join(filter(None, [
            subscription_summary.get('payer_first_name'),
            subscription_summary.get('payer_last_name'),
        ])).strip() or '—'
        status = subscription_summary.get('status') or '—'

        log.info(
            '[%d/%d] Subscription %s — %s (%s)',
            index,
            total,
            sub_id,
            payer_name,
            status,
        )

        try:
            result, active_sub_id = _process_subscription(
                sdk,
                subscription_summary,
                user_index,
                assigned_users,
                log,
            )
            if result.get('matched'):
                matched_count += 1
                if active_sub_id:
                    active_subscription_ids.append(active_sub_id)
            elif result.get('message', '').startswith('Conflicto'):
                conflict_count += 1
            else:
                unmatched_count += 1
        except Exception as exc:
            error_count += 1
            log.exception('  Error processing subscription %s: %s', sub_id, exc)

    log.info('Deactivating stale members...')
    deactivated = _deactivate_stale_members(active_subscription_ids, log)

    summary = {
        'total': total,
        'matched': matched_count,
        'unmatched': unmatched_count,
        'conflicts': conflict_count,
        'errors': error_count,
        'active_members': len(active_subscription_ids),
        'deactivated': deactivated,
    }

    log.info('=' * 80)
    log.info('La Sede membership sync completed')
    log.info('Total subscriptions: %d', summary['total'])
    log.info('Matched: %d', summary['matched'])
    log.info('Unmatched: %d', summary['unmatched'])
    log.info('Conflicts: %d', summary['conflicts'])
    log.info('Errors: %d', summary['errors'])
    log.info('Active members: %d', summary['active_members'])
    log.info('Deactivated: %d', summary['deactivated'])
    log.info('=' * 80)

    return summary
