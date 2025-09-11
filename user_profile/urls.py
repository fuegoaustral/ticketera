from django.urls import path

from .views import (
    complete_profile,
    verification_congrats,
    profile_congrats,
    my_fire_view,
    my_ticket_view,
    transferable_tickets_view,
    volunteering,
)

urlpatterns = [
    # Profile related paths
    path("complete-profile/", complete_profile, name="complete_profile"),
    path("verification-congrats/", verification_congrats, name="verification_congrats"),
    path("profile-congrats/", profile_congrats, name="profile_congrats"),
    path("", my_fire_view, name="mi_fuego"),
    # Event-specific ticket views
    path("mis-bonos/<slug:event_slug>/", my_ticket_view, name="my_ticket_event"),
    # Past events view (specific slug)
    path("mis-bonos/eventos-anteriores/", my_ticket_view, name="my_ticket_past"),
    # Default redirect (no slug)
    path("mis-bonos/", my_ticket_view, name="my_ticket"),
    path(
        "mis-bonos/bonos-transferibles",
        transferable_tickets_view,
        name="transferable_tickets",
    ),
    path("mis-bonos/volunteering/", volunteering, name="volunteering"),
]
