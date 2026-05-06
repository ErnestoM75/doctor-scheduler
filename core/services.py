from .models import DiscoveredClinic, IntakeForm
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

    query = f"{form.search_query} near {form.firefighter.address}"
    logger.info(f"Searching for: {query}")

    clinics_found = 0
    try:
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        results = ddgs.maps(query, max_results=5)
        
        for place in results:
            title = place.get('title')
            phone = place.get('phone', '')
            address = place.get('address', '')
            
            if title and phone:
                # Clean phone number for Twilio (E.164 format)
                phone_clean = ''.join(c for c in phone if c.isdigit() or c == '+')
                if len(phone_clean) == 10 and not phone_clean.startswith('+'):
                    phone_clean = '+1' + phone_clean
                    
                # Store valid clinic
                DiscoveredClinic.objects.get_or_create(
                    intake_form=form,
                    name=title[:200],
                    phone_number=phone_clean[:50],
                    defaults={'address': address[:255], 'call_status': 'pending'}
                )
                clinics_found += 1
                
    except Exception as e:
        logger.error(f"Search failed: {e}")
        
    return clinics_found
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
                        url=f"{domain}/twilio/twiml/?ff_id={form.firefighter.id}&clinic_id={clinic.id}"
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
