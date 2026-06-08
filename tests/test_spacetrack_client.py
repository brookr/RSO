import io
import unittest
import urllib.error

from pipeline import snapshot


class FakeClock:
    """Monotonic clock whose sleep() advances time, for deterministic pacing tests."""

    def __init__(self, start=1000.0):
        self.t = start
        self.sleeps = []

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def open(self, request, timeout=None):
        item = self.responses[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://www.space-track.org/x", code, "err", {}, io.BytesIO(body))


def make_client(responses):
    clock = FakeClock()
    client = snapshot.SpaceTrackClient(
        rate_limiter=snapshot.SpaceTrackRateLimiter(
            per_minute=10**9, per_hour=10**9, sleep=clock.sleep, clock=clock.time
        ),
        sleep=clock.sleep,
        jitter=lambda lo, hi: 0.0,
    )
    client.opener = FakeOpener(responses)
    return client, clock


class RateLimiterTests(unittest.TestCase):
    def test_per_minute_window_paces(self):
        clock = FakeClock()
        limiter = snapshot.SpaceTrackRateLimiter(
            per_minute=3, per_hour=10**9, sleep=clock.sleep, clock=clock.time
        )
        for _ in range(3):
            limiter.acquire()
        self.assertEqual(clock.sleeps, [])  # first 3 are free
        limiter.acquire()  # 4th must wait for the oldest to age out of 60s
        self.assertEqual(clock.sleeps, [60])

    def test_per_hour_window_paces(self):
        clock = FakeClock()
        limiter = snapshot.SpaceTrackRateLimiter(
            per_minute=10**9, per_hour=2, sleep=clock.sleep, clock=clock.time
        )
        limiter.acquire()
        limiter.acquire()
        self.assertEqual(clock.sleeps, [])
        limiter.acquire()  # 3rd must wait out the rolling hour
        self.assertEqual(clock.sleeps, [3600])

    def test_min_spacing_enforced(self):
        clock = FakeClock()
        limiter = snapshot.SpaceTrackRateLimiter(
            per_minute=10**9, per_hour=10**9, min_spacing=5, sleep=clock.sleep, clock=clock.time
        )
        limiter.acquire()
        limiter.acquire()
        self.assertEqual(clock.sleeps, [5])

    def test_rejects_non_positive_limits(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.SpaceTrackRateLimiter(per_minute=0)


class RequestRetryTests(unittest.TestCase):
    def test_retries_rate_limit_500_then_succeeds(self):
        # Space-Track signals a rate-limit violation as HTTP 500 + body marker.
        client, clock = make_client(
            [http_error(500, b"Error: you have violated your query rate limit"), FakeResp(b'{"ok":1}')]
        )
        self.assertEqual(client._request("https://www.space-track.org/q"), b'{"ok":1}')
        self.assertEqual(client.opener.calls, 2)
        self.assertEqual(len(clock.sleeps), 1)  # one backoff
        self.assertGreaterEqual(clock.sleeps[0], 60)  # rate-limit backoff waits out the minute

    def test_does_not_retry_plain_500(self):
        client, clock = make_client([http_error(500, b"syntax error in query")])
        with self.assertRaisesRegex(snapshot.SnapshotError, "HTTP 500"):
            client._request("https://www.space-track.org/q")
        self.assertEqual(client.opener.calls, 1)  # not retried
        self.assertEqual(clock.sleeps, [])

    def test_retries_transient_503_then_succeeds(self):
        client, clock = make_client([http_error(503, b"bad gateway"), FakeResp(b"ok")])
        self.assertEqual(client._request("https://www.space-track.org/q"), b"ok")
        self.assertEqual(client.opener.calls, 2)
        self.assertEqual(len(clock.sleeps), 1)

    def test_gives_up_after_max_retries(self):
        attempts = snapshot.MAX_REQUEST_RETRIES + 1
        client, _ = make_client([http_error(503) for _ in range(attempts + 3)])
        with self.assertRaises(snapshot.SnapshotError):
            client._request("https://www.space-track.org/q")
        self.assertEqual(client.opener.calls, attempts)


if __name__ == "__main__":
    unittest.main()
