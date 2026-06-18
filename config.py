# config.py — all project settings in one place
# Change these values to control how your search engine behaves

# ──────────────────────────────────────────────
# CRAWLER SETTINGS
# ──────────────────────────────────────────────

# Seed URLs — the starting points for your crawler
SEED_URLS = [
    'https://realpython.com',
    'https://www.python.org/doc/',
    'https://docs.python-requests.org/en/latest/',
]

# How many pages to crawl in total before stopping
MAX_PAGES = 500

# Seconds to wait between requests (be polite — don't hammer websites!)
CRAWL_DELAY = 1.5

# How deep to follow links from the seed URL
# 1 = only the seed page itself
# 2 = seed page + all links found on it
# 3 = seed page + its links + those links' links
MAX_DEPTH = 3

# If True, only follow links that stay on the same domain as the seed URL
# e.g. realpython.com links will not go off to youtube.com
STAY_ON_DOMAIN = True

# ──────────────────────────────────────────────
# SEARCH SETTINGS
# ──────────────────────────────────────────────

# Number of results to show per search page
RESULTS_PER_PAGE = 10