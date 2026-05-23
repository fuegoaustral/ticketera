import logging
import os
import random
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import mercadopago
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from user_profile.models import (
    Profile,
    SedeSubscription,
    SedeSubscriptionPlan,
    SedeUnmatchedSubscription,
)

logger = logging.getLogger(__name__)

ACTIVE_SUBSCRIPTION_STATUSES = {'authorized'}
LAST_NAME_SIMILARITY_THRESHOLD = 0.85
FIRST_NAME_SIMILARITY_THRESHOLD = 0.90
SUBSCRIPTION_PAYMENTS_LOOKBACK_DAYS = 375
ALL_HINT_SOURCES = {'subscription', 'customer', 'payment', 'invoice'}
API_RETRY_ATTEMPTS = 4
API_RETRY_BASE_SECONDS = 0.75
PAYMENT_FETCH_WORKERS = int(os.environ.get('SEDE_SYNC_PAYMENT_FETCH_WORKERS', '4'))

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
    try:
        enabled_db_plan_ids = list(
            SedeSubscriptionPlan.objects.filter(is_enabled=True).values_list('plan_id', flat=True)
        )
    except Exception:
        enabled_db_plan_ids = []

    if enabled_db_plan_ids:
        return enabled_db_plan_ids

    plan_ids = list(getattr(settings, 'SEDE_SUBSCRIPTION_PLAN_IDS', []) or [])
    default_plan = getattr(settings, 'SEDE_DEFAULT_PLAN_ID', '')
    if default_plan and default_plan not in plan_ids:
        plan_ids.append(default_plan)
    return plan_ids


def refresh_sede_subscription_plans(log=None):
    log = log or logger
    sdk = get_mp_sdk()
    subscriptions = fetch_all_subscriptions(sdk=sdk)

    plan_catalog = {}
    for sub in subscriptions:
        plan_id = (sub.get('preapproval_plan_id') or '').strip()
        if not plan_id:
            continue
        plan_name = (sub.get('reason') or '').strip()
        item = plan_catalog.setdefault(plan_id, {'name': '', 'count': 0})
        item['count'] += 1
        if plan_name and (not item['name'] or len(plan_name) > len(item['name'])):
            item['name'] = plan_name

    refreshed = 0
    now = timezone.now()
    for plan_id, info in plan_catalog.items():
        SedeSubscriptionPlan.objects.update_or_create(
            plan_id=plan_id,
            defaults={
                'plan_name': info['name'] or '',
                'subscriptions_count': info['count'],
                'last_seen_at': now,
            },
        )
        refreshed += 1

    log.info('Refreshed %d MercadoPago subscription plan(s)', refreshed)
    return {
        'total_subscriptions': len(subscriptions),
        'refreshed_plans': refreshed,
    }


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


def _retry_delay_seconds(attempt, response=None):
    # Honor Retry-After if SDK exposes headers, otherwise exponential backoff + jitter.
    headers = (response or {}).get('headers') or {}
    retry_after = headers.get('Retry-After') or headers.get('retry-after')
    if retry_after:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            pass
    return API_RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.25)


def _call_with_backoff(search_fn, params):
    last_response = None
    for attempt in range(API_RETRY_ATTEMPTS):
        response = search_fn(params)
        status = response.get('status')
        last_response = response
        if status == 200:
            return response
        if status in (429, 500, 502, 503, 504):
            delay = _retry_delay_seconds(attempt, response=response)
            logger.warning(
                'MP transient error %s for %s. Retry %d/%d in %.2fs',
                status,
                params,
                attempt + 1,
                API_RETRY_ATTEMPTS,
                delay,
            )
            time.sleep(delay)
            continue
        return response
    return last_response or {'status': 500, 'response': {'message': 'No response'}}


def _paginated_search(search_fn, filters, limit=50):
    results = []
    offset = 0
    while True:
        response = _call_with_backoff(search_fn, {**filters, 'limit': limit, 'offset': offset})
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


def fetch_all_subscriptions(sdk=None, plan_ids=None):
    """Fetch every subscription on the MercadoPago account."""
    sdk = sdk or get_mp_sdk()
    seen_ids = set()
    subscriptions = []
    allowed_plan_ids = set(plan_ids or [])

    for item in _paginated_search(sdk.preapproval().search, {}):
        sub_id = item.get('id')
        plan_id = item.get('preapproval_plan_id') or ''
        if allowed_plan_ids and plan_id not in allowed_plan_ids:
            continue
        if sub_id and sub_id not in seen_ids:
            seen_ids.add(sub_id)
            subscriptions.append(item)

    return subscriptions


