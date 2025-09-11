#!/usr/bin/env python
"""
Production Fix Script for Duplicate Slug Migration Error
This script fixes the duplicate slug issue in production.
"""

import os
import sys
import django
from django.core.management import execute_from_command_line

def setup_django():
    """Setup Django environment"""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'deprepagos.settings')
    django.setup()

def check_migration_status():
    """Check current migration status"""
    print("🔍 Checking migration status...")
    from django.core.management import call_command
    from io import StringIO
    
    # Capture migration status
    out = StringIO()
    call_command('showmigrations', 'events', stdout=out)
    migration_status = out.getvalue()
    
    print("Current migration status:")
    print(migration_status)
    
    # Check if migration 0014 is applied but 0015 failed
    if '[X] 0014_add_slug_and_main_event_fields' in migration_status and \
       '[ ] 0015_populate_slugs_and_set_main_event' in migration_status:
        print("⚠️  Migration 0014 applied but 0015 failed due to duplicate slugs")
        return 'failed_0015'
    elif '[X] 0014_add_slug_and_main_event_fields' in migration_status and \
         '[X] 0015_populate_slugs_and_set_main_event' in migration_status:
        print("✅ Migrations 0014 and 0015 already applied")
        return 'completed'
    else:
        print("❌ Migration 0014 not applied")
        return 'not_started'

def fix_duplicate_slugs_manually():
    """Fix duplicate slugs manually"""
    print("🔧 Fixing duplicate slugs manually...")
    
    try:
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
                print(f"✅ Set slug for '{event.name}': {slug}")
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
                    print(f"✅ Fixed duplicate slug for '{event.name}': {old_slug} → {slug}")
                else:
                    used_slugs.add(event.slug)
                    print(f"✅ Slug already set for '{event.name}': {event.slug}")
        
        return True
        
    except Exception as e:
        print(f"❌ Manual slug fix failed: {e}")
        return False

def set_main_event():
    """Set the main event"""
    print("🎯 Setting main event...")
    
    try:
        from events.models import Event
        
        # Check if main event is already set
        main_event = Event.objects.filter(is_main=True).first()
        if main_event:
            print(f"✅ Main event already set: {main_event.name}")
            return True
        
        # Set the first active event as main
        main_event = Event.objects.filter(active=True).first()
        if main_event:
            main_event.is_main = True
            main_event.save()
            print(f"✅ Set main event: {main_event.name}")
            return True
        else:
            print("❌ No active events found")
            return False
        
    except Exception as e:
        print(f"❌ Setting main event failed: {e}")
        return False

def mark_migration_as_applied():
    """Mark migration 0015 as applied"""
    print("📝 Marking migration 0015 as applied...")
    
    try:
        from django.db import connection
        
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO django_migrations (app, name, applied)
                VALUES ('events', '0015_populate_slugs_and_set_main_event', NOW())
                ON CONFLICT (app, name) DO NOTHING
            """)
        
        print("✅ Migration 0015 marked as applied")
        return True
        
    except Exception as e:
        print(f"❌ Failed to mark migration as applied: {e}")
        return False

def run_remaining_migrations():
    """Run any remaining migrations"""
    print("🚀 Running remaining migrations...")
    
    try:
        from django.core.management import call_command
        call_command('migrate', 'events', verbosity=2)
        print("✅ Remaining migrations completed")
        return True
    except Exception as e:
        print(f"❌ Remaining migrations failed: {e}")
        return False

def verify_fix():
    """Verify the fix worked"""
    print("🔍 Verifying fix...")
    
    try:
        from events.models import Event
        
        # Check that all events have slugs
        events_without_slugs = Event.objects.filter(slug__isnull=True).count()
        if events_without_slugs > 0:
            print(f"❌ {events_without_slugs} events without slugs")
            return False
        
        # Check that only one event is main
        main_events_count = Event.objects.filter(is_main=True).count()
        if main_events_count != 1:
            print(f"❌ Expected 1 main event, found {main_events_count}")
            return False
        
        # Get main event info
        main_event = Event.get_main_event()
        if main_event:
            print(f"✅ Main event: {main_event.name} (slug: {main_event.slug})")
        else:
            print("❌ No main event found")
            return False
        
        # Check for duplicate slugs
        from django.db.models import Count
        duplicate_slugs = Event.objects.values('slug').annotate(count=Count('slug')).filter(count__gt=1)
        if duplicate_slugs.exists():
            print(f"❌ Found duplicate slugs: {list(duplicate_slugs)}")
            return False
        
        print("✅ All verifications passed")
        return True
        
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return False

def main():
    """Main fix function"""
    print("🔧 Production Migration Fix - Duplicate Slug Issue")
    print("=" * 60)
    
    # Setup Django
    setup_django()
    
    # Check migration status
    status = check_migration_status()
    
    if status == 'completed':
        print("✅ Migrations already completed successfully")
        if verify_fix():
            print("🎉 System is working correctly!")
            return 0
        else:
            print("❌ System has issues, please check manually")
            return 1
    
    elif status == 'failed_0015':
        print("🔧 Fixing failed migration 0015...")
        
        # Fix duplicate slugs manually
        if not fix_duplicate_slugs_manually():
            print("❌ Failed to fix duplicate slugs")
            return 1
        
        # Set main event
        if not set_main_event():
            print("❌ Failed to set main event")
            return 1
        
        # Mark migration as applied
        if not mark_migration_as_applied():
            print("❌ Failed to mark migration as applied")
            return 1
        
        # Run remaining migrations
        if not run_remaining_migrations():
            print("❌ Failed to run remaining migrations")
            return 1
        
        # Verify fix
        if not verify_fix():
            print("❌ Fix verification failed")
            return 1
        
        print("🎉 Migration fix completed successfully!")
        return 0
    
    else:
        print("❌ Migration 0014 not applied. Please run migrations normally first.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
