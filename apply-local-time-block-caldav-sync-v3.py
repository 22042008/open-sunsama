#!/usr/bin/env python3
"""Guarded source patch for exporting Open Sunsama local time blocks to CalDAV.

Run from the repository root. Every source anchor is validated before any existing
file is written. Then run the database migration generator and type-check.
"""
from pathlib import Path
import sys

ROOT = Path.cwd()

replacements = {
    "packages/database/src/schema/time-blocks.ts": [
        (
            "import { pgTable, uuid, varchar, timestamp, integer, date, index } from 'drizzle-orm/pg-core';",
            "import { pgTable, uuid, varchar, timestamp, integer, date, index, text } from 'drizzle-orm/pg-core';",
        ),
        (
            "import { tasks } from './tasks';",
            "import { tasks } from './tasks';\nimport { calendars } from './calendars';",
        ),
        (
            "    position: integer('position').notNull().default(0),\n    createdAt: timestamp('created_at').defaultNow().notNull(),",
            "    position: integer('position').notNull().default(0),\n"
            "    // Remote CalDAV mirror for this local time block. The UID is deterministic\n"
            "    // from the time-block id; the URL is the CalDAV resource HREF.\n"
            "    caldavCalendarId: uuid('caldav_calendar_id').references(() => calendars.id, { onDelete: 'set null' }),\n"
            "    caldavEventUrl: varchar('caldav_event_url', { length: 1000 }),\n"
            "    caldavSyncedAt: timestamp('caldav_synced_at'),\n"
            "    caldavSyncError: text('caldav_sync_error'),\n"
            "    createdAt: timestamp('created_at').defaultNow().notNull(),",
        ),
        (
            "    taskIdIdx: index('time_blocks_task_id_idx').on(table.taskId),",
            "    taskIdIdx: index('time_blocks_task_id_idx').on(table.taskId),\n"
            "    caldavCalendarIdx: index('time_blocks_caldav_calendar_idx').on(table.caldavCalendarId),",
        ),
    ],
    "apps/api/src/services/calendar-providers/index.ts": [
        (
            "export interface EventPatch {\n  title?: string;",
            "export interface EventPatch {\n  /** Optional stable UID for idempotent CalDAV creation (ignored by OAuth providers). */\n  uid?: string;\n  title?: string;",
        ),
    ],
    "apps/api/src/services/calendar-providers/icloud.ts": [
        (
            "    const uid = `${randomUUID()}@open-sunsama`;",
            "    const uid = payload.uid ?? `${randomUUID()}@open-sunsama`;",
        ),
    ],
    "apps/api/src/routes/time-blocks.ts": [
        (
            "import { publishEvent } from '../lib/websocket/index.js';",
            "import { publishEvent } from '../lib/websocket/index.js';\n"
            "import { syncTimeBlockToCalDav, deleteTimeBlockCalDavEvent } from '../services/time-block-caldav-sync.js';",
        ),
        (
            "  }).returning();\n\n  let task = null;\n  if (newTimeBlock?.taskId) {",
            "  }).returning();\n\n  if (newTimeBlock) await syncTimeBlockToCalDav(userId, newTimeBlock.id);\n\n  let task = null;\n  if (newTimeBlock?.taskId) {",
        ),
        (
            "  }).returning();\n\n  // Publish realtime event (fire and forget)\n  if (newTimeBlock) {",
            "  }).returning();\n\n  if (newTimeBlock) await syncTimeBlockToCalDav(userId, newTimeBlock.id);\n\n  // Publish realtime event (fire and forget)\n  if (newTimeBlock) {",
        ),
        (
            "  }).returning();\n\n  // Publish realtime event (fire and forget)\n  if (updatedTimeBlock) {",
            "  }).returning();\n\n  if (updatedTimeBlock) await syncTimeBlockToCalDav(userId, updatedTimeBlock.id);\n\n  // Publish realtime event (fire and forget)\n  if (updatedTimeBlock) {",
        ),
        (
            "  // Fetch tasks for all updated blocks\n  const updatedBlocksWithTasks = await Promise.all(",
            "  // Mirror every changed block after the cascade transaction has committed.\n"
            "  for (const block of updatedBlocks) await syncTimeBlockToCalDav(userId, block.id);\n\n"
            "  // Fetch tasks for all updated blocks\n  const updatedBlocksWithTasks = await Promise.all(",
        ),
        (
            "  await db.delete(timeBlocks).where(and(eq(timeBlocks.id, id), eq(timeBlocks.userId, userId)));",
            "  // Delete the remote mirror first. If CalDAV fails, preserve the local block\n"
            "  // and its mapping so the user can retry instead of leaving an orphan event.\n"
            "  await deleteTimeBlockCalDavEvent(userId, id);\n"
            "  await db.delete(timeBlocks).where(and(eq(timeBlocks.id, id), eq(timeBlocks.userId, userId)));",
        ),
    ],
    "apps/api/src/services/calendar-sync.ts": [
        (
            "    for (const event of slice.events) {\n      // Look up first in this slice's own calendar (the normal case",
            "    for (const event of slice.events) {\n"
            "      // Local time blocks are mirrored to CalDAV with a dedicated UID namespace.\n"
            "      // They already render from time_blocks, so importing them again as calendar_events\n"
            "      // would display duplicates. Clean up any legacy imported copy too.\n"
            "      if (event.externalId.startsWith('timeblock-') && event.externalId.endsWith('@open-sunsama')) {\n"
            "        await db.delete(calendarEvents).where(and(\n"
            "          eq(calendarEvents.userId, userId),\n"
            "          eq(calendarEvents.externalId, event.externalId)\n"
            "        ));\n"
            "        continue;\n"
            "      }\n"
            "      // Look up first in this slice's own calendar (the normal case",
        ),
    ],
    "apps/api/src/workers/calendar-sync/sync-check.ts": [
        (
            "import { type SyncAccountPayload } from './sync-account.js';",
            "import { type SyncAccountPayload } from './sync-account.js';\n"
            "import { processPendingTimeBlockCalDavSyncs } from '../../services/time-block-caldav-sync.js';",
        ),
        (
            "  const now = new Date();\n  console.log(`[Calendar Sync Check] Tick at ${now.toISOString()}`);\n\n"
            "  // Find active accounts that need syncing. An account qualifies when it is:",
            "  const now = new Date();\n  console.log(`[Calendar Sync Check] Tick at ${now.toISOString()}`);\n\n"
            "  // Retry local blocks whose CalDAV mirror failed or is behind the local update.\n"
            "  await processPendingTimeBlockCalDavSyncs();\n\n"
            "  // Find active accounts that need syncing. An account qualifies when it is:",
        ),
    ],
}

