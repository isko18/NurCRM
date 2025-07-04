# Generated by Django 5.2.1 on 2025-06-15 17:33

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0005_notification'),
        ('users', '0004_alter_company_options_company_owner_user_role_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='analytics',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='analytics', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='contact',
            name='client_company',
            field=models.CharField(default=1, max_length=128),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='deal',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='deals', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='integration',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='integrations', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='notification',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='order',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='orders', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pipeline',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='pipelines', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='product',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='products', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='review',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='users.company'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='task',
            name='company',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='users.company'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='analytics',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name='analytics',
            name='data',
            field=models.JSONField(),
        ),
        migrations.AlterField(
            model_name='analytics',
            name='type',
            field=models.CharField(choices=[('sales', 'Продажи'), ('activity', 'Активность')], max_length=20),
        ),
        migrations.AlterField(
            model_name='contact',
            name='address',
            field=models.CharField(max_length=256),
        ),
        migrations.AlterField(
            model_name='contact',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to='users.company'),
        ),
        migrations.AlterField(
            model_name='contact',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name='contact',
            name='department',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='contact',
            name='email',
            field=models.EmailField(max_length=254),
        ),
        migrations.AlterField(
            model_name='contact',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='contact',
            name='name',
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name='contact',
            name='notes',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='contact',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='contact',
            name='phone',
            field=models.CharField(max_length=32),
        ),
        migrations.AlterField(
            model_name='contact',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='deal',
            name='assigned_to',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_deals', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='deal',
            name='contact',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deals', to='main.contact'),
        ),
        migrations.AlterField(
            model_name='deal',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name='deal',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='deal',
            name='pipeline',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deals', to='main.pipeline'),
        ),
        migrations.AlterField(
            model_name='deal',
            name='stage',
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name='deal',
            name='status',
            field=models.CharField(choices=[('lead', 'Лид'), ('prospect', 'Потенциальный клиент'), ('deal', 'Сделка в работе'), ('closed', 'Закрыта'), ('lost', 'Потеряна')], max_length=20),
        ),
        migrations.AlterField(
            model_name='deal',
            name='title',
            field=models.CharField(max_length=255),
        ),
        migrations.AlterField(
            model_name='deal',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='deal',
            name='value',
            field=models.DecimalField(decimal_places=2, max_digits=12),
        ),
        migrations.AlterField(
            model_name='integration',
            name='config',
            field=models.JSONField(),
        ),
        migrations.AlterField(
            model_name='integration',
            name='status',
            field=models.CharField(choices=[('active', 'Активна'), ('inactive', 'Неактивна')], default='inactive', max_length=20),
        ),
        migrations.AlterField(
            model_name='integration',
            name='type',
            field=models.CharField(choices=[('telephony', 'Телефония'), ('messenger', 'Мессенджер'), ('1c', '1C')], max_length=20),
        ),
        migrations.AlterField(
            model_name='notification',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name='notification',
            name='is_read',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='notification',
            name='message',
            field=models.TextField(),
        ),
        migrations.AlterField(
            model_name='notification',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='order',
            name='customer_name',
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name='order',
            name='date_ordered',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='order',
            name='department',
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name='order',
            name='order_number',
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name='order',
            name='phone',
            field=models.CharField(max_length=32),
        ),
        migrations.AlterField(
            model_name='order',
            name='quantity',
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(choices=[('new', 'Новый'), ('pending', 'В процессе'), ('completed', 'Завершён')], default='new', max_length=20),
        ),
        migrations.AlterField(
            model_name='order',
            name='total',
            field=models.DecimalField(decimal_places=2, max_digits=12),
        ),
        migrations.AlterField(
            model_name='pipeline',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name='pipeline',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='pipeline',
            name='name',
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name='pipeline',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pipelines', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='pipeline',
            name='stages',
            field=models.JSONField(),
        ),
        migrations.AlterField(
            model_name='pipeline',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='product',
            name='article',
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name='product',
            name='brand',
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name='product',
            name='category',
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name='product',
            name='name',
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name='product',
            name='price',
            field=models.DecimalField(decimal_places=2, max_digits=10),
        ),
        migrations.AlterField(
            model_name='product',
            name='quantity',
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name='review',
            name='comment',
            field=models.TextField(),
        ),
        migrations.AlterField(
            model_name='review',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name='review',
            name='rating',
            field=models.PositiveSmallIntegerField(),
        ),
        migrations.AlterField(
            model_name='review',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='review',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='task',
            name='assigned_to',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='task',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name='task',
            name='deal',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='main.deal'),
        ),
        migrations.AlterField(
            model_name='task',
            name='description',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='task',
            name='due_date',
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name='task',
            name='status',
            field=models.CharField(choices=[('pending', 'В ожидании'), ('in_progress', 'В процессе'), ('done', 'Выполнена')], default='pending', max_length=20),
        ),
        migrations.AlterField(
            model_name='task',
            name='title',
            field=models.CharField(max_length=255),
        ),
        migrations.AlterField(
            model_name='task',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
