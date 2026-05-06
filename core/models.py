from django.db import models

class Firefighter(models.Model):
    name = models.CharField(max_length=100)
    dob = models.CharField(max_length=50, blank=True) # string for flexibility like 'Jan 1, 1990'
    address = models.CharField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=20, blank=True) # For SMS notifications
    elevenlabs_id = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.name

class IntakeForm(models.Model):
    firefighter = models.ForeignKey(Firefighter, on_delete=models.CASCADE, related_name='intake_forms')
    search_query = models.CharField(max_length=150, default="DOT medical exams")
    available_days = models.CharField(max_length=255, help_text="E.g., Monday, Wednesday, Friday")
    time_start = models.TimeField()
    time_end = models.TimeField()
    status = models.CharField(max_length=50, default='pending') # pending, searching, calling, booked
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Form for {self.firefighter.name} - {self.status}"

class DiscoveredClinic(models.Model):
    intake_form = models.ForeignKey(IntakeForm, on_delete=models.CASCADE, related_name='clinics')
    name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=50)
    address = models.CharField(max_length=255, blank=True)
    call_status = models.CharField(max_length=50, default='pending') # pending, calling, no_answer, success, failed

    def __str__(self):
        return f"{self.name} - {self.call_status}"

class Appointment(models.Model):
    firefighter = models.ForeignKey(Firefighter, on_delete=models.CASCADE)
    clinic = models.ForeignKey(DiscoveredClinic, on_delete=models.SET_NULL, null=True, blank=True)
    confirmed_time = models.CharField(max_length=150) # string to accept natural AI text like 'Oct 25 at 3:00 PM'
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Appt for {self.firefighter.name} at {self.confirmed_time}"
