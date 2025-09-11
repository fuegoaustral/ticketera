# Production Fix Guide - Duplicate Slug Migration Error

## 🚨 Problem
The migration `0015_populate_slugs_and_set_main_event` failed in production with:
```
IntegrityError: duplicate key value violates unique constraint "events_event_slug_key"
DETAIL: Key (slug)=(arte-activa-2025) already exists.
```

## 🔍 Root Cause
Multiple events have the same name or similar names that generate identical slugs when using `slugify()`. The migration didn't handle duplicate slugs properly.

## 🛠️ Solution Options

### Option 1: Use the Fix Script (Recommended)
Run the automated fix script that handles the duplicate slug issue:

```bash
# In production environment
python fix_production_migration.py
```

### Option 2: Manual Fix
If the script doesn't work, follow these manual steps:

#### Step 1: Check Current Status
```bash
python manage.py showmigrations events
```

#### Step 2: Fix Duplicate Slugs Manually
```python
# In Django shell
python manage.py shell

from events.models import Event
from django.utils.text import slugify

# Get all events
events = Event.objects.all()
used_slugs = set()

for event in events:
    if not event.slug:
        # Generate base slug
        base_slug = slugify(event.name)
        slug = base_slug
        counter = 1
        
        # Handle duplicates by adding counter
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        event.slug = slug
        used_slugs.add(slug)
        event.save()
        print(f"Set slug for '{event.name}': {slug}")
    else:
        # Check if existing slug is duplicate
        if event.slug in used_slugs:
            # Generate new unique slug
            base_slug = slugify(event.name)
            slug = base_slug
            counter = 1
            
            while slug in used_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            
            old_slug = event.slug
            event.slug = slug
            used_slugs.add(slug)
            event.save()
            print(f"Fixed duplicate slug for '{event.name}': {old_slug} → {slug}")
        else:
            used_slugs.add(event.slug)
```

#### Step 3: Set Main Event
```python
# Set the first active event as main
main_event = Event.objects.filter(active=True).first()
if main_event:
    main_event.is_main = True
    main_event.save()
    print(f"Set main event: {main_event.name}")
```

#### Step 4: Mark Migration as Applied
```sql
-- In database
INSERT INTO django_migrations (app, name, applied)
VALUES ('events', '0015_populate_slugs_and_set_main_event', NOW())
ON CONFLICT (app, name) DO NOTHING;
```

#### Step 5: Run Remaining Migrations
```bash
python manage.py migrate events
```

### Option 3: Rollback and Retry
If you need to start over:

#### Step 1: Rollback to Migration 0013
```bash
python manage.py migrate events 0013
```

#### Step 2: Replace Migration 0015
Replace the content of `events/migrations/0015_populate_slugs_and_set_main_event.py` with the fixed version:

```python
# events/migrations/0015_populate_slugs_and_set_main_event.py
from django.db import migrations
from django.utils.text import slugify

def populate_slugs_and_set_main_event(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    
    # Get all events
    events = Event.objects.all()
    
    if events.exists():
        # Set the first active event as main
        main_event = events.filter(active=True).first()
        if main_event:
            main_event.is_main = True
            main_event.save()
        
        # Populate slugs for all events with duplicate handling
        used_slugs = set()
        for event in events:
            if not event.slug:
                # Generate base slug
                base_slug = slugify(event.name)
                slug = base_slug
                counter = 1
                
                # Handle duplicates by adding counter
                while slug in used_slugs:
                    slug = f"{base_slug}-{counter}"
                    counter += 1
                
                event.slug = slug
                used_slugs.add(slug)
                event.save()

def reverse_populate_slugs_and_set_main_event(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Event.objects.update(slug=None, is_main=False)

class Migration(migrations.Migration):
    dependencies = [
        ('events', '0014_add_slug_and_main_event_fields'),
    ]
    operations = [
        migrations.RunPython(
            populate_slugs_and_set_main_event,
            reverse_populate_slugs_and_set_main_event
        ),
    ]
```

#### Step 3: Run Migrations Again
```bash
python manage.py migrate events
```

## ✅ Verification Steps

After applying the fix, verify everything works:

### 1. Check Migration Status
```bash
python manage.py showmigrations events
```

### 2. Verify Data Integrity
```python
# In Django shell
from events.models import Event

# Check that all events have slugs
events_without_slugs = Event.objects.filter(slug__isnull=True).count()
print(f"Events without slugs: {events_without_slugs}")  # Should be 0

# Check that only one event is main
main_events_count = Event.objects.filter(is_main=True).count()
print(f"Main events: {main_events_count}")  # Should be 1

# Check for duplicate slugs
from django.db.models import Count
duplicate_slugs = Event.objects.values('slug').annotate(count=Count('slug')).filter(count__gt=1)
print(f"Duplicate slugs: {list(duplicate_slugs)}")  # Should be empty

# Get main event info
main_event = Event.get_main_event()
if main_event:
    print(f"Main event: {main_event.name} (slug: {main_event.slug})")
```

### 3. Test Critical Functionality
- [ ] Root URL (/) shows main event
- [ ] Event-specific URLs work (/{slug}/)
- [ ] Checkout flow with event parameters
- [ ] User profile ticket views
- [ ] Events listing page
- [ ] Admin interface

## 🚨 Emergency Rollback

If the fix causes issues:

```bash
# Rollback to before the multi-event system
python manage.py migrate events 0013

# Restore database from backup if needed
psql -h your_host -U your_user -d your_database < backup_before_multi_event.sql
```

## 📋 Files Created for Fix

1. **`fix_production_migration.py`** - Automated fix script
2. **`events/migrations/0016_fix_duplicate_slugs.py`** - Additional migration for fixing duplicates
3. **`events/migrations/0015_populate_slugs_and_set_main_event_fixed.py`** - Fixed version of original migration

## 🎯 Expected Outcome

After the fix:
- All events have unique slugs
- One event is marked as main
- Multiple events can be active
- All URLs work correctly
- No duplicate slug errors

## 📞 Support

If you encounter issues:
1. Check the error logs
2. Verify database state
3. Test the fix script
4. Consider manual rollback if needed
