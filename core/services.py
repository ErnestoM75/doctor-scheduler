from duckduckgo_search import DDGS
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
