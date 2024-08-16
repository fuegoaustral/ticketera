from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('new-order/<int:ticket_type_id>/', views.order, name='order'),
    path('order/<str:order_key>', views.order_detail, name='order_detail'),
    path('order/<str:order_key>/payments/success', views.payment_success,  name='payment_success_callback'),
    path('order/<str:order_key>/payments/failure', views.payment_failure, name='payment_failure_callback'),
    path('order/<str:order_key>/payments/pending', views.payment_pending, name='payment_pending_callback'),
    path('order/<str:order_key>/confirm', views.free_order_confirmation, name='free_order_confirmation'),
    path('ticket/<str:ticket_key>', views.ticket_detail, name='ticket_detail'),
    path('ticket/<str:ticket_key>/transfer', views.ticket_transfer, name='ticket_transfer'),
    path('ticket/<str:ticket_key>/transfer/confirmation', views.ticket_transfer_confirmation, name='ticket_transfer_confirmation'),
    path('ticket/<str:transfer_key>/confirmed', views.ticket_transfer_confirmed, name='ticket_transfer_confirmed'),
    path('payments/ipn/', views.payment_notification, name='payment_notification'),

    path('dashboard/', views.dashboard_view, name='dashboard'),

    path('complete-profile/', views.complete_profile, name='complete_profile'),
    path('accounts/verification-congrats/', views.verification_congrats, name='verification_congrats'),


]
