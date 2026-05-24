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
UPDATE_ONLY_SUBSCRIPTION_STATUSES = {'paused', 'cancelled'}
LAST_NAME_SIMILARITY_THRESHOLD = 0.85
FIRST_NAME_SIMILARITY_THRESHOLD = 0.90
SUBSCRIPTION_PAYMENTS_LOOKBACK_DAYS = 375
ALL_HINT_SOURCES = {'subscription', 'customer', 'payment', 'invoice'}
API_RETRY_ATTEMPTS = 4
API_RETRY_BASE_SECONDS = 0.75
PAYMENT_FETCH_WORKERS = int(os.environ.get('SEDE_SYNC_PAYMENT_FETCH_WORKERS', '4'))
SUBSCRIPTION_SYNC_WORKERS = int(os.environ.get('SEDE_SYNC_SUBSCRIPTION_WORKERS', '8'))
PREAPPROVAL_FETCH_WORKERS = int(os.environ.get('SEDE_SYNC_PREAPPROVAL_FETCH_WORKERS', '6'))
FORCED_ACTIVE_SUBSCRIPTION_IDS = {'0fe8d0c8034d4802aab2057e4a46907f'}

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


def _get_forced_active_subscription_ids():
    configured = set(getattr(settings, 'SEDE_FORCED_ACTIVE_SUBSCRIPTION_IDS', []) or [])
    return FORCED_ACTIVE_SUBSCRIPTION_IDS | configured


def _is_forced_active_subscription(subscription_id):
    if not subscription_id:
        return False
    return str(subscription_id) in _get_forced_active_subscription_ids()


def _is_active_subscription_status(status):
    return str(status or '').strip().lower() in ACTIVE_SUBSCRIPTION_STATUSES


def _normalize_frequency_type(value):
    mapping = {
        'days': 'dia',
        'day': 'dia',
        'months': 'mes',
        'month': 'mes',
        'years': 'anio',
        'year': 'anio',
        'weeks': 'semana',
        'week': 'semana',
    }
    return mapping.get(str(value or '').strip().lower(), str(value or '').strip().lower())


def _format_billing_cycle(auto_recurring):
    auto_recurring = auto_recurring or {}
    frequency = auto_recurring.get('frequency')
    frequency_type = _normalize_frequency_type(auto_recurring.get('frequency_type'))
    if not frequency or not frequency_type:
        return ''
    if str(frequency) == '1':
        return f'cada {frequency_type}'
    return f'cada {frequency} {frequency_type}s'


def _build_plan_catalog(subscriptions):
    plan_catalog = {}
    for sub in subscriptions:
        plan_id = (sub.get('preapproval_plan_id') or '').strip()
        if not plan_id:
            continue
        plan_name = (sub.get('reason') or '').strip()
        billing_cycle = _format_billing_cycle(sub.get('auto_recurring'))
        item = plan_catalog.setdefault(plan_id, {'name': '', 'count': 0, 'billing_cycle': '', 'cycles': {}})
        item['count'] += 1
        if plan_name and (not item['name'] or len(plan_name) > len(item['name'])):
            item['name'] = plan_name
        if billing_cycle:
            item['cycles'][billing_cycle] = item['cycles'].get(billing_cycle, 0) + 1

    return plan_catalog


def _upsert_subscription_plans(plan_catalog, log=None):
    log = log or logger
    refreshed = 0
    now = timezone.now()
    for plan_id, info in plan_catalog.items():
        if info['cycles']:
            info['billing_cycle'] = max(info['cycles'].items(), key=lambda pair: pair[1])[0]

        SedeSubscriptionPlan.objects.update_or_create(
            plan_id=plan_id,
            defaults={
                'plan_name': info['name'] or '',
                'billing_cycle': info['billing_cycle'] or '',
                'subscriptions_count': info['count'],
                'last_seen_at': now,
            },
        )
        refreshed += 1

    log.info('Refreshed %d MercadoPago subscription plan(s)', refreshed)
    return refreshed


