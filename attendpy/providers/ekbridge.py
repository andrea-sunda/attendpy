"""Native macOS EventKit bridge for iCloud / CalDAV Cloud calendars.

Compiles a lightweight Objective-C helper binary using `/usr/bin/clang` linking
against `Foundation`, `EventKit`, and `AppKit`. This allows `attendpy` (with zero
external Python dependencies) to:
  1. Request macOS Calendar access properly on macOS Sonoma/Sequoia+.
  2. Create calendars directly in iCloud / CalDAV Cloud accounts (never in the
     local 'On My Mac' / 'Sul mio Mac' store).
  3. Automatically migrate and remove any accidental local ('On My Mac') calendars
     with the same name so calendars live exclusively in the Cloud.
  4. Fetch events (including automatically expanded recurring lessons) and move
     events between Attended and Skipped Cloud calendars in real time.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from attendpy.config import get_app_dir

OBJC_SOURCE = r"""
#import <Foundation/Foundation.h>
#import <EventKit/EventKit.h>
#import <AppKit/AppKit.h>

static BOOL request_calendar_access(EKEventStore *store) {
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    __block BOOL granted = NO;

    if ([store respondsToSelector:@selector(requestFullAccessToEventsWithCompletion:)]) {
        [store requestFullAccessToEventsWithCompletion:^(BOOL g, NSError * _Nullable err) {
            granted = g;
            dispatch_semaphore_signal(sem);
        }];
    } else {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        [store requestAccessToEntityType:EKEntityTypeEvent completion:^(BOOL g, NSError * _Nullable err) {
            granted = g;
            dispatch_semaphore_signal(sem);
        }];
#pragma clang diagnostic pop
    }

    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(30.0 * NSEC_PER_SEC)));
    return granted;
}

static BOOL is_cloud_source(EKSource *source) {
    if (!source) return NO;
    EKSourceType t = source.sourceType;
    return (t == EKSourceTypeCalDAV || t == EKSourceTypeMobileMe || t == EKSourceTypeExchange);
}

static void print_json(NSDictionary *dict) {
    NSError *err = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:dict options:0 error:&err];
    if (data) {
        NSString *str = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        printf("%s\n", [str UTF8String]);
    } else {
        printf("{\"ok\":false,\"error\":\"JSON serialization failed\"}\n");
    }
}

static NSArray<EKSource *> *find_writable_cloud_sources(EKEventStore *store, NSString *preferredName) {
    NSMutableArray<EKSource *> *candidates = [NSMutableArray array];
    NSMutableSet<NSString *> *seenIds = [NSMutableSet set];

    // Collect sources from existing writable cloud calendars first (most reliable)
    for (EKCalendar *cal in [store calendarsForEntityType:EKEntityTypeEvent]) {
        if (cal.source && is_cloud_source(cal.source) && ![seenIds containsObject:cal.source.sourceIdentifier]) {
            [seenIds addObject:cal.source.sourceIdentifier];
            [candidates addObject:cal.source];
        }
    }
    // Also inspect store.sources directly
    for (EKSource *src in store.sources) {
        if (is_cloud_source(src) && ![seenIds containsObject:src.sourceIdentifier]) {
            [seenIds addObject:src.sourceIdentifier];
            [candidates addObject:src];
        }
    }

    // Sort so preferredName (or "iCloud") comes first
    [candidates sortUsingComparator:^NSComparisonResult(EKSource *a, EKSource *b) {
        NSString *ta = a.title ?: @"";
        NSString *tb = b.title ?: @"";
        if (preferredName.length > 0) {
            BOOL ma = ([ta caseInsensitiveCompare:preferredName] == NSOrderedSame);
            BOOL mb = ([tb caseInsensitiveCompare:preferredName] == NSOrderedSame);
            if (ma != mb) return ma ? NSOrderedAscending : NSOrderedDescending;
        }
        BOOL ia = ([ta rangeOfString:@"iCloud" options:NSCaseInsensitiveSearch].location != NSNotFound);
        BOOL ib = ([tb rangeOfString:@"iCloud" options:NSCaseInsensitiveSearch].location != NSNotFound);
        if (ia != ib) return ia ? NSOrderedAscending : NSOrderedDescending;
        return [ta localizedCaseInsensitiveCompare:tb];
    }];

    return candidates;
}

