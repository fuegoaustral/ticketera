from datetime import date

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Team, TeamMembership


class TeamMembershipTest(TestCase):
    def setUp(self):
        self.team = Team.objects.get(slug='estafa')
        self.user = User.objects.create_user(username='ana', email='ana@example.com')

    def test_active_periods_keep_history(self):
        TeamMembership.objects.create(team=self.team, user=self.user, started_on=date(2022, 3, 1), ended_on=date(2024, 2, 29))
        self.assertFalse(self.team.is_member(self.user, on=date(2024, 6, 1)))
        self.assertTrue(self.team.is_member(self.user, on=date(2024, 2, 29)))
        TeamMembership.objects.create(team=self.team, user=self.user, started_on=date(2025, 10, 1))
        self.assertTrue(self.team.is_member(self.user, on=date(2025, 10, 1)))
        self.assertEqual(self.user.team_memberships.count(), 2)

    def test_periods_cannot_overlap_or_end_before_start(self):
        TeamMembership.objects.create(team=self.team, user=self.user, started_on=date(2024, 1, 1))
        with self.assertRaises(ValidationError):
            TeamMembership(team=self.team, user=self.user, started_on=date(2025, 1, 1)).full_clean()
        with self.assertRaises(ValidationError):
            TeamMembership(team=self.team, user=self.user, started_on=date(2020, 5, 1), ended_on=date(2020, 4, 1)).full_clean()
        TeamMembership(team=self.team, user=self.user, started_on=date(2020, 1, 1), ended_on=date(2020, 12, 31)).full_clean()
