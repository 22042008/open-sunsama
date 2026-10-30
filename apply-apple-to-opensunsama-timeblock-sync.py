#!/usr/bin/env python3
"""Guarded patch: apply Apple/CalDAV edits and deletions to mapped Open Sunsama time blocks.

Run from the Open Sunsama repository root. All anchors are validated in memory
before any source file is written. No database migration is required.
"""
from pathlib import Path
import re
import sys

path = Path('apps/api/src/services/calendar-sync.ts')
if not path.is_file():
    print(f'STOP: run from repository root; file not found: {path}')
    sys.exit(1)

original = path.read_text()
source = original

# Add the timezone formatter import if it is not already present.
if "from 'date-fns-tz'" not in source and 'from "date-fns-tz"' not in source:
    source = "import { formatInTimeZone } from 'date-fns-tz';\n" + source
elif 'formatInTimeZone' not in source:
    print('STOP: date-fns-tz is already imported under another shape; inspect imports first.')
    sys.exit(1)

# Ensure `users` is available from the database package.
pattern = re.compile(r"import\s*\{(?P<names>[\s\S]*?)\}\s*from\s*['\"]@open-sunsama/database['\"];")
matches = list(pattern.finditer(source))
if len(matches) != 1:
    print(f'STOP: expected exactly one @open-sunsama/database named import, found {len(matches)}.')
    print('No files have been modified.')
    sys.exit(1)
match = matches[0]
names = match.group('names')
if not re.search(r'\busers\b', names):
    replacement = 'import {' + names.rstrip() + '\n  users,\n} from \'@open-sunsama/database\';'
    source = source[:match.start()] + replacement + source[match.end():]

# Publish realtime changes to connected clients.
if 'publishEvent' not in source:
    source = "import { publishEvent } from '../lib/websocket/index.js';\n" + source
elif "from '../lib/websocket/index.js'" not in source and 'from "../lib/websocket/index.js"' not in source:
    print('STOP: publishEvent is referenced/imported in an unexpected way; inspect imports first.')
    sys.exit(1)

old_snapshot = '''    const localRows = await db
      .select({ externalId: calendarEvents.externalId })
      .from(calendarEvents)
      .where(eq(calendarEvents.calendarId, calendar.id));

    let deleted = result.deleted;
    if (localRows.length > 0) {
      const snapshot = await listCalDavEventIds(credentials, calendar.externalId);
      if (snapshot.complete) {
        const remoteIds = new Set(snapshot.ids);
        const inferred = localRows
          .map((row) => row.externalId)
          .filter((externalId) => !remoteIds.has(externalId));
        deleted = [...new Set([...deleted, ...inferred])];
      } else {
        console.warn(
          `[Calendar Sync] Skipping deletion reconciliation for calendar ${calendar.id}: CalDAV snapshot was incomplete`
        );
      }
    }'''
new_snapshot = '''    const localRows = await db
      .select({ externalId: calendarEvents.externalId })
      .from(calendarEvents)
      .where(eq(calendarEvents.calendarId, calendar.id));

    // Include exported local time blocks in the full-UID snapshot too. Without
    // this, Apple Calendar deletions are detected for calendar_events only.
    const localTimeBlockRows = await db
      .select({ externalId: timeBlocks.caldavEventUid })
      .from(timeBlocks)
      .where(and(
        eq(timeBlocks.userId, userId),
        eq(timeBlocks.caldavCalendarId, calendar.id)
      ));

    let deleted = result.deleted;
    if (localRows.length > 0 || localTimeBlockRows.length > 0) {
      const snapshot = await listCalDavEventIds(credentials, calendar.externalId);
      if (snapshot.complete) {
        const remoteIds = new Set(snapshot.ids);
        const inferredCalendarEvents = localRows
          .map((row) => row.externalId)
          .filter((externalId) => !remoteIds.has(externalId));
        const inferredTimeBlocks = localTimeBlockRows
          .map((row) => row.externalId)
          .filter((externalId): externalId is string =>
            !!externalId && !remoteIds.has(externalId)
          );
        deleted = [...new Set([
          ...deleted,
          ...inferredCalendarEvents,
          ...inferredTimeBlocks,
        ])];
      } else {
        console.warn(
          `[Calendar Sync] Skipping deletion reconciliation for calendar ${calendar.id}: CalDAV snapshot was incomplete`
        );
      }
    }'''
if source.count(old_snapshot) != 1:
    print(f'STOP: expected exactly one full CalDAV deletion-snapshot block, found {source.count(old_snapshot)}.')
    print('No files have been modified.')
    sys.exit(1)
source = source.replace(old_snapshot, new_snapshot, 1)

old_delete = '''    await db
      .delete(calendarEvents)
      .where(
        and(
          eq(calendarEvents.userId, userId),
          eq(calendarEvents.calendarId, slice.calendarId),
          inArray(calendarEvents.externalId, slice.deleted)
        )
      );'''
new_delete = '''    await db
      .delete(calendarEvents)
      .where(
        and(
          eq(calendarEvents.userId, userId),
          eq(calendarEvents.calendarId, slice.calendarId),
          inArray(calendarEvents.externalId, slice.deleted)
        )
      );

    // A removed CalDAV UID that belongs to a local time block means the user
    // deleted that event in Apple Calendar. Remove only the matching mapped
    // block; do not queue another CalDAV deletion or export it again.
    const removedTimeBlocks = await db
      .select({ id: timeBlocks.id, date: timeBlocks.date })
      .from(timeBlocks)
      .where(and(
        eq(timeBlocks.userId, userId),
        eq(timeBlocks.caldavCalendarId, slice.calendarId),
        inArray(timeBlocks.caldavEventUid, slice.deleted)
      ));

    for (const block of removedTimeBlocks) {
      await db
        .delete(timeBlocks)
        .where(and(
          eq(timeBlocks.id, block.id),
          eq(timeBlocks.userId, userId)
        ));
      publishEvent(userId, 'timeblock:deleted', {
        timeBlockId: block.id,
        date: block.date,
      });
      console.log(`[Calendar Sync] Removed local time block ${block.id}: CalDAV event was deleted`);
    }'''
