"""What an unattended run is allowed to send.

The CLI has a human who can say "I've seen it", and that acknowledgement is what
ends a notification. A scheduled run has nobody: left as-is it would re-send the
same overdue deadline every morning until the deadline passed, which is the
nagging this product exists to replace — only now it is in your inbox.

So the scheduled path notifies on *change*. The set of reasons the agent wants a
human is fingerprinted; an unchanged fingerprint sends nothing. New reasons, or
reasons that have gone away, are news. Everything else is the weather.
"""

from __future__ import annotations

from src.notify import fingerprint, should_notify


class TestFingerprint:
    def test_is_stable_across_ordering(self) -> None:
        """Reason order comes from dict iteration and must not create false news."""
        a = fingerprint(["VAT registration is overdue", "MTD applies to you"])
        b = fingerprint(["MTD applies to you", "VAT registration is overdue"])
        assert a == b

    def test_differs_when_a_reason_appears(self) -> None:
        before = fingerprint(["MTD applies to you"])
        after = fingerprint(["MTD applies to you", "VAT registration is overdue"])
        assert before != after

    def test_differs_when_a_reason_disappears(self) -> None:
        before = fingerprint(["MTD applies to you", "VAT registration is overdue"])
        after = fingerprint(["MTD applies to you"])
        assert before != after

    def test_no_reasons_has_its_own_fingerprint(self) -> None:
        assert fingerprint([]) != fingerprint(["anything"])


class TestShouldNotify:
    def test_first_ever_run_with_something_to_say_notifies(self) -> None:
        assert should_notify(["MTD applies to you"], last_sent=None) is True

    def test_repeating_the_same_thing_is_silent(self) -> None:
        reasons = ["MTD applies to you", "quarterly update was due 9 days ago"]
        assert should_notify(reasons, last_sent=fingerprint(reasons)) is False

    def test_a_new_reason_breaks_the_silence(self) -> None:
        before = ["MTD applies to you"]
        after = ["MTD applies to you", "VAT registration applies to you"]
        assert should_notify(after, last_sent=fingerprint(before)) is True

    def test_nothing_to_say_never_notifies(self) -> None:
        """Not even to report that the previous problem cleared.

        An unattended agent has no business sending mail to say it has nothing to
        say. The state is recorded so the next real change is detected; no
        message goes out.
        """
        assert should_notify([], last_sent=None) is False
        assert should_notify([], last_sent=fingerprint(["MTD applies to you"])) is False

    def test_a_resolved_problem_updates_state_without_mailing(self) -> None:
        cleared = fingerprint([])
        assert should_notify([], last_sent=cleared) is False
        # ...and the next genuine problem still gets through.
        assert should_notify(["something new"], last_sent=cleared) is True