static void migrate_and_remove_local_calendars(EKEventStore *store, NSArray<EKCalendar *> *localCals, EKCalendar *cloudCal) {
    if (!cloudCal || localCals.count == 0) return;
    NSDate *start = [NSDate dateWithTimeIntervalSinceNow:-365 * 24 * 3600 * 2];
    NSDate *end = [NSDate dateWithTimeIntervalSinceNow:365 * 24 * 3600 * 2];
    NSPredicate *pred = [store predicateForEventsWithStartDate:start endDate:end calendars:localCals];
    NSArray<EKEvent *> *events = [store eventsMatchingPredicate:pred];
    for (EKEvent *ev in events) {
        ev.calendar = cloudCal;
        [store saveEvent:ev span:EKSpanThisEvent commit:NO error:nil];
    }
    for (EKCalendar *lcal in localCals) {
        [store removeCalendar:lcal commit:NO error:nil];
    }
    [store commit:nil];
}

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        if (argc < 2) {
            print_json(@{@"ok": @NO, @"error": @"Missing command"});
            return 1;
        }
        NSString *cmd = [NSString stringWithUTF8String:argv[1]];
        EKEventStore *store = [[EKEventStore alloc] init];
        if (!request_calendar_access(store)) {
            print_json(@{
                @"ok": @NO,
                @"error": @"Calendar permission not granted. Enable Calendar access for your Terminal in System Settings -> Privacy & Security -> Calendars."
            });
            return 1;
        }

        if ([cmd isEqualToString:@"list-calendars"]) {
            NSMutableArray *calList = [NSMutableArray array];
            NSMutableArray *cloudSources = [NSMutableArray array];
            for (EKSource *src in find_writable_cloud_sources(store, @"")) {
                [cloudSources addObject:(src.title ?: @"Cloud")];
            }
            for (EKCalendar *cal in [store calendarsForEntityType:EKEntityTypeEvent]) {
                [calList addObject:@{
                    @"title": cal.title ?: @"",
                    @"identifier": cal.calendarIdentifier ?: @"",
                    @"source_title": (cal.source && cal.source.title) ? cal.source.title : @"Unknown",
                    @"source_type": @(cal.source ? cal.source.sourceType : -1),
                    @"is_cloud": @(is_cloud_source(cal.source)),
                    @"writable": @(cal.allowsContentModifications)
                }];
            }
            print_json(@{@"ok": @YES, @"calendars": calList, @"cloud_sources": cloudSources});
            return 0;
        }

        if ([cmd isEqualToString:@"ensure-cloud-calendar"]) {
            if (argc < 3) {
                print_json(@{@"ok": @NO, @"error": @"Missing calendar name"});
                return 1;
            }
            NSString *targetName = [NSString stringWithUTF8String:argv[2]];
            NSString *prefSource = (argc >= 4) ? [NSString stringWithUTF8String:argv[3]] : @"";

            EKCalendar *existingCloudCal = nil;
            NSMutableArray<EKCalendar *> *matchingLocalCals = [NSMutableArray array];

            for (EKCalendar *cal in [store calendarsForEntityType:EKEntityTypeEvent]) {
                if (cal.title && [cal.title caseInsensitiveCompare:targetName] == NSOrderedSame) {
                    if (is_cloud_source(cal.source)) {
                        if (!existingCloudCal || (prefSource.length > 0 && [cal.source.title caseInsensitiveCompare:prefSource] == NSOrderedSame)) {
                            existingCloudCal = cal;
                        }
                    } else if (cal.source && cal.source.sourceType == EKSourceTypeLocal) {
                        [matchingLocalCals addObject:cal];
                    }
                }
            }

            if (existingCloudCal) {
                BOOL cleanedLocal = (matchingLocalCals.count > 0);
                if (cleanedLocal) {
                    migrate_and_remove_local_calendars(store, matchingLocalCals, existingCloudCal);
                }
                NSString *srcName = existingCloudCal.source.title ?: @"Cloud";
                NSString *msg = cleanedLocal
                    ? [NSString stringWithFormat:@"Calendar '%@' verified on %@ Cloud (migrated & removed duplicate local 'On My Mac' calendar).", targetName, srcName]
                    : [NSString stringWithFormat:@"Calendar '%@' already exists on %@ Cloud.", targetName, srcName];
                print_json(@{
                    @"ok": @YES,
                    @"status": @"exists_cloud",
                    @"source": srcName,
                    @"message": msg
                });
                return 0;
            }

            NSArray<EKSource *> *cloudSources = find_writable_cloud_sources(store, prefSource);
            if (cloudSources.count == 0) {
                print_json(@{
                    @"ok": @NO,
                    @"error": @"No Cloud account (iCloud / CalDAV) found in macOS Calendar. Sign in to iCloud Calendar in System Settings -> Internet Accounts."
                });
                return 1;
            }

            NSError *lastErr = nil;
            for (EKSource *src in cloudSources) {
                EKCalendar *newCal = [EKCalendar calendarForEntityType:EKEntityTypeEvent eventStore:store];
                newCal.title = targetName;
                newCal.source = src;
                newCal.CGColor = [[NSColor systemBlueColor] CGColor];
                NSError *saveErr = nil;
                if ([store saveCalendar:newCal commit:YES error:&saveErr]) {
                    BOOL cleanedLocal = (matchingLocalCals.count > 0);
                    if (cleanedLocal) {
                        migrate_and_remove_local_calendars(store, matchingLocalCals, newCal);
                    }
                    NSString *srcName = src.title ?: @"Cloud";
                    NSString *msg = cleanedLocal
                        ? [NSString stringWithFormat:@"Created calendar '%@' on %@ Cloud (and removed local 'On My Mac' copy).", targetName, srcName]
                        : [NSString stringWithFormat:@"Created calendar '%@' directly on %@ Cloud.", targetName, srcName];
                    print_json(@{
                        @"ok": @YES,
                        @"status": @"created_cloud",
                        @"source": srcName,
                        @"message": msg
                    });
                    return 0;
                }
                lastErr = saveErr;
            }

            print_json(@{
                @"ok": @NO,
                @"error": lastErr ? [lastErr localizedDescription] : @"Failed to create calendar on Cloud source."
            });
            return 1;
        }

        if ([cmd isEqualToString:@"fetch-events"]) {
            if (argc < 4) {
                print_json(@{@"ok": @NO, @"error": @"Usage: fetch-events <attended_cal> <skipped_cal>"});
                return 1;
            }
            NSString *attName = [NSString stringWithUTF8String:argv[2]];
            NSString *skpName = [NSString stringWithUTF8String:argv[3]];

            NSMutableArray<EKCalendar *> *targetCals = [NSMutableArray array];
            for (EKCalendar *cal in [store calendarsForEntityType:EKEntityTypeEvent]) {
                if (!cal.title) continue;
                BOOL matchAtt = ([cal.title caseInsensitiveCompare:attName] == NSOrderedSame);
                BOOL matchSkp = ([cal.title caseInsensitiveCompare:skpName] == NSOrderedSame);
                if (matchAtt || matchSkp) {
                    [targetCals addObject:cal];
                }
            }

            if (targetCals.count == 0) {
                print_json(@{@"ok": @YES, @"events": @[]});
                return 0;
            }

            // Prefer Cloud calendars if both Cloud and Local exist for the same name
            NSMutableArray<EKCalendar *> *filteredCals = [NSMutableArray array];
            BOOL hasCloudAtt = NO;
            BOOL hasCloudSkp = NO;
            for (EKCalendar *cal in targetCals) {
                if (is_cloud_source(cal.source)) {
                    if ([cal.title caseInsensitiveCompare:attName] == NSOrderedSame) hasCloudAtt = YES;
                    if ([cal.title caseInsensitiveCompare:skpName] == NSOrderedSame) hasCloudSkp = YES;
                }
            }
            for (EKCalendar *cal in targetCals) {
                BOOL matchAtt = ([cal.title caseInsensitiveCompare:attName] == NSOrderedSame);
                BOOL matchSkp = ([cal.title caseInsensitiveCompare:skpName] == NSOrderedSame);
                if (matchAtt && hasCloudAtt && !is_cloud_source(cal.source)) continue;
                if (matchSkp && hasCloudSkp && !is_cloud_source(cal.source)) continue;
                [filteredCals addObject:cal];
            }

            NSDate *start = [NSDate dateWithTimeIntervalSinceNow:-365 * 24 * 3600 * 2];
            NSDate *end = [NSDate dateWithTimeIntervalSinceNow:365 * 24 * 3600 * 1];
            NSPredicate *pred = [store predicateForEventsWithStartDate:start endDate:end calendars:filteredCals];
            NSArray<EKEvent *> *events = [store eventsMatchingPredicate:pred];

            NSISO8601DateFormatter *fmt = [[NSISO8601DateFormatter alloc] init];
            fmt.timeZone = [NSTimeZone localTimeZone];
            fmt.formatOptions = NSISO8601DateFormatWithFullDate | NSISO8601DateFormatWithTime | NSISO8601DateFormatWithColonSeparatorInTime;

            NSMutableArray *outEvents = [NSMutableArray array];
            for (EKEvent *ev in events) {
                if (!ev.title || !ev.startDate || !ev.endDate) continue;
                BOOL isSkipped = ([ev.calendar.title caseInsensitiveCompare:skpName] == NSOrderedSame);
                [outEvents addObject:@{
                    @"raw_uid": ev.calendarItemExternalIdentifier ?: (ev.eventIdentifier ?: @""),
                    @"event_id": ev.eventIdentifier ?: @"",
                    @"title": ev.title ?: @"",
                    @"description": ev.notes ?: @"",
                    @"location": ev.location ?: @"",
                    @"start": [fmt stringFromDate:ev.startDate] ?: @"",
                    @"end": [fmt stringFromDate:ev.endDate] ?: @"",
                    @"calendar": ev.calendar.title ?: @"",
                    @"source": (ev.calendar.source && ev.calendar.source.title) ? ev.calendar.source.title : @"",
                    @"is_cloud": @(is_cloud_source(ev.calendar.source)),
                    @"is_skipped": @(isSkipped)
                }];
            }

            print_json(@{@"ok": @YES, @"events": outEvents});
            return 0;
        }

        if ([cmd isEqualToString:@"move-event"]) {
            if (argc < 5) {
                print_json(@{@"ok": @NO, @"error": @"Usage: move-event <raw_uid> <from_cal> <to_cal>"});
                return 1;
            }
            NSString *rawUid = [NSString stringWithUTF8String:argv[2]];
            NSString *fromName = [NSString stringWithUTF8String:argv[3]];
            NSString *toName = [NSString stringWithUTF8String:argv[4]];

            EKCalendar *srcCal = nil;
            EKCalendar *dstCal = nil;
            for (EKCalendar *cal in [store calendarsForEntityType:EKEntityTypeEvent]) {
                if (!cal.title) continue;
                if ([cal.title caseInsensitiveCompare:fromName] == NSOrderedSame) {
                    if (!srcCal || is_cloud_source(cal.source)) srcCal = cal;
                }
                if ([cal.title caseInsensitiveCompare:toName] == NSOrderedSame) {
                    if (!dstCal || is_cloud_source(cal.source)) dstCal = cal;
                }
            }

            if (!srcCal || !dstCal) {
                print_json(@{@"ok": @NO, @"error": @"Source or destination calendar not found."});
                return 1;
            }

            NSDate *start = [NSDate dateWithTimeIntervalSinceNow:-365 * 24 * 3600 * 2];
            NSDate *end = [NSDate dateWithTimeIntervalSinceNow:365 * 24 * 3600 * 1];
            NSPredicate *pred = [store predicateForEventsWithStartDate:start endDate:end calendars:@[srcCal]];
            NSArray<EKEvent *> *events = [store eventsMatchingPredicate:pred];

            for (EKEvent *ev in events) {
                NSString *extId = ev.calendarItemExternalIdentifier ?: @"";
                NSString *evId = ev.eventIdentifier ?: @"";
                if ([extId isEqualToString:rawUid] || [evId isEqualToString:rawUid]) {
                    ev.calendar = dstCal;
                    NSError *err = nil;
                    if ([store saveEvent:ev span:EKSpanThisEvent commit:YES error:&err]) {
                        print_json(@{
                            @"ok": @YES,
                            @"message": [NSString stringWithFormat:@"Moved '%@' to '%@' on %@ Cloud.", ev.title, toName, dstCal.source.title ?: @"Cloud"]
                        });
                        return 0;
                    } else {
                        print_json(@{@"ok": @NO, @"error": err ? [err localizedDescription] : @"Save failed"});
                        return 1;
                    }
                }
            }

            print_json(@{@"ok": @NO, @"error": @"Event not found in source calendar."});
            return 1;
        }

        print_json(@{@"ok": @NO, @"error": @"Unknown command"});
        return 1;
    }
}
"""

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>io.github.andrea.attendpy.ekbridge</string>
    <key>CFBundleName</key>
    <string>attendpy-ekbridge</string>
    <key>NSCalendarsFullAccessUsageDescription</key>
    <string>attendpy needs access to your Cloud calendars to track university course attendance.</string>
    <key>NSCalendarsUsageDescription</key>
    <string>attendpy needs access to your Cloud calendars to track university course attendance.</string>
</dict>
</plist>
"""


