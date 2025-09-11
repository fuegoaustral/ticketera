GROUPS_PERMISSIONS = {
    'Caja': [
        {'app_label': 'tickets', 'codename': 'can_sell_tickets'},
    ],
    'Admin Voluntarios': [
        {'app_label': 'tickets', 'codename': 'admin_volunteers'},
        {'app_label': 'tickets', 'codename': 'add_directtickettemplate'},
        {'app_label': 'tickets', 'codename': 'change_directtickettemplate'},
        {'app_label': 'tickets', 'codename': 'delete_directtickettemplate'},
        {'app_label': 'tickets', 'codename': 'view_directtickettemplate'},
    ],
    'Event Organizer': [
        {'app_label': 'events', 'codename': 'view_tickets_sold_report'},
    ],
}