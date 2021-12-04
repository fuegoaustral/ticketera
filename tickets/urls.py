from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('ticket/<int:ticket_type_id>/', views.order, name='order')
]
