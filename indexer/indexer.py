import string
import sys

from nltk.corpus import stopwords
from nltk.stem import PorterStemmer
from nltk.tokenize import word_tokenize

from database.db import (
    compute_inbound_link_counts,
    get_all_pages_with_body,
    update_inbound_links,
    update_word_count,
)

_stemmer = PorterStemmer()
_stop_words = set(stopwords.words("english"))


def clean_text(text):
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return word_tokenize(text)


def remove_stopwords(tokens):
    return [token for token in tokens if token not in _stop_words]


def stem_tokens(tokens):
    return [_stemmer.stem(token) for token in tokens]


def process_text(text):
    tokens = clean_text(text)
    tokens = remove_stopwords(tokens)
    tokens = stem_tokens(tokens)
    return tokens


def reindex_all():
    """Process every crawled page: update word counts and inbound link counts."""
    pages = get_all_pages_with_body()
    total = len(pages)
    print(f'Reindexing {total} pages...')

    for i, page in enumerate(pages, start=1):
        tokens = process_text(page['body'] or '')
        update_word_count(page['url'], len(tokens))
        _print_progress(i, total)

    print()  # move past the progress line

    inbound_counts = compute_inbound_link_counts()
    for url, count in inbound_counts.items():
        update_inbound_links(url, count)

    print(f'Updated inbound link counts for {len(inbound_counts)} URLs.')
    print('Reindexing complete.')


def _print_progress(current, total, width=30):
    filled = int(width * current / total) if total else width
    bar = '#' * filled + '-' * (width - filled)
    sys.stdout.write(f'\r[{bar}] {current}/{total}')
    sys.stdout.flush()


if __name__ == "__main__":
    reindex_all()
