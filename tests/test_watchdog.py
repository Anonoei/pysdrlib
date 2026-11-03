"""The stream watchdog: stalls, and samples a device failed to deliver. Pure logic on a fake clock."""
import threading

import pytest

from pysdrlib.base.watchdog import StreamStats, Watchdog

RATE = 1_000_000
CHUNK = 10_000   # 10 ms of samples


class Clock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def dog(clock):
    d = Watchdog(stall_timeout=1.0, clock=clock)
    d.start(RATE)
    return d


def stream(dog, clock, seconds, rate=RATE, chunk=CHUNK):
    """Deliver `seconds` of stream at `rate`, a chunk at a time"""
    for _ in range(int(seconds * rate / chunk)):
        clock.advance(chunk / rate)
        dog.feed(chunk)


class TestStall:
    def test_a_steady_stream_never_stalls(self, dog, clock):
        for _ in range(20):
            stream(dog, clock, 0.5)
            report = dog.poll()
            assert not report.stalled and not report.new_stall and report.missing == 0

    def test_stalls_after_the_timeout_without_data(self, dog, clock):
        stream(dog, clock, 0.5)
        clock.advance(0.9)
        assert not dog.poll().stalled
        clock.advance(0.2)
        report = dog.poll()
        assert report.stalled and report.new_stall

    def test_is_reported_new_only_once(self, dog, clock):
        stream(dog, clock, 0.2)
        clock.advance(2)
        assert dog.poll().new_stall
        clock.advance(2)
        report = dog.poll()
        assert report.stalled and not report.new_stall
        assert dog.stats().stalls == 1

    def test_a_stream_that_never_starts_stalls(self, dog, clock):
        clock.advance(1.5)
        assert dog.poll().stalled

    def test_recovers_when_data_resumes(self, dog, clock):
        stream(dog, clock, 0.2)
        clock.advance(2)
        assert dog.poll().stalled
        stream(dog, clock, 0.1)
        assert not dog.poll().stalled
        clock.advance(2)
        assert dog.poll().new_stall and dog.stats().stalls == 2

    def test_the_gap_of_a_stall_is_not_also_a_rate_shortfall(self, dog, clock):
        stream(dog, clock, 0.5)
        dog.poll()
        clock.advance(3)
        dog.poll()
        stream(dog, clock, 1.0)
        assert dog.poll().missing == 0 and dog.stats().missing == 0

    def test_disabled_with_none(self, dog, clock):
        dog.stall_timeout = None
        clock.advance(1000)
        report = dog.poll()
        assert not report.stalled and report.missing == 0

    def test_not_watching_before_start_or_after_stop(self, clock):
        dog = Watchdog(clock=clock)
        clock.advance(1000)
        assert not dog.poll().stalled
        dog.start(RATE)
        dog.stop()
        clock.advance(1000)
        assert not dog.poll().stalled and dog.stats().age is None

    def test_start_resets_everything(self, dog, clock):
        stream(dog, clock, 0.5)
        clock.advance(2)
        dog.poll()
        dog.start(RATE)
        assert dog.stats() == StreamStats(0, 0, 0, 0, 0.0)
        assert not dog.poll().stalled