service_source = r'''/**
 * Mirrors local Open Sunsama time blocks to a connected CalDAV calendar.
 *
 * The calendar URL and event UID are persisted/derived so retries update the
 * same remote resource. Set CALENDAR_TIMEZONE to the user's IANA timezone;
 * Europe/London is the default for this self-hosted setup.
 */
import {
  getDb, and, eq, or, isNull, isNotNull, gt, desc,
  timeBlocks, calendars, calendarAccounts,
} from '@open-sunsama/database';
import { getAccessTokenForProvider, getProvider } from './calendar-sync.js';
import { ProviderEventNotFoundError } from './calendar-providers/index.js';

const DEFAULT_TIMEZONE = 'Europe/London';
const TIMEBLOCK_UID_PREFIX = 'timeblock-';
const TIMEBLOCK_UID_SUFFIX = '@open-sunsama';

function timeBlockUid(id: string): string {
  return `${TIMEBLOCK_UID_PREFIX}${id}${TIMEBLOCK_UID_SUFFIX}`;
}

/** Convert a local wall-clock date/time into a UTC instant without an extra tz library. */
function wallTimeToUtc(date: string, time: string, timeZone: string): Date {
  const [year, month, day] = date.split('-').map(Number);
  const [hour, minute] = time.split(':').map(Number);
  if (![year, month, day, hour, minute].every(Number.isFinite)) {
    throw new Error(`Invalid local date/time: ${date} ${time}`);
  }
  const desiredWallAsUtc = Date.UTC(year!, month! - 1, day!, hour!, minute!);
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  let candidate = desiredWallAsUtc;
  for (let i = 0; i < 4; i++) {
    const parts = formatter.formatToParts(new Date(candidate));
    const value = (key: Intl.DateTimeFormatPartTypes) =>
      Number(parts.find((part) => part.type === key)?.value ?? NaN);
    const formattedWallAsUtc = Date.UTC(
      value('year'), value('month') - 1, value('day'),
      value('hour'), value('minute'), value('second'),
    );
    if (!Number.isFinite(formattedWallAsUtc)) throw new Error(`Invalid timezone: ${timeZone}`);
    const offset = formattedWallAsUtc - candidate;
    const next = desiredWallAsUtc - offset;
    if (next === candidate) break;
    candidate = next;
  }
  const check = formatter.formatToParts(new Date(candidate));
  const value = (key: Intl.DateTimeFormatPartTypes) =>
    Number(check.find((part) => part.type === key)?.value ?? NaN);
  if (
    value('year') !== year || value('month') !== month || value('day') !== day ||
    value('hour') !== hour || value('minute') !== minute
  ) {
    throw new Error(`${date} ${time} does not exist in timezone ${timeZone} (DST transition?)`);
  }
  return new Date(candidate);
}

async function chooseCalDavCalendar(userId: string): Promise<string | null> {
  const db = getDb();
  const [candidate] = await db
    .select({ id: calendars.id })
    .from(calendars)
    .innerJoin(calendarAccounts, eq(calendars.accountId, calendarAccounts.id))
    .where(and(
      eq(calendars.userId, userId),
      eq(calendarAccounts.userId, userId),
      eq(calendarAccounts.provider, 'icloud'),
      eq(calendarAccounts.isActive, true),
      eq(calendars.isEnabled, true),
      eq(calendars.isReadOnly, false),
    ))
    .orderBy(desc(calendars.isDefaultForEvents), desc(calendars.updatedAt))
    .limit(1);
  return candidate?.id ?? null;
}

async function getMappedTarget(userId: string, calendarId: string) {
  const db = getDb();
  const [target] = await db
    .select({ calendar: calendars, account: calendarAccounts })
    .from(calendars)
    .innerJoin(calendarAccounts, eq(calendars.accountId, calendarAccounts.id))
    .where(and(
      eq(calendars.id, calendarId),
      eq(calendars.userId, userId),
      eq(calendarAccounts.userId, userId),
      eq(calendarAccounts.provider, 'icloud'),
      eq(calendarAccounts.isActive, true),
    ))
    .limit(1);
  return target ?? null;
}

/** Best-effort immediate export; failures are stored for scheduled retry. */
export async function syncTimeBlockToCalDav(userId: string, timeBlockId: string): Promise<void> {
  const db = getDb();
  const [block] = await db.select().from(timeBlocks).where(and(
    eq(timeBlocks.id, timeBlockId), eq(timeBlocks.userId, userId),
  )).limit(1);
  if (!block) return;

  try {
    let calendarId = block.caldavCalendarId;
    if (!calendarId) {
      calendarId = await chooseCalDavCalendar(userId);
      // No CalDAV calendar is connected; this remains a normal local-only block.
      if (!calendarId) return;
      await db.update(timeBlocks).set({
        caldavCalendarId: calendarId,
        caldavEventUrl: null,
        caldavSyncedAt: null,
        caldavSyncError: null,
      }).where(and(eq(timeBlocks.id, timeBlockId), eq(timeBlocks.userId, userId)));
    }

    const target = await getMappedTarget(userId, calendarId);
    if (!target) throw new Error('The linked CalDAV calendar is no longer active or available');
    const provider = getProvider('icloud');
    if (!provider?.createEvent || !provider.updateEvent) {
      throw new Error('The CalDAV provider does not support event creation and updates');
    }
    const accessToken = await getAccessTokenForProvider(target.account, provider);
    const uid = timeBlockUid(block.id);
    const timezone = process.env.CALENDAR_TIMEZONE || DEFAULT_TIMEZONE;
    const payload = {
      uid,
      title: block.title,
      description: block.description ?? null,
      startTime: wallTimeToUtc(block.date, block.startTime, timezone),
      endTime: wallTimeToUtc(block.date, block.endTime, timezone),
      timezone,
      isAllDay: false,
    };

    let remote;
    if (block.caldavEventUrl) {
      try {
        remote = await provider.updateEvent(accessToken, target.calendar.externalId, uid, {
          ...payload,
          eventUrl: block.caldavEventUrl,
        });
      } catch (error) {
        if (!(error instanceof ProviderEventNotFoundError)) throw error;
        // The iOS user may have removed the event. Recreate it with the stable UID.
        remote = await provider.createEvent(accessToken, target.calendar.externalId, payload);
      }
    } else {
      // Recover safely if the previous create succeeded remotely but timed out before
      // the URL could be saved in Postgres: look for this block's deterministic UID.
      const listed = await provider.listEvents(accessToken, target.calendar.externalId, {});
      const existing = listed.events.find((event) => event.externalId === uid);
      if (existing?.htmlLink) {
        remote = await provider.updateEvent(accessToken, target.calendar.externalId, uid, {
          ...payload,
          eventUrl: existing.htmlLink,
        });
      } else {
        remote = await provider.createEvent(accessToken, target.calendar.externalId, payload);
      }
    }
    if (!remote.htmlLink) throw new Error('CalDAV did not return an event resource URL');
    await db.update(timeBlocks).set({
      caldavCalendarId: target.calendar.id,
      caldavEventUrl: remote.htmlLink,
      caldavSyncedAt: new Date(),
      caldavSyncError: null,
    }).where(and(eq(timeBlocks.id, timeBlockId), eq(timeBlocks.userId, userId)));
    console.log(`[Time Block CalDAV] Synced local block ${timeBlockId}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown CalDAV export error';
    await db.update(timeBlocks).set({ caldavSyncError: message })
      .where(and(eq(timeBlocks.id, timeBlockId), eq(timeBlocks.userId, userId)));
    console.error(`[Time Block CalDAV] Failed to sync local block ${timeBlockId}: ${message}`);
  }
}

/** Delete only the explicitly mapped CalDAV resource, before deleting the local block. */
export async function deleteTimeBlockCalDavEvent(userId: string, timeBlockId: string): Promise<void> {
  const db = getDb();
  const [block] = await db.select().from(timeBlocks).where(and(
    eq(timeBlocks.id, timeBlockId), eq(timeBlocks.userId, userId),
  )).limit(1);
  if (!block?.caldavCalendarId) return;

  const target = await getMappedTarget(userId, block.caldavCalendarId);
  if (!target) throw new Error('Cannot delete the time block because its mapped CalDAV calendar is unavailable');
  const provider = getProvider('icloud');
  if (!provider?.deleteEvent) throw new Error('The CalDAV provider does not support event deletion');
  const accessToken = await getAccessTokenForProvider(target.account, provider);
  const uid = timeBlockUid(block.id);
  let eventUrl = block.caldavEventUrl;
  if (!eventUrl) {
    const listed = await provider.listEvents(accessToken, target.calendar.externalId, {});
    eventUrl = listed.events.find((event) => event.externalId === uid)?.htmlLink ?? null;
  }
  if (!eventUrl) return; // Nothing was created remotely, or it was already deleted.
  try {
    await provider.deleteEvent(accessToken, target.calendar.externalId, uid, { eventUrl });
  } catch (error) {
    // If Apple Calendar already removed the event, the local block should still
    // be deletable. Preserve the local block for all other CalDAV failures.
    if (!(error instanceof ProviderEventNotFoundError)) throw error;
  }
}

/** Retry failed/pending block exports from the five-minute calendar check. */
export async function processPendingTimeBlockCalDavSyncs(): Promise<void> {
  const db = getDb();
  const pending = await db.select({ id: timeBlocks.id, userId: timeBlocks.userId })
    .from(timeBlocks)
    .where(and(
      isNotNull(timeBlocks.caldavCalendarId),
      or(
        isNull(timeBlocks.caldavSyncedAt),
        isNotNull(timeBlocks.caldavSyncError),
        gt(timeBlocks.updatedAt, timeBlocks.caldavSyncedAt),
      ),
    ))
    .limit(25);
  for (const block of pending) {
    await syncTimeBlockToCalDav(block.userId, block.id);
  }
}
'''

