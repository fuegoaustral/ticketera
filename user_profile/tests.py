import logging
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from user_profile.forms import (
    MAX_EVENT_REQUEST_BANNER_BYTES,
    EventRequestForm,
)
from user_profile.models import SedeSubscription
from user_profile.services.sede_mercadopago import (
    _deactivate_stale_members,
    _reconcile_local_subscriptions_with_remote_detail_truth,
)

TINY_GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


def _make_profile(username='socio'):
    user = User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='pass',
    )
    profile = user.profile
    profile.document_number = '30111222'
    profile.phone = '+5491112345678'
    profile.save()
    return profile


class ManualSedeMembershipSyncTests(TestCase):
    def test_cron_does_not_deactivate_manual_members(self):
        profile = _make_profile()
        manual = SedeSubscription.objects.create(
            profile=profile,
            subscription_id=f'{SedeSubscription.MANUAL_SUBSCRIPTION_ID_PREFIX}{profile.pk}',
            plan_id=SedeSubscription.MANUAL_PLAN_ID,
            matched_via=SedeSubscription.MANUAL_MATCHED_VIA,
            status='authorized',
            is_active=True,
        )
        mp_stale = SedeSubscription.objects.create(
            profile=profile,
            subscription_id='mp-sub-old',
            plan_id='plan-mp',
            matched_via='email',
            status='authorized',
            is_active=True,
        )

        deactivated = _deactivate_stale_members(active_subscription_ids=[], log=logging.getLogger('test'))

        manual.refresh_from_db()
        mp_stale.refresh_from_db()
        self.assertTrue(manual.is_active)
        self.assertEqual(manual.status, 'authorized')
        self.assertTrue(profile.miembro_sede)
        self.assertFalse(mp_stale.is_active)
        self.assertEqual(mp_stale.status, 'inactive')
        self.assertEqual(deactivated, 1)

    def test_detail_truth_skips_manual_and_keeps_them_active(self):
        profile = _make_profile('manual-only')
        SedeSubscription.objects.create(
            profile=profile,
            subscription_id=f'{SedeSubscription.MANUAL_SUBSCRIPTION_ID_PREFIX}{profile.pk}',
            plan_id=SedeSubscription.MANUAL_PLAN_ID,
            matched_via=SedeSubscription.MANUAL_MATCHED_VIA,
            status='authorized',
            is_active=True,
            synced_at=timezone.now(),
        )

        summary = _reconcile_local_subscriptions_with_remote_detail_truth(sdk=None)
        self.assertEqual(summary['updated'], 0)
        self.assertEqual(summary['errors'], 0)
        self.assertTrue(any(
            sub_id.startswith(SedeSubscription.MANUAL_SUBSCRIPTION_ID_PREFIX)
            for sub_id in summary['active_ids']
        ))
        self.assertTrue(profile.miembro_sede)


def _event_request_form_data(**overrides):
    now = timezone.now()
    data = {
        'name': 'Noche en La Sede',
        'description': '<p>Una noche</p>',
        'start': (now + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'),
        'end': (now + timedelta(days=7, hours=5)).strftime('%Y-%m-%dT%H:%M'),
        'location': 'Paz Soldán 5150, CABA',
        'max_tickets': 300,
    }
    data.update(overrides)
    return data


class EventRequestFormTests(TestCase):
    def test_rejects_banner_over_api_gateway_safe_limit(self):
        banner = SimpleUploadedFile('banner.gif', TINY_GIF, content_type='image/gif')
        banner.size = MAX_EVENT_REQUEST_BANNER_BYTES + 1
        form = EventRequestForm(data=_event_request_form_data(), files={'header_image': banner})
        self.assertFalse(form.is_valid())
        self.assertIn('header_image', form.errors)

    def test_rejects_base64_images_in_description(self):
        form = EventRequestForm(
            data=_event_request_form_data(
                description='<p>hola</p><img src="data:image/png;base64,iVBORw0KGgo=">',
            ),
            files={'header_image': SimpleUploadedFile('banner.gif', TINY_GIF, content_type='image/gif')},
        )
        self.assertFalse(form.is_valid())
        self.assertIn('description', form.errors)

    def test_accepts_small_banner_and_html_description(self):
        form = EventRequestForm(
            data=_event_request_form_data(),
            files={'header_image': SimpleUploadedFile('banner.gif', TINY_GIF, content_type='image/gif')},
        )
        self.assertTrue(form.is_valid(), form.errors)
