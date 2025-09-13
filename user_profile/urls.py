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
    my_events_view,
    event_admin_view,
    puerta_admin_view,
    scanner_events_view,
    bonus_report_view,
    profile_view,
    send_phone_code_ajax,
    verify_phone_code_ajax,
)

urlpatterns = [
    # Profile related paths
    path("complete-profile/", complete_profile, name="complete_profile"),
    path("verification-congrats/", verification_congrats, name="verification_congrats"),
    path("profile-congrats/", profile_congrats, name="profile_congrats"),
    path("perfil/", profile_view, name="profile"),
    path("perfil/send-phone-code/", send_phone_code_ajax, name="send_phone_code_ajax"),
    path("perfil/verify-phone-code/", verify_phone_code_ajax, name="verify_phone_code_ajax"),
    path("", my_fire_view, name="mi_fuego"),
    # Specific paths (must come before generic slug pattern)
    path("mis-bonos/eventos-anteriores/", my_ticket_view, {"event_slug": "eventos-anteriores"}, name="my_ticket_past"),
    path("mis-bonos/ordenes/", my_orders_view, name="my_orders"),
    path("mis-eventos/", my_events_view, name="my_events"),
    path("mis-eventos/<slug:event_slug>/", event_admin_view, name="event_admin"),
    path("mis-eventos/<slug:event_slug>/puerta/", puerta_admin_view, name="puerta_admin"),
    path("mis-eventos/<slug:event_slug>/reporte-bonos/", bonus_report_view, name="bonus_report"),
    path("scanner/", scanner_events_view, name="scanner_events"),
    # Event-specific paths
    path("mis-bonos/<slug:event_slug>/volunteering/", volunteering, name="volunteering"),
    path("mis-bonos/<slug:event_slug>/bonos-transferibles/", transferable_tickets_view, name="transferable_tickets"),
    # Event-specific ticket views (after specific paths)
    path("mis-bonos/<slug:event_slug>/", my_ticket_view, name="my_ticket_event"),
    # Default redirect (no slug) - MUST BE LAST
    path("mis-bonos/", my_ticket_view, name="my_ticket"),
]
