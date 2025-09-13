from django.urls import path
from . import views

app_name = 'forum'

urlpatterns = [
    path('', views.forum_index, name='forum_index'),
    path('seccion/<int:section_id>/', views.section_detail, name='section_detail'),
    path('hilo/<int:thread_id>/', views.thread_detail, name='thread_detail'),
    path('seccion/<int:section_id>/nuevo-hilo/', views.new_thread, name='new_thread'),
    path('mensaje/<int:message_id>/editar/', views.edit_message, name='edit_message'),
    path('mensaje/<int:message_id>/eliminar/', views.delete_message, name='delete_message'),
]
