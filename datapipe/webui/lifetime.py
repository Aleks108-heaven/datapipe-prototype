"""Knowing whether anyone still has the page open, so that a program with no window of its own (the Mac app) can stop itself.

Every open page tells the server it is alive every few seconds and says goodbye when it is closed or reloaded. The server may stop
when no page is left, and only then: a run in progress, or a page that merely went quiet (a background tab, a sleeping laptop) never
ends the program."""
import time

FIRST_PAGE_WITHIN = 600       # seconds the browser is given to open the page the first time
GOODBYE_GRACE = 10            # seconds after the last goodbye: a reload says goodbye and is back within moments
STALE_AFTER = 30 * 60         # a page not heard from this long (a frozen tab) is given up on


class PageWatch:
    def __init__(self, clock=time.monotonic, first_page_within=FIRST_PAGE_WITHIN, grace=GOODBYE_GRACE, stale_after=STALE_AFTER):
        self._clock = clock
        self._first, self._grace, self._stale = first_page_within, grace, stale_after
        self._born = clock()
        self._pages = {}                  # page id -> when it was last heard from
        self._left_at = None              # when the last page went away (None while there never was one)

    def alive(self, page_id):
        self._pages[page_id] = self._clock()
        self._left_at = None

    def bye(self, page_id):
        if self._pages.pop(page_id, None) is not None and not self._pages:
            self._left_at = self._clock()

    def open_pages(self):
        self._forget_stale()
        return len(self._pages)

    def _forget_stale(self):
        now = self._clock()
        for page_id in [p for p, seen in self._pages.items() if now - seen > self._stale]:
            del self._pages[page_id]
            if not self._pages:
                self._left_at = now

    def should_stop(self, busy=False):
        """True when nobody has the page open any more and nothing is running."""
        if busy:
            return False
        self._forget_stale()
        if self._pages:
            return False
        now = self._clock()
        if self._left_at is None:                       # no page has ever been opened
            return now - self._born > self._first
        return now - self._left_at > self._grace
