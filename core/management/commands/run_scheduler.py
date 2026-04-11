from django.core.management.base import BaseCommand
from core.models import IntakeForm, DiscoveredClinic, Appointment
from core.services import discover_clinics_for_form
from twilio.rest import Client
import time
from datetime import datetime
import os

class Command(BaseCommand):
    help = 'Runs the asynchronous scheduler queue'

    def handle(self, *args, **options):
        self.stdout.write("Starting AI Scheduler Background Worker...")
        
        while True:
            self.process_queue()
            time.sleep(10) # check every 10 seconds

    def is_business_hours(self):
        # We assume local time for business hours 
        now = datetime.now()
        # For testing, we might want to bypass this. Returning true.
        # return 8 <= now.hour < 17
        return True

    def process_queue(self):
        # 1. Check for newly submitted intake forms that need searching
        pending_forms = IntakeForm.objects.filter(status='pending')
        for form in pending_forms:
            self.stdout.write(f"Scraping clinics for Form {form.id}...")
            form.status = 'searching'
            form.save()
            
            clinics_found = discover_clinics_for_form(form)
            if clinics_found > 0:
                form.status = 'queued'
                self.stdout.write(f"Found {clinics_found} clinics. Moving to queue.")
            else:
                form.status = 'failed_search'
                self.stdout.write("No clinics found.")
            form.save()

        # 2. Check for queued forms and trigger calls IF business hours
        if not self.is_business_hours():
            return
            
        queued_forms = IntakeForm.objects.filter(status='queued')
        for form in queued_forms:
            # Get next pending clinic
            clinic = form.clinics.filter(call_status='pending').first()
            if not clinic:
                form.status = 'failed'
                form.save()
                self.stdout.write(f"Form {form.id} exhausted all clinics.")
                continue
                
            self.trigger_call(form, clinic)

    def trigger_call(self, form, clinic):
        self.stdout.write(f"Dialing {clinic.name} ({clinic.phone_number})...")
        
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        from_number = os.environ.get("TWILIO_PHONE_NUMBER")
        
        domain = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if not domain:
            domain = self.get_ngrok_domain()
            if not domain:
                self.stdout.write("Error: Could not determine domain.")
                return
            
        if not domain.startswith('http'):
            domain = f"https://{domain}"

        client = Client(account_sid, auth_token)
        
        try:
            call = client.calls.create(
                to=clinic.phone_number,
                from_=from_number,
                url=f"{domain}/twilio/twiml/?ff_id={form.firefighter.id}&clinic_id={clinic.id}"
            )
            self.stdout.write(f"Call initiated: {call.sid}")
            clinic.call_status = 'calling'
            clinic.save()
            
            form.status = 'calling'
            form.save()
            
        except Exception as e:
            self.stdout.write(f"Twilio error: {e}")
            clinic.call_status = 'failed'
            clinic.save()

    def get_ngrok_domain(self):
        try:
            import requests
            res = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=2)
            data = res.json()
            return data['tunnels'][0]['public_url']
        except:
            return None