def refresh_sede_subscription_plans(log=None):
    log = log or logger
    sdk = get_mp_sdk()
    subscriptions = fetch_all_subscriptions(sdk=sdk)
    refreshed = _upsert_subscription_plans(_build_plan_catalog(subscriptions), log=log)

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
    page_limit = 50

    # Fetch first page to learn total and then request remaining pages concurrently.
    first_response = _call_with_backoff(
        sdk.preapproval().search,
        {'limit': page_limit, 'offset': 0},
    )
    if first_response.get('status') != 200:
        logger.warning('MP preapproval search first page failed: %s', first_response)
        return subscriptions

    first_data = first_response.get('response', {}) or {}
    first_batch = first_data.get('results', []) or []
    paging = first_data.get('paging', {}) or {}
    total = int(paging.get('total') or len(first_batch))

    all_items = list(first_batch)
    remaining_offsets = list(range(page_limit, total, page_limit))
    if remaining_offsets:
        workers = max(1, min(PREAPPROVAL_FETCH_WORKERS, len(remaining_offsets), 10))
        logger.info(
            'Fetching %d remaining preapproval page(s) with %d worker(s)',
            len(remaining_offsets),
            workers,
        )

        def fetch_offset(offset):
            response = _call_with_backoff(
                sdk.preapproval().search,
                {'limit': page_limit, 'offset': offset},
            )
            return offset, response

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(fetch_offset, offset): offset for offset in remaining_offsets}
            for future in as_completed(future_map):
                offset = future_map[future]
                try:
                    _, response = future.result()
                except Exception:
                    logger.exception('Failed to fetch preapproval page offset=%s', offset)
                    continue

                if response.get('status') != 200:
                    logger.warning('MP preapproval search failed at offset=%s: %s', offset, response.get('status'))
                    continue
                batch = (response.get('response', {}) or {}).get('results', []) or []
                all_items.extend(batch)

    for item in all_items:
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
    card = payment.get('card') or {}
    cardholder = card.get('cardholder') or {}
    card_identification = cardholder.get('identification') or {}
    card_doc_type = card_identification.get('type')
    card_doc_number = card_identification.get('number')
    if card_doc_number:
        return card_doc_type, card_doc_number

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

    if not first_name and not last_name:
        additional_info = payment.get('additional_info') or {}
        additional_payer = additional_info.get('payer') or {}
        first_name = additional_payer.get('first_name') or first_name
        last_name = additional_payer.get('last_name') or last_name

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


def fetch_recent_payer_payments(sdk, payer_id, log=None, limit=40):
    log = log or logger
    if not payer_id:
        return []

    response = _call_with_backoff(
        sdk.payment().search,
        {
            'payer.id': str(payer_id),
            'sort': 'date_created',
            'criteria': 'desc',
            'limit': min(limit, 50),
            'offset': 0,
        },
    )
    if response.get('status') != 200:
        log.warning('  payer_id payment search failed for %s: %s', payer_id, response.get('status'))
        return []

    rows = response.get('response', {}).get('results', []) or []
    if not rows:
        return []

    payments = []
    for row in rows:
        payment_id = row.get('id')
        if not payment_id:
            continue
        detail = _call_with_backoff(lambda _: sdk.payment().get(str(payment_id)), {})
        if detail.get('status') == 200:
            payments.append(detail.get('response', {}))

    log.info('  Loaded %d payer payment(s) for payer_id=%s', len(payments), payer_id)
    return payments


def _resolve_subscription_prefetch_worker_count(total_subscriptions):
    if total_subscriptions <= 0:
        return 1
    return max(1, min(SUBSCRIPTION_SYNC_WORKERS, total_subscriptions, 12))


def _prefetch_subscription_remote_data(subscription_summary, log=None, include_payments=True):
    log = log or logger
    sub_id = subscription_summary.get('id') or ''
    if not sub_id:
        return {
            'subscription_id': sub_id,
            'error': 'missing_id',
            'detail': None,
            'payments': [],
            'invoices': [],
        }

    sdk = get_mp_sdk()
    detail_response = sdk.preapproval().get(sub_id)
    if detail_response.get('status') != 200:
        return {
            'subscription_id': sub_id,
            'error': f'Subscription detail fetch failed: {detail_response.get("status")}',
            'detail': None,
            'payments': [],
            'invoices': [],
        }

    detail_payload = detail_response.get('response', {})
    payments = []
    invoices = []
    if include_payments:
        payments, invoices = fetch_subscription_recent_payments(sdk, sub_id, log=log)
    return {
        'subscription_id': sub_id,
        'error': None,
        'detail': detail_payload,
        'payments': payments,
        'invoices': invoices,
    }


