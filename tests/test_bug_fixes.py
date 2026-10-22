"""
tests/test_bug_fixes.py — Mixtape

Regression tests for the three bugs being fixed:
  #1 Listening streak resets on Sunday       (services/streak_service.py)
  #2 Friends Listening Now shows yesterday   (services/feed_service.py)
  #4 Rating a song sends no notification     (services/notification_service.py)

Each "bug" test fails before its fix and should pass after it.
Run with:  pytest tests/test_bug_fixes.py -v
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent
import services.feed_service as feed_service
from services.streak_service import update_listening_streak
from services.feed_service import get_friends_listening_now
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def users(app):
    """Four users: nova is friends with darius; aaliya shares a song."""
    with app.app_context():
        aaliya = User(username="aaliya", email="aaliya@example.com")
        kenji = User(username="kenji", email="kenji@example.com")
        nova = User(username="nova", email="nova@example.com")
        darius = User(username="darius", email="darius@example.com")
        db.session.add_all([aaliya, kenji, nova, darius])
        db.session.commit()

        nova.friends.append(darius)
        song = Song(title="Shared Song", artist="Artist", shared_by=aaliya.id)
        db.session.add(song)
        db.session.commit()

        yield {
            "aaliya": aaliya.id,
            "kenji": kenji.id,
            "nova": nova.id,
            "darius": darius.id,
            "song": song.id,
        }


def freeze_feed_clock(monkeypatch, fixed_now):
    """Make datetime.now() inside feed_service return fixed_now."""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(feed_service, "datetime", FrozenDatetime)


# ---------------------------------------------------------------------------
# Bug #1 — streak should never depend on the day of the week
# ---------------------------------------------------------------------------

def test_streak_counts_every_day_for_a_full_week(app, users):
    """Listening Mon through the next Mon (including Sunday) counts 1..8."""
    with app.app_context():
        u = db.session.get(User, users["kenji"])
        start = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)  # Monday

        for day in range(8):
            update_listening_streak(u, start + timedelta(days=day))
            assert u.listening_streak == day + 1


def test_long_streak_continues_on_sunday(app, users):
    """Kenji's report: streak of 12 on Saturday should become 13 on Sunday."""
    with app.app_context():
        u = db.session.get(User, users["kenji"])
        u.listening_streak = 12
        u.last_listened_at = datetime(2026, 10, 17, 22, 0, 0, tzinfo=timezone.utc)  # Saturday night

        sunday_morning = datetime(2026, 10, 18, 9, 0, 0, tzinfo=timezone.utc)
        update_listening_streak(u, sunday_morning)
        assert u.listening_streak == 13


def test_streak_continues_from_sunday_to_monday(app, users):
    """Sunday -> Monday is a consecutive day and should increment."""
    with app.app_context():
        u = db.session.get(User, users["kenji"])
        u.listening_streak = 5
        u.last_listened_at = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)  # Sunday

        update_listening_streak(u, datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc))
        assert u.listening_streak == 6


def test_streak_same_day_on_sunday_does_not_change(app, users):
    """Side-effect check: two listens on the same Sunday leave the streak alone."""
    with app.app_context():
        u = db.session.get(User, users["kenji"])
        u.listening_streak = 4
        u.last_listened_at = datetime(2026, 10, 18, 9, 0, 0, tzinfo=timezone.utc)  # Sunday

        update_listening_streak(u, datetime(2026, 10, 18, 20, 0, 0, tzinfo=timezone.utc))
        assert u.listening_streak == 4


def test_streak_still_resets_when_sunday_is_skipped(app, users):
    """Side-effect check: Saturday -> Monday skips a day and should reset."""
    with app.app_context():
        u = db.session.get(User, users["kenji"])
        u.listening_streak = 9
        u.last_listened_at = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)  # Saturday

        update_listening_streak(u, datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc))
        assert u.listening_streak == 1


# ---------------------------------------------------------------------------
# Bug #2 — Listening Now should only show friends who listened today
# ---------------------------------------------------------------------------

def add_listen(user_id, song_id, when):
    db.session.add(ListeningEvent(user_id=user_id, song_id=song_id, listened_at=when))
    db.session.commit()


def test_listening_now_hides_listen_from_last_night(app, users, monkeypatch):
    """Nova's report: darius listened at 11pm yesterday; at 9am he should not show."""
    with app.app_context():
        add_listen(users["darius"], users["song"], datetime(2026, 10, 20, 23, 0, 0, tzinfo=timezone.utc))
        freeze_feed_clock(monkeypatch, datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc))

        feed = get_friends_listening_now(users["nova"])
        assert feed == []


def test_listening_now_shows_listen_from_today(app, users, monkeypatch):
    """Side-effect check: a listen earlier today should still appear."""
    with app.app_context():
        add_listen(users["darius"], users["song"], datetime(2026, 10, 21, 8, 0, 0, tzinfo=timezone.utc))
        freeze_feed_clock(monkeypatch, datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc))

        feed = get_friends_listening_now(users["nova"])
        assert len(feed) == 1
        assert feed[0]["friend"]["username"] == "darius"


def test_listening_now_shows_only_latest_song_per_friend(app, users, monkeypatch):
    """Side-effect check: dedup still returns one entry per friend, the newest."""
    with app.app_context():
        add_listen(users["darius"], users["song"], datetime(2026, 10, 21, 7, 0, 0, tzinfo=timezone.utc))
        add_listen(users["darius"], users["song"], datetime(2026, 10, 21, 8, 30, 0, tzinfo=timezone.utc))
        freeze_feed_clock(monkeypatch, datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc))

        feed = get_friends_listening_now(users["nova"])
        assert len(feed) == 1
        assert feed[0]["listened_at"].startswith("2026-10-21T08:30")


# ---------------------------------------------------------------------------
# Bug #4 — rating someone's song should notify the person who shared it
# ---------------------------------------------------------------------------

def test_rating_notifies_song_sharer(app, users):
    """Aaliya's report: kenji rates her song 5 stars; she should be notified."""
    with app.app_context():
        rate_song(users["kenji"], users["song"], 5)

        notifications = get_notifications(users["aaliya"])
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"


def test_rating_own_song_does_not_notify(app, users):
    """Side-effect check: like playlist adds, rating your own song sends nothing."""
    with app.app_context():
        rate_song(users["aaliya"], users["song"], 4)

        assert get_notifications(users["aaliya"]) == []


def test_rating_is_still_saved(app, users):
    """Side-effect check: the rating itself is still stored with the right score."""
    with app.app_context():
        rating = rate_song(users["kenji"], users["song"], 5)

        assert rating.score == 5
        assert rating.user_id == users["kenji"]
        assert rating.song_id == users["song"]
