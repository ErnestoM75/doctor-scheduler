from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Firefighter, IntakeForm, Appointment, DiscoveredClinic
import json
import os

def calendar_home(request):
    ff, _ = Firefighter.objects.get_or_create(name='Demo Firefighter')
    return render(request, 'calendar.html', {'firefighter': ff})

@csrf_exempt
def submit_intake(request):
    """PWA Form hits this to submit requirements and enter the queue."""
    if request.method == 'POST':
        data = json.loads(request.body)
        ff = Firefighter.objects.first()
        if not ff:
            return JsonResponse({'status': 'error', 'msg': 'No ff found'})
        
        # Update FF info
        ff.name = data.get('name', ff.name)
        ff.dob = data.get('dob', ff.dob)
        ff.address = data.get('address', ff.address)
        ff.save()

        # Create IntakeForm
        form = IntakeForm.objects.create(
            firefighter=ff,
            search_query=data.get('search_query', 'DOT medical exams'),
            available_days=','.join(data.get('available_days', [])),
            time_start=data.get('time_start', '08:00'),
            time_end=data.get('time_end', '17:00'),
            status='pending'
        )
        return JsonResponse({'status': 'success', 'form_id': form.id})
    return JsonResponse({'status': 'error'})

@csrf_exempt
def twilio_twiml(request):
    """Twilio hits this to get instructions when the doctor answers."""
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID")
    ff_id = request.GET.get('ff_id', '1')
    clinic_id = request.GET.get('clinic_id', '')
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}">
            <Parameter name="firefighter_id" value="{ff_id}" />
            <Parameter name="clinic_id" value="{clinic_id}" />
        </Stream>
    </Connect>
</Response>"""
    return HttpResponse(twiml, content_type='text/xml')

# --- ELEVENLABS AI TOOL WEBHOOKS ---

@csrf_exempt
def ai_get_availability(request):
    """ElevenLabs Tool: AI calls this to find out when the FF is free."""
    body = json.loads(request.body.decode('utf-8')) if request.body else {}
    ff_id = body.get('firefighter_id', 1)
    
    try:
        ff = Firefighter.objects.get(id=ff_id)
        # Get their active intake form
        form = IntakeForm.objects.filter(firefighter=ff).latest('created_at')
        
        return JsonResponse({
            "status": "success",
            "message": f"{ff.name} is available on these days: {form.available_days}, between {form.time_start.strftime('%I:%M %p')} and {form.time_end.strftime('%I:%M %p')}. Ask the receptionist if any of these specific times work. Also, patient DOB is {ff.dob}.",
        })
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})

@csrf_exempt
def ai_book_appointment(request):
    """ElevenLabs Tool: AI calls this to save the booking."""
    body = json.loads(request.body.decode('utf-8')) if request.body else {}
    ff_id = body.get('firefighter_id', 1)
    clinic_id = body.get('clinic_id', '')
    confirmed_time = body.get('confirmed_time', '')
    
    try:
        ff = Firefighter.objects.get(id=ff_id)
        clinic = DiscoveredClinic.objects.get(id=clinic_id) if clinic_id else None
        
        Appointment.objects.create(
            firefighter=ff,
            clinic=clinic,
            confirmed_time=confirmed_time,
            notes="Booked autonomously by AI."
        )
        # Mark form as booked
        form = IntakeForm.objects.filter(firefighter=ff).latest('created_at')
        form.status = 'booked'
        form.save()
        
        if clinic:
            clinic.call_status = 'success'
            clinic.save()

        return JsonResponse({"status": "success", "message": f"Successfully booked for {confirmed_time}!"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": f"Could not book appointment: {str(e)}"})
