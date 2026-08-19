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
    check_and_unlock_for_user,
    evaluate_and_get_pending_payload,
    get_achievements_for_user,
    mark_celebrations_shown,
)
from tickets.models import NewTicket, Order, OrderTicket, Ticket, TicketType
from utils.context_processors import pending_logro_celebrations

TINY_GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


def _image(name='logro.gif'):
    return SimpleUploadedFile(name, TINY_GIF, content_type='image/gif')


def _make_user(username='logros-user'):
    user = User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='pass',
        first_name='Ada',
        last_name='Lovelace',
    )
    profile = user.profile
    profile.document_number = '30111222'
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
