from django.urls import path

from .views import (
    complete_profile,
    verification_congrats,
    profile_congrats,
    my_fire_view,
    my_ticket_view,
    transferable_tickets_view,
    volunteering,
    my_orders_view,
)

urlpatterns = [
    # Profile related paths
    path("complete-profile/", complete_profile, name="complete_profile"),
    path("verification-congrats/", verification_congrats, name="verification_congrats"),
    path("profile-congrats/", profile_congrats, name="profile_congrats"),
    path("", my_fire_view, name="mi_fuego"),
    # Specific paths (must come before generic slug pattern)
    path("mis-bonos/eventos-anteriores/", my_ticket_view, {"event_slug": "eventos-anteriores"}, name="my_ticket_past"),
    path("mis-bonos/ordenes/", my_orders_view, name="my_orders"),
    # Event-specific paths
    path("mis-bonos/<slug:event_slug>/volunteering/", volunteering, name="volunteering"),
    path("mis-bonos/<slug:event_slug>/bonos-transferibles/", transferable_tickets_view, name="transferable_tickets"),
    # Event-specific ticket views (after specific paths)
    path("mis-bonos/<slug:event_slug>/", my_ticket_view, name="my_ticket_event"),
    # Default redirect (no slug) - MUST BE LAST
    path("mis-bonos/", my_ticket_view, name="my_ticket"),
]
