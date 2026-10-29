#!/usr/bin/env python3
"""Guarded patch: resolve a missing/stale CalDAV HREF by matching the event UID.

Run from the Open Sunsama repository root. It changes only
apps/api/src/services/calendar-providers/icloud.ts and aborts if expected
source anchors are not present exactly once.
"""
from pathlib import Path
import sys

path = Path("apps/api/src/services/calendar-providers/icloud.ts")
if not path.is_file():
    print(f"STOP: run from repository root; file not found: {path}")
    sys.exit(1)

source = path.read_text()
old_signature = """  async deleteEvent(
    accessToken: string,
    _calendarId: string,
    _eventExternalId: string,
    extra?: { eventUrl?: string | null; etag?: string | null }
  ): Promise<void> {
    const eventUrl = extra?.eventUrl;
    if (!eventUrl) {
      throw new ProviderEventNotFoundError('icloud');
    }
    const credentials =
      this.credentials || (JSON.parse(accessToken) as CalDavCredentials);
    const client = await getCalDavClient(credentials);
    try {
      const response = (await client.deleteCalendarObject({
        calendarObject: {
          url: eventUrl,
          etag: extra.etag ?? '',
        },
      })) as unknown as Response;"""
new_signature = """  async deleteEvent(
    accessToken: string,
    calendarId: string,
    eventExternalId: string,
    extra?: { eventUrl?: string | null; etag?: string | null }
  ): Promise<void> {
    const credentials =
      this.credentials || (JSON.parse(accessToken) as CalDavCredentials);
    const client = await getCalDavClient(credentials);
    try {
      // Resolve the live CalDAV resource by UID first. Older local rows can
      // have a missing/stale htmlLink, but that must not make us assume the
      // remote event is already deleted. The lookup is unbounded by date so
      // events moved outside the normal sync window can still be found.
      const objects = await client.fetchCalendarObjects({
        calendar: { url: calendarId },
      });
      const match = objects
        .map((obj) => ({ obj, parsed: parseCalDavEventWithRaw(obj) }))
        .find(({ parsed }) => parsed?.event.externalId === eventExternalId);

      const eventUrl = match?.obj.url ?? match?.parsed?.event.htmlLink ?? extra?.eventUrl ?? null;
      if (!eventUrl) {
        throw new ProviderEventNotFoundError('icloud');
      }
      const eventEtag = match?.obj.etag ?? match?.parsed?.event.etag ?? extra?.etag ?? '';

      const response = (await client.deleteCalendarObject({
        calendarObject: {
          url: eventUrl,
          etag: eventEtag,
        },
      })) as unknown as Response;"""

count = source.count(old_signature)
if count != 1:
    print(f"STOP: expected exactly one matching deleteEvent implementation, found {count}.")
    print("No files have been modified. Inspect icloud.ts and adapt the patch to your version.")
    sys.exit(1)

path.write_text(source.replace(old_signature, new_signature, 1))
print(f"Updated {path}")
print("Next: inspect git diff, run API typecheck, and test deletion with a disposable event.")