class TestMissingSamples:
    def test_a_device_at_half_rate_is_missing_half(self, dog, clock):
        stream(dog, clock, 0.1, rate=RATE)   # first chunks mark time zero
        dog.poll()
        total = 0
        for _ in range(8):
            stream(dog, clock, 0.25, rate=RATE // 2)
            total += dog.poll().missing
        assert dog.stats().missing == total
        actual = RATE // 2 * 2   # half the stream, for the 2 s that followed
        assert 0.7 * actual < total <= actual   # a lower bound: each window forgives two chunks

    def test_a_shortfall_is_one_episode_until_the_stream_recovers(self, dog, clock):
        stream(dog, clock, 0.2)
        dog.poll()
        starts = []
        for rate in [RATE // 2] * 3 + [RATE] * 2 + [RATE // 2] * 2:
            stream(dog, clock, 0.3, rate=rate)   # every poll closes a window
            report = dog.poll()
            starts.append((report.missing > 0, report.shortfall_start))
        assert starts == [(True, True), (True, False), (True, False), (False, False), (False, False),
                          (True, True), (True, False)]

    def test_normal_jitter_is_not_missing(self, dog, clock):
        stream(dog, clock, 0.2)
        dog.poll()
        for _ in range(40):
            stream(dog, clock, 0.25, rate=int(RATE * 0.95))   # 5% slow is inside the tolerance
            assert dog.poll().missing == 0

    def test_chunks_arrive_whole(self, dog, clock):
        """With big chunks a window can legitimately hold one fewer than the rate suggests"""
        big = RATE // 4   # 250 ms chunks
        stream(dog, clock, 1.0, chunk=big)
        dog.poll()
        for _ in range(8):
            stream(dog, clock, 0.5, chunk=big)
            assert dog.poll().missing == 0

    def test_windows_are_at_least_window_long(self, dog, clock):
        stream(dog, clock, 0.3)
        dog.poll()
        clock.advance(0.1)   # too short to judge, even though nothing arrived
        assert dog.poll().missing == 0

    def test_rate_change_restarts_judgement(self, dog, clock):
        stream(dog, clock, 0.5)
        dog.poll()
        dog.set_rate(RATE * 4)   # the device is now supposed to deliver 4x: don't blame the old data
        stream(dog, clock, 0.5, rate=RATE * 4)
        assert dog.poll().missing == 0


def test_stats_age(dog, clock):
    stream(dog, clock, 0.5)
    clock.advance(0.3)
    stats = dog.stats(dropped=7)
    assert stats.samples == 50 * CHUNK and stats.dropped == 7
    assert stats.age == pytest.approx(0.3)


def test_feeding_from_another_thread_while_polling():
    dog = Watchdog(stall_timeout=5.0)
    dog.start(RATE)
    done = threading.Event()
    def feeder():
        for _ in range(2000):
            dog.feed(1)
        done.set()
    t = threading.Thread(target=feeder)
    t.start()
    while not done.is_set():
        dog.poll()
        dog.stats()
    t.join()
    assert dog.stats().samples == 2000


class Driver:
    """The counters a native driver keeps on its own thread, as `observe` is given them"""
    def __init__(self, clock):
        self.clock, self.samples, self.last, self.max_chunk = clock, 0, None, 0

    def deliver(self, n):
        self.samples += n
        self.last = self.clock()
        self.max_chunk = max(self.max_chunk, n)

    def observe(self, dog):
        dog.observe(self.samples, None if self.last is None else self.clock() - self.last, self.max_chunk)

    def stream(self, dog, seconds, rate=RATE, chunk=CHUNK, poll_every=0.25):
        """Deliver `seconds` of stream, polling as a consumer would; returns the reports"""
        reports, since = [], 0.0
        for _ in range(int(seconds * rate / chunk)):
            self.clock.advance(chunk / rate)
            self.deliver(chunk)
            since += chunk / rate
            if since >= poll_every:
                since = 0.0
                self.observe(dog)
                reports.append(dog.poll())
        return reports


class TestObserve:
    """The same judgements from the driver's counters (`observe`) as from per-chunk `feed`"""

    @pytest.fixture
    def driver(self, clock):
        return Driver(clock)

    def test_a_steady_stream_is_healthy(self, dog, clock, driver):
        reports = driver.stream(dog, 5)
        assert reports and not any(r.stalled or r.missing for r in reports)

    def test_stalls_after_the_timeout_without_data(self, dog, clock, driver):
        driver.stream(dog, 0.5)
        clock.advance(0.9)
        driver.observe(dog)
        assert not dog.poll().stalled
        clock.advance(0.2)
        driver.observe(dog)
        report = dog.poll()
        assert report.stalled and report.new_stall

    def test_a_device_that_never_starts_stalls(self, dog, clock, driver):
        clock.advance(2)
        driver.observe(dog)   # age None: nothing has arrived
        assert dog.poll().stalled

    def test_a_shortfall_is_reported_once(self, dog, clock, driver):
        driver.stream(dog, 0.5)
        reports = driver.stream(dog, 1.0, rate=RATE // 2)   # delivering half what was asked
        assert sum(r.shortfall_start for r in reports) == 1 and sum(r.missing for r in reports) > 0

    def test_samples_and_age_are_in_stats(self, dog, clock, driver):
        driver.deliver(1000)
        clock.advance(0.1)
        driver.observe(dog)
        stats = dog.stats()
        assert stats.samples == 1000 and stats.age == pytest.approx(0.1)

    def test_ignored_when_not_running(self, clock, driver):
        dog = Watchdog(clock=clock)   # never started
        driver.deliver(1000)
        dog.observe(driver.samples, 0.0, 1000)
        assert dog.stats().samples == 0 and dog.stats().age is None

    def test_a_late_burst_is_not_a_shortfall(self, dog, clock, driver):
        """Delivery held up for 100 ms and then caught up: nothing was lost, so nothing is reported"""
        driver.stream(dog, 0.5)
        clock.advance(0.1)
        driver.deliver(RATE // 10)   # the whole backlog at once
        driver.observe(dog)
        assert dog.poll().missing == 0
        reports = driver.stream(dog, 1.0)
        assert not any(r.missing for r in reports)
