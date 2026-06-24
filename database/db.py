import sqlite3
import os

# Path to your database file — it will be created here automatically
DB_PATH = os.path.join('data', 'search.db')


def get_connection():
    """Open and return a connection to the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets you access columns by name like a dict
    return conn


def init_db():
    """Create all tables if they don't already exist."""
    conn = get_connection()

    # --- TABLE 1: pages ---
    # Stores every webpage you crawl — its URL, title, description, etc.
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT UNIQUE NOT NULL,
            title       TEXT,
            description TEXT,
            word_count  INTEGER DEFAULT 0,
            inbound_links INTEGER DEFAULT 0,
            crawled_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- TABLE 2: links ---
    # Stores relationships between pages (which page links to which)
    # This is what PageRank uses later to rank results
    conn.execute('''
        CREATE TABLE IF NOT EXISTS links (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            from_url TEXT NOT NULL,
            to_url   TEXT NOT NULL,
            UNIQUE(from_url, to_url)  -- prevents duplicate link records
        )
    ''')

    # --- TABLE 3: crawl_queue ---
    # A to-do list of URLs your crawler still needs to visit
    conn.execute('''
        CREATE TABLE IF NOT EXISTS crawl_queue (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            url      TEXT UNIQUE NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status   TEXT DEFAULT 'pending'  -- can be: pending, done, failed
        )
    ''')

    # --- FTS5 VIRTUAL TABLE: search_index ---
    # This is the actual search engine heart.
    # FTS5 builds an inverted index automatically so searches are instant.
    # 'porter ascii' tokenizer means: "running" also matches "run" and "runs"
    # Standalone (not external-content) FTS5 table — it stores its own copy
    # of title/body since the 'pages' table has no 'body' column to link to.
    conn.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS search_index
        USING fts5(
            url    UNINDEXED,   -- stored but NOT searchable (just an identifier)
            title,              -- searchable
            body,               -- searchable (the page's full text)
            tokenize='porter ascii'
        )
    ''')

    conn.commit()
    conn.close()
    print('Database initialized successfully.')


# ──────────────────────────────────────────────
# HELPER FUNCTIONS
# ──────────────────────────────────────────────

def save_page(url, title, description, body, word_count=0):
    """
    Insert a crawled page into the database.
    Also adds it to the FTS5 search index so it becomes searchable.
    """
    conn = get_connection()
    try:
        # Save into the main pages table
        conn.execute('''
            INSERT OR IGNORE INTO pages (url, title, description, word_count)
            VALUES (?, ?, ?, ?)
        ''', (url, title, description, word_count))

        # Also add to the search index (so users can find it)
        conn.execute('''
            INSERT INTO search_index (url, title, body)
            VALUES (?, ?, ?)
        ''', (url, title, body))

        conn.commit()
        print(f'Saved: {url}')
    except Exception as e:
        print(f'Error saving page: {e}')
    finally:
        conn.close()


def get_page(url):
    """
    Fetch a single page from the database by its URL.
    Returns the row as a dict-like object, or None if not found.
    """
    conn = get_connection()
    row = conn.execute('''
        SELECT * FROM pages WHERE url = ?
    ''', (url,)).fetchone()
    conn.close()
    return row


def page_exists(url):
    """
    Check if a URL has already been crawled.
    Returns True or False.
    """
    conn = get_connection()
    row = conn.execute('''
        SELECT id FROM pages WHERE url = ?
    ''', (url,)).fetchone()
    conn.close()
    return row is not None


# ──────────────────────────────────────────────
# CRAWL QUEUE — lets the crawler persist where it's up to,
# so a run can be killed and resumed without losing progress.
# ──────────────────────────────────────────────

def add_to_queue(url):
    """Add a URL to the crawl queue (no-op if already queued)."""
    conn = get_connection()
    try:
        conn.execute('''
            INSERT OR IGNORE INTO crawl_queue (url, status)
            VALUES (?, 'pending')
        ''', (url,))
        conn.commit()
    finally:
        conn.close()


def get_next_url():
    """Pop the oldest pending URL off the queue and mark it in-progress.

    Returns the URL string, or None if the queue is empty.
    """
    conn = get_connection()
    try:
        row = conn.execute('''
            SELECT url FROM crawl_queue WHERE status = 'pending'
            ORDER BY id ASC LIMIT 1
        ''').fetchone()
        if row is None:
            return None
        conn.execute('''
            UPDATE crawl_queue SET status = 'done' WHERE url = ?
        ''', (row['url'],))
        conn.commit()
        return row['url']
    finally:
        conn.close()


def mark_queue_failed(url):
    """Mark a queued URL as failed (so it's not retried forever)."""
    conn = get_connection()
    try:
        conn.execute('''
            UPDATE crawl_queue SET status = 'failed' WHERE url = ?
        ''', (url,))
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# TEST — run this file directly to test everything
# ──────────────────────────────────────────────

if __name__ == '__main__':
    # Step 1: Initialize the database (creates the file + all tables)
    init_db()

    # Step 2: Insert a dummy page to test save_page()
    save_page(
        url='https://example.com',
        title='Example Domain',
        description='This is a test page for our search engine.',
        body='This is the full body text of the example page. It talks about searching and indexing.',
        word_count=18
    )

    # Step 3: Test page_exists()
    print('Page exists?', page_exists('https://example.com'))       # True
    print('Page exists?', page_exists('https://nothere.com'))       # False

    # Step 4: Test get_page()
    page = get_page('https://example.com')
    print('Fetched page title:', page['title'])
    print('Fetched page description:', page['description'])

    print('\nAll tests passed!')