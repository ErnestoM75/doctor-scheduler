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
        print("\n" + "="*40)
        print("🚀 --- NEW FORM RECEIVED FROM BROWSER ---")
        print("="*40 + "\n")
        
        try:
            data = json.loads(request.body)
            ff = Firefighter.objects.first()
            if not ff:
                return JsonResponse({'status': 'error', 'msg': 'No ff found'})
            
            # Update FF info
            ff.name = data.get('name', ff.name)
            ff.dob = data.get('dob', ff.dob)
            ff.address = data.get('address', ff.address)
            ff.phone_number = data.get('phone_number', ff.phone_number)
            ff.save()
    
            # Create IntakeForm
            available_days = data.get('available_days', [])
            if not isinstance(available_days, list):
                available_days = [available_days]
                
            form = IntakeForm.objects.create(
                firefighter=ff,
                search_query=data.get('search_query', 'DOT medical exams'),
                available_days=','.join(available_days),
                time_start=data.get('time_start', '08:00'),
                time_end=data.get('time_end', '17:00'),
                status='pending'
            )
            print(f"✅ Form saved (ID: {form.id}) for {ff.name}. Days: {form.available_days}")
    
            # --- AUTO-TRIGGER FOR TEST MODE (BACKGROUND THREAD) ---
            if form.search_query.strip().upper() == "TEST":
                import threading
                from .services import run_queue_logic
                
                # Start the AI call in a separate thread so the browser doesn't wait/hang
                thread = threading.Thread(target=run_queue_logic)
                thread.daemon = True  # Don't block server shutdown
                thread.start()
                
                print("🚀 Background thread started for AI call...")
                
            return JsonResponse({'status': 'success', 'form_id': form.id})
        except Exception as e:
            print(f"❌ ERROR saving form: {e}")
            return JsonResponse({'status': 'error', 'msg': str(e)})

    return JsonResponse({'status': 'error'})

@csrf_exempt
def twilio_twiml(request):
    """Twilio hits this to get instructions when the doctor answers.
    Uses ElevenLabs register_call API via direct HTTP to get TwiML."""
    import requests as http_requests
    import logging
    logger = logging.getLogger(__name__)
    
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID")
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    ff_id = request.GET.get('ff_id', '1')
    clinic_id = request.GET.get('clinic_id', '')

    # Get call metadata from Twilio's POST data
    from_number = request.POST.get('From', '')
    to_number = request.POST.get('To', '')

    # Load firefighter and form data and build a prompt with real values baked in
    patient_name = 'the patient'
    patient_dob = 'unknown'
    available_days = 'unknown'
    time_start = 'unknown'
    time_end = 'unknown'
    clinic_name = 'the clinic'

    try:
        ff = Firefighter.objects.get(id=ff_id)
        form = IntakeForm.objects.filter(firefighter=ff).latest('created_at')
        clinic_obj = DiscoveredClinic.objects.get(id=clinic_id) if clinic_id else None
        patient_name = ff.name
        patient_dob = ff.dob
        available_days = form.available_days
        time_start = form.time_start.strftime('%I:%M %p')
        time_end = form.time_end.strftime('%I:%M %p')
        clinic_name = clinic_obj.name if clinic_obj else 'the clinic'
    except Exception as e:
        logger.warning(f"Could not load firefighter data: {e}")

    prompt = f"""You are an AI scheduling assistant calling a medical clinic on behalf of a firefighter to book a DOT medical exam appointment.

You already have all the information you need. Do NOT ask the receptionist for patient details — YOU provide them.

PATIENT:
- Name: {patient_name}
- Date of Birth: {patient_dob}

AVAILABILITY:
- Days: {available_days}
- Hours: {time_start} to {time_end}

YOU ARE CALLING: {clinic_name}

HOW TO HANDLE THE CALL:
1. When someone answers, say: "Hi, I'm calling to schedule a DOT medical exam for one of our firefighters. The patient's name is {patient_name}, date of birth {patient_dob}. Do you have any availability?"
2. If they offer a day that IS in '{available_days}' and between {time_start} and {time_end}, accept it and use the book_appointment tool to save the booking.
3. If they offer a day or time that does NOT match, say: "I'm sorry, {patient_name} is only available on {available_days} between {time_start} and {time_end}. Do you have anything within that window?"
4. Keep negotiating politely until you find a matching slot or they confirm nothing is available.
5. If nothing is available, thank them and end the call professionally.

RULES:
- Never ask the receptionist for the patient name, DOB, or availability — you already have it all.
- Never accept a day not listed in: {available_days}.
- Never accept a time outside {time_start} to {time_end}.
- Be professional, calm, and brief."""

    try:
        resp = http_requests.post(
            'https://api.elevenlabs.io/v1/convai/twilio/register-call',
            headers={
                'xi-api-key': api_key,
                'Content-Type': 'application/json',
            },
            json={
                'agent_id': agent_id,
                'from_number': from_number,
                'to_number': to_number,
                'direction': 'outbound',
                'conversation_initiation_client_data': {
                    'dynamic_variables': {
                        'firefighter_id': str(ff_id),
                        'clinic_id': str(clinic_id),
                        'patient_name': patient_name,
                        'patient_dob': patient_dob,
                        'available_days': available_days,
                        'time_start': time_start,
                        'time_end': time_end,
                        'clinic_name': clinic_name,
                    },
                },
            },
            timeout=10,
        )
        resp.raise_for_status()
        twiml = resp.text
        logger.info(f"ElevenLabs register_call success: {twiml[:200]}")
        return HttpResponse(twiml, content_type='text/xml')
    except Exception as e:
        logger.error(f"ElevenLabs register_call failed: {e}")
        fallback = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>We're sorry, the AI assistant could not connect. Please try again later.</Say>
