import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Achievement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('slug', models.SlugField(max_length=64, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('image', models.CharField(help_text='Ruta relativa a MEDIA_ROOT, ej: logros/3-oscuras.jpg', max_length=255)),
                ('description', models.TextField(blank=True)),
                ('condition_type', models.CharField(choices=[('purchased_events', 'Compró en eventos')], max_length=32)),
                ('condition_config', models.JSONField(blank=True, default=dict, help_text='Parámetros de la condición, ej: {"event_ids": [9, 10, 17]}')),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Logro',
                'verbose_name_plural': 'Logros',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='UserAchievement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('unlocked_at', models.DateTimeField(auto_now_add=True)),
                ('celebration_shown', models.BooleanField(default=False, help_text='True cuando el usuario ya vio el modal de desbloqueo')),
                ('achievement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_achievements', to='logros.achievement')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='achievements', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Logro de usuario',
                'verbose_name_plural': 'Logros de usuarios',
                'unique_together': {('user', 'achievement')},
            },
        ),
    ]