def _prefetch_subscription_remote_map(subscriptions, log=None, include_payments=True):
    log = log or logger
    preloaded = {}
    workers = _resolve_subscription_prefetch_worker_count(len(subscriptions))
    if not subscriptions:
        return preloaded

    lane_label = 'full remote data' if include_payments else 'detail data'
    log.info(
        'Preloading %s for %d subscription(s) with %d worker(s)...',
        lane_label,
        len(subscriptions),
        workers,
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(
                _prefetch_subscription_remote_data,
                subscription_summary,
                log,
                include_payments,
            ): (subscription_summary.get('id', ''))
            for subscription_summary in subscriptions
        }
        for future in as_completed(future_map):
            sub_id = future_map[future]
            try:
                payload = future.result()
            except Exception as exc:
                log.exception('  Error preloading subscription %s: %s', sub_id, exc)
                payload = {
                    'subscription_id': sub_id,
                    'error': f'Preload error: {exc}',
                    'detail': None,
                    'payments': [],
                    'invoices': [],
                }
            preloaded[sub_id] = payload

    log.info('Preloaded %s for %d subscription(s)', lane_label, len(preloaded))
    return preloaded


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


def collect_payer_hints(sdk, subscription_summary, subscription_detail, payments, log=None, payment_source='payment'):
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
            payment_source,
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
        # Argentina hint normalization:
        # when payer DNI starts with 20/27 and still no match, try without first two and last digit.
        if normalized.startswith(('20', '23', '24', '27', '30', '33', '34')) and len(normalized) == 11:
            trimmed_normalized = normalized[2:-1]
            trimmed_user = user_index['by_document'].get(trimmed_normalized)
            if trimmed_user:
                return trimmed_user, 'dni_trim', {
                    'document_type': document.get('type'),
                    'document_number': document.get('number'),
                    'trimmed_document_number': trimmed_normalized,
                    'source': document.get('source'),
                }

    best_user = None
    best_meta = None
    best_score = 0.0

    for source_bucket in (('subscription', 'customer'), ('payment', 'payer_payment')):
        for name_hint in hints['names']:
            if name_hint.get('source') not in source_bucket:
                continue
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
            break

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

    subscription_id = detail.get('id') or subscription_summary.get('id')
    resolved_status = detail.get('status') or subscription_summary.get('status') or ''
    if _is_forced_active_subscription(subscription_id):
        resolved_status = 'authorized'

    return {
        'subscription_id': subscription_id,
        'plan_id': detail.get('preapproval_plan_id') or subscription_summary.get('preapproval_plan_id') or '',
        'tier_name': detail.get('reason') or subscription_summary.get('reason') or '',
        'status': resolved_status,
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


def match_subscription_to_user(
    sdk,
    subscription_summary,
    subscription_detail=None,
    user_index=None,
    log=None,
    payments=None,
    invoices=None,
):
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

    if payments is None or invoices is None:
        payments, invoices = fetch_subscription_recent_payments(sdk, sub_id, log=log)
    hints = collect_payer_hints(sdk, subscription_summary, subscription_detail, payments, log=log)
    user, match_method, match_meta = match_payer_hints_to_user(hints, user_index)
    if not user:
        payer_id = subscription_detail.get('payer_id') or subscription_summary.get('payer_id')
        payer_payments = fetch_recent_payer_payments(sdk, payer_id, log=log)
        if payer_payments:
            payer_hints = collect_payer_hints(
                sdk,
                subscription_summary,
                subscription_detail,
                payer_payments,
                log=log,
                payment_source='payer_payment',
            )
            hints['documents'].extend(payer_hints['documents'])
            hints['names'].extend(payer_hints['names'])
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


def apply_subscription_to_profile(profile, details, match_method=''):
    subscription_id = details.get('subscription_id') or ''
    if not subscription_id:
        return

    is_active = (
        _is_active_subscription_status(details.get('status'))
        or _is_forced_active_subscription(subscription_id)
    )
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
        if existing.is_soft_removed:
            incoming_last_payment_date = defaults.get('last_payment_date')
            # Soft-removed subscriptions stay excluded unless a new charge appears.
            if not incoming_last_payment_date or incoming_last_payment_date == existing.last_payment_date:
                return
            existing.is_soft_removed = False
            existing.soft_removed_at = None
        # Do not replace an existing subscription/user match on periodic sync.
        # Keep the matched profile and only refresh subscription values.
        for field, value in defaults.items():
            setattr(existing, field, value)
        update_fields = [*defaults.keys(), 'updated_at']
        if existing.is_soft_removed is False:
            update_fields.extend(['is_soft_removed', 'soft_removed_at'])
        existing.save(update_fields=update_fields)
        return

    SedeSubscription.objects.create(
        subscription_id=subscription_id,
        profile=profile,
        **defaults,
    )


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
    stale_subs.update(is_active=False, status='inactive', synced_at=timezone.now())

    return stale_count


def _process_subscription(
    sdk,
    subscription_summary,
    user_index,
    assigned_users,
    log,
    apply_changes=True,
    preloaded_remote=None,
):
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

    preloaded_error = (preloaded_remote or {}).get('error')
    if preloaded_error:
        result['message'] = preloaded_error
        return result, None, {}

    existing_subscription = SedeSubscription.objects.filter(subscription_id=sub_id).select_related('profile__user').first()
    if existing_subscription:
        detail_payload = (preloaded_remote or {}).get('detail')
        payments = (preloaded_remote or {}).get('payments')
        invoices = (preloaded_remote or {}).get('invoices')
        if detail_payload is None:
            detail_response = sdk.preapproval().get(sub_id)
            if detail_response.get('status') != 200:
                result['message'] = f'Subscription detail fetch failed: {detail_response.get("status")}'
                return result, None, {}
            detail_payload = detail_response.get('response', {})
        if payments is None or invoices is None:
            payments, invoices = fetch_subscription_recent_payments(sdk, sub_id, log=log)
        details = _extract_subscription_details(subscription_summary, detail_payload, payments, invoices)
        if apply_changes:
            apply_subscription_to_profile(
                existing_subscription.profile,
                details,
                match_method=existing_subscription.matched_via or 'subscription_id',
            )
        active = (
            _is_active_subscription_status(details.get('status'))
            or _is_forced_active_subscription(sub_id)
        )
        result.update({
            'matched': True,
            'match_method': 'subscription_id',
            'user_id': existing_subscription.profile.user_id,
            'user_email': existing_subscription.profile.user.email,
            'message': f'✅ Updated existing subscription for {existing_subscription.profile.user.email}',
        })
        return result, sub_id if active else None, details

    user, match_method, details = match_subscription_to_user(
        sdk,
        subscription_summary,
        subscription_detail=(preloaded_remote or {}).get('detail'),
        user_index=user_index,
        log=log,
        payments=(preloaded_remote or {}).get('payments'),
        invoices=(preloaded_remote or {}).get('invoices'),
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
        'message': f'✅ Vinculado con {user.email} ({match_method})',
    })
    if match_method == 'name' and details.get('match_meta'):
        result['message'] += f" — score {details['match_meta'].get('score')}"
    if payments_checked:
        result['message'] += f' — {payments_checked} pago(s)'

    active = (
        _is_active_subscription_status(details.get('status'))
        or _is_forced_active_subscription(sub_id)
    )
    log.info(
        '  ✅ Matched -> %s via %s (active=%s)',
        user.email,
        match_method,
        active,
    )
    return result, sub_id if active else None, details or {}


