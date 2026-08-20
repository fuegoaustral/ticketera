import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, EventRequest
from events.services.event_request_slack import (
    ACTION_APPROVE,
    ACTION_REJECT,
    post_event_request_to_slack,
    slack_api_configured,
    slack_missing_config,
)
from events.services.main_event import reconcile_main_event

TINY_GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)

SLACK_SETTINGS = {
    'SLACK_BOT_TOKEN': 'xoxb-test-token',
    'SLACK_SIGNING_SECRET': 'test-signing-secret',
    'SLACK_EVENT_REQUESTS_CHANNEL': 'C123456',
}


def _image(name='banner.gif'):
    return SimpleUploadedFile(name, TINY_GIF, content_type='image/gif')


def _make_event(**kwargs):
    now = timezone.now()
    counter = Event.objects.count() + 1
    defaults = {
        'name': f'Evento {counter}',
        'title': f'Evento {counter}',
        'description': 'desc',
        'start': now + timedelta(days=1),
        'end': now + timedelta(days=2),
        'transfers_enabled_until': now + timedelta(days=1),
        'header_image': _image(f'banner-{counter}.gif'),
        'active': True,
        'is_main': False,
        'slug': f'evento-{counter}',
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _make_event_request(**kwargs):
    now = timezone.now()
    user = kwargs.pop('requested_by', None) or User.objects.create_user(
        username=kwargs.pop('username', f'user-{User.objects.count() + 1}'),
        email=kwargs.pop('email', f'user{User.objects.count() + 1}@example.com'),
        password='pass',
    )
    defaults = {
        'requested_by': user,
        'name': 'Fiesta Sede',
        'description': '<p>Una noche</p>',
        'start': now + timedelta(days=7),
        'end': now + timedelta(days=7, hours=6),
        'header_image': _image(),
        'location': 'Paz Soldán 5150, CABA',
        'max_tickets': 300,
        'status': EventRequest.Status.PENDING,
    }
    defaults.update(kwargs)
    return EventRequest.objects.create(**defaults)


def _sign_slack_body(body, secret, timestamp):
    basestring = f'v0:{timestamp}:{body}'
    digest = hmac.new(
        secret.encode('utf-8'),
        basestring.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f'v0={digest}'


class SlackEventRequestTests(TestCase):
    @override_settings(SLACK_BOT_TOKEN='', SLACK_SIGNING_SECRET='', SLACK_EVENT_REQUESTS_CHANNEL='')
    def test_skips_post_when_config_missing(self):
        self.assertFalse(slack_api_configured())
        self.assertIn('SLACK_BOT_TOKEN', slack_missing_config())
        event_request = _make_event_request()
        self.assertFalse(post_event_request_to_slack(event_request))
        event_request.refresh_from_db()
        self.assertEqual(event_request.slack_message_ts, '')

    @override_settings(**SLACK_SETTINGS)
    @patch('events.services.event_request_slack.requests.post')
    def test_posts_message_with_approve_reject_buttons(self, mock_post):
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {
            'ok': True,
            'channel': 'C123456',
            'ts': '1710000000.123456',
        }
        event_request = _make_event_request()
        self.assertTrue(post_event_request_to_slack(event_request))
        event_request.refresh_from_db()
        self.assertEqual(event_request.slack_channel, 'C123456')
        self.assertEqual(event_request.slack_message_ts, '1710000000.123456')

        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['channel'], 'C123456')
        action_ids = [
            element['action_id']
            for block in payload['blocks']
            if block['type'] == 'actions'
            for element in block['elements']
        ]
    @override_settings(SLACK_BOT_TOKEN='xoxb-test-token', SLACK_SIGNING_SECRET='', SLACK_EVENT_REQUESTS_CHANNEL='C123456')
    @patch('events.services.event_request_slack.requests.post')
    def test_posts_without_signing_secret(self, mock_post):
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {
            'ok': True,
            'channel': 'C123456',
            'ts': '1710000000.123456',
        }
        event_request = _make_event_request()
        self.assertTrue(post_event_request_to_slack(event_request))

    @override_settings(**SLACK_SETTINGS)
    def test_webhook_rejects_invalid_signature(self):
        response = self.client.post(
            reverse('slack_event_request_webhook'),
            data=urlencode({'payload': '{}'}),
            content_type='application/x-www-form-urlencoded',
            HTTP_X_SLACK_REQUEST_TIMESTAMP=str(int(time.time())),
            HTTP_X_SLACK_SIGNATURE='v0=deadbeef',
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(**SLACK_SETTINGS)
    @patch('events.services.event_request_processing.send_mail')
    @patch('events.services.event_request_slack.requests.post')
    def test_webhook_approve_button_creates_event(self, mock_post, mock_mail):
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {'ok': True}
        event_request = _make_event_request()
        event_request.slack_channel = 'C123456'
        event_request.slack_message_ts = '1710000000.1'
        event_request.save(update_fields=['slack_channel', 'slack_message_ts'])

        payload = {
            'type': 'block_actions',
            'user': {'username': 'soporte'},
            'actions': [{
                'action_id': ACTION_APPROVE,
                'value': str(event_request.pk),
            }],
        }
        body = urlencode({'payload': json.dumps(payload)})
        timestamp = str(int(time.time()))
        signature = _sign_slack_body(body, SLACK_SETTINGS['SLACK_SIGNING_SECRET'], timestamp)

        response = self.client.post(
            reverse('slack_event_request_webhook'),
            data=body,
            content_type='application/x-www-form-urlencoded',
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        event_request.refresh_from_db()
        self.assertEqual(event_request.status, EventRequest.Status.APPROVED)
        self.assertIsNotNone(event_request.created_event_id)

    @override_settings(**SLACK_SETTINGS)
    @patch('events.services.event_request_slack.requests.post')
    def test_webhook_reject_button_rejects_request(self, mock_post):
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {'ok': True}
        event_request = _make_event_request()
        event_request.slack_channel = 'C123456'
        event_request.slack_message_ts = '1710000000.1'
        event_request.save(update_fields=['slack_channel', 'slack_message_ts'])

        payload = {
            'type': 'block_actions',
            'user': {'username': 'soporte'},
            'actions': [{
                'action_id': ACTION_REJECT,
                'value': str(event_request.pk),
            }],
        }
        body = urlencode({'payload': json.dumps(payload)})
        timestamp = str(int(time.time()))
        signature = _sign_slack_body(body, SLACK_SETTINGS['SLACK_SIGNING_SECRET'], timestamp)

        response = self.client.post(
            reverse('slack_event_request_webhook'),
            data=body,
            content_type='application/x-www-form-urlencoded',
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        event_request.refresh_from_db()
        self.assertEqual(event_request.status, EventRequest.Status.REJECTED)
        self.assertEqual(event_request.rejection_reason, 'Rechazada desde Slack')


class ChatwootOneClickReviewTests(TestCase):
    def test_proposal_message_includes_approve_and_reject_links(self):
        from events.services.event_request_chatwoot import build_proposal_message

        event_request = _make_event_request()
        message = build_proposal_message(event_request)
        approve = reverse('event_request_review', kwargs={
            'request_id': event_request.pk,
            'action': 'aprobar',
        })
        reject = reverse('event_request_review', kwargs={
            'request_id': event_request.pk,
            'action': 'desaprobar',
        })
        self.assertIn(approve, message)
        self.assertIn(reject, message)
        self.assertIn('[✅ Aprobar]', message)
        self.assertIn('[❌ Desaprobar]', message)

    @patch('events.services.event_request_processing.send_mail')
    def test_approve_link_creates_event(self, mock_mail):
        event_request = _make_event_request()
        from events.services.event_request_actions import make_action_token
        token = make_action_token(event_request.pk, 'approve')
        url = reverse('event_request_review', kwargs={
            'request_id': event_request.pk,
            'action': 'aprobar',
        })
        response = self.client.get(url, {'t': token})
        self.assertEqual(response.status_code, 200)
        event_request.refresh_from_db()
        self.assertEqual(event_request.status, EventRequest.Status.APPROVED)
        self.assertIsNotNone(event_request.created_event_id)

    def test_reject_link_rejects_request(self):
        event_request = _make_event_request()
        from events.services.event_request_actions import make_action_token
        token = make_action_token(event_request.pk, 'reject')
        url = reverse('event_request_review', kwargs={
            'request_id': event_request.pk,
            'action': 'desaprobar',
        })
        response = self.client.get(url, {'t': token})
        self.assertEqual(response.status_code, 200)
        event_request.refresh_from_db()
        self.assertEqual(event_request.status, EventRequest.Status.REJECTED)
        self.assertEqual(event_request.rejection_reason, 'Rechazada desde Chatwoot')

    def test_invalid_token_is_rejected(self):
        event_request = _make_event_request()
        url = reverse('event_request_review', kwargs={
            'request_id': event_request.pk,
            'action': 'aprobar',
        })
        response = self.client.get(url, {'t': 'token-falso'})
        self.assertEqual(response.status_code, 400)
        event_request.refresh_from_db()
        self.assertEqual(event_request.status, EventRequest.Status.PENDING)


class ChatwootWebsiteInboxTests(TestCase):
    def test_extract_source_id_prefers_fuego_austral_inbox(self):
        from events.services.event_request_chatwoot import _extract_source_id

        data = {
            'contact_inboxes': [
                {'source_id': 'api-inbox', 'inbox': {'id': 115671}},
                {'source_id': 'web-inbox', 'inbox': {'id': 46478}},
            ]
        }
        self.assertEqual(_extract_source_id(data, 46478), 'web-inbox')

    @override_settings(
        CHATWOOT_SOPORTE_INBOX_ID='46478',
        CHATWOOT_SOPORTE_ASSIGNEE_ID='107003',
    )
    @patch('events.services.event_request_chatwoot._request')
    def test_create_conversation_uses_website_source_id(self, mock_request):
        from events.services.event_request_chatwoot import _create_conversation

        mock_request.side_effect = [
            {'payload': {'contact_inboxes': [
                {'source_id': 'sess-fa', 'inbox': {'id': 46478}},
            ]}},
            {'id': 99},
        ]
        self.assertEqual(_create_conversation(12), 99)
        _method, path = mock_request.call_args_list[1].args[:2]
        self.assertEqual(path, '/conversations')
        payload = mock_request.call_args_list[1].kwargs['json']
        self.assertEqual(payload['inbox_id'], 46478)
        self.assertEqual(payload['source_id'], 'sess-fa')
        self.assertEqual(payload['contact_id'], 12)
        self.assertEqual(payload['assignee_id'], 107003)


class EventRequestNotifyTests(TestCase):
    @patch('events.services.event_request_notify.post_event_request_to_slack', return_value=False)
    @patch('events.services.event_request_notify.post_event_request_to_chatwoot', return_value=False)
    @patch('events.services.event_request_notify.send_staff_mail', return_value=True)
    def test_emails_staff_when_chatwoot_and_slack_fail(self, mock_mail, _cw, _slack):
        from events.services.event_request_notify import notify_event_request_for_review

        event_request = _make_event_request()
        result = notify_event_request_for_review(event_request)
        self.assertTrue(result.email)
        self.assertTrue(result.notified)
        mock_mail.assert_called_once()
        self.assertEqual(mock_mail.call_args.kwargs['template_name'], 'event_request_pending')
        context = mock_mail.call_args.kwargs['context']
        self.assertIn('/aprobar/', context['approve_path'])
        self.assertIn('/desaprobar/', context['reject_path'])

    @patch('events.services.event_request_notify.send_staff_mail')
    @patch('events.services.event_request_notify.post_event_request_to_slack', return_value=True)
    @patch('events.services.event_request_notify.post_event_request_to_chatwoot', return_value=False)
    def test_skips_email_when_slack_succeeds(self, _cw, _slack, mock_mail):
        from events.services.event_request_notify import notify_event_request_for_review

        result = notify_event_request_for_review(_make_event_request())
        self.assertTrue(result.slack)
        self.assertFalse(result.email)
        mock_mail.assert_not_called()


class MainEventRotationTests(TestCase):
    def test_expired_main_rotates_to_other_active_event(self):
        now = timezone.now()
        old_main = _make_event(is_main=True, slug='old-main')
        replacement = _make_event(
            slug='nuevo',
            start=now + timedelta(days=3),
            end=now + timedelta(days=4),
        )
        Event.objects.filter(pk=old_main.pk).update(
            start=now - timedelta(days=3),
            end=now - timedelta(hours=1),
        )

        result = reconcile_main_event()
        self.assertEqual(result.pk, replacement.pk)
        old_main.refresh_from_db()
        replacement.refresh_from_db()
        self.assertFalse(old_main.is_main)
        self.assertTrue(replacement.is_main)

    def test_expired_main_without_replacement_is_not_shown_on_home(self):
        now = timezone.now()
        old_main = _make_event(is_main=True, slug='expired-only')
        Event.objects.filter(pk=old_main.pk).update(
            start=now - timedelta(days=3),
            end=now - timedelta(hours=1),
        )

        self.assertIsNone(Event.get_main_event())

    def test_inactive_main_rotates_to_other_active_event(self):
        now = timezone.now()
        old_main = _make_event(is_main=True, slug='inactive-main')
        replacement = _make_event(
            slug='activo',
            start=now - timedelta(hours=1),
            end=now + timedelta(days=1),
        )
        Event.objects.filter(pk=old_main.pk).update(active=False)

        result = reconcile_main_event()
        self.assertEqual(result.pk, replacement.pk)
        old_main.refresh_from_db()
        replacement.refresh_from_db()
        self.assertFalse(old_main.is_main)
        self.assertTrue(replacement.is_main)

    def test_saving_new_main_unsets_previous(self):
        first = _make_event(is_main=True, slug='primero')
        second = _make_event(is_main=True, slug='segundo')
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_main)
        self.assertTrue(second.is_main)
        self.assertEqual(Event.objects.filter(is_main=True).count(), 1)
