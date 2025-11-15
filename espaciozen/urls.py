from django.urls import path
from . import views

urlpatterns = [
    path('', views.espaciozen_home, name='espaciozen_home'),
    path('api/verificar-disponibilidad/', views.verificar_disponibilidad, name='verificar_disponibilidad'),
    path('api/crear-reserva/', views.crear_reserva, name='crear_reserva'),
    path('api/listar-reservas/', views.listar_reservas, name='listar_reservas'),
    path('api/editar-reserva/', views.editar_reserva, name='editar_reserva'),
    path('api/borrar-reserva/', views.borrar_reserva, name='borrar_reserva'),
]

