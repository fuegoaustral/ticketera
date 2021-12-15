from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('new-order/<int:ticket_type_id>/', views.order, name='order'),
    path('order/<str:order_key>', views.order_detail, name='order_detail'),
    path('ticket/<str:ticket_key>', views.ticket_detail, name='ticket_detail'),
    path('order/<str:order_key>/payments/success', views.payment_success,  name='payment_success_callback'),
    path('order/<str:order_key>/payments/failure', views.payment_failure, name='payment_failure_callback'),
    path('order/<str:order_key>/payments/pending', views.payment_pending, name='payment_pending_callback'),
    path('order/<str:order_key>/confirm', views.free_order_confirmation, name='free_order_confirmation'),
    path('payments/ipn/', views.payment_notification, name='payment_notification'),

]