def get_ekbridge_binary() -> Optional[Path]:
    """Return path to compiled EventKit helper binary on macOS, compiling if needed."""
    if sys.platform != "darwin" or not shutil.which("/usr/bin/clang"):
        return None

    digest = hashlib.sha256((OBJC_SOURCE + INFO_PLIST).encode("utf-8")).hexdigest()[:12]
    bin_name = f"attendpy-ekbridge-{digest}"

    candidate_dirs = [
        get_app_dir(ensure_exists=False) / "bin",
        Path(tempfile.gettempdir()) / "attendpy-bin",
    ]

    for bin_dir in candidate_dirs:
        bin_path = bin_dir / bin_name
        if bin_path.exists() and bin_path.is_file():
            return bin_path

    # Compile into the first writable candidate directory
    for bin_dir in candidate_dirs:
        try:
            bin_dir.mkdir(parents=True, exist_ok=True)
            src_path = bin_dir / "ekbridge.m"
            plist_path = bin_dir / "Info.plist"
            out_path = bin_dir / bin_name

            src_path.write_text(OBJC_SOURCE, encoding="utf-8")
            plist_path.write_text(INFO_PLIST, encoding="utf-8")

            proc = subprocess.run(
                [
                    "/usr/bin/clang",
                    "-O2",
                    "-fobjc-arc",
                    "-framework",
                    "Foundation",
                    "-framework",
                    "EventKit",
                    "-framework",
                    "AppKit",
                    f"-Wl,-sectcreate,__TEXT,__info_plist,{plist_path}",
                    str(src_path),
                    "-o",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and out_path.exists():
                return out_path
        except (OSError, subprocess.SubprocessError):
            continue

    return None


def run_ekbridge(args: list[str], timeout: int = 20) -> Optional[dict[str, Any]]:
    """Execute the native EventKit bridge binary with `args` and parse JSON output."""
    binary = get_ekbridge_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [str(binary), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = (proc.stdout or "").strip()
        if not stdout:
            return None
        last_line = stdout.splitlines()[-1]
        return json.loads(last_line)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
