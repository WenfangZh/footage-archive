from django.urls import path

from . import views

app_name = 'clips'

urlpatterns = [
    path('upload/', views.upload, name='upload'),
    path('upload/<uuid:original_id>/', views.confirm, name='confirm'),
    path('clip/', views.code_holder, name='code_holder'),
    path('clip/edit/', views.edit, name='edit'),
    path('clip/delete/', views.delete, name='delete'),
]
