from django.urls import path
from . import views

urlpatterns = [
    path('', views.calendar_home, name='calendar_home'),
    path('submit-intake/', views.submit_intake, name='submit_intake'),
    path('twilio/twiml/', views.twilio_twiml, name='twilio_twiml'),
    path('ai/get-availability/', views.ai_get_availability, name='ai_get_availability'),
    path('ai/book-appointment/', views.ai_book_appointment, name='ai_book_appointment'),
    path('api/internal/process-queue/', views.process_queue_webhook, name='process_queue_webhook'),
]
