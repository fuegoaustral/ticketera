from django.urls import path

from .views import complete_profile, verification_congrats, profile_congrats, my_fire_view, my_tickets_view

urlpatterns = [
    # Profile related paths
    path('complete-profile/', complete_profile, name='complete_profile'),
    path('verification-congrats/', verification_congrats, name='verification_congrats'),
    path('profile-congrats/', profile_congrats, name='profile_congrats'),

    path('', my_fire_view, name='mi_fuego'),
    path('mis-bonos/', my_tickets_view, name='my_tickets'),

]