</Response>"""
        return HttpResponse(fallback, content_type='text/xml')

# --- ELEVENLABS AI TOOL WEBHOOKS ---

@csrf_exempt
def ai_get_availability(request):
    """ElevenLabs Tool: AI calls this to find out when the FF is free."""
    body = json.loads(request.body.decode('utf-8')) if request.body else {}
    ff_id = body.get('firefighter_id', 1)
    clinic_id = body.get('clinic_id', '')
    
    try:
        ff = Firefighter.objects.get(id=ff_id)
        # Get their active intake form
        form = IntakeForm.objects.filter(firefighter=ff).latest('created_at')
        
        # Fetch clinic info if available to give AI better context
        clinic_name = "the clinic"
        clinic_address = "the address provided"
        if clinic_id:
            try:
                clinic = DiscoveredClinic.objects.get(id=clinic_id)
                clinic_name = clinic.name
                clinic_address = clinic.address
            except DiscoveredClinic.DoesNotExist:
                pass

        name = ff.name
        dob = str(ff.dob)
        days = form.available_days
        t_start = form.time_start.strftime('%I:%M %p')
        t_end = form.time_end.strftime('%I:%M %p')

        resp_data = {
            "status": "success",
            "opening_line": f"Hi, I'm calling to schedule a DOT medical exam. The patient's name is {name}, date of birth {dob}. Do you have availability on {days}?",
            "decline_line": f"I'm sorry, the patient is only available on {days} between {t_start} and {t_end}. Do you have anything during that window?",
            "available_days": days,
            "time_start": t_start,
            "time_end": t_end,
            "patient_name": name,
            "patient_dob": dob,
        }
        print(f"DEBUG: AI Tool get_availability for FF {ff_id}: days={days}, {t_start}-{t_end}")
        return JsonResponse(resp_data)
    except Exception as e:
        print(f"DEBUG: AI Tool get_availability ERROR: {e}")
        return JsonResponse({"status": "error", "message": str(e)})

@csrf_exempt
def ai_book_appointment(request):
    """ElevenLabs Tool: AI calls this to save the booking and notify the firefighter via SMS."""
    import logging
    logger = logging.getLogger(__name__)
    
    body = json.loads(request.body.decode('utf-8')) if request.body else {}
    raw_ff_id = request.GET.get('firefighter_id') or body.get('firefighter_id', 1)
    clinic_id = request.GET.get('clinic_id') or request.GET.get('clininc_id') or body.get('clinic_id', '')
    confirmed_time = request.GET.get('confirmed_time') or body.get('confirmed_time', '')

    # Ensure ff_id is a number — fall back to first firefighter if agent sends a name
    try:
        ff_id = int(raw_ff_id)
    except (ValueError, TypeError):
        ff_id = 1

    try:
        ff = Firefighter.objects.get(id=ff_id)
        clinic = DiscoveredClinic.objects.filter(id=clinic_id).first() if clinic_id else None
        
        # --- VALIDATION RAIL ---
        form = IntakeForm.objects.filter(firefighter=ff).latest('created_at')
        allowed_days = [d.strip().lower() for d in form.available_days.split(',')]
        proposed_time_lower = confirmed_time.lower()
        
        # Check if any allowed day is mentioned in the confirmed_time string
        day_match = any(day in proposed_time_lower for day in allowed_days)
        
        if not day_match:
            error_msg = (
                f"STOP! YOU CANNOT BOOK THIS. The firefighter is ONLY available on: {form.available_days}. "
                f"You just tried to book {confirmed_time}, which is INVALID. "
                f"APOLOGIZE to the receptionist immediately and ask for a slot on {form.available_days}."
            )
            print(f"DEBUG: AI Tool book_appointment BLOCKED: {confirmed_time} is not in {form.available_days}")
            return JsonResponse({"status": "error", "message": error_msg})
        
        # If valid, proceed with booking
        appt = Appointment.objects.create(
            firefighter=ff,
            clinic=clinic,
            confirmed_time=confirmed_time,
            notes="Booked autonomously by AI."
        )
        # Mark form as booked
        form.status = 'booked'
        form.save()
        
        if clinic:
            clinic.call_status = 'success'
            clinic.save()

        print(f"DEBUG: AI Tool book_appointment SUCCESS: {confirmed_time} for {ff.name}")

        # --- SMS NOTIFICATION ---
        _send_appointment_sms(ff, clinic, confirmed_time, logger)

        return JsonResponse({"status": "success", "message": f"Successfully booked for {confirmed_time}!"})
    except Exception as e:
        print(f"DEBUG: AI Tool book_appointment ERROR: {e}")
        logger.error(f"ai_book_appointment error: {e}")
        return JsonResponse({"status": "error", "message": f"Could not book appointment: {str(e)}"})


def _send_appointment_sms(ff, clinic, confirmed_time, logger):
    """Send SMS notification to the firefighter about their booked appointment."""
    from twilio.rest import Client
    
    phone = ff.phone_number
    print(f"DEBUG SMS: phone on file = '{phone}'")
    if not phone:
        print(f"DEBUG SMS: No phone number for {ff.name}, skipping SMS.")
        return
    
    # Clean phone to E.164
    phone_clean = ''.join(c for c in phone if c.isdigit() or c == '+')
    if len(phone_clean) == 10 and not phone_clean.startswith('+'):
        phone_clean = '+1' + phone_clean
    
    clinic_name = clinic.name if clinic else "your clinic"
    clinic_addr = clinic.address if clinic else ""
    
    msg = (
        f"📅 APPOINTMENT CONFIRMED\n\n"
        f"Hi {ff.name}, your medical exam has been successfully booked.\n\n"
        f"📍 WHERE: {clinic_name}\n"
        f"🏠 ADDRESS: {clinic_addr}\n"
        f"⏰ WHEN: {confirmed_time}\n\n"
        f"Please arrive 15 minutes early and bring your ID. If you need to reschedule, please call the clinic directly at {clinic.phone_number if clinic else '[Number Not Found]'}."
    )
    
    try:
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        from_number = os.environ.get("TWILIO_PHONE_NUMBER")
        
        client = Client(account_sid, auth_token)
        sms = client.messages.create(
            body=msg,
            from_=from_number,
            to=phone_clean,
        )
        print(f"DEBUG SMS: Sent to {phone_clean}: {sms.sid}")
    except Exception as e:
        print(f"DEBUG SMS ERROR: Failed for {phone_clean}: {e}")

@csrf_exempt
def process_queue_webhook(request):
    """GitHub Actions hits this every 10 minutes to process the queue natively."""
    from .services import run_queue_logic
    calls_made = run_queue_logic()
    return JsonResponse({"status": "success", "calls_made": calls_made})
