# Generated manually for caja app

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('events', '0018_event_access_caja'),
        ('tickets', '0061_tickettype_ignore_max_amount_tickettype_show_in_caja'),
    ]

    operations = [
        migrations.CreateModel(
            name='EventProduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(blank=True, max_length=200)),
                ('price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='event_products', to='events.event')),
                ('ticket_type', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='event_product', to='tickets.tickettype')),
            ],
            options={
                'ordering': ['name', 'id'],
            },
        ),
        migrations.CreateModel(
            name='EventCaja',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.IntegerField(default=0)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cajas', to='events.event')),
            ],
            options={
                'verbose_name': 'Caja',
                'verbose_name_plural': 'Cajas',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='CajaSale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('payment_method', models.CharField(choices=[('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia'), ('MP_QR', 'Mercado Pago QR'), ('MP_POINT', 'Mercado Pago Postnet')], max_length=20)),
                ('status', models.CharField(choices=[('PENDING', 'Pendiente'), ('PAID', 'Pagada'), ('CANCELLED', 'Cancelada'), ('EXPIRED', 'Expirada')], default='PENDING', max_length=20)),
                ('total_amount', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('customer_email', models.EmailField(blank=True, max_length=254)),
                ('mark_as_used', models.BooleanField(default=False)),
                ('mp_order_id', models.CharField(blank=True, max_length=64)),
                ('mp_payment_id', models.CharField(blank=True, max_length=64)),
                ('mp_qr_data', models.TextField(blank=True)),
                ('processor_callback', models.JSONField(blank=True, null=True)),
                ('event_caja', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales', to='caja.eventcaja')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='caja_sales', to='tickets.order')),
                ('sold_by', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='caja_sales', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='EventProductStock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.IntegerField(blank=True, help_text='Null = stock ilimitado', null=True)),
                ('event_product', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='stock', to='caja.eventproduct')),
            ],
        ),
        migrations.CreateModel(
            name='EventProductStockRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('delta', models.IntegerField()),
                ('reason', models.CharField(choices=[('INITIAL', 'Stock inicial'), ('ADMIN_ADJUST', 'Ajuste admin'), ('SALE', 'Venta'), ('SALE_CANCEL', 'Cancelación de venta'), ('MIGRATION', 'Migración'), ('ORDER_MINT', 'Emisión online')], max_length=20)),
                ('balance_after', models.IntegerField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('caja_sale', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_records', to='caja.cajasale')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_adjustments', to=settings.AUTH_USER_MODEL)),
                ('event_product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stock_records', to='caja.eventproduct')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_records', to='tickets.order')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='EventCajaProduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sort_order', models.IntegerField(default=0)),
                ('event_caja', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='caja.eventcaja')),
                ('event_product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='caja.eventproduct')),
            ],
            options={
                'ordering': ['sort_order', 'id'],
                'unique_together': {('event_caja', 'event_product')},
            },
        ),
        migrations.AddField(
            model_name='eventcaja',
            name='products',
            field=models.ManyToManyField(related_name='cajas', through='caja.EventCajaProduct', to='caja.eventproduct'),
        ),
        migrations.CreateModel(
            name='EventCajaMercadoPagoConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('external_store_id', models.CharField(blank=True, max_length=64)),
                ('external_pos_id', models.CharField(blank=True, max_length=64)),
                ('store_id', models.BigIntegerField(blank=True, null=True)),
                ('pos_id', models.BigIntegerField(blank=True, null=True)),
                ('terminal_id', models.CharField(blank=True, max_length=128)),
                ('event_caja', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='mercadopago_config', to='caja.eventcaja')),
            ],
        ),
        migrations.CreateModel(
            name='CajaSaleLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.PositiveIntegerField()),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('caja_sale', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='caja.cajasale')),
                ('event_product', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='caja.eventproduct')),
            ],
            options={
                'ordering': ['id'],
            },
        ),
    ]