def _update_existing_non_authorized_subscription(sdk, subscription_summary, log):
    sub_id = subscription_summary.get('id') or ''
    if not sub_id:
        return 'missing_id'

    detail_response = sdk.preapproval().get(sub_id)
    if detail_response.get('status') != 200:
        return 'detail_fetch_failed'
    detail_payload = detail_response.get('response', {})
    payments, invoices = fetch_subscription_recent_payments(sdk, sub_id, log=log)
    details = _extract_subscription_details(subscription_summary, detail_payload, payments, invoices)

    existing_subscription = SedeSubscription.objects.filter(subscription_id=sub_id).select_related('profile').first()
    if existing_subscription:
        apply_subscription_to_profile(
            existing_subscription.profile,
            details,
            match_method=existing_subscription.matched_via or 'subscription_id',
        )
        return 'updated_subscription'

    unmatched = SedeUnmatchedSubscription.objects.filter(subscription_id=sub_id).first()
    if unmatched:
        _store_unmatched_subscription(subscription_summary, details, unmatched.unresolved_reason)
        return 'updated_unmatched'

    return 'ignored_non_authorized_new'


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
    sync_started = time.perf_counter()
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
    fetch_started = time.perf_counter()
    subscriptions = fetch_all_subscriptions(sdk, plan_ids=plan_ids)
    soft_removed_skipped = 0
    soft_removed_reactivated = 0
    soft_removed_records = SedeSubscription.objects.filter(is_soft_removed=True).in_bulk(field_name='subscription_id')
    soft_removed_ids = set(soft_removed_records.keys())
    if soft_removed_ids:
        soft_removed_candidates = [
            sub for sub in subscriptions if str(sub.get('id') or '') in soft_removed_ids
        ]
        soft_removed_reactivated_ids = set()
        if soft_removed_candidates:
            log.info(
                'Checking %d soft-removed subscription(s) for new payments...',
                len(soft_removed_candidates),
            )
            soft_removed_payloads = _prefetch_subscription_remote_map(
                soft_removed_candidates,
                log=log,
                include_payments=False,
            )
            for subscription_summary in soft_removed_candidates:
                sub_id = str(subscription_summary.get('id') or '')
                existing_subscription = soft_removed_records.get(sub_id)
                payload = soft_removed_payloads.get(sub_id, {})
                if not existing_subscription or payload.get('error'):
                    continue
                details = _extract_subscription_details(
                    subscription_summary,
                    payload.get('detail') or subscription_summary,
                )
                previous_last_payment = existing_subscription.last_payment_date
                latest_last_payment = details.get('last_payment_date')
                if latest_last_payment and latest_last_payment != previous_last_payment:
                    apply_subscription_to_profile(
                        existing_subscription.profile,
                        details,
                        match_method=existing_subscription.matched_via or 'subscription_id',
                    )
                    soft_removed_reactivated_ids.add(sub_id)
                    soft_removed_reactivated += 1
        soft_removed_skipped = sum(
            1
            for sub in subscriptions
            if str(sub.get('id') or '') in (soft_removed_ids - soft_removed_reactivated_ids)
        )
        if soft_removed_reactivated:
            log.info('Reactivated %d soft-removed subscription(s) due to new payment', soft_removed_reactivated)
        if soft_removed_skipped:
            log.info(
                'Skipping %d soft-removed subscription(s) with unchanged payment date',
                soft_removed_skipped,
            )
        subscriptions = [
            sub for sub in subscriptions if str(sub.get('id') or '') not in (soft_removed_ids - soft_removed_reactivated_ids)
        ]
    refreshed_plans = _upsert_subscription_plans(_build_plan_catalog(subscriptions), log=log)
    authorized_subscriptions = [
        sub for sub in subscriptions if (sub.get('status') or '').lower() == 'authorized'
    ]
    update_only_subscriptions = [
        sub for sub in subscriptions if (sub.get('status') or '').lower() in UPDATE_ONLY_SUBSCRIPTION_STATUSES
    ]
    total = len(subscriptions)
    fetch_duration = time.perf_counter() - fetch_started
    log.info('Found %d subscription(s)', total)

    active_subscription_ids = []
    matched_count = 0
    unmatched_count = 0
    conflict_count = 0
    error_count = 0

    authorized_started = time.perf_counter()
    authorized_ids = [str(sub.get('id')) for sub in authorized_subscriptions if sub.get('id')]
    existing_subscriptions = (
        SedeSubscription.objects.filter(subscription_id__in=authorized_ids)
        .select_related('profile__user')
        .in_bulk(field_name='subscription_id')
    )
    existing_unmatched = (
        SedeUnmatchedSubscription.objects.filter(subscription_id__in=authorized_ids).in_bulk(field_name='subscription_id')
    )

    known_matched_subscriptions = []
    known_unmatched_subscriptions = []
    new_candidate_subscriptions = []
    for subscription_summary in authorized_subscriptions:
        sub_id = subscription_summary.get('id', '—')
        if sub_id in existing_subscriptions:
            known_matched_subscriptions.append(subscription_summary)
        elif sub_id in existing_unmatched:
            known_unmatched_subscriptions.append(subscription_summary)
        else:
            new_candidate_subscriptions.append(subscription_summary)

    log.info(
        'Authorized lanes: %d known matched, %d known unmatched, %d new candidates',
        len(known_matched_subscriptions),
        len(known_unmatched_subscriptions),
        len(new_candidate_subscriptions),
    )

    if known_matched_subscriptions:
        known_payloads = _prefetch_subscription_remote_map(
            known_matched_subscriptions,
            log=log,
            include_payments=False,
        )
        now = timezone.now()
        matched_updates = []
        for subscription_summary in known_matched_subscriptions:
            sub_id = subscription_summary.get('id', '')
            existing_subscription = existing_subscriptions.get(sub_id)
            payload = known_payloads.get(sub_id, {})
            if payload.get('error'):
                error_count += 1
                log.warning('  Known matched preload failed for %s: %s', sub_id, payload.get('error'))
                continue

            details = _extract_subscription_details(
                subscription_summary,
                payload.get('detail') or subscription_summary,
            )
            existing_subscription.plan_id = details.get('plan_id') or ''
            existing_subscription.tier_name = details.get('tier_name') or ''
            existing_subscription.status = details.get('status') or ''
            existing_subscription.payment_method = details.get('payment_method') or ''
            existing_subscription.last_payment_date = details.get('last_payment_date')
            existing_subscription.last_payment_amount = details.get('last_payment_amount')
            existing_subscription.next_payment_date = details.get('next_payment_date')
            existing_subscription.member_since = details.get('member_since')
            existing_subscription.is_active = (
                _is_active_subscription_status(details.get('status'))
                or _is_forced_active_subscription(sub_id)
            )
            existing_subscription.matched_via = existing_subscription.matched_via or 'subscription_id'
            existing_subscription.synced_at = now
            existing_subscription.updated_at = now
            matched_updates.append(existing_subscription)
            matched_count += 1
            _clear_unmatched_subscription(sub_id)
            if existing_subscription.is_active:
                active_subscription_ids.append(sub_id)

        if matched_updates:
            SedeSubscription.objects.bulk_update(
                matched_updates,
                fields=[
                    'plan_id',
                    'tier_name',
                    'status',
                    'payment_method',
                    'last_payment_date',
                    'last_payment_amount',
                    'next_payment_date',
                    'member_since',
                    'is_active',
                    'matched_via',
                    'synced_at',
                    'updated_at',
                ],
            )
        log.info('Updated %d known matched subscription(s) via bulk update', len(matched_updates))

    if known_unmatched_subscriptions:
        now = timezone.now()
        unmatched_updates = []
        for subscription_summary in known_unmatched_subscriptions:
            sub_id = subscription_summary.get('id', '')
            unmatched = existing_unmatched.get(sub_id)
            if not unmatched:
                continue
            unmatched.plan_id = subscription_summary.get('preapproval_plan_id') or unmatched.plan_id or ''
            unmatched.tier_name = subscription_summary.get('reason') or unmatched.tier_name or ''
            unmatched.status = subscription_summary.get('status') or unmatched.status or ''
            unmatched.payer_id = str(subscription_summary.get('payer_id') or unmatched.payer_id or '')
            unmatched.payer_email = subscription_summary.get('payer_email') or unmatched.payer_email or ''
            unmatched.payer_first_name = subscription_summary.get('payer_first_name') or unmatched.payer_first_name or ''
            unmatched.payer_last_name = subscription_summary.get('payer_last_name') or unmatched.payer_last_name or ''
            unmatched.last_seen_at = now
            unmatched.updated_at = now
            unmatched_updates.append(unmatched)

        if unmatched_updates:
            SedeUnmatchedSubscription.objects.bulk_update(
                unmatched_updates,
                fields=[
                    'plan_id',
                    'tier_name',
                    'status',
                    'payer_id',
                    'payer_email',
                    'payer_first_name',
                    'payer_last_name',
                    'last_seen_at',
                    'updated_at',
                ],
            )
        unmatched_count += len(known_unmatched_subscriptions)
        log.info('Refreshed %d known unmatched subscription(s) without rematching', len(unmatched_updates))

    if new_candidate_subscriptions:
        log.info('Building user match index for new authorized candidates...')
        user_index = build_user_match_index()
        log.info(
            'User index ready: %d emails, %d documents, %d name entries',
            len(user_index['by_email']),
            len(user_index['by_document']),
            len(user_index['by_name']),
        )
        assigned_users = {}
        preloaded_new_candidates = _prefetch_subscription_remote_map(
            new_candidate_subscriptions,
            log=log,
            include_payments=True,
        )

        for index, subscription_summary in enumerate(new_candidate_subscriptions, start=1):
            sub_id = subscription_summary.get('id', '—')
            payer_name = ' '.join(filter(None, [
                subscription_summary.get('payer_first_name'),
                subscription_summary.get('payer_last_name'),
            ])).strip() or '—'
            status = subscription_summary.get('status') or '—'

            log.info(
                '[AUTHORIZED NEW %d/%d] Subscription %s — %s (%s)',
                index,
                len(new_candidate_subscriptions),
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
                    preloaded_remote=preloaded_new_candidates.get(sub_id),
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
                log.exception('  Error processing new subscription %s: %s', sub_id, exc)
    authorized_duration = time.perf_counter() - authorized_started

    update_only_updated = 0
    update_only_ignored = 0
    non_auth_started = time.perf_counter()
    update_only_ids = [str(sub.get('id')) for sub in update_only_subscriptions if sub.get('id')]
    update_existing_subscriptions = SedeSubscription.objects.filter(subscription_id__in=update_only_ids).in_bulk(
        field_name='subscription_id'
    )
    update_existing_unmatched = SedeUnmatchedSubscription.objects.filter(subscription_id__in=update_only_ids).in_bulk(
        field_name='subscription_id'
    )

    update_known_matched = []
    update_known_unmatched = []
    update_new_unknown = []
    for subscription_summary in update_only_subscriptions:
        sub_id = subscription_summary.get('id', '')
        if sub_id in update_existing_subscriptions:
            update_known_matched.append(subscription_summary)
        elif sub_id in update_existing_unmatched:
            update_known_unmatched.append(subscription_summary)
        else:
            update_new_unknown.append(subscription_summary)

    log.info(
        'Non-auth lanes: %d known matched, %d known unmatched, %d unknown skipped',
        len(update_known_matched),
        len(update_known_unmatched),
        len(update_new_unknown),
    )

    if update_known_matched:
        known_non_auth_payloads = _prefetch_subscription_remote_map(
            update_known_matched,
            log=log,
            include_payments=False,
        )
        now = timezone.now()
        matched_non_auth_updates = []
        for subscription_summary in update_known_matched:
            sub_id = subscription_summary.get('id', '')
            existing_subscription = update_existing_subscriptions.get(sub_id)
            payload = known_non_auth_payloads.get(sub_id, {})
            if payload.get('error'):
                error_count += 1
                log.warning('  Non-auth matched preload failed for %s: %s', sub_id, payload.get('error'))
                continue

            details = _extract_subscription_details(
                subscription_summary,
                payload.get('detail') or subscription_summary,
            )
            existing_subscription.plan_id = details.get('plan_id') or ''
            existing_subscription.tier_name = details.get('tier_name') or ''
            existing_subscription.status = details.get('status') or ''
            existing_subscription.payment_method = details.get('payment_method') or ''
            existing_subscription.last_payment_date = details.get('last_payment_date')
            existing_subscription.last_payment_amount = details.get('last_payment_amount')
            existing_subscription.next_payment_date = details.get('next_payment_date')
            existing_subscription.member_since = details.get('member_since')
            existing_subscription.is_active = (
                _is_active_subscription_status(details.get('status'))
                or _is_forced_active_subscription(sub_id)
            )
            existing_subscription.synced_at = now
            existing_subscription.updated_at = now
            matched_non_auth_updates.append(existing_subscription)
            update_only_updated += 1
            if existing_subscription.is_active:
                active_subscription_ids.append(sub_id)

        if matched_non_auth_updates:
            SedeSubscription.objects.bulk_update(
                matched_non_auth_updates,
                fields=[
                    'plan_id',
                    'tier_name',
                    'status',
                    'payment_method',
                    'last_payment_date',
                    'last_payment_amount',
                    'next_payment_date',
                    'member_since',
                    'is_active',
                    'synced_at',
                    'updated_at',
                ],
            )
        log.info('Updated %d known non-auth matched subscription(s)', len(matched_non_auth_updates))

    if update_known_unmatched:
        now = timezone.now()
        unmatched_non_auth_updates = []
        for subscription_summary in update_known_unmatched:
            sub_id = subscription_summary.get('id', '')
            unmatched = update_existing_unmatched.get(sub_id)
            if not unmatched:
                continue
            unmatched.plan_id = subscription_summary.get('preapproval_plan_id') or unmatched.plan_id or ''
            unmatched.tier_name = subscription_summary.get('reason') or unmatched.tier_name or ''
            unmatched.status = subscription_summary.get('status') or unmatched.status or ''
            unmatched.payer_id = str(subscription_summary.get('payer_id') or unmatched.payer_id or '')
            unmatched.payer_email = subscription_summary.get('payer_email') or unmatched.payer_email or ''
            unmatched.payer_first_name = subscription_summary.get('payer_first_name') or unmatched.payer_first_name or ''
            unmatched.payer_last_name = subscription_summary.get('payer_last_name') or unmatched.payer_last_name or ''
            unmatched.last_seen_at = now
            unmatched.updated_at = now
            unmatched_non_auth_updates.append(unmatched)
            update_only_updated += 1

        if unmatched_non_auth_updates:
            SedeUnmatchedSubscription.objects.bulk_update(
                unmatched_non_auth_updates,
                fields=[
                    'plan_id',
                    'tier_name',
                    'status',
                    'payer_id',
                    'payer_email',
                    'payer_first_name',
                    'payer_last_name',
                    'last_seen_at',
                    'updated_at',
                ],
            )
        log.info('Updated %d known non-auth unmatched subscription(s)', len(unmatched_non_auth_updates))

    update_only_ignored += len(update_new_unknown)
    non_auth_duration = time.perf_counter() - non_auth_started

    deactivate_started = time.perf_counter()
    log.info('Deactivating stale members...')
    deactivated = _deactivate_stale_members(active_subscription_ids, log)
    deactivate_duration = time.perf_counter() - deactivate_started
    total_duration = time.perf_counter() - sync_started

    summary = {
        'total': total,
        'refreshed_plans': refreshed_plans,
        'authorized_total': len(authorized_subscriptions),
        'update_only_total': len(update_only_subscriptions),
        'matched': matched_count,
        'unmatched': unmatched_count,
        'conflicts': conflict_count,
        'errors': error_count,
        'active_members': len(active_subscription_ids),
        'deactivated': deactivated,
        'update_only_updated': update_only_updated,
        'update_only_ignored': update_only_ignored,
        'soft_removed_skipped': soft_removed_skipped,
        'soft_removed_reactivated': soft_removed_reactivated,
        'duration_seconds': round(total_duration, 2),
        'timings': {
            'fetch_seconds': round(fetch_duration, 2),
            'authorized_seconds': round(authorized_duration, 2),
            'non_auth_seconds': round(non_auth_duration, 2),
            'deactivate_seconds': round(deactivate_duration, 2),
        },
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
    log.info(
        'Timing: total=%ss fetch=%ss authorized=%ss non_auth=%ss deactivate=%ss',
        summary['duration_seconds'],
        summary['timings']['fetch_seconds'],
        summary['timings']['authorized_seconds'],
        summary['timings']['non_auth_seconds'],
        summary['timings']['deactivate_seconds'],
    )
    log.info('=' * 80)

    return summary
