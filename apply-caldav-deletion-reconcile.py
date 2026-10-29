#!/usr/bin/env python3
"""Guarded patch: detect Apple/Radicale deletions by reconciling a full CalDAV UID snapshot.

Run from the Open Sunsama repository root. Updates only:
  - apps/api/src/services/calendar-providers/icloud.ts
  - apps/api/src/services/calendar-sync.ts

The snapshot is marked incomplete if any resource cannot be inspected safely; in that
case no inferred deletions are returned. Normal event fetching remains date-windowed.
"""
from pathlib import Path
import sys

changes = {
    "apps/api/src/services/calendar-providers/icloud.ts": [
        (
            "  buildVCalendar,\n  parseCalDavEvent,\n  parseCalDavEventWithRaw,\n} from './icloud-helpers';",
            "  buildVCalendar,\n  parseCalDavEvent,\n  parseCalDavEventWithRaw,\n  parseVCalendar,\n} from './icloud-helpers';",
        ),
        (
            "/**\n * ICloudCalendarProvider implements the CalendarProvider interface\n",
            '''/**
 * List all event UIDs in a calendar, without a time-range restriction.
 * This is used only for deletion reconciliation; the normal event fetch remains
 * date-windowed. A resource without a readable VEVENT UID makes the snapshot
 * incomplete, so callers must not infer deletions from that snapshot.
 */
export async function listCalDavEventIds(
  credentials: CalDavCredentials,
  calendarUrl: string
): Promise<{ ids: string[]; complete: boolean }> {
  const client = new DAVClient({
    serverUrl: credentials.serverUrl || ICLOUD_CALDAV_URL,
    credentials: {
      username: credentials.username,
      password: credentials.password,
    },
    authMethod: 'Basic',
    defaultAccountType: 'caldav',
  });

  await client.login();
  const objects = await client.fetchCalendarObjects({ calendar: { url: calendarUrl } });
  const ids = new Set<string>();
  let complete = true;

  for (const obj of objects) {
    // `data` is the raw VCALENDAR resource returned by tsdav. Use the raw
    // parser so an event omitted by the richer date/time parser still counts
    // as present and cannot be incorrectly deleted from the local database.
    if (typeof obj.data !== 'string') {
      complete = false;
      continue;
    }
    const raw = parseVCalendar(obj.data);
    if (!raw?.uid) {
      complete = false;
      continue;
    }
    ids.add(raw.uid);
  }

  return { ids: [...ids], complete };
}

/**
 * ICloudCalendarProvider implements the CalendarProvider interface
''',
        ),
    ],
    "apps/api/src/services/calendar-sync.ts": [
        (
            "  ICloudCalendarProvider,\n  listCalDavEvents,\n} from './calendar-providers/icloud.js';",
            "  ICloudCalendarProvider,\n  listCalDavEvents,\n  listCalDavEventIds,\n} from './calendar-providers/icloud.js';",
        ),
        (
            "  const perCalendar: PerCalendarSyncResult[] = [];\n  let totalEvents = 0;\n  let totalDeleted = 0;\n\n  for (const calendar of accountCalendars) {\n    const result = await listCalDavEvents(\n      credentials,\n      calendar.externalId,\n      syncOptions\n    );\n    perCalendar.push({\n      calendarId: calendar.id,\n      events: result.events,\n      deleted: result.deleted,\n    });\n    totalEvents += result.events.length;\n    totalDeleted += result.deleted.length;\n  }",
            "  const db = getDb();\n  const perCalendar: PerCalendarSyncResult[] = [];\n  let totalEvents = 0;\n  let totalDeleted = 0;\n\n  for (const calendar of accountCalendars) {\n    const result = await listCalDavEvents(\n      credentials,\n      calendar.externalId,\n      syncOptions\n    );\n\n    // Compare known local IDs with a complete remote snapshot. This detects\n    // deletions regardless of the rolling event time window, while avoiding\n    // destructive inference if even one remote resource cannot be inspected.\n    const localRows = await db\n      .select({ externalId: calendarEvents.externalId })\n      .from(calendarEvents)\n      .where(eq(calendarEvents.calendarId, calendar.id));\n\n    let deleted = result.deleted;\n    if (localRows.length > 0) {\n      const snapshot = await listCalDavEventIds(credentials, calendar.externalId);\n      if (snapshot.complete) {\n        const remoteIds = new Set(snapshot.ids);\n        const inferred = localRows\n          .map((row) => row.externalId)\n          .filter((externalId) => !remoteIds.has(externalId));\n        deleted = [...new Set([...deleted, ...inferred])];\n      } else {\n        console.warn(\n          `[Calendar Sync] Skipping deletion reconciliation for calendar ${calendar.id}: CalDAV snapshot was incomplete`\n        );\n      }\n    }\n\n    perCalendar.push({\n      calendarId: calendar.id,\n      events: result.events,\n      deleted,\n    });\n    totalEvents += result.events.length;\n    totalDeleted += deleted.length;\n  }",
        ),
    ],
}

# Validate every anchor across every file before writing anything.
updated_files = {}
for filename, replacements in changes.items():
    path = Path(filename)
    if not path.is_file():
        print(f"STOP: run from repository root; file not found: {path}")
        sys.exit(1)
    source = path.read_text()
    updated = source
    for old, new in replacements:
        count = updated.count(old)
        if count != 1:
            print(f"STOP: expected exactly one matching section in {filename}, found {count}.")
            print("No files have been modified by this script.")
            sys.exit(1)
        updated = updated.replace(old, new, 1)
    updated_files[filename] = (path, updated)

for filename, (path, updated) in updated_files.items():
    path.write_text(updated)
    print(f"Updated {filename}")

print("Guarded deletion-reconciliation patch applied.")
print("Next: review git diff, run API typecheck, then test with a disposable event.")
