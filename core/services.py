import re
from .models import DiscoveredClinic, IntakeForm, Clinic
import logging

logger = logging.getLogger(__name__)

def discover_clinics_for_form(form: IntakeForm):
    """
    Uses DuckDuckGo Maps Search to find clinics near the FF's address.
    """
    if not form.firefighter.address:
        logger.error("No address provided for firefighter.")
        return 0

    # TEST MODE PASS-THROUGH
    if form.search_query.strip().upper() == "TEST":
        # First try the firefighter's real phone number
        raw_phone = form.firefighter.phone_number or ""
        phone_override = ''.join(c for c in raw_phone if c.isdigit() or c == '+')
        
        # If phone is empty, try extracting from address (legacy behavior)
        if not phone_override:
            phone_override = ''.join(c for c in form.firefighter.address if c.isdigit() or c == '+')
            
        if len(phone_override) == 10 and not phone_override.startswith('+'):
            phone_override = '+1' + phone_override
            
        DiscoveredClinic.objects.create(
            intake_form=form,
            name="Test Clinic (Safe Mode)",
            phone_number=phone_override,
            address="123 Mock Street",
            call_status='pending'
        )
        return 1

    address = form.firefighter.address
    state_match = re.search(r'\b([A-Z]{2})\b', address)
    state = state_match.group(1) if state_match else ''

    if not state:
        logger.error(f"Could not extract state from address: {address}")
        return 0

    candidates = Clinic.objects.filter(
        is_active=True,
        scheduling_method=Clinic.SCHEDULING_PHONE,
        state=state,
    ).order_by('city', 'name')

    # Prefer same-city matches
    address_lower = address.lower()
    same_city = [c for c in candidates if c.city and c.city.lower() in address_lower]
    others = [c for c in candidates if c not in same_city]
    matched = (same_city + others)[:5]

    logger.info(f"Directory search for state={state}: {len(matched)} clinic(s)")

    clinics_found = 0
    for c in matched:
        phone_clean = ''.join(ch for ch in c.phone if ch.isdigit() or ch == '+')
        if len(phone_clean) == 10 and not phone_clean.startswith('+'):
            phone_clean = '+1' + phone_clean
        if not phone_clean:
            continue

        DiscoveredClinic.objects.get_or_create(
            intake_form=form,
            name=c.name[:200],
            phone_number=phone_clean[:50],
            defaults={'address': c.address[:255], 'call_status': 'pending'},
        )
        clinics_found += 1

    return clinics_found
def _get_public_domain():
    """Return the public domain for Twilio webhooks (Render hostname or ngrok)."""
    import os
    domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not domain:
        try:
            import requests
            res = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=2)
            domain = res.json()['tunnels'][0]['public_url']
        except Exception:
            pass
    if domain and not domain.startswith('http'):
        domain = f"https://{domain}"
    return domain


def _trigger_clinic_call(form, clinic, domain, client, from_number):
    """Place one Twilio call to a clinic for a form. Returns True on success."""
    try:
        client.calls.create(
            to=clinic.phone_number,
            from_=from_number,
            url=f"{domain}/twilio/twiml/?ff_id={form.firefighter.id}&clinic_id={clinic.id}",
            status_callback=f"{domain}/twilio/call-status/?ff_id={form.firefighter.id}&clinic_id={clinic.id}",
            status_callback_event=['completed'],
            status_callback_method='POST',
        )
        clinic.call_status = 'calling'
        clinic.save()
        form.status = 'calling'
        form.save()
        logger.info(f"Triggered call for {form.firefighter.name} to {clinic.phone_number}")
        return True
    except Exception as e:
        logger.error(f"Twilio call failed: {e}")
        clinic.call_status = 'failed'
        clinic.save()
        return False


def try_next_clinic(form):
    """Find the next un-tried clinic for the form and call it. Returns True if a call was placed."""
    import os
    from twilio.rest import Client

    next_clinic = form.clinics.filter(call_status='pending').first()
    if not next_clinic:
        form.status = 'failed'
        form.save()
        logger.info(f"No more clinics to try for {form.firefighter.name}.")
        return False

    domain = _get_public_domain()
    if not domain:
        logger.error("No public domain available — cannot retry call.")
        return False

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_PHONE_NUMBER")
    if not (account_sid and auth_token):
        logger.error("Twilio credentials missing.")
        return False

    client = Client(account_sid, auth_token)
    return _trigger_clinic_call(form, next_clinic, domain, client, from_number)


def run_queue_logic():
    """
    Core engine that scrapes clinics for pending forms and triggers Twilio calls.
    Returns the number of calls initiated.
    """
    import os
    from twilio.rest import Client
    
    # 1. Scrape Clinics
    pending_forms = IntakeForm.objects.filter(status='pending')
    for form in pending_forms:
        form.status = 'searching'
        form.save()
        clinics_found = discover_clinics_for_form(form)
        if clinics_found > 0:
            form.status = 'queued'
        else:
            form.status = 'failed_search'
        form.save()
        
    # 2. Trigger Calls
    queued_forms = IntakeForm.objects.filter(status='queued')
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_PHONE_NUMBER")
    
    # Determine domain for Webhooks
    domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not domain:
        try:
            import requests
            res = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=2)
            domain = res.json()['tunnels'][0]['public_url']
        except:
            pass
            
    if not domain:
        logger.error("No public domain found for Webhooks. Skipping calls.")
        return 0
        
    if not domain.startswith('http'):
        domain = f"https://{domain}"

    calls_made = 0
    if account_sid and auth_token:
        client = Client(account_sid, auth_token)
        for form in queued_forms:
            clinic = form.clinics.filter(call_status='pending').first()
            if clinic:
                try:
                    client.calls.create(
                        to=clinic.phone_number,
                        from_=from_number,
                        url=f"{domain}/twilio/twiml/?ff_id={form.firefighter.id}&clinic_id={clinic.id}",
                        status_callback=f"{domain}/twilio/call-status/?ff_id={form.firefighter.id}&clinic_id={clinic.id}",
                        status_callback_event=['completed'],
                        status_callback_method='POST',
                    )
                    clinic.call_status = 'calling'
                    clinic.save()
                    form.status = 'calling'
                    form.save()
                    calls_made += 1
                    logger.info(f"Triggered call for {form.firefighter.name} to {clinic.phone_number}")
                except Exception as e:
                    logger.error(f"Twilio call failed: {e}")
                    clinic.call_status = 'failed'
                    clinic.save()
            else:
                form.status = 'failed'
                form.save()
                
    return calls_made
