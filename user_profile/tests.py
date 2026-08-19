import logging

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from user_profile.models import SedeSubscription
from user_profile.services.sede_mercadopago import (
    _deactivate_stale_members,
    _reconcile_local_subscriptions_with_remote_detail_truth,
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
