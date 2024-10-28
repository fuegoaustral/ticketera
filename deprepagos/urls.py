from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

from tickets.admin import admin_caja_view, email_has_account, admin_caja_order_view, admin_direct_tickets_view, \
    admin_direct_tickets_buyer_view, admin_direct_tickets_congrats_view

urlpatterns = [
    path('admin/caja/', admin_caja_view, name='admin_caja_view'),

    path('admin/caja/order/<str:order_key>/', admin_caja_order_view, name='admin_caja_order_view'),
    path('admin/caja/email-has-account/', email_has_account, name='email_has_account'),

    path('admin/direct_tickets/', admin_direct_tickets_view, name='admin_direct_tickets_view'),
    path('admin/direct_tickets/buyer/', admin_direct_tickets_buyer_view, name='admin_direct_tickets_buyer_view'),
    path('admin/direct_tickets/congrats/<int:new_order_id>/', admin_direct_tickets_congrats_view,
         name='admin_direct_tickets_congrats_view'),

    path('admin/', admin.site.urls),

    path('mi-fuego/', include('allauth.urls')),
    path('mi-fuego', include('profile.urls')),
    path('', include('tickets.urls')),

]

if settings.DEBUG == True:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
