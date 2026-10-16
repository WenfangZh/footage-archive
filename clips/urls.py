from django.urls import path

from . import views

app_name = 'clips'

urlpatterns = [
    path('upload/', views.upload, name='upload'),
    path('upload/<uuid:original_id>/', views.confirm, name='confirm'),
]