if source.count(old_delete) != 1:
    print(f'STOP: expected exactly one deleteRemovedEvents calendarEvents delete block, found {source.count(old_delete)}.')
    print('No files have been modified.')
    sys.exit(1)
source = source.replace(old_delete, new_delete, 1)

old_skip = '''      // Open Sunsama time blocks are exported to CalDAV, but remain
      // owned by time_blocks. Never import them as calendar_events.
      const [exportedTimeBlock] = await db
        .select({ id: timeBlocks.id })
        .from(timeBlocks)
        .where(
          and(
            eq(timeBlocks.userId, userId),
            eq(timeBlocks.caldavEventUid, event.externalId)
          )
        )
        .limit(1);

      if (exportedTimeBlock) continue;'''
new_skip = '''      // A CalDAV event with a mapped local UID belongs to time_blocks. Apply
      // Apple-originated edits to that row rather than importing a duplicate.
      const [exportedTimeBlock] = await db
        .select({
          id: timeBlocks.id,
          title: timeBlocks.title,
          description: timeBlocks.description,
          date: timeBlocks.date,
          startTime: timeBlocks.startTime,
          endTime: timeBlocks.endTime,
          durationMins: timeBlocks.durationMins,
        })
        .from(timeBlocks)
        .where(and(
          eq(timeBlocks.userId, userId),
          eq(timeBlocks.caldavCalendarId, slice.calendarId),
          eq(timeBlocks.caldavEventUid, event.externalId)
        ))
        .limit(1);

      if (exportedTimeBlock) {
        // Local time blocks are timed, same-day records. Do not silently
        // corrupt one if Apple changes it to an all-day or cross-midnight event.
        if (event.isAllDay) {
          console.warn(`[Calendar Sync] Skipping all-day edit for mapped time block ${exportedTimeBlock.id}`);
          continue;
        }

        const [user] = await db
          .select({ timezone: users.timezone })
          .from(users)
          .where(eq(users.id, userId))
          .limit(1);
        const timezone = user?.timezone || event.timezone || 'UTC';

        try {
          const startDate = formatInTimeZone(event.startTime, timezone, 'yyyy-MM-dd');
          const endDate = formatInTimeZone(event.endTime, timezone, 'yyyy-MM-dd');
          if (startDate !== endDate) {
            console.warn(`[Calendar Sync] Skipping cross-midnight edit for mapped time block ${exportedTimeBlock.id}`);
            continue;
          }

          const startTime = formatInTimeZone(event.startTime, timezone, 'HH:mm');
          const endTime = formatInTimeZone(event.endTime, timezone, 'HH:mm');
          const durationMins = Math.round(
            (event.endTime.getTime() - event.startTime.getTime()) / 60000
          );
          if (durationMins <= 0 || endTime <= startTime) {
            console.warn(`[Calendar Sync] Skipping invalid time range for mapped time block ${exportedTimeBlock.id}`);
            continue;
          }

          const changed =
            exportedTimeBlock.title !== event.title ||
            exportedTimeBlock.description !== event.description ||
            exportedTimeBlock.date !== startDate ||
            exportedTimeBlock.startTime !== startTime ||
            exportedTimeBlock.endTime !== endTime ||
            exportedTimeBlock.durationMins !== durationMins;
          const syncedAt = new Date();
          await db
            .update(timeBlocks)
            .set({
              title: event.title.slice(0, 255),
              description: event.description?.slice(0, 1000) ?? null,
              date: startDate,
              startTime,
              endTime,
              durationMins,
              caldavEventUrl: event.htmlLink,
              caldavEventEtag: event.etag,
              caldavSyncedAt: syncedAt,
              caldavSyncError: null,
              caldavDeletePending: false,
              updatedAt: syncedAt,
            })
            .where(and(
              eq(timeBlocks.id, exportedTimeBlock.id),
              eq(timeBlocks.userId, userId)
            ));

          if (changed) {
            publishEvent(userId, 'timeblock:updated', {
              timeBlockId: exportedTimeBlock.id,
              date: startDate,
            });
            console.log(`[Calendar Sync] Applied Apple edit to local time block ${exportedTimeBlock.id}`);
          }
        } catch (error) {
          console.error(
            `[Calendar Sync] Could not apply Apple edit to time block ${exportedTimeBlock.id}:`,
            error
          );
        }
        continue;
      }'''
if source.count(old_skip) != 1:
    print(f'STOP: expected exactly one exported-time-block skip block, found {source.count(old_skip)}.')
    print('No files have been modified.')
    sys.exit(1)
source = source.replace(old_skip, new_skip, 1)

if source == original:
    print('STOP: no changes were generated.')
    sys.exit(1)

path.write_text(source)
print(f'Updated {path}')
print('Added Apple -> Open Sunsama time-block edit handling.')
print('Added Apple -> Open Sunsama time-block deletion handling.')
print('No schema or migration changes were made.')
print('Next: review git diff, run API typecheck, then test with a disposable time block.')
