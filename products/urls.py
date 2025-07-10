from django.urls import path
from . import views

urlpatterns = [
    path('process/', views.process_product, name='process_product'),
] 