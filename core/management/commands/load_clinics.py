"""
Load the clinic directory from data/clinics.md into the Clinic model.

Run: python manage.py load_clinics
"""
import re
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from core.models import Clinic


CITY_HEADER = re.compile(r'^###\s+(.+?),\s+([A-Z]{2})\s*$')
CLINIC_HEADER = re.compile(r'^####\s+(.+?)\s*$')
FIELD_LINE = re.compile(r'^\s*-\s+\*\*(.+?):\*\*\s+(.+?)\s*$')


def detect_scheduling_method(text_block: str) -> str:
    """Infer how appointments are made from the clinic's notes."""
    t = text_block.lower()
    if 'self-schedule' in t or 'self schedule' in t or 'patient calls themselves' in t:
        return Clinic.SCHEDULING_SELF
    if 'walk-in only' in t or 'walk in only' in t or '(walk-in)' in t:
        return Clinic.SCHEDULING_WALKIN
    if 'send excel' in t or 'send spreadsheet' in t or 'compile mspa' in t:
        return Clinic.SCHEDULING_EMAIL
    if 'app.squarespacescheduling' in t or 'seekhealth.com' in t:
        return Clinic.SCHEDULING_ONLINE
    return Clinic.SCHEDULING_PHONE


def detect_network(name: str) -> str:
    if name.lower().startswith('concentra'):
        return 'Concentra'
    return ''


def parse_clinic_block(name_line: str, body_lines: list, city: str, state: str) -> dict | None:
    """Turn a #### header + the lines under it into a dict of clinic fields."""
    raw_name = name_line.strip()

    # Skip closed clinics
    if 'PERMANENTLY CLOSED' in raw_name.upper():
        return None
    if raw_name.lower().startswith('(pending'):
        return None

    # Extract optional short name in parentheses: "Cascade Health Solutions (Cascade)"
    short_name = ''
    m = re.match(r'^(.+?)\s*\((.+?)\)\s*$', raw_name)
    if m:
        name = m.group(1).strip()
        short_name = m.group(2).strip()
    else:
        name = raw_name
    # Strip trailing markdown emphasis or status text after em-dash
    name = re.sub(r'\s+—\s+\*\*.+?\*\*$', '', name).strip()

    fields: dict[str, str] = {}
    body_text = '\n'.join(body_lines)
    for line in body_lines:
        fm = FIELD_LINE.match(line)
        if fm:
            key = fm.group(1).strip().lower()
            value = fm.group(2).strip()
            fields[key] = value

    address = fields.get('address', '') or fields.get('address (primary)', '')
    address = re.sub(r'`', '', address)

    phone = fields.get('phone', '')
    phone = re.sub(r'\(.*?\)', '', phone).strip()  # strip "(follow prompts...)"

    hours = fields.get('hours', '') or fields.get('hours (walk-in)', '') or fields.get('hours (walk-in only)', '')
    payment = fields.get('payment', '') or fields.get('billing', '')
    scheduling = fields.get('scheduling', '')
    notes = fields.get('notes', '')

    instructions_parts = []
    if scheduling:
        instructions_parts.append(f"Scheduling: {scheduling}")
    if payment:
        instructions_parts.append(f"Payment: {payment}")
    if notes:
        instructions_parts.append(f"Notes: {notes}")
    agent_instructions = '\n'.join(instructions_parts)

    return {
        'name': name,
        'short_name': short_name,
        'city': city,
        'state': state,
        'address': address,
        'phone': phone,
        'hours': hours,
        'scheduling_method': detect_scheduling_method(body_text),
        'payment_notes': payment,
        'agent_instructions': agent_instructions,
        'network': detect_network(name),
        'is_active': True,
    }


class Command(BaseCommand):
    help = 'Load clinic directory from data/clinics.md into the database.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Delete all existing Clinic rows first.')
        parser.add_argument('--file', default='data/clinics.md', help='Path to the markdown file.')

    def handle(self, *args, **opts):
        path = Path(settings.BASE_DIR) / opts['file']
        if not path.exists():
            self.stderr.write(f"File not found: {path}")
            return

        if opts['wipe']:
            count = Clinic.objects.all().count()
            Clinic.objects.all().delete()
            self.stdout.write(f"Deleted {count} existing clinic(s).")

        text = path.read_text(encoding='utf-8')
        lines = text.splitlines()

        current_city = ''
        current_state = ''
        current_clinic_name = None
        current_clinic_body: list[str] = []

        created = 0
        skipped = 0

        def flush():
            nonlocal created, skipped, current_clinic_name, current_clinic_body
            if not current_clinic_name:
                return
            data = parse_clinic_block(current_clinic_name, current_clinic_body, current_city, current_state)
            if data is None or not data.get('name') or not data.get('city'):
                skipped += 1
            else:
                Clinic.objects.update_or_create(
                    name=data['name'],
                    city=data['city'],
                    state=data['state'],
                    defaults=data,
                )
                created += 1
            current_clinic_name = None
            current_clinic_body = []

        for line in lines:
            city_match = CITY_HEADER.match(line)
            if city_match:
                flush()
                current_city = city_match.group(1).strip()
                current_state = city_match.group(2).strip()
                continue

            clinic_match = CLINIC_HEADER.match(line)
            if clinic_match:
                flush()
                current_clinic_name = clinic_match.group(1).strip()
                current_clinic_body = []
                continue

            if current_clinic_name is not None:
                # Stop the current block when we hit another section heading
                if line.startswith('## ') or line.startswith('---'):
                    flush()
                    continue
                current_clinic_body.append(line)

        flush()

        self.stdout.write(self.style.SUCCESS(f"Loaded {created} clinic(s). Skipped {skipped}."))
