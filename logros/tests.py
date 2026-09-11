from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from logros.models import Achievement, UserAchievement
from logros.services import (
    RedeemCodeError,
    check_and_unlock_for_user,
    evaluate_and_get_pending_payload,
    get_achievements_for_user,
    grant_achievement,
    mark_celebrations_shown,
    redeem_achievement_code,
    revoke_achievement,
)
from tickets.models import NewTicket, Order, OrderTicket, Ticket, TicketType
from utils.context_processors import pending_logro_celebrations

TINY_GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


def _image(name='logro.gif'):
    return SimpleUploadedFile(name, TINY_GIF, content_type='image/gif')


def _make_user(username='logros-user', document_number=None):
    user = User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='pass',
        first_name='Ada',
        last_name='Lovelace',
    )
    profile = user.profile
    profile.document_number = document_number or f'30{User.objects.count():06d}'
    profile.phone = '+5491112345678'
    profile.profile_completion = 'COMPLETE'
    profile.save()
    return user


def _make_event(**kwargs):
    now = timezone.now()
    counter = Event.objects.count() + 1
    defaults = {
        'name': f'Evento {counter}',
        'title': f'Evento {counter}',
        'description': 'desc',
        'start': now - timedelta(days=1),
        'end': now + timedelta(days=2),
        'transfers_enabled_until': now + timedelta(days=1),
        'header_image': _image(f'banner-{counter}.gif'),
        'active': True,
        'is_main': False,
        'slug': f'evento-logro-{counter}',
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _make_order(user, event, status=Order.OrderStatus.CONFIRMED):
    return Order.objects.create(
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        phone='1111111111',
        dni='30111222',
        amount=Decimal('10.00'),
        event=event,
        user=user,
        status=status,
    )


def _make_ticket_type(event, name='General'):
    return TicketType.objects.create(
        event=event,
        name=name,
        price=Decimal('10.00'),
        ticket_count=10,
    )


def _make_ticket(user, event, order, **kwargs):
    ticket_type = kwargs.pop('ticket_type', None) or _make_ticket_type(event)
    defaults = {
        'event': event,
        'order': order,
        'ticket_type': ticket_type,
        'owner': user,
        'holder': user,
        'is_used': True,
    }
    defaults.update(kwargs)
    return NewTicket.objects.create(**defaults)


def _make_achievement(slug, name, event_ids, sort_order=1, **kwargs):
    defaults = {
        'slug': slug,
        'name': name,
        'image': _image(f'{slug}.gif'),
        'description': f'Desc {name}',
        'condition_type': Achievement.ConditionType.PURCHASED_EVENTS,
        'condition_config': {'event_ids': event_ids},
        'is_active': True,
        'sort_order': sort_order,
    }
    defaults.update(kwargs)
    return Achievement.objects.create(**defaults)


class LogrosServiceTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.event_a = _make_event(is_main=True)
        self.event_b = _make_event()
        self.achievement = _make_achievement(
            'dos-eventos',
            'Dos eventos',
            [self.event_a.id, self.event_b.id],
        )

    def test_locked_until_all_required_events_are_purchased(self):
        _make_order(self.user, self.event_a)
        unlocked = check_and_unlock_for_user(self.user)
        self.assertEqual(unlocked, [])
        self.assertFalse(UserAchievement.objects.filter(user=self.user).exists())

        _make_order(self.user, self.event_b)
        unlocked = check_and_unlock_for_user(self.user)
        self.assertEqual([item.slug for item in unlocked], ['dos-eventos'])
        ua = UserAchievement.objects.get(user=self.user, achievement=self.achievement)
        self.assertFalse(ua.celebration_shown)

    def test_gallery_separates_unlocked_and_locked_without_unlocking_twice(self):
        other = _make_achievement('otro', 'Otro', [self.event_a.id], sort_order=2)
        _make_order(self.user, self.event_a)
        check_and_unlock_for_user(self.user)

        items = get_achievements_for_user(self.user)
        by_slug = {item['achievement'].slug: item for item in items}
        self.assertTrue(by_slug['otro']['unlocked'])
        self.assertFalse(by_slug['dos-eventos']['unlocked'])
        self.assertIsNotNone(by_slug['otro']['unlocked_at'])
        self.assertIsNone(by_slug['dos-eventos']['unlocked_at'])

        self.assertEqual(check_and_unlock_for_user(self.user), [])

    def test_pending_payload_clears_after_marking_shown(self):
        _make_order(self.user, self.event_a)
        _make_order(self.user, self.event_b)
        payload = evaluate_and_get_pending_payload(self.user)
        self.assertEqual(payload[0]['slug'], 'dos-eventos')
        self.assertIn('image_url', payload[0])

        mark_celebrations_shown(self.user, ['dos-eventos'])
        self.assertEqual(evaluate_and_get_pending_payload(self.user), [])


class VolunteerLogroTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.event_a = _make_event(is_main=True)
        self.event_b = _make_event()
        self.order_a = _make_order(self.user, self.event_a)
        self.order_b = _make_order(self.user, self.event_b)
        self.achievement = _make_achievement(
            'vol-transmutador',
            'Transmutador',
            [self.event_a.id, self.event_b.id],
            condition_type=Achievement.ConditionType.VOLUNTEER_AT_EVENTS,
            condition_config={
                'event_ids': [self.event_a.id, self.event_b.id],
                'role': 'transmutator',
                'must_be_used': True,
            },
        )

    def test_unlocks_if_volunteer_at_any_listed_event(self):
        _make_ticket(
            self.user,
            self.event_b,
            self.order_b,
            volunteer_transmutator=True,
            is_used=True,
        )
        unlocked = check_and_unlock_for_user(self.user)
        self.assertEqual([item.slug for item in unlocked], ['vol-transmutador'])

    def test_does_not_unlock_without_used_ticket_when_required(self):
        _make_ticket(
            self.user,
            self.event_a,
            self.order_a,
            volunteer_transmutator=True,
            is_used=False,
        )
        self.assertEqual(check_and_unlock_for_user(self.user), [])

    def test_does_not_unlock_other_volunteer_roles(self):
        _make_ticket(
            self.user,
            self.event_a,
            self.order_a,
            volunteer_ranger=True,
            volunteer_umpalumpa=True,
            volunteer_mad=True,
            is_used=True,
        )
        self.assertEqual(check_and_unlock_for_user(self.user), [])

    def test_caos_and_ranger_roles_map_to_ticket_fields(self):
        caos = _make_achievement(
            'vol-caos',
            'CAOS',
            [self.event_a.id],
            sort_order=2,
            condition_type=Achievement.ConditionType.VOLUNTEER_AT_EVENTS,
            condition_config={
                'event_ids': [self.event_a.id],
                'role': 'caos',
                'must_be_used': True,
            },
        )
        _make_ticket(
            self.user,
            self.event_a,
            self.order_a,
            volunteer_umpalumpa=True,
            is_used=True,
        )
        unlocked = check_and_unlock_for_user(self.user)
        self.assertEqual({item.slug for item in unlocked}, {'vol-caos'})
        self.assertTrue(
            UserAchievement.objects.filter(user=self.user, achievement=caos).exists()
        )

    def test_without_event_ids_matches_any_event(self):
        open_ended = _make_achievement(
            'vol-ranger-open',
            'Ranger',
            [],
            sort_order=3,
            condition_type=Achievement.ConditionType.VOLUNTEER_AT_EVENTS,
            condition_config={
                'role': 'ranger',
                'must_be_used': True,
            },
        )
        _make_ticket(
            self.user,
            self.event_b,
            self.order_b,
            volunteer_ranger=True,
            is_used=True,
        )
        unlocked = check_and_unlock_for_user(self.user)
        self.assertEqual({item.slug for item in unlocked}, {'vol-ranger-open'})
        self.assertTrue(
            UserAchievement.objects.filter(user=self.user, achievement=open_ended).exists()
        )


class AttendedLogroTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.event_a = _make_event(is_main=True)
        self.event_b = _make_event()
        self.event_c = _make_event()
        self.event_ids = [self.event_a.id, self.event_b.id, self.event_c.id]
        self.order_a = _make_order(self.user, self.event_a)
        self.order_b = _make_order(self.user, self.event_b)
        self.order_c = _make_order(self.user, self.event_c)

    def _achievement(self, slug, min_count, sort_order=1):
        return _make_achievement(
            slug,
            slug,
            self.event_ids,
            sort_order=sort_order,
            condition_type=Achievement.ConditionType.ATTENDED_EVENTS,
            condition_config={
                'event_ids': self.event_ids,
                'min_count': min_count,
                'must_be_used': True,
            },
        )

    def test_one_used_ticket_unlocks_min_count_one_not_two(self):
        one = self._achievement('fa-1', 1, sort_order=1)
        two = self._achievement('fa-2', 2, sort_order=2)
        _make_ticket(self.user, self.event_a, self.order_a, is_used=True)
        unlocked = {item.slug for item in check_and_unlock_for_user(self.user)}
        self.assertEqual(unlocked, {'fa-1'})
        self.assertTrue(UserAchievement.objects.filter(user=self.user, achievement=one).exists())
        self.assertFalse(UserAchievement.objects.filter(user=self.user, achievement=two).exists())

    def test_two_used_tickets_unlock_min_count_two(self):
        self._achievement('fa-2', 2)
        _make_ticket(self.user, self.event_a, self.order_a, is_used=True)
        _make_ticket(self.user, self.event_c, self.order_c, is_used=True)
        unlocked = {item.slug for item in check_and_unlock_for_user(self.user)}
        self.assertEqual(unlocked, {'fa-2'})

    def test_unused_ticket_does_not_count(self):
        self._achievement('fa-1', 1)
        _make_ticket(self.user, self.event_a, self.order_a, is_used=False)
        self.assertEqual(check_and_unlock_for_user(self.user), [])

    def test_holder_without_owner_counts_when_used(self):
        self._achievement('fa-1', 1)
        _make_ticket(self.user, self.event_a, self.order_a, owner=None, holder=self.user, is_used=True)
        unlocked = {item.slug for item in check_and_unlock_for_user(self.user)}
        self.assertEqual(unlocked, {'fa-1'})

    def test_must_be_used_false_counts_confirmed_orders(self):
        _make_achievement(
            'fa-3-orders',
            'fa-3-orders',
            self.event_ids,
            condition_type=Achievement.ConditionType.ATTENDED_EVENTS,
            condition_config={
                'event_ids': self.event_ids,
                'min_count': 3,
                'must_be_used': False,
            },
        )
        unlocked = {item.slug for item in check_and_unlock_for_user(self.user)}
        self.assertEqual(unlocked, {'fa-3-orders'})

    def test_legacy_ticket_counts_metanoia_style_orders_without_event_fk(self):
        """Bonos pre-NewTicket: event sale del TicketType, el Order puede no tener event_id."""
        order = _make_order(self.user, self.event_c)
        order.event = None
        order.save(update_fields=['event'])
        ticket_type = _make_ticket_type(self.event_c, name='Metanoia')
        OrderTicket.objects.create(order=order, ticket_type=ticket_type, quantity=1)
        Ticket.objects.create(
            first_name=self.user.first_name,
            last_name=self.user.last_name,
            email=self.user.email,
            phone='1111111111',
            dni='30111222',
            volunteer='no',
            volunteer_ranger=False,
            volunteer_transmutator=False,
            volunteer_umpalumpa=False,
            order=order,
            price=10,
        )
        _make_achievement(
            'fa-legacy',
            'fa-legacy',
            [self.event_c.id],
            condition_type=Achievement.ConditionType.ATTENDED_EVENTS,
            condition_config={
                'event_ids': [self.event_c.id],
                'min_count': 1,
                'must_be_used': True,
            },
        )
        unlocked = {item.slug for item in check_and_unlock_for_user(self.user)}
        self.assertIn('fa-legacy', unlocked)


class LogrosUITests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.event = _make_event(is_main=True)
        self.unlocked = _make_achievement('visible', 'Logro visible', [self.event.id], sort_order=1)
        self.locked = _make_achievement('secreto', 'Logro secreto', [99999], sort_order=2)
        _make_order(self.user, self.event)
        self.client.force_login(self.user)

    def test_mis_logros_hides_image_for_locked_and_shows_both_sections(self):
        response = self.client.get(reverse('mis_logros'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Logro visible', content)
        self.assertIn('Logro secreto', content)
        self.assertIn(self.unlocked.image.url, content)
        self.assertNotIn(self.locked.image.url, content)
        self.assertIn('Por desbloquear', content)
        self.assertIn('Desbloqueados', content)
        self.assertIn('logro-locked-placeholder', content)
        self.assertIn('¿Tenés un código?', content)
        self.assertIn('name="redeem_code"', content)

    def test_home_shows_unseen_logro_payload_when_logged_in(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'pending-logro-celebrations-data')
        self.assertContains(response, 'Logro visible')
        self.assertContains(response, 'logroUnlockedModal')
        ua = UserAchievement.objects.get(user=self.user, achievement=self.unlocked)
        self.assertFalse(ua.celebration_shown)

    def test_home_does_not_prompt_after_celebration_was_shown(self):
        check_and_unlock_for_user(self.user)
        mark_celebrations_shown(self.user, ['visible'])
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'pending-logro-celebrations-data')

    def test_mark_celebration_shown_endpoint(self):
        check_and_unlock_for_user(self.user)
        response = self.client.post(
            reverse('logros_mark_celebration_shown'),
            data='{"slugs": ["visible"]}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        ua = UserAchievement.objects.get(user=self.user, achievement=self.unlocked)
        self.assertTrue(ua.celebration_shown)


class LogrosContextProcessorTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.event = _make_event(is_main=True)
        self.achievement = _make_achievement('home-logro', 'En home', [self.event.id])
        _make_order(self.user, self.event)
        self.factory = RequestFactory()

    def test_skips_payment_callback_so_checkout_owns_the_queue(self):
        request = self.factory.get('/checkout/payment-callback/fake')
        request.user = self.user
        request.resolver_match = type('Match', (), {'url_name': 'checkout_payment_callback'})()
        context = pending_logro_celebrations(request)
        self.assertEqual(context['pending_logro_celebrations'], [])
        self.assertFalse(UserAchievement.objects.filter(user=self.user).exists())

    def test_anonymous_users_get_empty_payload(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get('/')
        request.user = AnonymousUser()
        context = pending_logro_celebrations(request)
        self.assertEqual(context['pending_logro_celebrations'], [])


class ManualGrantAndRevokeTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.event = _make_event(is_main=True)
        self.manual_only = Achievement.objects.create(
            slug='solo-manual',
            name='Solo manual',
            image=_image('solo-manual.gif'),
            description='Sin condición',
            condition_type=None,
            condition_config={},
            is_active=True,
            sort_order=1,
        )
        self.auto = _make_achievement(
            'auto-compra',
            'Auto compra',
            [self.event.id],
            sort_order=2,
        )

    def test_manual_only_achievement_does_not_auto_unlock(self):
        self.assertEqual(check_and_unlock_for_user(self.user), [])
        self.assertFalse(
            UserAchievement.objects.filter(user=self.user, achievement=self.manual_only).exists()
        )

    def test_grant_and_revoke_manual_achievement(self):
        grant_achievement(self.user, self.manual_only, manual=True)
        items = {item['achievement'].slug: item for item in get_achievements_for_user(self.user)}
        self.assertTrue(items['solo-manual']['unlocked'])

        revoke_achievement(self.user, self.manual_only)
        ua = UserAchievement.objects.get(user=self.user, achievement=self.manual_only)
        self.assertTrue(ua.revoked)
        items = {item['achievement'].slug: item for item in get_achievements_for_user(self.user)}
        self.assertFalse(items['solo-manual']['unlocked'])

    def test_revoke_blocks_auto_regrant_when_condition_met(self):
        _make_order(self.user, self.event)
        unlocked = check_and_unlock_for_user(self.user)
        self.assertEqual([a.slug for a in unlocked], ['auto-compra'])

        revoke_achievement(self.user, self.auto)
        self.assertEqual(check_and_unlock_for_user(self.user), [])
        items = {item['achievement'].slug: item for item in get_achievements_for_user(self.user)}
        self.assertFalse(items['auto-compra']['unlocked'])

    def test_regrant_clears_revoked_and_shows_again(self):
        _make_order(self.user, self.event)
        check_and_unlock_for_user(self.user)
        revoke_achievement(self.user, self.auto)

        ua = grant_achievement(self.user, self.auto, manual=True)
        self.assertFalse(ua.revoked)
        self.assertTrue(ua.granted_manually)
        self.assertFalse(ua.celebration_shown)
        items = {item['achievement'].slug: item for item in get_achievements_for_user(self.user)}
        self.assertTrue(items['auto-compra']['unlocked'])


class AdminLogrosViewsTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group, Permission

        self.user = _make_user('admin-logros')
        self.other = _make_user('sin-permiso')
        self.event = _make_event(is_main=True)
        self.achievement = _make_achievement('admin-visible', 'Admin visible', [self.event.id])

        permission = Permission.objects.get(
            codename='manage_achievements',
            content_type__app_label='logros',
        )
        group, _ = Group.objects.get_or_create(name='Administrador de Logros')
        group.permissions.set([permission])
        self.user.groups.add(group)
        self.client.force_login(self.user)

    def test_admin_pages_forbidden_without_permission(self):
        self.client.force_login(self.other)
        for name in ('admin_logros', 'admin_logros_assign'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 403)

    def test_admin_pages_ok_with_permission(self):
        for name in ('admin_logros', 'admin_logros_assign'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)

    def test_create_achievement_without_condition(self):
        response = self.client.post(
            reverse('admin_logros'),
            data={
                'name': 'Nuevo logro',
                'description': 'Desc',
                'condition_type': '',
                'image': _image('nuevo.gif'),
            },
        )
        self.assertEqual(response.status_code, 302)
        achievement = Achievement.objects.get(name='Nuevo logro')
        self.assertIsNone(achievement.condition_type)
        self.assertEqual(achievement.condition_config, {})

    def test_grant_and_revoke_by_email(self):
        target = _make_user('target-user')
        response = self.client.post(
            reverse('admin_logros_assign'),
            data={
                'action': 'grant',
                'achievement_id': self.achievement.id,
                'identifier': target.email,
            },
        )
        self.assertEqual(response.status_code, 302)
        ua = UserAchievement.objects.get(user=target, achievement=self.achievement)
        self.assertFalse(ua.revoked)
        self.assertTrue(ua.granted_manually)

        response = self.client.post(
            reverse('admin_logros_assign'),
            data={
                'action': 'revoke',
                'achievement_id': self.achievement.id,
                'user_id': target.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        ua.refresh_from_db()
        self.assertTrue(ua.revoked)

    def test_grant_by_dni(self):
        target = _make_user('dni-user')
        response = self.client.post(
            reverse('admin_logros_assign'),
            data={
                'action': 'grant',
                'achievement_id': self.achievement.id,
                'identifier': target.profile.document_number,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            UserAchievement.objects.filter(
                user=target,
                achievement=self.achievement,
                revoked=False,
            ).exists()
        )


class RedeemCodeLogroTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.other = _make_user('otro-user')
        self.achievement = _make_achievement(
            'codigo-secreto',
            'Logro secreto',
            [],
            condition_type=None,
            condition_config={},
            redeem_code='  fuego2026  ',
        )

    def test_redeem_code_is_normalized_on_save(self):
        self.achievement.refresh_from_db()
        self.assertEqual(self.achievement.redeem_code, 'FUEGO2026')

    def test_redeems_shared_code_case_insensitive(self):
        unlocked = redeem_achievement_code(self.user, 'fuego2026')
        self.assertEqual(unlocked.slug, 'codigo-secreto')
        ua = UserAchievement.objects.get(user=self.user, achievement=self.achievement)
        self.assertFalse(ua.revoked)
        self.assertFalse(ua.granted_manually)
        self.assertFalse(ua.celebration_shown)

    def test_same_code_can_be_used_by_multiple_users(self):
        redeem_achievement_code(self.user, 'FUEGO2026')
        redeem_achievement_code(self.other, 'FUEGO2026')
        self.assertEqual(
            UserAchievement.objects.filter(achievement=self.achievement, revoked=False).count(),
            2,
        )

    def test_already_unlocked_raises(self):
        redeem_achievement_code(self.user, 'FUEGO2026')
        with self.assertRaises(RedeemCodeError) as ctx:
            redeem_achievement_code(self.user, 'FUEGO2026')
        self.assertEqual(ctx.exception.code, 'already_unlocked')

    def test_invalid_code_raises(self):
        with self.assertRaises(RedeemCodeError) as ctx:
            redeem_achievement_code(self.user, 'NOEXISTE')
        self.assertEqual(ctx.exception.code, 'invalid')

    def test_empty_code_raises(self):
        with self.assertRaises(RedeemCodeError) as ctx:
            redeem_achievement_code(self.user, '   ')
        self.assertEqual(ctx.exception.code, 'empty')

    def test_inactive_achievement_cannot_be_redeemed(self):
        self.achievement.is_active = False
        self.achievement.save()
        with self.assertRaises(RedeemCodeError) as ctx:
            redeem_achievement_code(self.user, 'FUEGO2026')
        self.assertEqual(ctx.exception.code, 'inactive')

    def test_code_only_achievement_is_not_auto_unlocked(self):
        self.assertEqual(check_and_unlock_for_user(self.user), [])
        self.assertFalse(UserAchievement.objects.filter(user=self.user).exists())

    def test_revoked_user_can_redeem_again(self):
        redeem_achievement_code(self.user, 'FUEGO2026')
        revoke_achievement(self.user, self.achievement)
        unlocked = redeem_achievement_code(self.user, 'FUEGO2026')
        self.assertEqual(unlocked.slug, 'codigo-secreto')
        ua = UserAchievement.objects.get(user=self.user, achievement=self.achievement)
        self.assertFalse(ua.revoked)

    def test_mixed_condition_and_code_can_unlock_either_way(self):
        event = _make_event(is_main=True)
        mixed = _make_achievement(
            'mixto',
            'Mixto',
            [event.id],
            sort_order=2,
            redeem_code='MIXTO123',
        )
        unlocked = redeem_achievement_code(self.user, 'mixto123')
        self.assertEqual(unlocked.slug, 'mixto')

        other_user = _make_user('mixto-buyer')
        _make_order(other_user, event)
        auto = check_and_unlock_for_user(other_user)
        self.assertEqual([item.slug for item in auto], ['mixto'])
        self.assertTrue(
            UserAchievement.objects.filter(user=other_user, achievement=mixed).exists()
        )


class RedeemCodeUITests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.event = _make_event(is_main=True)
        self.unlocked = _make_achievement('visible', 'Logro visible', [self.event.id], sort_order=1)
        _make_order(self.user, self.event)
        self.client.force_login(self.user)

    def test_redeem_code_via_mis_logros_post(self):
        redeemable = _make_achievement(
            'canjeable',
            'Canjeable',
            [],
            sort_order=3,
            condition_type=None,
            condition_config={},
            redeem_code='CANJE123',
        )
        response = self.client.post(
            reverse('mis_logros'),
            {'redeem_code': 'canje123'},
        )
        self.assertRedirects(response, reverse('mis_logros'))
        self.assertTrue(
            UserAchievement.objects.filter(user=self.user, achievement=redeemable, revoked=False).exists()
        )

        follow = self.client.get(reverse('mis_logros'))
        self.assertContains(follow, 'Canjeable')
        self.assertContains(follow, 'pending-logro-celebrations-data')

    def test_redeem_invalid_code_shows_error(self):
        response = self.client.post(
            reverse('mis_logros'),
            {'redeem_code': 'INVALIDO'},
            follow=True,
        )
        self.assertContains(response, 'Ese código no es válido.')
