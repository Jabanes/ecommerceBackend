from django.urls import path
from . import views

urlpatterns = [
    path('create/', views.create_paypal_order, name='create-paypal-order'),
    path('capture/<uuid:order_id>/', views.capture_payment, name='capture-payment'),
    path('process/<uuid:order_id>/', views.process_order, name='process-order'),
    path('test-process-order/<uuid:order_id>/', views.test_process_order, name='test-process-order'),
    path('return/', views.paypal_return, name='paypal-return'),
    path('cancel/', views.paypal_cancel, name='paypal-cancel'),
    path('webhook/', views.paypal_webhook, name='paypal-webhook'),
] 