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
    caja_config_view,
    caja_config_ajax,
    scanner_events_view,
    caja_events_view,
    bonus_report_view,
    caja_view,
    profile_view,
    send_phone_code_ajax,
    verify_phone_code_ajax,
    my_tickets_ajax,
    roles_management_view,
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
    path("mis-eventos/<slug:event_slug>/roles/", roles_management_view, name="roles_management"),
    path("mis-eventos/<slug:event_slug>/puerta/", puerta_admin_view, name="puerta_admin"),
    path("mis-eventos/<slug:event_slug>/reporte-bonos/", bonus_report_view, name="bonus_report"),
    path("mis-eventos/<slug:event_slug>/caja/", caja_view, name="caja"),
    path("mis-eventos/<slug:event_slug>/configuracion-caja/", caja_config_view, name="caja_config"),
    path("mis-eventos/<slug:event_slug>/configuracion-caja/ajax/", caja_config_ajax, name="caja_config_ajax"),
    path("scanner/", scanner_events_view, name="scanner_events"),
    path("cajas/", caja_events_view, name="caja_events"),
    # Event-specific paths
    path("mis-bonos/<slug:event_slug>/volunteering/", volunteering, name="volunteering"),
    path("mis-bonos/<slug:event_slug>/bonos-transferibles/", transferable_tickets_view, name="transferable_tickets"),
    # Event-specific ticket views (after specific paths)
    path("mis-bonos/<slug:event_slug>/", my_ticket_view, name="my_ticket_event"),
    # AJAX endpoint for auto-refresh
    path("mis-bonos/<slug:event_slug>/ajax/", my_tickets_ajax, name="my_tickets_ajax"),
    # Default redirect (no slug) - MUST BE LAST
    path("mis-bonos/", my_ticket_view, name="my_ticket"),
]