# Validate all source files and all anchors first: the script must be all-or-nothing
# for existing source files, not partially edit a checkout with unexpected changes.
loaded = {}
for filename, edits in replacements.items():
    path = ROOT / filename
    if not path.exists():
        print(f"STOP: required file not found: {filename}")
        sys.exit(1)
    original = path.read_text()
    updated = original
    for old, new in edits:
        count = updated.count(old)
        expected = 2 if filename.endswith("apps/api/src/routes/time-blocks.ts") and old.startswith("  }).returning();\n\n  // Publish realtime event (fire and forget)\n  if (newTimeBlock)") else 1
        if count != expected:
            print(f"STOP: {filename}: expected {expected} match(es), found {count} for anchor:\n{old[:180]!r}")
            print("No existing source files have been modified.")
            sys.exit(1)
        updated = updated.replace(old, new, expected)
    loaded[filename] = (path, updated)

new_service_path = ROOT / "apps/api/src/services/time-block-caldav-sync.ts"
if new_service_path.exists():
    print("STOP: apps/api/src/services/time-block-caldav-sync.ts already exists; refusing to overwrite it.")
    print("No existing source files have been modified.")
    sys.exit(1)

for _, (path, updated) in loaded.items():
    path.write_text(updated)
new_service_path.write_text(service_source)
print("Applied guarded source edits:")
for filename in replacements:
    print(f"  {filename}")
print("  apps/api/src/services/time-block-caldav-sync.ts (new)")
print("\nNext: run the database migration generator, then type-check. No migration has been generated by this script.")