def build_user_match_index():
    by_email = {}
    by_document = {}
    by_name = []

    log = logger
    log.info('Loading profiles for match index...')
    try:
        profiles_qs = Profile.objects.select_related('user').prefetch_related('user__emailaddress_set')
        processed = 0
        for profile in profiles_qs.iterator(chunk_size=500):
            user = profile.user
            emails = {user.email.strip().lower()} if user.email else set()
            # Use prefetched related objects to avoid one query per user (N+1).
            emails.update(
                email_obj.email.strip().lower()
                for email_obj in user.emailaddress_set.all()
                if getattr(email_obj, 'email', None)
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

            processed += 1
            if processed % 200 == 0:
                log.info('  Indexed %d profiles...', processed)
    except DatabaseError:
        log.exception('Database error while building user match index')
        raise

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


def _is_within_lookback_window(value, days=SUBSCRIPTION_PAYMENTS_LOOKBACK_DAYS):
    dt = parse_mp_datetime(value)
    if not dt:
        return False
    cutoff = timezone.now() - timedelta(days=days)
    return dt >= cutoff


def fetch_subscription_recent_payments(sdk, subscription_id, log=None, lookback_days=SUBSCRIPTION_PAYMENTS_LOOKBACK_DAYS):
    """
    Fetch subscription invoices via /authorized_payments/search, filter last N months,
    then load full payment records for payer identification data.
    """
    log = log or logger
    if not subscription_id:
        return [], []

    log.info('  Searching authorized payments for subscription %s', subscription_id)
    # MercadoPago authorized payments search can reject high limit values.
    # In this account, limit=10 is accepted consistently.
    invoices = _paginated_search(
        lambda filters: _mp_api_get(sdk, '/authorized_payments/search', filters),
        {'preapproval_id': subscription_id},
        limit=10,
    )
    log.info('  Found %d invoice(s) total', len(invoices))

    recent_invoices = [
        invoice for invoice in invoices
        if _is_within_lookback_window(invoice.get('debit_date') or invoice.get('date_created'), lookback_days)
    ]
    log.info(
        '  %d invoice(s) within last %d day(s)',
        len(recent_invoices),
        lookback_days,
    )

    payment_ids = []
    seen_payment_ids = set()
    for invoice in recent_invoices:
        payment_ref = invoice.get('payment') or {}
        payment_id = payment_ref.get('id')
        if not payment_id or payment_id in seen_payment_ids:
            continue
        seen_payment_ids.add(payment_id)
        payment_ids.append(str(payment_id))

    payments = []
    if payment_ids:
        worker_count = max(1, min(PAYMENT_FETCH_WORKERS, len(payment_ids), 6))
        log.info('  Fetching %d payment detail(s) with %d worker(s)', len(payment_ids), worker_count)

        def fetch_payment(payment_id):
            response = _call_with_backoff(
                lambda _: sdk.payment().get(str(payment_id)),
                {},
            )
            return payment_id, response

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(fetch_payment, pid): pid for pid in payment_ids}
            for future in as_completed(future_map):
                payment_id = future_map[future]
                try:
                    _, response = future.result()
                    if response.get('status') == 200:
                        payments.append(response.get('response', {}))
                        log.info('    Loaded payment %s', payment_id)
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
    summary = subscription_summary or {}
    sub_first_name = detail.get('payer_first_name') or summary.get('payer_first_name')
    sub_last_name = detail.get('payer_last_name') or summary.get('payer_last_name')
    _add_payer_hint(
        hints,
        'subscription',
        first_name=sub_first_name,
        last_name=sub_last_name,
    )
    log.info(
        '  Subscription payer: %s %s',
        sub_first_name or '—',
        sub_last_name or '—',
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
        'plan_id': detail.get('preapproval_plan_id') or subscription_summary.get('preapproval_plan_id') or '',
        'tier_name': detail.get('reason') or subscription_summary.get('reason') or '',
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


def _refresh_profile_membership_summary(profile):
    subs_qs = profile.sede_subscriptions.all()
    active_subs = subs_qs.filter(is_active=True)
    primary = (
        active_subs.order_by('-last_payment_date', '-synced_at').first()
        or subs_qs.order_by('-last_payment_date', '-synced_at').first()
    )

    profile.miembro_sede = active_subs.exists()
    if primary:
        profile.sede_subscription_id = primary.subscription_id or ''
        profile.sede_subscription_status = primary.status or ''
        profile.sede_payment_method = primary.payment_method or ''
        profile.sede_last_payment_date = primary.last_payment_date
        profile.sede_last_payment_amount = primary.last_payment_amount
        profile.sede_next_payment_date = primary.next_payment_date
        profile.sede_member_since = primary.member_since
        profile.sede_synced_at = primary.synced_at or timezone.now()
    else:
        profile.sede_subscription_id = ''
        profile.sede_subscription_status = ''
        profile.sede_payment_method = ''
        profile.sede_last_payment_date = None
        profile.sede_last_payment_amount = None
        profile.sede_next_payment_date = None
        profile.sede_member_since = None
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


def apply_subscription_to_profile(profile, details, match_method=''):
    subscription_id = details.get('subscription_id') or ''
    if not subscription_id:
        return

    is_active = details.get('status') in ACTIVE_SUBSCRIPTION_STATUSES
    defaults = {
        'plan_id': details.get('plan_id') or '',
        'tier_name': details.get('tier_name') or '',
        'status': details.get('status') or '',
        'payment_method': details.get('payment_method') or '',
        'last_payment_date': details.get('last_payment_date'),
        'last_payment_amount': details.get('last_payment_amount'),
        'next_payment_date': details.get('next_payment_date'),
        'member_since': details.get('member_since'),
        'is_active': is_active,
        'matched_via': match_method or '',
        'synced_at': timezone.now(),
    }

    existing = SedeSubscription.objects.filter(subscription_id=subscription_id).select_related('profile').first()
    if existing:
        # Do not replace an existing subscription/user match on periodic sync.
        # Keep the matched profile and only refresh subscription values.
        for field, value in defaults.items():
            setattr(existing, field, value)
        existing.save(update_fields=[*defaults.keys(), 'updated_at'])
        _refresh_profile_membership_summary(existing.profile)
        return

    SedeSubscription.objects.create(
        subscription_id=subscription_id,
        profile=profile,
        **defaults,
    )
    _refresh_profile_membership_summary(profile)


def _format_unmatched_message(details):
    hints = details.get('hints') or {}
    parts = []

    if hints.get('document_numbers'):
        parts.append('DNI: ' + ', '.join(hints['document_numbers'][:3]))
    if hints.get('names'):
        parts.append('Nombre: ' + ', '.join(hints['names'][:3]))

    payments_checked = details.get('payments_checked')
    if payments_checked is not None:
        parts.append(f'{payments_checked} pago(s) revisados')

    if parts:
        return 'Sin match (' + ' | '.join(parts) + ')'
    return 'Sin match (sin datos de pagador)'


def _clear_unmatched_subscription(subscription_id):
    if not subscription_id:
        return
    SedeUnmatchedSubscription.objects.filter(subscription_id=subscription_id).delete()


def _store_unmatched_subscription(subscription_summary, details, result_message):
    subscription_id = (details or {}).get('subscription_id') or (subscription_summary or {}).get('id')
    if not subscription_id:
        return

    hints = (details or {}).get('hints') or {}
    document_numbers = hints.get('document_numbers') or []
    normalized_doc_hint = ', '.join(document_numbers[:5])

    SedeUnmatchedSubscription.objects.update_or_create(
        subscription_id=subscription_id,
        defaults={
            'plan_id': (details or {}).get('plan_id') or (subscription_summary or {}).get('preapproval_plan_id') or '',
            'tier_name': (details or {}).get('tier_name') or (subscription_summary or {}).get('reason') or '',
            'status': (details or {}).get('status') or (subscription_summary or {}).get('status') or '',
            'payer_id': str((details or {}).get('payer_id') or (subscription_summary or {}).get('payer_id') or ''),
            'payer_email': (details or {}).get('payer_email') or (subscription_summary or {}).get('payer_email') or '',
            'payer_first_name': (details or {}).get('payer_first_name') or (subscription_summary or {}).get(
                'payer_first_name'
            ) or '',
            'payer_last_name': (details or {}).get('payer_last_name') or (subscription_summary or {}).get(
                'payer_last_name'
            ) or '',
            'document_number': normalized_doc_hint,
            'payment_method': (details or {}).get('payment_method') or '',
            'last_payment_date': (details or {}).get('last_payment_date'),
            'last_payment_amount': (details or {}).get('last_payment_amount'),
            'next_payment_date': (details or {}).get('next_payment_date'),
            'member_since': (details or {}).get('member_since'),
            'hints': hints,
            'unresolved_reason': result_message or '',
            'last_seen_at': timezone.now(),
        },
    )


def _deactivate_stale_members(active_subscription_ids, log):
    active_ids = set(active_subscription_ids)
    stale_subs = SedeSubscription.objects.filter(is_active=True).exclude(subscription_id__in=active_ids)
    stale_count = stale_subs.count()
    if stale_count:
        log.info('Deactivating %d stale subscription(s)', stale_count)
    affected_profile_ids = list(stale_subs.values_list('profile_id', flat=True).distinct())
    stale_subs.update(is_active=False, status='inactive', synced_at=timezone.now())

    for profile in Profile.objects.filter(id__in=affected_profile_ids):
        _refresh_profile_membership_summary(profile)

    return stale_count


def _process_subscription(sdk, subscription_summary, user_index, assigned_users, log, apply_changes=True):
    sub_id = subscription_summary.get('id', '')
    payer_name = ' '.join(filter(None, [
        subscription_summary.get('payer_first_name'),
        subscription_summary.get('payer_last_name'),
    ])).strip()
    payer_email = subscription_summary.get('payer_email') or '—'
    status = subscription_summary.get('status') or '—'

    result = {
        'subscription_id': sub_id,
        'plan_id': subscription_summary.get('preapproval_plan_id') or '—',
        'tier_name': subscription_summary.get('reason') or '—',
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
        return result, None, details or {}

    previous_name = assigned_users.get(user.id)
    if previous_name and payer_name and name_similarity(previous_name, payer_name) < 0.5:
        result['message'] = (
            f'Conflicto: {user.email} ya vinculado a "{previous_name}", '
            f'no coincide con "{payer_name}"'
        )
        log.warning('  %s', result['message'])
        return result, None, details or {}

    if apply_changes:
        apply_subscription_to_profile(user.profile, details, match_method=match_method)
    assigned_users[user.id] = payer_name or user.get_full_name()

    result.update({
        'matched': True,
        'match_method': match_method,
        'user_id': user.id,
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
    return result, sub_id if active else None, details or {}


def run_match_audit(log=None):
    """
    Build full match report without persisting any Profile changes.
    Returns a dict with summary and detailed per-subscription rows.
    """
    log = log or logger
    log.info('=' * 80)
    log.info('La Sede match audit started')

    access_token = settings.MERCADOPAGO.get('ACCESS_TOKEN')
    if not access_token:
        log.error('MERCADOPAGO_ACCESS_TOKEN not configured')
        return {'error': 'MERCADOPAGO_ACCESS_TOKEN not configured'}

    sdk = get_mp_sdk()
    plan_ids = get_sede_plan_ids()
    subscriptions = fetch_all_subscriptions(sdk, plan_ids=plan_ids)
    user_index = build_user_match_index()

    matched_count = 0
    unmatched_count = 0
    conflict_count = 0
    error_count = 0
    active_candidate_count = 0
    assigned_users = {}
    rows = []
    active_by_user = {}

    total = len(subscriptions)
    for index, subscription_summary in enumerate(subscriptions, start=1):
        sub_id = subscription_summary.get('id', '—')
        payer_name = ' '.join(filter(None, [
            subscription_summary.get('payer_first_name'),
            subscription_summary.get('payer_last_name'),
        ])).strip() or '—'
        status = subscription_summary.get('status') or '—'

        log.info('[AUDIT %d/%d] %s — %s (%s)', index, total, sub_id, payer_name, status)
        try:
            result, active_sub_id, _details = _process_subscription(
                sdk,
                subscription_summary,
                user_index,
                assigned_users,
                log,
                apply_changes=False,
            )
            rows.append(result)
            if result.get('matched'):
                matched_count += 1
                if active_sub_id:
                    active_candidate_count += 1
                    active_by_user[result.get('user_id')] = active_by_user.get(result.get('user_id'), 0) + 1
            elif result.get('message', '').startswith('Conflicto'):
                conflict_count += 1
            else:
                unmatched_count += 1
        except Exception as exc:
            error_count += 1
            log.exception('  Audit error processing %s: %s', sub_id, exc)
            rows.append({
                'subscription_id': sub_id,
                'payer_name': payer_name,
                'payer_email': subscription_summary.get('payer_email') or '—',
                'status': status,
                'matched': False,
                'match_method': None,
                'user_email': None,
                'message': f'Error: {exc}',
            })

    for row in rows:
        user_id = row.get('user_id')
        row['active_subscriptions_for_user'] = active_by_user.get(user_id, 0) if user_id else 0

    summary = {
        'total': total,
        'matched': matched_count,
        'unmatched': unmatched_count,
        'conflicts': conflict_count,
        'errors': error_count,
        'active_candidates': active_candidate_count,
        'users_with_multiple_active': sum(1 for count in active_by_user.values() if count > 1),
    }
    log.info('La Sede match audit completed: %s', summary)
    log.info('=' * 80)

    return {
        'summary': summary,
        'rows': rows,
        'generated_at': timezone.now(),
    }


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
    subscriptions = fetch_all_subscriptions(sdk, plan_ids=plan_ids)
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
            result, active_sub_id, details = _process_subscription(
                sdk,
                subscription_summary,
                user_index,
                assigned_users,
                log,
            )
            if result.get('matched'):
                matched_count += 1
                _clear_unmatched_subscription(sub_id)
                if active_sub_id:
                    active_subscription_ids.append(active_sub_id)
            elif result.get('message', '').startswith('Conflicto'):
                conflict_count += 1
                _store_unmatched_subscription(subscription_summary, details, result.get('message'))
            else:
                unmatched_count += 1
                _store_unmatched_subscription(subscription_summary, details, result.get('message'))
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
