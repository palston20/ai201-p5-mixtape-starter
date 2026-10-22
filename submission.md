

## Main Files 
- **apnotification_service.pyry. Configures SQLite, registers the four blueprints (`/songs`, `/playlists`, `/users`, `/feed`), and creates tables. No route logic lives here.
- **models.py**: SQLAlchemy models: User, Song, Tag, Rating, ListeningEvent, Playlist, Notification. Association tables: `friendships` (user↔user), `song_tags` (song↔tag), and `playlist_entries` (playlist↔song, with `position`, `added_by`, `added_at`).
- **routes/**: Thin HTTP layer. Each route validates JSON/query params, calls one service function, and maps `ValueError` to a 400/404.
- **services/**: All business logic (and all five bugs):
  - `streak_service.py`: records listening events, updates the daily streak (Bug #1)
  - `feed_service.py`: "Friends Listening Now" (last 24h, one event per friend) and the activity feed (Bug #2)
  - `search_service.py`: song search and lookup (Bug #3)
  - `notification_service.py`: creates notifications; also owns `add_to_playlist` and `rate_song` (Bug #4)
  - `playlist_service.py`: create/get playlists and ordered playlist songs (Bug #5)

## Data Flow
**Creating a playlist:** `POST /playlists/` → `routes/playlists.py: create()` checks `name` and `created_by` → `playlist_service.create_playlist()` looks up the user (raises `ValueError` if not found), inserts the Playlist, commits → route returns `playlist.to_dict()` with 201.

**Adding a song to a playlist:** `POST /playlists/<id>/songs` → `routes/playlists.py: add_song()` → `notification_service.add_to_playlist()` validates song/user/playlist, appends the song to `playlist.songs`, then notifies the song's original sharer if someone else added it.

## Patterns 
A pattern I noticed is that some functions live in files you wouldn't expect from their names. For example, rate_song() is in notification_service.py even though rating a song is song activity, not notification logic. add_to_playlist() is also in notification_service.py instead of playlist_service.py. 


## Issues
The three issues im gonna fix are: 
- Rate song issue 
- Streak issue 
- Listening Now issue 

## How You Reproduced 
* Issue #4 --> Rate Song 
Steps I took:

Had a friend add my shared song to a playlist → notification arrived. ✅
Had the same friend rate a different song I shared (POST /songs/<song_id>/rate) → checked my notifications.
Expected: a notification for the rating, same as for the playlist add. Actual: rating is saved (it shows on the song), but no notification is ever created.

* Issue #1 --> Streak 
Steps I took:

Listened to a song every calendar day, including Saturday.
Listened again Sunday morning and checked my streak (GET /users/<my_id>/streak).
Expected: streak goes from 12 to 13 — I listened on consecutive days. Actual: streak shows 1, as if I'd skipped a day.

* Issue #2 --> Listening Activity 
Steps I took:

Opened my feed in the morning (GET /feed/<my_id>/listening-now).
Cross-checked with darius: his last listen was the previous night.
Expected: only friends who have listened today appear. Actual: friends whose last listen was yesterday evening still show up the next morning.


## Root Cause Analysis
**Issue #1 -- Streak Resetting On Sunday**
Kenji's report said his streak reset only on Sundays, so I traced the listen endpoint (POST /songs/<id>/listen in routes/songs.py) to record_listening_event() in services/streak_service.py, which calls update_listening_streak(). In that function, days_since_last correctly came out as 1 for a Saturday-to-Sunday listen, so the day math was fine. I knew I'd found the cause when I read the elif condition, days_since_last == 1 and today.weekday() != 6. Python's weekday() returns 6 for Sunday, so on Sundays that condition failed even when the user had listened the day before. The code then fell through to the else branch and reset the streak to 1, no matter how long it was.

I removed and today.weekday() != 6, leaving only days_since_last == 1. The day of the week should never decide whether a streak continues; the only rule is whether the user listened yesterday, which is what the docstring describes. Now the streak can't reset because of what day it is, Sunday or any other. To check for side effects, I reran the reproduction with one listen per day from Monday through the next Monday, and the streak counted 1 through 8 with no reset. I also confirmed that listening twice on the same day leaves the streak unchanged and that skipping a day still resets it to 1. o make sure nothing else broke, I ran the existing streak tests in tests/test_streaks.py along with a new test file, tests/test_bug_fixes.py. The new file covers edge cases around the fix: a full Monday-to-Monday week that should count 1 through 8, a 12-day streak continuing on Sunday, Sunday to Monday adding 1, two listens on the same Sunday leaving the streak unchanged, and skipping Sunday still resetting the streak to 1. After my fix, all of the streak tests passed.

**Issue #2--> Listening Activity Won't Update** 
Nova's report said friends from the night before still appeared in "Friends Listening Now" the next morning, so I traced the endpoint she used. GET /feed/<id>/listening-now in routes/feed.py calls get_friends_listening_now() in services/feed_service.py, which filters listening events with ListeningEvent.listened_at >= cutoff. I knew I'd found the cause when I saw cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD, where RECENT_THRESHOLD is timedelta(hours=24). That made the cutoff a rolling window of the last 24 hours instead of the start of today. A listen at 11pm stayed inside the window until 11pm the next day, which is exactly what nova described.

I changed the cutoff to datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0), which is midnight UTC at the start of the current day, so only friends who have listened today appear. The old calculation wasn't wrong; it measured the wrong window for a feature meant to show today's listening. To check for side effects, I ran tests confirming that a listen from 11pm the night before no longer appears the next morning, that a listen from earlier today still appears, and that each friend still shows only their most recent song. All of them passed. The separate activity feed (get_activity_feed()) doesn't use the cutoff, so it wasn't affected.

**Issue #4--> No Notificaiton for Ratings**
Aaliya's report said she got a notification when kenji added her song to a playlist but not when he rated one, so I compared the two code paths. POST /songs/<id>/rate in routes/songs.py calls rate_song(), and POST /playlists/<id>/songs calls add_to_playlist(); both live in services/notification_service.py. I knew I'd found the cause when I put the two functions side by side. add_to_playlist() ends by calling create_notification() for the song's sharer, but rate_song() saves the rating, commits, and returns without ever calling it. Nothing in the rating path was broken; the notification step was missing entirely, which is why the rating showed on the song but no notification ever appeared.

I added the same notification step to rate_song(), after the rating is committed. If the rater isn't the person who shared the song, it calls create_notification() with the type song_rated and a message naming the rater, the song, and the score. To check for side effects, I ran tests confirming that the sharer gets exactly one song_rated notification, that rating your own song sends no notification (matching how playlist adds work), and that the rating is still saved with the correct score. All of them passed, and the other test files showed no new failures.
