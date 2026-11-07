from django.contrib.auth.models import AnonymousUser, User
from django.template import Context, Template
from django.test import TestCase

from user_profile.display import display_full_name, display_label, display_name, nickname
from user_profile.models import Profile


class DisplayNameTests(TestCase):
    def _user(self, email='juan@example.com', first='', last='', nick=None):
        # A signal creates the Profile for non-staff users; nick=None means "no Profile at all".
        user = User.objects.create_user(username=email, email=email, first_name=first, last_name=last)
        if nick is None:
            Profile.objects.filter(user=user).delete()
        else:
            Profile.objects.filter(user=user).update(nickname=nick)
        return User.objects.select_related('profile').get(pk=user.pk)

    def test_nickname_wins(self):
        user = self._user(first='Juan Bautista', last='Sanchez', nick='  Juancho ')
        self.assertEqual(display_name(user), 'Juancho')
        self.assertEqual(display_full_name(user), 'Juan Bautista Sanchez')
        self.assertEqual(display_label(user), 'Juancho (Juan Bautista Sanchez)')
        self.assertEqual(user.profile.display_name, 'Juancho')

    def test_full_name_when_no_nickname(self):
        user = self._user(first='Juan', last='Sanchez', nick='')
        self.assertEqual(display_name(user), 'Juan Sanchez')
        self.assertEqual(display_full_name(user), '')
        self.assertEqual(display_label(user), 'Juan Sanchez')

    def test_email_when_nothing_else(self):
        user = self._user(nick='')
        self.assertEqual(display_name(user), 'juan@example.com')
        self.assertEqual(display_name(user, fallback_to_email=False), '')

    def test_user_without_profile(self):
        user = self._user(first='Ana', last='Gomez')
        self.assertFalse(Profile.objects.filter(user=user).exists())
        self.assertEqual(nickname(user), '')
        self.assertEqual(display_name(user), 'Ana Gomez')
        self.assertEqual(display_label(user), 'Ana Gomez')
        self.assertEqual(display_name(self._user(email='solo@example.com')), 'solo@example.com')

    def test_none_and_anonymous(self):
        for user in (None, AnonymousUser()):
            self.assertEqual(display_name(user), '')
            self.assertEqual(display_label(user), '')
            self.assertEqual(display_full_name(user), '')

    def test_select_related_avoids_queries(self):
        for i in range(3):
            self._user(email=f'p{i}@example.com', first='P', last=str(i), nick=f'Nick{i}')
        users = list(User.objects.select_related('profile').order_by('email'))
        with self.assertNumQueries(0):
            names = [display_name(user) for user in users]
        self.assertEqual(names, ['Nick0', 'Nick1', 'Nick2'])


class DisplayNameFilterTests(TestCase):
    def render(self, text, **context):
        return Template('{% load display_name %}' + text).render(Context(context))

    def test_filters(self):
        user = User.objects.create_user(username='j@example.com', email='j@example.com', first_name='Juan', last_name='Pérez')
        Profile.objects.filter(user=user).update(nickname='Juanchi')
        user = User.objects.select_related('profile').get(pk=user.pk)
        self.assertEqual(
            self.render('{{ u|display_name }}|{{ u|display_full_name }}|{{ u|display_label }}|{{ u|greeting_name }}', u=user),
            'Juanchi|Juan Pérez|Juanchi (Juan Pérez)|Juanchi',
        )

    def test_filters_without_profile_or_name(self):
        user = User.objects.create_user(username='x@example.com', email='x@example.com')
        Profile.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)
        self.assertEqual(self.render('[{{ u|display_name }}][{{ u|greeting_name }}]', u=user), '[x@example.com][]')
        self.assertEqual(self.render('[{{ u|display_name }}]', u=None), '[]')

    def test_escapes_nickname(self):
        user = User.objects.create_user(username='y@example.com', email='y@example.com')
        Profile.objects.filter(user=user).update(nickname='<b>Yo</b>')
        user = User.objects.get(pk=user.pk)
        self.assertEqual(self.render('{{ u|display_name }}', u=user), '&lt;b&gt;Yo&lt;/b&gt;')
