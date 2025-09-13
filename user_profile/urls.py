from django.urls import path

app_name = 'user_profile'

from .views import (
    complete_profile,
    verification_congrats,
    profile_congrats,
    my_fire_view,
    my_ticket_view,
    transferable_tickets_view,
    volunteering,
    my_orders_view,
    my_events_view,
    event_admin_view,
    forum_profile,
    phone_profile,
    update_private_profile_ajax,
)

urlpatterns = [
    # Profile related paths
    path("complete-profile/", complete_profile, name="complete_profile"),
    path("verification-congrats/", verification_congrats, name="verification_congrats"),
    path("profile-congrats/", profile_congrats, name="profile_congrats"),
    path("perfil/", forum_profile, name="forum_profile"),
    path("telefono/", phone_profile, name="phone_profile"),
    path("perfil/update-private/", update_private_profile_ajax, name="update_private_profile_ajax"),
    path("", my_fire_view, name="mi_fuego"),
    # Specific paths (must come before generic slug pattern)
    path("mis-bonos/eventos-anteriores/", my_ticket_view, {"event_slug": "eventos-anteriores"}, name="my_ticket_past"),
    path("mis-bonos/ordenes/", my_orders_view, name="my_orders"),
    path("mis-eventos/", my_events_view, name="my_events"),
    path("mis-eventos/<slug:event_slug>/", event_admin_view, name="event_admin"),
    # Event-specific paths
    path("mis-bonos/<slug:event_slug>/volunteering/", volunteering, name="volunteering"),
    path("mis-bonos/<slug:event_slug>/bonos-transferibles/", transferable_tickets_view, name="transferable_tickets"),
    # Event-specific ticket views (after specific paths)
    path("mis-bonos/<slug:event_slug>/", my_ticket_view, name="my_ticket_event"),
    # Default redirect (no slug) - MUST BE LAST
    path("mis-bonos/", my_ticket_view, name="my_ticket"),
]
