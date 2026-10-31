from django.urls import path

from . import resumable, views

app_name = 'clips'

urlpatterns = [
    path('upload/', views.upload, name='upload'),
    path('upload/<uuid:original_id>/', views.confirm, name='confirm'),
    path('upload/resumable/', resumable.start, name='resumable_start'),
    path('upload/resumable/<uuid:partial_id>/', resumable.resume, name='resumable'),
    path('upload/resumable/<uuid:partial_id>/finish/', resumable.finish, name='resumable_finish'),
    path('clip/', views.code_holder, name='code_holder'),
    path('clip/edit/', views.edit, name='edit'),
    path('clip/delete/', views.delete, name='delete'),
    path('clip/reply/', views.reply, name='reply'),
]
